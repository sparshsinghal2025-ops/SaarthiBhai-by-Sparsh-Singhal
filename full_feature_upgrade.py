"""SaarthiBhai v8.1 completeness upgrade.

This module is intentionally imported by the existing app.py after all core
objects/functions are defined. It adds concrete implementations for features
that were previously aliases/stubs/partial integrations while keeping all
business/provider values in configuration or environment variables.
"""
from __future__ import annotations

import base64
import difflib
import io
import json
import os
import re
import time
import urllib.parse
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import requests
from flask import jsonify, request, send_file


def register_full_feature_upgrade(namespace: dict) -> None:
    app = namespace["app"]
    config = namespace["config"]
    db = namespace["db"]
    ai = namespace["ai"]
    run_ai = namespace["run_ai"]
    get_ai = namespace["get_ai_answer_v9"]
    normalize_phone = namespace["normalize_phone"]
    guess_exam_subject = namespace["guess_exam_subject"]
    youtube_links = namespace.get("_youtube_links")
    now_ist = namespace["_now_ist"]
    today_ist = namespace["_today_ist"]

    upgrade_cfg = getattr(config, "CONTENT", {}).get("upgrade_completeness", {}) or {}
    source_cfg = upgrade_cfg.get("verified_sources", {}) or {}
    rss_feeds = upgrade_cfg.get("current_affairs", {}).get("rss_feeds", []) or []
    ext_cfg = upgrade_cfg.get("external", {}) or {}
    ppt_cfg = upgrade_cfg.get("ppt", {}) or {}
    copy_cfg = upgrade_cfg.get("copy_check", {}) or {}
    diagram_cfg = upgrade_cfg.get("diagram", {}) or {}
    voice_cfg = upgrade_cfg.get("voice", {}) or {}

    def _uid(data: dict) -> str:
        uid = str(data.get("uid") or data.get("client_id") or "").strip()
        if uid and not uid.startswith(("web:", "wa:", "tg:", "ig:", "reddit:", "discord:", "snap:")):
            if data.get("client_id"):
                uid = f"web:{uid}"
        return uid

    def _user(uid: str) -> dict:
        if not uid:
            return {}
        u = db.get_user(uid) or {}
        if not u and uid.startswith("web:"):
            u = db.ensure_user(uid, full_name=config.DEFAULT_STUDENT_NAMES.get("web", "Web Student"), platform="web")
        return u

    def _pro(uid: str) -> bool:
        return bool(uid and db.is_pro(uid))

    def _limit_guard(uid: str) -> Optional[Any]:
        if not uid or _pro(uid):
            return None
        can, quota = db.try_consume_quota(uid)
        if not can:
            return jsonify({
                "ok": False,
                "error": config.PRODUCT_TEXT.get("free_limit", "Free limit khatam. Kal phir aana ya Pro le lo."),
                "quota": quota,
                "upgrade": True,
            }), 429
        return None

    def _library_source(question: str, exam: str = "", subject: str = "", source_types: Optional[List[str]] = None) -> Optional[dict]:
        """Only call a source 'verified' when a matching library row carries an allowed source_type."""
        rows = db.search_library(question, exam_type=exam, subject=subject, limit=8)
        allowed = {str(x).strip().lower() for x in (source_types or [])}
        for row in rows:
            st = str(row.get("source_type") or "").strip().lower()
            if allowed and st not in allowed:
                continue
            if row.get("content") or row.get("answer"):
                return row
        return None

    def _answer(prompt: str, tool: str, uid: str = "", language: str = "hinglish", pro: Optional[bool] = None, exam: str = "", subject: str = "") -> str:
        is_pro = _pro(uid) if pro is None else bool(pro)
        return get_ai(prompt, tool, is_pro, language=language, phone_number=(normalize_phone((_user(uid) or {}).get("phone_number", "")) if uid else ""), exam_type=exam or (_user(uid) or {}).get("exam_type", ""), subject=subject or (_user(uid) or {}).get("subject", ""), uid=uid) or ""

    @app.route("/api/tool/notes", methods=["POST"])
    def full_notes_tool():
        data = request.get_json(silent=True) or {}
        q = str(data.get("topic") or data.get("question") or "").strip()
        if not q:
            return jsonify({"ok": False, "error": "topic required"}), 400
        uid = _uid(data); pro = _pro(uid)
        requested = str(data.get("length") or data.get("mode") or "short").lower()
        length = requested if requested in {"short", "long"} else "short"
        if not pro and length == "long":
            return jsonify({"ok": False, "error": "Long notes are Pro-only", "pro_only": True}), 403
        cap = int(upgrade_cfg.get("notes", {}).get("short_words" if length == "short" else "long_words", 450 if length == "short" else 1400))
        prompt = (
            f"Create {'short' if length == 'short' else 'detailed'} student notes on: {q}. "
            f"Maximum about {cap} words. Include definitions, key points, examples, common mistakes and a 1-line recap."
        )
        ans = _answer(prompt, "notes", uid=uid, language=str(data.get("language") or db.get_language(uid) if uid else "hinglish"))
        return jsonify({"ok": True, "notes": ans, "length": length, "pro": pro})

    @app.route("/api/tool/pyq", methods=["POST"])
    def full_pyq_tool():
        data = request.get_json(silent=True) or {}
        q = str(data.get("topic") or data.get("question") or "").strip()
        if not q:
            return jsonify({"ok": False, "error": "topic required"}), 400
        uid = _uid(data); pro = _pro(uid)
        if not pro:
            return jsonify({"ok": False, "error": "PYQ bank is Pro-only", "pro_only": True}), 403
        exam = str(data.get("exam") or "").strip()
        subject = str(data.get("subject") or "").strip()
        verified = _library_source(q, exam, subject, source_cfg.get("pyq", []))
        if verified:
            return jsonify({"ok": True, "verified": True, "source": verified.get("source_type"), "content": verified.get("content") or verified.get("answer"), "url": verified.get("url", "")})
        ans = _answer(f"Create 5 PYQ-style questions for {exam or 'the requested exam'} on {q}. Label them clearly as AI-generated PYQ-style practice, not as verified historical questions.", "pyq", uid=uid, language=str(data.get("language") or "hinglish"), exam=exam, subject=subject)
        return jsonify({"ok": True, "verified": False, "content": ans, "note": "No verified PYQ source was found in the library; this is PYQ-style practice."})

    @app.route("/api/tool/ncert", methods=["POST"])
    def full_ncert_tool():
        data = request.get_json(silent=True) or {}
        q = str(data.get("topic") or data.get("question") or "").strip()
        if not q:
            return jsonify({"ok": False, "error": "topic required"}), 400
        uid = _uid(data); pro = _pro(uid)
        verified = _library_source(q, str(data.get("exam") or ""), str(data.get("subject") or ""), source_cfg.get("ncert", []))
        if verified:
            return jsonify({"ok": True, "verified_source": True, "content": verified.get("content") or verified.get("answer"), "source": verified.get("source_type"), "url": verified.get("url", "")})
        ans = _answer(f"Explain the NCERT-aligned concept for: {q}. Do not pretend you are quoting NCERT verbatim. Give a clear paraphrased solution and say it is an AI-generated explanation when no verified source is available.", "ncert", uid=uid, language=str(data.get("language") or "hinglish"), pro=pro, exam="k12", subject=str(data.get("subject") or ""))
        return jsonify({"ok": True, "verified_source": False, "content": ans, "note": "No verified NCERT source was found in the library; answer is a paraphrased AI explanation."})

    @app.route("/api/tool/important-questions", methods=["POST"])
    def important_questions_tool():
        data = request.get_json(silent=True) or {}
        q = str(data.get("topic") or data.get("question") or "").strip()
        if not q:
            return jsonify({"ok": False, "error": "topic required"}), 400
        uid = _uid(data); pro = _pro(uid)
        if not pro:
            return jsonify({"ok": False, "error": "Important Questions are Pro-only", "pro_only": True}), 403
        ans = _answer(f"Generate the most important exam-focused questions for {q}. Separate conceptual, numerical and long-answer questions. Do not claim any question will definitely appear.", "important_questions", uid=uid, language=str(data.get("language") or "hinglish"))
        return jsonify({"ok": True, "content": ans})

    @app.route("/api/tool/youtube-notes", methods=["POST"])
    def youtube_notes_tool():
        data = request.get_json(silent=True) or {}
        q = str(data.get("topic") or data.get("question") or "").strip()
        url = str(data.get("url") or "").strip()
        transcript = str(data.get("transcript") or "").strip()
        uid = _uid(data); pro = _pro(uid)
        if not q and not url and not transcript:
            return jsonify({"ok": False, "error": "topic, url or transcript required"}), 400
        if not pro and uid:
            if db.redis:
                daykey = f"resource:video-notes:{uid}:{today_ist()}"
                if not db.redis.set(daykey, "1", nx=True, ex=90000):
                    return jsonify({"ok": False, "error": "Free video-notes quota used for today", "upgrade": True}), 429
        if not transcript and url:
            try:
                from youtube_transcript_api import YouTubeTranscriptApi
                parsed = urllib.parse.urlparse(url)
                video_id = urllib.parse.parse_qs(parsed.query).get("v", [""])[0]
                if not video_id and parsed.netloc.endswith("youtu.be"):
                    video_id = parsed.path.strip("/").split("/")[0]
                if not video_id and "/shorts/" in parsed.path:
                    video_id = parsed.path.split("/shorts/",1)[1].split("/",1)[0]
                if video_id:
                    fetched = YouTubeTranscriptApi().fetch(video_id)
                    transcript = "\n".join(seg.text for seg in fetched)[:30000]
            except Exception:
                transcript = ""
        if transcript:
            ans = _answer(f"Summarize this supplied video transcript into student notes with 5 revision questions.\n\nTranscript:\n{transcript[:30000]}", "youtube", uid=uid)
            return jsonify({"ok": True, "notes": ans, "source_url": url, "source_type": "youtube_transcript" if url else "user_supplied_transcript"})
        links = youtube_links(q or url, config.PRO_VIDEO_LINKS if pro else config.FREE_VIDEO_LINKS) if callable(youtube_links) else []
        return jsonify({"ok": True, "videos": links, "notes": "No accessible transcript was found; use the linked video or paste a transcript.", "source_url": url})

    @app.route("/api/tool/flashcards", methods=["POST"])
    def flashcards_tool():
        data = request.get_json(silent=True) or {}
        q = str(data.get("topic") or data.get("question") or "").strip()
        if not q:
            return jsonify({"ok": False, "error": "topic required"}), 400
        uid = _uid(data); pro = _pro(uid)
        if not pro:
            return jsonify({"ok": False, "error": "Flashcards are Pro-only", "pro_only": True}), 403
        count = max(3, min(int(data.get("count") or 10), 40))
        ans = _answer(f"Create exactly {count} study flashcards for {q}. Return JSON array with front and back fields only.", "flashcards", uid=uid, language=str(data.get("language") or "hinglish"), pro=True)
        cards = _safe_json_array(ans)
        if cards is None:
            cards = [{"front": f"{q} — card {i+1}", "back": ans} for i in range(min(count, 3))]
        return jsonify({"ok": True, "cards": cards[:count]})

    @app.route("/api/tool/revision", methods=["POST"])
    def revision_tool():
        data = request.get_json(silent=True) or {}
        uid = _uid(data)
        if not uid:
            return jsonify({"ok": False, "error": "uid/client_id required"}), 400
        user = _user(uid)
        mistakes = db.get_mistakes(uid, limit=int(data.get("mistake_limit") or 12))
        history = []
        try:
            phone = (user or {}).get("phone_number", "")
            if phone and db.supabase.enabled:
                history = db.supabase.select_many("personal_history", {"phone_number": f"eq.{phone}"}, limit=12, columns="question,tool,exam_type,subject")
        except Exception:
            history = []
        context = "\n".join([*(f"MISTAKE: {m.get('question','')}" for m in mistakes), *(f"HISTORY: {h.get('question','')}" for h in history)])
        if not context:
            return jsonify({"ok": False, "error": "Revision needs some study history first."}), 400
        ans = _answer(f"Build a 7-minute revision session from this student's own mistakes/history. Include 3 recall questions, one mini-summary, and one next-step.\n\n{context[:18000]}", "revision", uid=uid)
        return jsonify({"ok": True, "revision": ans})

    @app.route("/api/tool/current-affairs", methods=["GET", "POST"])
    def current_affairs_tool():
        data = request.get_json(silent=True) if request.method == "POST" else request.args
        topic = str(data.get("topic") or "").strip().lower()
        max_items = max(3, min(int(data.get("limit") or 8), 15))
        items = []
        for feed in rss_feeds:
            url = str(feed).strip()
            if not url:
                continue
            try:
                r = requests.get(url, timeout=10, headers={"User-Agent": "SaarthiBhai/8"})
                r.raise_for_status()
                root = ET.fromstring(r.text)
                for node in root.iter():
                    tag = node.tag.lower().split("}")[-1]
                    if tag != "item" and tag != "entry":
                        continue
                    title = ""; link = ""; summary = ""; published = ""
                    for c in list(node):
                        ct = c.tag.lower().split("}")[-1]
                        txt = (c.text or "").strip()
                        if ct == "title": title = txt
                        elif ct == "link": link = txt or c.attrib.get("href", "")
                        elif ct in {"description", "summary", "content"}: summary = re.sub(r"<[^>]+>", " ", txt)
                        elif ct in {"pubdate", "published", "updated"}: published = txt
                    if topic and topic not in (title + " " + summary).lower():
                        continue
                    if title:
                        items.append({"title": title, "url": link, "summary": summary[:500], "published": published})
                    if len(items) >= max_items * 3:
                        break
            except Exception:
                continue
            if len(items) >= max_items:
                break
        items = items[:max_items]
        if not items:
            return jsonify({"ok": False, "error": "No configured current-affairs feed returned usable items."}), 503
        digest = "\n\n".join([f"{i+1}. {x['title']}\n{x['summary']}\n{x['url']}" for i,x in enumerate(items)])
        ans = _answer(f"Summarize the following current-affairs headlines into factual student notes. Use only the supplied headlines/summaries and do not invent details. Topic: {topic or 'general'}\n\n{digest}", "current_affairs", uid=_uid(data))
        return jsonify({"ok": True, "items": items, "summary": ans})

    @app.route("/api/tool/copy-check", methods=["POST"])
    def copy_check_tool():
        data = request.get_json(silent=True) or {}
        text = str(data.get("text") or "").strip()
        if not text:
            return jsonify({"ok": False, "error": "text required"}), 400
        uid = _uid(data)
        n = int(copy_cfg.get("library_limit", 10))
        candidates = []
        try:
            candidates = db.search_library(text[:5000], limit=n)
        except Exception:
            candidates = []
        def sim(a: str, b: str) -> float:
            aa = set(re.findall(r"[A-Za-z0-9]{4,}", a.lower()))
            bb = set(re.findall(r"[A-Za-z0-9]{4,}", b.lower()))
            return (len(aa & bb) / max(1, len(aa | bb))) if aa and bb else 0.0
        matches=[]
        for c in candidates:
            base = str(c.get("content") or c.get("answer") or "")
            s=sim(text, base)
            if s >= float(copy_cfg.get("match_threshold", 0.45)):
                matches.append({"title":c.get("title",""),"similarity":round(s,3),"source_type":c.get("source_type",""),"url":c.get("url","")})
        return jsonify({"ok": True, "matches": sorted(matches,key=lambda x:x["similarity"],reverse=True), "scope": "SaarthiBhai library corpus only", "disclaimer": "This is a corpus-similarity check, not a web-wide plagiarism verdict."})

    @app.route("/api/tool/diagram", methods=["POST"])
    def diagram_tool():
        data = request.get_json(silent=True) or {}
        q = str(data.get("topic") or data.get("question") or "").strip()
        if not q:
            return jsonify({"ok": False, "error": "topic required"}), 400
        uid = _uid(data); pro = _pro(uid)
        if not pro:
            return jsonify({"ok": False, "error": "Diagram Maker is Pro-only", "pro_only": True}), 403
        ans = _answer(f"Create a compact node-and-arrow diagram specification for: {q}. Return 5-10 nodes, each on a new line as NODE => NEXT. No Mermaid code; just the relationship list.", "diagram", uid=uid, pro=True)
        svg = _relationship_svg(q, ans, diagram_cfg)
        return jsonify({"ok": True, "svg": svg, "relationships": ans})

    @app.route("/api/tool/ppt", methods=["POST"])
    def ppt_tool():
        data = request.get_json(silent=True) or {}
        q = str(data.get("topic") or data.get("question") or "").strip()
        uid = _uid(data)
        if not q:
            return jsonify({"ok": False, "error": "topic required"}), 400
        if not _pro(uid):
            return jsonify({"ok": False, "error": "PPT Maker is Pro-only", "pro_only": True}), 403
        try:
            from pptx import Presentation
            from pptx.util import Pt
        except Exception:
            return jsonify({"ok": False, "error": "python-pptx is required on the server"}), 503
        count = max(4, min(int(data.get("slides") or ppt_cfg.get("default_slides", 7)), int(ppt_cfg.get("max_slides", 15))))
        text = _answer(f"Create a {count}-slide teaching presentation outline on {q}. Return JSON array of objects with title and bullets (list of strings).", "ppt", uid=uid, pro=True, language="english")
        slides = _safe_json_array(text) or []
        prs = Presentation()
        for idx, item in enumerate(slides[:count]):
            layout = prs.slide_layouts[1] if idx else prs.slide_layouts[0]
            slide = prs.slides.add_slide(layout)
            slide.shapes.title.text = str(item.get("title") or f"{q} — Slide {idx+1}")
            if len(slide.placeholders) > 1:
                tf = slide.placeholders[1].text_frame
                tf.clear()
                for j, bullet in enumerate(item.get("bullets") or []):
                    p = tf.paragraphs[0] if j == 0 else tf.add_paragraph()
                    p.text = str(bullet)
                    p.font.size = Pt(22)
        if len(prs.slides) == 0:
            slide = prs.slides.add_slide(prs.slide_layouts[1]); slide.shapes.title.text = q
        out = io.BytesIO(); prs.save(out); out.seek(0)
        filename = re.sub(r"[^A-Za-z0-9_-]+", "-", q)[:80].strip("-") or "saarthibhai-study"
        return send_file(out, as_attachment=True, download_name=f"{filename}.pptx", mimetype="application/vnd.openxmlformats-officedocument.presentationml.presentation")

    @app.route("/api/tool/voice/transcribe", methods=["POST"])
    def voice_transcribe_tool():
        data = request.get_json(silent=True) or {}
        audio_b64 = str(data.get("audio_base64") or "").strip()
        mime = str(data.get("mime_type") or voice_cfg.get("default_mime", "audio/webm")).strip()
        if not audio_b64:
            return jsonify({"ok": False, "error": "audio_base64 required"}), 400
        if not config.OPENAI_API_KEY:
            return jsonify({"ok": False, "error": "Voice transcription provider is not configured. Browser SpeechRecognition remains available."}), 503
        try:
            raw = base64.b64decode(audio_b64, validate=True)
            max_bytes = int(voice_cfg.get("max_bytes", 10485760))
            if len(raw) > max_bytes:
                return jsonify({"ok": False, "error": "audio too large"}), 413
            files={"file":("audio"+voice_cfg.get("extension", ".webm"),raw,mime)}
            data_form={"model": voice_cfg.get("transcription_model", "gpt-4o-mini-transcribe"), "response_format":"json"}
            r=requests.post(str(config.URLS.get("openai_audio_transcriptions", "https://api.openai.com/v1/audio/transcriptions")), headers={"Authorization":f"Bearer {config.OPENAI_API_KEY}"}, files=files, data=data_form, timeout=60)
            if r.status_code>=300:
                return jsonify({"ok":False,"error":"transcription_failed"}),502
            payload=r.json(); return jsonify({"ok":True,"text":payload.get("text","")})
        except Exception as exc:
            return jsonify({"ok":False,"error":str(exc)}),500

    @app.route("/api/answer/10-in-1", methods=["POST"])
    def ten_in_one_endpoint():
        data = request.get_json(silent=True) or {}
        q = str(data.get("question") or data.get("topic") or "").strip()
        uid = _uid(data)
        if not q:
            return jsonify({"ok": False, "error": "question required"}), 400
        prompt = (
            "Return a JSON object with exactly these keys: answer, steps, formula, trick, pyq_tag, mcq, next_question, notes_action, video_tip, planner_tip. "
            f"Keep each value compact and useful for a student. Topic: {q}"
        )
        raw = _answer(prompt, "general", uid=uid)
        parsed = _safe_json_object(raw)
        if not parsed:
            parsed = {"answer": raw, "steps": [], "formula":"", "trick":"", "pyq_tag":"AI-generated", "mcq":"", "next_question":"", "notes_action":"Use Notes quick action", "video_tip":"Use Video quick action", "planner_tip":"Use Planner quick action"}
        return jsonify({"ok": True, "data": parsed, "actions": namespace.get("TEXT_ACTIONS", [])})

    @app.route("/openapi-v8.1.yaml")
    def openapi_v81_route():
        from flask import Response
        try:
            import yaml
            doc={
                "openapi":"3.1.0",
                "info":{"title":config.BRAND_NAME+" API","version":"8.1"},
                "servers":[{"url":config.PUBLIC_SCHEME+"://"+config.PUBLIC_DOMAIN}],
                "security":[{"bearerAuth":[]}],
                "paths":{
                    "/api/ai/ask":{"post":{"operationId":"askSaarthiBhai","requestBody":{"required":True,"content":{"application/json":{"schema":{"type":"object","required":["question"],"properties":{"question":{"type":"string"},"tool":{"type":"string"},"language":{"type":"string"},"exam":{"type":"string"},"subject":{"type":"string"},"client_id":{"type":"string"},"uid":{"type":"string"}}}}}},"responses":{"200":{"description":"Answer"}}}},
                    "/api/tool/notes":{"post":{"operationId":"makeNotes","responses":{"200":{"description":"Notes"}}}},
                    "/api/tool/pyq":{"post":{"operationId":"makePYQ","responses":{"200":{"description":"PYQ"}}}},
                    "/api/tool/ncert":{"post":{"operationId":"ncertExplanation","responses":{"200":{"description":"NCERT-aligned explanation"}}}},
                    "/api/tool/flashcards":{"post":{"operationId":"flashcards","responses":{"200":{"description":"Flashcards"}}}},
                    "/api/tool/revision":{"post":{"operationId":"revision","responses":{"200":{"description":"Revision"}}}}
                },
                "components":{"securitySchemes":{"bearerAuth":{"type":"http","scheme":"bearer"}}}
            }
            return Response(yaml.safe_dump(doc,sort_keys=False,allow_unicode=True),mimetype="text/yaml")
        except Exception:
            return Response("openapi: 3.1.0\n",mimetype="text/yaml")

    @app.route("/api/queue/status", methods=["GET"])
    def queue_status():
        uid = str(request.args.get("uid") or request.args.get("client_id") or "").strip()
        if uid and not uid.startswith("web:") and request.args.get("client_id"):
            uid = f"web:{uid}"
        if not uid or not db.redis:
            return jsonify({"ok": True, "queued": False, "position": 0, "message": "No queue data available"})
        pro = _pro(uid)
        pending = int(db.redis.zcard("ai:queue") or 0)
        pro_pending = int(db.redis.zcard("ai:queue:pro") or 0)
        return jsonify({"ok": True, "queued": pending > 0, "position": pending, "pro_priority": pro, "pro_pending": pro_pending})

    @app.route("/api/health/completeness")
    def completeness_health():
        return jsonify({"ok": True, "features": {
            "pptx_export": True,
            "flashcards": True,
            "revision": True,
            "current_affairs_rss": bool(rss_feeds),
            "copy_check_library": True,
            "diagram_svg": True,
            "voice_server_transcribe": bool(config.OPENAI_API_KEY),
            "verified_pyq_library": True,
            "verified_ncert_library": True,
            "ten_in_one": True,
            "external_openapi": bool(os.getenv("SAARTHIBHAI_API_KEY")),
            "mcp_source": True,
            "native_snapchat_dm": False,
        }})


def _safe_json_array(text: str):
    if not text:
        return None
    try:
        x = json.loads(text)
        return x if isinstance(x, list) else None
    except Exception:
        m = re.search(r"\[.*\]", text, flags=re.S)
        if not m:
            return None
        try:
            x = json.loads(m.group(0)); return x if isinstance(x, list) else None
        except Exception:
            return None


def _safe_json_object(text: str):
    if not text:
        return None
    try:
        x = json.loads(text); return x if isinstance(x, dict) else None
    except Exception:
        m = re.search(r"\{.*\}", text, flags=re.S)
        if not m:
            return None
        try:
            x=json.loads(m.group(0)); return x if isinstance(x, dict) else None
        except Exception:
            return None


def _relationship_svg(topic: str, relationships: str, cfg: dict) -> str:
    lines=[x.strip() for x in relationships.splitlines() if x.strip()][:12]
    width=int(cfg.get("width",900)); row_h=int(cfg.get("row_height",70)); height=max(180,row_h*(len(lines)+2))
    esc=lambda s: str(s).replace("&","&amp;").replace("<","&lt;").replace(">","&gt;").replace('"',"&quot;")
    parts=[f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">',
           f'<rect width="100%" height="100%" rx="18" fill="{cfg.get("background","#ffffff")}"/>',
           f'<text x="40" y="42" font-size="24" font-family="Arial" font-weight="700">{esc(topic)}</text>']
    y=80
    for idx,line in enumerate(lines):
        left,right=(line.split("=>",1)+[""])[:2] if "=>" in line else (line,"")
        parts.append(f'<rect x="40" y="{y}" width="{int(width*0.35)}" height="44" rx="12" fill="{cfg.get("node_fill","#FFD600")}"/>')
        parts.append(f'<text x="55" y="{y+28}" font-size="16" font-family="Arial">{esc(left.strip())}</text>')
        parts.append(f'<line x1="{int(width*0.35)+60}" y1="{y+22}" x2="{int(width*0.65)-10}" y2="{y+22}" stroke="{cfg.get("line_color","#111111")}" stroke-width="2" marker-end="url(#arrow)"/>')
        parts.append(f'<rect x="{int(width*0.65)}" y="{y}" width="{int(width*0.3)}" height="44" rx="12" fill="{cfg.get("node_fill_secondary","#f4f4f4")}"/>')
        parts.append(f'<text x="{int(width*0.65)+15}" y="{y+28}" font-size="16" font-family="Arial">{esc(right.strip() or "next")}</text>')
        y+=row_h
    parts.insert(1,'<defs><marker id="arrow" markerWidth="10" markerHeight="10" refX="8" refY="3" orient="auto"><path d="M0,0 L0,6 L9,3 z" fill="#111"/></marker></defs>')
    parts.append('</svg>')
    return ''.join(parts)

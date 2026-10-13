"""Phase 2 Study Intelligence for SaarthiBhai.

The module stores source metadata and concise study outputs. It never mirrors
NCERT books or persists raw YouTube transcripts. Admin curation endpoints are
protected by STUDY_INTEL_ADMIN_TOKEN (server environment only).
"""
from __future__ import annotations

import hashlib
import html
from email.utils import parsedate_to_datetime
import hmac
import json
import os
import re
import time
import xml.etree.ElementTree as ET
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from urllib.parse import parse_qs, urlparse

import requests
from flask import Blueprint, jsonify, request, render_template_string

OFFICIAL_SOURCE_DOMAINS = {
    "ncert.nic.in",
    "cbse.gov.in",
    "cbseacademic.nic.in",
    "upsc.gov.in",
    "nta.ac.in",
    "exams.nta.ac.in",
    "pib.gov.in",
    "education.gov.in",
}
PIB_RSS_URL = "https://pib.gov.in/RssMain.aspx?ModId=6&Lang=1&Regid=1&reg=48"
MAX_TEXT = 12000


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _clean_text(value, limit=2000):
    value = html.unescape(str(value or ""))
    value = re.sub(r"<[^>]+>", " ", value)
    value = re.sub(r"\s+", " ", value).strip()
    return value[:limit]


def _normal_words(text):
    return re.findall(r"[a-z0-9]+", str(text or "").lower())


def _shingles(text, n=5):
    words = _normal_words(text)
    if not words:
        return set()
    if len(words) < n:
        return {" ".join(words)}
    return {" ".join(words[i:i+n]) for i in range(len(words)-n+1)}


def shingle_overlap(text, reference, n=5):
    """Return input containment overlap percentage, not a plagiarism verdict."""
    a = _shingles(text, n)
    b = _shingles(reference, n)
    if not a or not b:
        return 0.0
    return round(100.0 * len(a & b) / len(a), 2)


def allowed_source_url(url, allowed_domains=None):
    domains = allowed_domains or OFFICIAL_SOURCE_DOMAINS
    try:
        parsed = urlparse(str(url or "").strip())
        host = (parsed.hostname or "").lower().rstrip(".")
        return parsed.scheme == "https" and any(host == d or host.endswith("." + d) for d in domains)
    except Exception:
        return False


def parse_youtube_video_id(url):
    try:
        p = urlparse(str(url or "").strip())
        host = (p.hostname or "").lower()
        video_id = ""
        if host in {"youtu.be", "www.youtu.be"}:
            video_id = p.path.strip("/").split("/")[0]
        elif host.endswith("youtube.com"):
            if p.path == "/watch":
                video_id = parse_qs(p.query).get("v", [""])[0]
            elif p.path.startswith(("/embed/", "/shorts/", "/live/")):
                bits = p.path.strip("/").split("/")
                video_id = bits[1] if len(bits) > 1 else ""
        return video_id if re.fullmatch(r"[A-Za-z0-9_-]{11}", video_id or "") else ""
    except Exception:
        return ""


def extractive_summary(text, max_sentences=6, max_chars=1800):
    text = _clean_text(text, 20000)
    parts = [p.strip() for p in re.split(r"(?<=[.!?।])\s+", text) if len(p.strip()) > 35]
    if not parts:
        return text[:max_chars]
    # Prefer first sentence and high-information sentences, preserving original order.
    scored = []
    for i, sentence in enumerate(parts):
        words = _normal_words(sentence)
        score = min(len(words), 35) + min(len(set(words)), 25) - i * 0.2
        scored.append((score, i, sentence))
    chosen = sorted(sorted(scored, reverse=True)[:max_sentences], key=lambda x: x[1])
    out = " ".join(row[2] for row in chosen)
    return out[:max_chars]


def rank_verified_topics(rows):
    """Evidence-based ranking from verified rows; never invent a rank without records."""
    grouped = defaultdict(lambda: {"topic": "", "items": 0, "years": set(), "subjects": set(), "latest_year": 0})
    for row in rows:
        topic = _clean_text(row.get("topic") or row.get("question_stem") or "", 220)
        if not topic:
            continue
        key = re.sub(r"\W+", " ", topic.lower()).strip()
        if not key:
            continue
        g = grouped[key]
        g["topic"] = topic
        g["items"] += 1
        try:
            year = int(row.get("year") or 0)
        except Exception:
            year = 0
        if year:
            g["years"].add(year)
            g["latest_year"] = max(g["latest_year"], year)
        if row.get("subject"):
            g["subjects"].add(str(row["subject"]))
    out = []
    current_year = datetime.now(timezone.utc).year
    for g in grouped.values():
        distinct_years = len(g["years"])
        recency = max(0, 10 - max(0, current_year - g["latest_year"])) if g["latest_year"] else 0
        score = distinct_years * 10 + g["items"] * 2 + recency
        out.append({"topic": g["topic"], "verified_item_count": g["items"],
                    "distinct_year_count": distinct_years, "latest_year": g["latest_year"] or None,
                    "subjects": sorted(g["subjects"]), "evidence_score": score})
    return sorted(out, key=lambda x: (-x["evidence_score"], x["topic"].lower()) )


def sm2_next_review(grade, repetitions, interval_days, ease_factor):
    """Small SM-2-like flashcard interval helper. grade: 0 (forgot) to 5 (easy)."""
    grade = max(0, min(5, int(grade)))
    reps = max(0, int(repetitions or 0))
    interval = max(0, int(interval_days or 0))
    ease = max(1.3, float(ease_factor or 2.5))
    if grade < 3:
        return 1, 0, max(1.3, ease - 0.2)
    new_ease = max(1.3, ease + 0.1 - (5-grade) * (0.08 + (5-grade) * 0.02))
    if reps == 0:
        next_days = 1
    elif reps == 1:
        next_days = 3
    else:
        next_days = max(interval + 1, round(max(1, interval) * new_ease))
    return min(365, next_days), reps + 1, round(new_ease, 2)


PAGE = r'''<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>SaarthiBhai · Study Intelligence</title>
<style>
:root{color-scheme:dark;--bg:#08101d;--panel:#101c2e;--line:#24344d;--ink:#eaf2ff;--muted:#a9bbd3;--accent:#ffd12e;--green:#6ee7b7}*{box-sizing:border-box}body{margin:0;background:linear-gradient(135deg,#08101d,#101a2b);color:var(--ink);font:15px/1.55 system-ui,Segoe UI,Arial}header{padding:24px max(18px,calc((100vw - 1180px)/2));border-bottom:1px solid var(--line);background:#091322}h1{margin:0;font-size:clamp(24px,4vw,35px)}header p{color:var(--muted);margin:7px 0 0}.wrap{max-width:1180px;margin:auto;padding:22px 18px 50px}.notice{border:1px solid #685b20;background:#2c2815;padding:13px 15px;border-radius:12px;color:#ffedaa;margin-bottom:18px}.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(290px,1fr));gap:16px}.card{background:var(--panel);border:1px solid var(--line);border-radius:16px;padding:17px;min-width:0}.card h2{margin:0 0 4px;font-size:18px}.sub{color:var(--muted);font-size:13px;margin:0 0 12px}.pill{display:inline-block;border:1px solid #394862;border-radius:999px;padding:2px 8px;font-size:11px;color:var(--green);margin-bottom:8px}label{display:block;color:var(--muted);font-size:12px;margin:8px 0 4px}input,textarea,select{width:100%;background:#07101d;border:1px solid #33445c;border-radius:9px;padding:10px;color:var(--ink);font:inherit}textarea{min-height:96px;resize:vertical}button{border:0;border-radius:9px;padding:10px 13px;background:var(--accent);color:#141414;font-weight:750;cursor:pointer;margin-top:9px}button:disabled{opacity:.6;cursor:wait}.out{white-space:pre-wrap;background:#07101d;border:1px solid #2e4059;border-radius:10px;padding:10px;margin-top:10px;min-height:30px;overflow-wrap:anywhere;font-size:13px;max-height:300px;overflow:auto}.small{font-size:12px;color:var(--muted)}a{color:#8cdcff}.wide{grid-column:1/-1}.split{display:flex;gap:8px;align-items:center}.split>*{flex:1}.feature-list{padding-left:18px;color:var(--muted);margin-bottom:0}footer{text-align:center;color:var(--muted);padding:24px;font-size:12px}
</style></head><body><header><h1>📚 SaarthiBhai Study Intelligence</h1><p>Source-linked learning tools · by Sparsh Singhal</p></header><main class="wrap"><div class="notice"><b>Verification rule:</b> “Verified” means an official source link was checked and the record was reviewed. The system will not fabricate past-paper claims or rankings where verified records are missing. NCERT textbooks remain on NCERT’s own site.</div>
<div class="grid">
<section class="card"><span class="pill">01 · VERIFIED PYQ</span><h2>Past-paper index</h2><p class="sub">Search reviewed question records with official paper links.</p><label>Exam</label><input id="pyqExam" placeholder="e.g. CBSE Class 10"><label>Subject</label><input id="pyqSubject" placeholder="e.g. Science"><button onclick="searchPyq()">Search verified PYQs</button><div id="pyqOut" class="out">No search yet.</div></section>
<section class="card"><span class="pill">02 · NCERT INDEX</span><h2>Textbook / chapter index</h2><p class="sub">Find official NCERT chapter records; opens NCERT’s own page.</p><div class="split"><div><label>Class</label><input id="nClass" placeholder="10"></div><div><label>Subject</label><input id="nSubject" placeholder="Science"></div></div><label>Search term</label><input id="nQuery" placeholder="chapter / book name"><button onclick="searchNcert()">Search NCERT index</button><div id="nOut" class="out">No search yet.</div></section>
<section class="card"><span class="pill">03 · IMPORTANT QUESTIONS</span><h2>Evidence-based ranking</h2><p class="sub">Ranks topics by recurrence across verified PYQ items, not by guesswork.</p><label>Exam</label><input id="rankExam" placeholder="optional exam"><label>Subject</label><input id="rankSubject" placeholder="optional subject"><button onclick="rankImportant()">Rank from evidence</button><div id="rankOut" class="out">Not ranked yet.</div></section>
<section class="card"><span class="pill">04 · YOUTUBE TRANSCRIPTS</span><h2>Video-to-notes pipeline</h2><p class="sub">Fetches available captions, returns a concise summary, and does not retain raw transcripts.</p><label>YouTube URL</label><input id="ytUrl" placeholder="https://www.youtube.com/watch?v=..."><div class="split"><div><label>Subject</label><input id="ytSubject" placeholder="Physics"></div><div><label>Language</label><select id="ytLang"><option value="hinglish">Hinglish</option><option value="english">English</option><option value="hindi">Hindi</option></select></div></div><button onclick="summarizeVideo()">Create revision notes</button><div id="ytOut" class="out">No video processed.</div></section>
<section class="card"><span class="pill">05 · FLASHCARDS</span><h2>Spaced flashcards</h2><p class="sub">Review ratings update the next interval using a small SM-2-like algorithm.</p><label>Question / front</label><textarea id="fcQ" placeholder="What do you need to remember?"></textarea><label>Answer / back</label><textarea id="fcA" placeholder="Short answer"></textarea><div class="split"><div><label>Subject</label><input id="fcSubject" placeholder="Biology"></div><div><label>Topic</label><input id="fcTopic" placeholder="Cell"></div></div><button onclick="createFlashcard()">Save flashcard</button><button onclick="loadFlashcards()">Load due cards</button><div id="fcOut" class="out">No cards loaded.</div></section>
<section class="card"><span class="pill">06 · REVISION 1/3/7/15</span><h2>Revision planner</h2><p class="sub">Schedules a topic for day 1, 3, 7 and 15. Repeated requests on the same day are idempotent.</p><label>Topic</label><input id="revTopic" placeholder="e.g. Photosynthesis"><label>Subject</label><input id="revSubject" placeholder="Biology"><label>Exam</label><input id="revExam" placeholder="optional"><button onclick="scheduleRevision()">Create revision schedule</button><button onclick="loadRevision()">Show due revisions</button><div id="revOut" class="out">No revision items loaded.</div></section>
<section class="card"><span class="pill">07 · CURRENT AFFAIRS</span><h2>Official-source digest</h2><p class="sub">Latest indexed headlines from PIB RSS, with source links and dates.</p><button onclick="loadCurrent()">Load latest current affairs</button><div id="caOut" class="out">No current affairs loaded.</div><p class="small">Refresh is an admin-only server action; see PHASE2_SETUP.md. Never put the admin token in this page.</p></section>
<section class="card"><span class="pill">08 · DIAGRAM ENGINE</span><h2>Generate a study diagram</h2><p class="sub">Creates Mermaid flowchart source you can edit. Verify subject facts before exam use.</p><label>Topic</label><input id="diagTopic" placeholder="e.g. Water cycle"><label>Level / exam</label><input id="diagLevel" placeholder="Class 8 / NEET / GATE"><button onclick="makeDiagram()">Generate diagram</button><div id="diagOut" class="out">No diagram generated.</div></section>
<section class="card"><span class="pill">09 · ORIGINALITY CHECK</span><h2>Check against indexed material</h2><p class="sub">Checks internal SaarthiBhai indexed references only; this is not a web-wide plagiarism scan.</p><label>Text to check</label><textarea id="origText" placeholder="Paste your draft (at least 30 characters). It is compared for this request and is not saved."></textarea><button onclick="checkOriginality()">Check overlap</button><div id="origOut" class="out">No text checked.</div></section>
<section class="card wide"><h2>Official source directory</h2><p class="sub">These are discovery entry points, not a claim that every possible paper or chapter is indexed.</p><div id="sourcesOut" class="out">Loading source registry…</div><p class="small">Content coverage grows through the reviewer-only ingestion workflow. Do not label an item verified until its source, metadata, and answer/explanation have been checked.</p></section>
</div></main><footer>SaarthiBhai by Sparsh Singhal · Official-source links and learner-owned study tools</footer>
<script>
const API='';let clientId=localStorage.getItem('sg_client')||localStorage.getItem('saarthibhai_study_id');if(!clientId){clientId='web_'+(crypto.randomUUID?crypto.randomUUID():Date.now()+'_'+Math.random().toString(16).slice(2));localStorage.setItem('sg_client',clientId)}localStorage.setItem('saarthibhai_study_id',clientId);
async function api(path,method='GET',body=null){const opt={method,headers:{'Content-Type':'application/json'}};if(body)opt.body=JSON.stringify({...body,client_id:clientId});const r=await fetch(API+path,opt);const d=await r.json().catch(()=>({ok:false,error:'Invalid server response'}));if(!r.ok||d.ok===false)throw new Error(d.error||('HTTP '+r.status));return d}
function out(id,data){document.getElementById(id).textContent=typeof data==='string'?data:JSON.stringify(data,null,2)}function esc(s){return String(s??'')}
async function searchPyq(){try{let p=new URLSearchParams();if(pyqExam.value)p.set('exam',pyqExam.value);if(pyqSubject.value)p.set('subject',pyqSubject.value);out('pyqOut',await api('/api/study-intelligence/pyq?'+p))}catch(e){out('pyqOut',e.message)}}
async function searchNcert(){try{let p=new URLSearchParams();if(nClass.value)p.set('class',nClass.value);if(nSubject.value)p.set('subject',nSubject.value);if(nQuery.value)p.set('q',nQuery.value);out('nOut',await api('/api/study-intelligence/ncert-index?'+p))}catch(e){out('nOut',e.message)}}
async function rankImportant(){try{let p=new URLSearchParams();if(rankExam.value)p.set('exam',rankExam.value);if(rankSubject.value)p.set('subject',rankSubject.value);out('rankOut',await api('/api/study-intelligence/important-questions?'+p))}catch(e){out('rankOut',e.message)}}
async function summarizeVideo(){try{out('ytOut','Processing captions…');out('ytOut',await api('/api/study-intelligence/youtube/transcript','POST',{url:ytUrl.value,subject:ytSubject.value,language:ytLang.value}))}catch(e){out('ytOut',e.message)}}
async function createFlashcard(){try{out('fcOut',await api('/api/study-intelligence/flashcards','POST',{question:fcQ.value,answer:fcA.value,subject:fcSubject.value,topic:fcTopic.value}))}catch(e){out('fcOut',e.message)}}
async function loadFlashcards(){try{const d=await api('/api/study-intelligence/flashcards/due?client_id='+encodeURIComponent(clientId));out('fcOut',d);const box=document.getElementById('fcOut');if(d.items?.length){box.textContent='';d.items.forEach(c=>{let p=document.createElement('p');p.textContent='Q: '+c.question+'\nA: '+c.answer+'\nDue: '+c.due_at;box.appendChild(p);[0,3,4,5].forEach(g=>{let b=document.createElement('button');b.textContent='Grade '+g;b.onclick=async()=>{try{await api('/api/study-intelligence/flashcards/'+c.id+'/review','POST',{grade:g});loadFlashcards()}catch(e){alert(e.message)}};box.appendChild(b)})})}}catch(e){out('fcOut',e.message)}}
async function scheduleRevision(){try{out('revOut',await api('/api/study-intelligence/revision/schedule','POST',{topic:revTopic.value,subject:revSubject.value,exam_name:revExam.value}))}catch(e){out('revOut',e.message)}}
async function loadRevision(){try{out('revOut',await api('/api/study-intelligence/revision/due?client_id='+encodeURIComponent(clientId)))}catch(e){out('revOut',e.message)}}
async function loadCurrent(){try{out('caOut',await api('/api/study-intelligence/current-affairs?limit=20'))}catch(e){out('caOut',e.message)}}
async function makeDiagram(){try{out('diagOut',await api('/api/study-intelligence/diagram','POST',{topic:diagTopic.value,level:diagLevel.value,language:'hinglish'}))}catch(e){out('diagOut',e.message)}}
async function checkOriginality(){try{out('origOut',await api('/api/study-intelligence/originality/check','POST',{text:origText.value}))}catch(e){out('origOut',e.message)}}
(async()=>{try{const d=await api('/api/study-intelligence/sources');out('sourcesOut',d)}catch(e){out('sourcesOut',e.message)}})();
</script></body></html>'''


def register_study_intelligence(app, supa, config, ai_generator, rate_limited=None, is_pro_user=None, consume_video_quota=None):
    bp = Blueprint("study_intelligence", __name__)
    admin_token_env = "STUDY_INTEL_ADMIN_TOKEN"
    timeout = float(getattr(config, "SUPABASE_TIMEOUT", 10) or 10)

    def _db_ready():
        return bool(getattr(supa, "enabled", False) and getattr(supa, "base", ""))

    def _select(table, filters=None, columns="*", limit=100, order=None):
        if not _db_ready():
            raise RuntimeError("Supabase is not configured on the backend")
        params = {"select": columns, "limit": str(max(1, min(int(limit), 1000)))}
        params.update(filters or {})
        if order:
            params["order"] = order
        r = requests.get(f"{supa.base}/{table}", headers=supa.headers, params=params, timeout=timeout)
        if r.status_code >= 300:
            if r.status_code == 404 or "does not exist" in (r.text or "").lower():
                raise RuntimeError(f"Phase 2 database tables are not installed yet. Run PHASE2_STUDY_INTELLIGENCE.sql. Detail: {r.text[:180]}")
            raise RuntimeError(f"Database query failed ({r.status_code}): {r.text[:220]}")
        payload = r.json() if r.content else []
        return payload if isinstance(payload, list) else []

    def _insert(table, row):
        if not _db_ready():
            raise RuntimeError("Supabase is not configured on the backend")
        headers = dict(supa.headers); headers["Prefer"] = "return=representation"
        r = requests.post(f"{supa.base}/{table}", headers=headers, json=row, timeout=timeout)
        if r.status_code >= 300:
            raise RuntimeError(f"Database insert failed ({r.status_code}): {r.text[:220]}")
        data = r.json() if r.content else []
        return data[0] if isinstance(data, list) and data else (data if isinstance(data, dict) else {})

    def _upsert(table, row, conflict):
        if not _db_ready():
            raise RuntimeError("Supabase is not configured on the backend")
        headers = dict(supa.headers); headers["Prefer"] = "resolution=merge-duplicates,return=representation"
        r = requests.post(f"{supa.base}/{table}", params={"on_conflict": conflict}, headers=headers, json=row, timeout=timeout)
        if r.status_code >= 300:
            raise RuntimeError(f"Database upsert failed ({r.status_code}): {r.text[:220]}")
        data = r.json() if r.content else []
        return data[0] if isinstance(data, list) and data else (data if isinstance(data, dict) else {})

    def _patch(table, filters, row):
        if not _db_ready():
            raise RuntimeError("Supabase is not configured on the backend")
        headers = dict(supa.headers); headers["Prefer"] = "return=representation"
        r = requests.patch(f"{supa.base}/{table}", params=filters, headers=headers, json=row, timeout=timeout)
        if r.status_code >= 300:
            raise RuntimeError(f"Database update failed ({r.status_code}): {r.text[:220]}")
        data = r.json() if r.content else []
        return data if isinstance(data, list) else []

    def _admin_ok():
        expected = os.getenv(admin_token_env, "").strip()
        if not expected:
            return False, (jsonify({"ok": False, "error": f"Admin actions disabled. Set {admin_token_env} on Render backend."}), 503)
        supplied = request.headers.get("X-Study-Admin-Token", "")
        auth = request.headers.get("Authorization", "")
        if auth.lower().startswith("bearer "):
            supplied = auth[7:].strip()
        if not hmac.compare_digest(supplied or "", expected):
            return False, (jsonify({"ok": False, "error": "Admin token missing or invalid"}), 401)
        return True, None

    def _student_key(data=None):
        data = data or {}
        # Prefer the app's canonical client/legacy identity when supplied.
        # A phone number entered in the request is not proof of account ownership.
        cid = str(data.get("client_id") or data.get("uid") or request.args.get("client_id") or request.args.get("uid") or "").strip()
        cid = re.sub(r"[^A-Za-z0-9:_-]", "", cid)[:140]
        if cid:
            return cid if cid.startswith(("web:", "phone:", "wa:", "tg:", "ig:", "discord:", "reddit:")) else "web:" + cid
        phone = re.sub(r"\D", "", str(data.get("phone_number") or request.args.get("phone_number") or ""))
        if len(phone) >= 10:
            return "phone:" + phone[-15:]
        return ""

    def _is_pro(student_key):
        try:
            return bool(is_pro_user and is_pro_user(student_key))
        except Exception:
            return False

    def _json_error(exc, status=500):
        msg = str(exc)
        if "not configured" in msg.lower(): status = 503
        if "not installed" in msg.lower(): status = 503
        return jsonify({"ok": False, "error": msg}), status

    def _apply_filters(params, fields):
        filters = {"review_status": "eq.verified"}
        for query_key, column in fields.items():
            value = _clean_text(params.get(query_key, ""), 100)
            # Keep PostgREST filter syntax out of user-supplied search terms.
            value = re.sub(r"[^A-Za-z0-9 _-]", "", value).strip()
            if value:
                filters[column] = f"ilike.*{value}*"
        return filters

    @bp.get("/study-intelligence")
    def study_intelligence_page():
        return render_template_string(PAGE)

    @bp.get("/api/study-intelligence/health")
    def study_intelligence_health():
        tables = ["study_official_sources", "study_ncert_index", "study_pyq_papers", "study_pyq_items", "study_flashcards", "study_revision_tasks", "study_youtube_summaries", "study_current_affairs"]
        status = {}
        try:
            if not _db_ready():
                return jsonify({"ok": False, "database": "not_configured", "tables": {}, "admin_ingest_enabled": bool(os.getenv(admin_token_env))}), 503
            for table in tables:
                _select(table, columns="id", limit=1)
                status[table] = "ready"
            return jsonify({"ok": True, "database": "connected", "tables": status,
                            "admin_ingest_enabled": bool(os.getenv(admin_token_env)),
                            "note": "A ready table does not imply its content corpus is populated."})
        except Exception as exc:
            return _json_error(exc, 503)

    @bp.get("/api/study-intelligence/sources")
    def study_intelligence_sources():
        try:
            rows = _select("study_official_sources", {"is_active": "eq.true"}, limit=100, order="source_kind.asc,title.asc")
            return jsonify({"ok": True, "items": rows,
                            "notes": ["Directory entries are source pointers, not a claim that all exam materials are indexed.",
                                      "A record is counted as verified only after explicit review."]})
        except Exception as exc:
            return _json_error(exc, 503)

    @bp.get("/api/study-intelligence/pyq")
    def study_intelligence_pyq():
        try:
            # Official paper catalogue links and reviewed short question records are distinct.
            paper_filters = {"review_status": "eq.verified"}
            if request.args.get("exam"):
                exam_query = re.sub(r"[^A-Za-z0-9 _-]", "", _clean_text(request.args["exam"], 100)).strip()
                # A common search such as “CBSE Class 10” should still find the
                # official CBSE catalogue row, which is correctly labelled “CBSE”.
                exam_lower = exam_query.lower()
                if exam_lower.startswith("cbse"):
                    exam_query = "CBSE"
                elif exam_lower.startswith("upsc"):
                    exam_query = "UPSC"
                paper_filters["exam_name"] = "ilike.*" + exam_query + "*"
            papers = _select("study_pyq_papers", paper_filters, limit=100, order="year.desc.nullslast,exam_name.asc")
            filters = _apply_filters(request.args, {"exam": "exam_name", "subject": "subject", "topic": "topic"})
            if request.args.get("year", "").isdigit(): filters["year"] = "eq." + request.args["year"]
            rows = _select("study_pyq_items", filters, limit=min(int(request.args.get("limit", 50) or 50), 100))
            return jsonify({"ok": True, "papers": papers, "items": rows, "count": len(rows), "question_records_count": len(rows), "verified_only": True,
                            "message": None if rows else "No matching verified individual question records yet. Official paper-catalogue links are shown separately; generated practice questions are not labelled as actual PYQs."})
        except Exception as exc:
            return _json_error(exc, 503)

    @bp.post("/api/study-intelligence/admin/pyq-papers")
    def study_intelligence_admin_add_pyq_paper():
        ok, failure = _admin_ok()
        if not ok: return failure
        data = request.get_json(silent=True) or {}
        source_url = str(data.get("source_url") or "").strip()
        if not allowed_source_url(source_url): return jsonify({"ok": False, "error": "source_url must be HTTPS on an approved official domain"}), 400
        exam = _clean_text(data.get("exam_name"), 160); title = _clean_text(data.get("paper_title"), 240)
        if not exam or not title: return jsonify({"ok": False, "error": "exam_name and paper_title are required"}), 400
        year = data.get("year")
        if year not in (None, "") and (not str(year).isdigit() or not 1950 <= int(year) <= datetime.now(timezone.utc).year + 1):
            return jsonify({"ok": False, "error": "year is outside the accepted range"}), 400
        row = {"exam_name": exam, "year": int(year) if year not in (None, "") else None,
               "subject": _clean_text(data.get("subject") or "All subjects", 100), "paper_title": title,
               "source_url": source_url, "source_domain": urlparse(source_url).hostname,
               "review_status": "pending", "created_at": _now_iso()}
        try:
            rec = _insert("study_pyq_papers", row)
            return jsonify({"ok": True, "item": rec, "review_status": "pending", "message": "Paper link saved for human review."}), 201
        except Exception as exc: return _json_error(exc)

    @bp.post("/api/study-intelligence/admin/pyq-papers/<int:item_id>/verify")
    def study_intelligence_admin_verify_pyq_paper(item_id):
        ok, failure = _admin_ok()
        if not ok: return failure
        data = request.get_json(silent=True) or {}
        if not all(data.get(k) is True for k in ("source_opened", "metadata_matches")):
            return jsonify({"ok": False, "error": "Reviewer checklist required: source_opened and metadata_matches must both be true."}), 400
        try:
            existing = _select("study_pyq_papers", {"id": f"eq.{item_id}"}, limit=1)
            if not existing: return jsonify({"ok": False, "error": "Paper entry not found"}), 404
            if not allowed_source_url(existing[0].get("source_url")): return jsonify({"ok": False, "error": "Source domain is not on the official allowlist"}), 400
            updated = _patch("study_pyq_papers", {"id": f"eq.{item_id}"}, {"review_status": "verified", "verified_at": _now_iso(), "verified_by": "admin-review"})
            return jsonify({"ok": True, "item": updated[0] if updated else {"id": item_id, "review_status": "verified"}})
        except Exception as exc: return _json_error(exc)

    @bp.post("/api/study-intelligence/admin/pyq-items")
    def study_intelligence_admin_add_pyq():
        ok, failure = _admin_ok()
        if not ok: return failure
        data = request.get_json(silent=True) or {}
        source_url = str(data.get("source_url") or "").strip()
        if not allowed_source_url(source_url): return jsonify({"ok": False, "error": "source_url must be HTTPS on an approved official domain"}), 400
        exam = _clean_text(data.get("exam_name"), 160); subject = _clean_text(data.get("subject"), 100)
        stem = _clean_text(data.get("question_stem"), 700); topic = _clean_text(data.get("topic"), 180)
        if not exam or not subject or not stem or not topic: return jsonify({"ok": False, "error": "exam_name, subject, topic and question_stem are required"}), 400
        year = data.get("year")
        if not str(year or "").isdigit() or not 1950 <= int(year) <= datetime.now(timezone.utc).year + 1:
            return jsonify({"ok": False, "error": "year is outside the accepted range"}), 400
        row = {"exam_name": exam, "year": int(year), "subject": subject, "topic": topic,
               "question_stem": stem, "answer_explanation": _clean_text(data.get("answer_explanation"), 1600),
               "paper_title": _clean_text(data.get("paper_title"), 240), "source_url": source_url,
               "source_domain": urlparse(source_url).hostname, "review_status": "pending",
               "source_kind": _clean_text(data.get("source_kind") or "official_paper", 40), "created_at": _now_iso()}
        try:
            rec = _insert("study_pyq_items", row)
            return jsonify({"ok": True, "item": rec, "review_status": "pending", "message": "Saved for human review; it is not searchable as a verified PYQ yet."}), 201
        except Exception as exc: return _json_error(exc)

    @bp.post("/api/study-intelligence/admin/pyq-items/<int:item_id>/verify")
    def study_intelligence_admin_verify_pyq(item_id):
        ok, failure = _admin_ok()
        if not ok: return failure
        data = request.get_json(silent=True) or {}
        if not all(data.get(k) is True for k in ("source_opened", "metadata_matches", "answer_checked")):
            return jsonify({"ok": False, "error": "Reviewer checklist required: source_opened, metadata_matches and answer_checked must all be true."}), 400
        try:
            existing = _select("study_pyq_items", {"id": f"eq.{item_id}"}, limit=1)
            if not existing: return jsonify({"ok": False, "error": "PYQ item not found"}), 404
            if not allowed_source_url(existing[0].get("source_url")): return jsonify({"ok": False, "error": "Source domain is not on the official allowlist"}), 400
            updated = _patch("study_pyq_items", {"id": f"eq.{item_id}"}, {"review_status": "verified", "verified_at": _now_iso(), "verified_by": "admin-review"})
            return jsonify({"ok": True, "item": updated[0] if updated else {"id": item_id, "review_status": "verified"}})
        except Exception as exc: return _json_error(exc)

    @bp.get("/api/study-intelligence/ncert-index")
    def study_intelligence_ncert_index():
        try:
            filters = _apply_filters(request.args, {"class": "class_name", "subject": "subject", "q": "chapter_title"})
            rows = _select("study_ncert_index", filters, limit=min(int(request.args.get("limit", 100) or 100), 200), order="class_name.asc,subject.asc,chapter_no.asc")
            catalogue = _select("study_official_sources", {"source_key": "eq.ncert_textbooks", "is_active": "eq.true"}, limit=1)
            return jsonify({"ok": True, "items": rows, "count": len(rows), "official_catalogue": catalogue[0] if catalogue else None, "official_links_only": True,
                            "message": None if rows else "No chapter metadata has been curated yet. Use the official catalogue link below; do not treat catalogue metadata as chapter coverage."})
        except Exception as exc: return _json_error(exc, 503)

    @bp.post("/api/study-intelligence/admin/ncert-index")
    def study_intelligence_admin_add_ncert():
        ok, failure = _admin_ok()
        if not ok: return failure
        data = request.get_json(silent=True) or {}
        source_url = str(data.get("source_url") or "").strip()
        if not allowed_source_url(source_url, {"ncert.nic.in"}): return jsonify({"ok": False, "error": "source_url must be an HTTPS NCERT official URL"}), 400
        class_name = _clean_text(data.get("class_name"), 40); subject = _clean_text(data.get("subject"), 100)
        book = _clean_text(data.get("book_title"), 240); chapter = _clean_text(data.get("chapter_title"), 240)
        if not all((class_name, subject, book, chapter)): return jsonify({"ok": False, "error": "class_name, subject, book_title and chapter_title are required"}), 400
        try: chapter_no = int(data.get("chapter_no") or 0)
        except Exception: chapter_no = 0
        row = {"class_name": class_name, "subject": subject, "book_title": book, "chapter_title": chapter,
               "chapter_no": max(0, chapter_no), "source_url": source_url, "source_domain": urlparse(source_url).hostname,
               "language": _clean_text(data.get("language") or "English", 40), "review_status": "pending", "created_at": _now_iso()}
        try:
            rec = _insert("study_ncert_index", row)
            return jsonify({"ok": True, "item": rec, "review_status": "pending", "message": "Metadata only; NCERT book files/content are not copied."}), 201
        except Exception as exc: return _json_error(exc)

    @bp.post("/api/study-intelligence/admin/ncert-index/<int:item_id>/verify")
    def study_intelligence_admin_verify_ncert(item_id):
        ok, failure = _admin_ok()
        if not ok: return failure
        data = request.get_json(silent=True) or {}
        if not all(data.get(k) is True for k in ("source_opened", "metadata_matches")):
            return jsonify({"ok": False, "error": "Reviewer checklist required: source_opened and metadata_matches must both be true."}), 400
        try:
            existing = _select("study_ncert_index", {"id": f"eq.{item_id}"}, limit=1)
            if not existing: return jsonify({"ok": False, "error": "NCERT index item not found"}), 404
            if not allowed_source_url(existing[0].get("source_url"), {"ncert.nic.in"}): return jsonify({"ok": False, "error": "Source domain is not an NCERT official domain"}), 400
            updated = _patch("study_ncert_index", {"id": f"eq.{item_id}"}, {"review_status": "verified", "verified_at": _now_iso(), "verified_by": "admin-review"})
            return jsonify({"ok": True, "item": updated[0] if updated else {"id": item_id, "review_status": "verified"}})
        except Exception as exc: return _json_error(exc)

    @bp.get("/api/study-intelligence/important-questions")
    def study_intelligence_important():
        try:
            filters = {"review_status": "eq.verified"}
            if request.args.get("exam"):
                exam_filter = re.sub(r"[^A-Za-z0-9 _-]", "", _clean_text(request.args["exam"], 100)).strip()
                if exam_filter: filters["exam_name"] = f"ilike.*{exam_filter}*"
            if request.args.get("subject"):
                subject_filter = re.sub(r"[^A-Za-z0-9 _-]", "", _clean_text(request.args["subject"], 100)).strip()
                if subject_filter: filters["subject"] = f"ilike.*{subject_filter}*"
            rows = _select("study_pyq_items", filters, columns="id,exam_name,year,subject,topic,question_stem,source_url", limit=1000)
            ranked = rank_verified_topics(rows)
            return jsonify({"ok": True, "ranked_topics": ranked[:50], "basis": "frequency across reviewed PYQ items, distinct exam years, and recency",
                            "verified_records_used": len(rows), "message": None if ranked else "Not enough verified PYQ records to rank topics yet. This avoids presenting guesses as exam predictions."})
        except Exception as exc: return _json_error(exc, 503)

    @bp.post("/api/study-intelligence/flashcards")
    def study_intelligence_flashcard_create():
        data = request.get_json(silent=True) or {}; student = _student_key(data)
        question = _clean_text(data.get("question"), 1400); answer = _clean_text(data.get("answer"), 2400)
        if not student: return jsonify({"ok": False, "error": "client_id or phone_number is required"}), 400
        if len(question) < 2 or len(answer) < 1: return jsonify({"ok": False, "error": "question and answer are required"}), 400
        row = {"student_key": student, "question": question, "answer": answer, "subject": _clean_text(data.get("subject"), 100),
               "topic": _clean_text(data.get("topic"), 180), "due_at": _now_iso(), "interval_days": 0,
               "repetitions": 0, "ease_factor": 2.5, "created_at": _now_iso(), "source_kind": "student_created"}
        try:
            rec = _insert("study_flashcards", row)
            return jsonify({"ok": True, "item": rec, "message": "Flashcard saved and due for first review now."}), 201
        except Exception as exc: return _json_error(exc)

    @bp.get("/api/study-intelligence/flashcards/due")
    def study_intelligence_flashcards_due():
        student = _student_key()
        if not student: return jsonify({"ok": False, "error": "client_id or phone_number is required"}), 400
        try:
            rows = _select("study_flashcards", {"student_key": f"eq.{student}", "due_at": "lte." + _now_iso()}, limit=100, order="due_at.asc")
            return jsonify({"ok": True, "items": rows, "count": len(rows)})
        except Exception as exc: return _json_error(exc, 503)

    @bp.post("/api/study-intelligence/flashcards/<int:card_id>/review")
    def study_intelligence_flashcard_review(card_id):
        data = request.get_json(silent=True) or {}; student = _student_key(data)
        try: grade = int(data.get("grade"))
        except Exception: grade = -1
        if grade < 0 or grade > 5: return jsonify({"ok": False, "error": "grade must be an integer from 0 (forgot) to 5 (easy)"}), 400
        if not student: return jsonify({"ok": False, "error": "client_id or phone_number is required"}), 400
        try:
            rows = _select("study_flashcards", {"id": f"eq.{card_id}", "student_key": f"eq.{student}"}, limit=1)
            if not rows: return jsonify({"ok": False, "error": "Flashcard not found for this learner"}), 404
            card = rows[0]
            days, reps, ease = sm2_next_review(grade, card.get("repetitions", 0), card.get("interval_days", 0), card.get("ease_factor", 2.5))
            now = datetime.now(timezone.utc)
            due = (now + timedelta(days=days)).isoformat()
            updated = _patch("study_flashcards", {"id": f"eq.{card_id}", "student_key": f"eq.{student}"},
                             {"interval_days": days, "repetitions": reps, "ease_factor": ease, "last_reviewed_at": now.isoformat(), "due_at": due})
            return jsonify({"ok": True, "id": card_id, "grade": grade, "next_interval_days": days, "next_due_at": due,
                            "ease_factor": ease, "repetitions": reps, "item": updated[0] if updated else None})
        except Exception as exc: return _json_error(exc)

    @bp.post("/api/study-intelligence/revision/schedule")
    def study_intelligence_revision_schedule():
        data = request.get_json(silent=True) or {}; student = _student_key(data)
        topic = _clean_text(data.get("topic"), 220); subject = _clean_text(data.get("subject"), 100)
        if not student: return jsonify({"ok": False, "error": "client_id or phone_number is required"}), 400
        if not topic: return jsonify({"ok": False, "error": "topic is required"}), 400
        now = datetime.now(timezone.utc); base_date = now.date().isoformat(); results = []
        try:
            for days in (1, 3, 7, 15):
                schedule_key = hashlib.sha256(f"{student}|{topic.lower()}|{subject.lower()}|{base_date}|{days}".encode()).hexdigest()
                row = {"schedule_key": schedule_key, "student_key": student, "topic": topic, "subject": subject,
                       "exam_name": _clean_text(data.get("exam_name"), 120), "step_days": days,
                       "due_at": (now + timedelta(days=days)).isoformat(), "scheduled_from": base_date,
                       "status": "pending", "created_at": now.isoformat()}
                item = _upsert("study_revision_tasks", row, "schedule_key")
                results.append(item or row)
            return jsonify({"ok": True, "items": results, "schedule_days": [1, 3, 7, 15], "idempotent_for_same_topic_today": True})
        except Exception as exc: return _json_error(exc)

    @bp.get("/api/study-intelligence/revision/due")
    def study_intelligence_revision_due():
        student = _student_key()
        if not student: return jsonify({"ok": False, "error": "client_id or phone_number is required"}), 400
        try:
            rows = _select("study_revision_tasks", {"student_key": f"eq.{student}", "due_at": "lte." + _now_iso(), "status": "eq.pending"}, limit=100, order="due_at.asc")
            return jsonify({"ok": True, "items": rows, "count": len(rows), "note": "Due items only; future revisions remain scheduled."})
        except Exception as exc: return _json_error(exc, 503)

    @bp.post("/api/study-intelligence/revision/<int:task_id>/complete")
    def study_intelligence_revision_complete(task_id):
        data = request.get_json(silent=True) or {}; student = _student_key(data)
        if not student: return jsonify({"ok": False, "error": "client_id or phone_number is required"}), 400
        try:
            rows = _patch("study_revision_tasks", {"id": f"eq.{task_id}", "student_key": f"eq.{student}"}, {"status": "completed", "completed_at": _now_iso()})
            if not rows: return jsonify({"ok": False, "error": "Revision task not found"}), 404
            return jsonify({"ok": True, "item": rows[0]})
        except Exception as exc: return _json_error(exc)

    @bp.post("/api/study-intelligence/youtube/transcript")
    def study_intelligence_youtube_transcript():
        data = request.get_json(silent=True) or {}; student = _student_key(data)
        url = str(data.get("url") or "").strip(); video_id = parse_youtube_video_id(url)
        if not student: return jsonify({"ok": False, "error": "client_id or phone_number is required"}), 400
        if not video_id: return jsonify({"ok": False, "error": "Valid YouTube video URL required"}), 400
        if rate_limited and rate_limited("study-intel:yt:" + hashlib.sha256(student.encode()).hexdigest()[:24], 5, 3600):
            return jsonify({"ok": False, "error": "Transcript limit reached for this learner. Try again later."}), 429
        if consume_video_quota and not consume_video_quota(student):
            return jsonify({"ok": False, "error": "Free video-notes quota used for today. Try again tomorrow or use Pro."}), 429
        try:
            from youtube_transcript_api import YouTubeTranscriptApi
            languages = ["en", "hi"]
            fetched = YouTubeTranscriptApi().fetch(video_id, languages=languages)
            segments = fetched.to_raw_data() if hasattr(fetched, "to_raw_data") else list(fetched)
            transcript = " ".join(_clean_text(s.get("text"), 600) for s in segments if s.get("text"))
            transcript = transcript[:MAX_TEXT]
            if len(transcript) < 80: return jsonify({"ok": False, "error": "Caption transcript is unavailable or too short for this video."}), 422
            digest = hashlib.sha256(transcript.encode("utf-8")).hexdigest()
            subject = _clean_text(data.get("subject") or "General", 100)
            language = _clean_text(data.get("language") or "hinglish", 40)
            prompt = ("Create exam-ready study notes from the following video captions. Be accurate, separate key facts from uncertain details, give 5 key points and 5 self-test questions. Do not invent external citations. Do not reproduce the captions verbatim; paraphrase. Keep under 900 words. Subject: " + subject + "\nLanguage: " + language + "\nCAPTIONS:\n" + transcript)
            summary = ""
            if ai_generator:
                try: summary = _clean_text(ai_generator(prompt, "notes", subject, language), 8000)
                except Exception: summary = ""
            if len(summary) < 80:
                summary = "Revision summary (extractive fallback):\n" + extractive_summary(transcript, 7, 1800) + "\n\nCaption-derived only; verify definitions and numerical values before relying on it."
            title = "YouTube video " + video_id
            key = getattr(config, "YOUTUBE_API_KEY", "")
            if key:
                try:
                    vr = requests.get("https://www.googleapis.com/youtube/v3/videos", params={"part":"snippet","id":video_id,"key":key}, timeout=8)
                    if vr.status_code < 300:
                        items = (vr.json() or {}).get("items") or []
                        if items: title = _clean_text(items[0].get("snippet",{}).get("title") or title, 240)
                except Exception: pass
            rec = {"student_key": student, "video_id": video_id, "video_url": "https://www.youtube.com/watch?v="+video_id,
                   "title": title, "subject": subject, "language": language, "summary": summary,
                   "transcript_sha256": digest, "source_url": "https://www.youtube.com/watch?v="+video_id, "created_at": _now_iso()}
            # Raw captions are intentionally not persisted.
            existing = _select("study_youtube_summaries", {"student_key": f"eq.{student}", "video_id": f"eq.{video_id}", "transcript_sha256": f"eq.{digest}"}, limit=1)
            stored = existing[0] if existing else _insert("study_youtube_summaries", rec)
            return jsonify({"ok": True, "item": stored, "raw_transcript_saved": False, "caption_segments": len(segments),
                            "source_url": rec["source_url"], "note": "Summarized notes only are saved; caption availability depends on the video owner and YouTube."})
        except ImportError: return jsonify({"ok": False, "error": "youtube-transcript-api is not installed in the runtime"}), 503
        except Exception as exc:
            logger = app.logger; logger.warning("study intelligence transcript error: %s", exc)
            return jsonify({"ok": False, "error": "Could not retrieve captions for this video. It may have captions disabled, be restricted, or block automated access."}), 422

    @bp.post("/api/study-intelligence/admin/current-affairs/refresh")
    def study_intelligence_current_affairs_refresh():
        ok, failure = _admin_ok()
        if not ok: return failure
        try:
            r = requests.get(PIB_RSS_URL, headers={"User-Agent": "SaarthiBhaiStudyIntelligence/1.0"}, timeout=20)
            r.raise_for_status()
            root = ET.fromstring(r.content)
            items = root.findall(".//item")
            if not items:
                items = root.findall(".//{*}entry")
            saved = 0; skipped = 0; errors = []
            for item in items[:100]:
                title = _clean_text(item.findtext("title") or item.findtext("{*}title"), 240)
                link = _clean_text(item.findtext("link") or item.findtext("{*}link"), 1000)
                if not link:
                    elem = item.find("{*}link")
                    link = _clean_text(elem.attrib.get("href") if elem is not None else "", 1000)
                description = _clean_text(item.findtext("description") or item.findtext("{*}description") or item.findtext("summary") or "", 4000)
                pub = _clean_text(item.findtext("pubDate") or item.findtext("{*}published") or item.findtext("{*}updated") or "", 100)
                pub_iso = None
                if pub:
                    try:
                        parsed_date = parsedate_to_datetime(pub)
                        if parsed_date.tzinfo is None: parsed_date = parsed_date.replace(tzinfo=timezone.utc)
                        pub_iso = parsed_date.astimezone(timezone.utc).isoformat()
                    except Exception:
                        try:
                            parsed_date = datetime.fromisoformat(pub.replace("Z", "+00:00"))
                            if parsed_date.tzinfo is None: parsed_date = parsed_date.replace(tzinfo=timezone.utc)
                            pub_iso = parsed_date.astimezone(timezone.utc).isoformat()
                        except Exception: pub_iso = None
                if not title or not link or not allowed_source_url(link, {"pib.gov.in"}): skipped += 1; continue
                digest = hashlib.sha256(link.encode()).hexdigest()
                summary = extractive_summary(description or title, 3, 650)
                if ai_generator and description:
                    try:
                        ai_summary = _clean_text(ai_generator("Write a neutral 2-3 sentence current-affairs summary from only this PIB press release description. Do not infer or add facts. Headline: " + title + "\nDescription: " + description, "notes", "Current Affairs", "english"), 900)
                        if len(ai_summary) > 40: summary = ai_summary
                    except Exception: pass
                row = {"item_guid": digest, "title": title, "summary": summary or title, "link": link,
                       "source_name": "Press Information Bureau, Government of India", "published_at": pub_iso,
                       "fetched_at": _now_iso(), "source_domain": "pib.gov.in", "review_status": "verified"}
                try:
                    _upsert("study_current_affairs", row, "item_guid"); saved += 1
                except Exception as e: errors.append(str(e)[:160])
            return jsonify({"ok": True, "feed_url": PIB_RSS_URL, "items_seen": len(items), "saved_or_updated": saved,
                            "skipped": skipped, "errors": errors[:3], "note": "Headline/summary metadata and links only; source article remains at PIB."})
        except Exception as exc: return _json_error(RuntimeError("PIB RSS refresh failed: " + str(exc)), 502)

    @bp.get("/api/study-intelligence/current-affairs")
    def study_intelligence_current_affairs():
        try:
            limit = min(max(int(request.args.get("limit", 20) or 20), 1), 100)
            rows = _select("study_current_affairs", {"review_status": "eq.verified"}, limit=limit, order="published_at.desc.nullslast,fetched_at.desc")
            return jsonify({"ok": True, "items": rows, "count": len(rows), "feed_source": PIB_RSS_URL,
                            "message": None if rows else "No PIB items indexed yet. Admin must run the first refresh."})
        except Exception as exc: return _json_error(exc, 503)

    @bp.post("/api/study-intelligence/diagram")
    def study_intelligence_diagram():
        data = request.get_json(silent=True) or {}; student = _student_key(data)
        topic = _clean_text(data.get("topic"), 240); level = _clean_text(data.get("level") or "General", 100)
        language = _clean_text(data.get("language") or "hinglish", 40)
        if not student: return jsonify({"ok": False, "error": "client_id or phone_number is required"}), 400
        if len(topic) < 2: return jsonify({"ok": False, "error": "topic is required"}), 400
        if not _is_pro(student): return jsonify({"ok": False, "error": "Diagram engine is Pro-only", "pro_only": True}), 403
        if rate_limited and rate_limited("study-intel:diagram:" + hashlib.sha256(student.encode()).hexdigest()[:24], 10, 3600):
            return jsonify({"ok": False, "error": "Diagram request limit reached for this learner. Try again later."}), 429
        prompt = ("Create an accurate original educational flowchart as Mermaid syntax only, starting with 'flowchart TD'. Use 5-10 nodes. Use quoted node labels for punctuation. No HTML, scripts, external links, or Markdown fences. Do not invent facts; if the topic is ambiguous use a generic conceptual flow and label it. Then include a short source/accuracy note after a delimiter '---EXPLANATION---'. Topic: " + topic + "; level: " + level + "; language: " + language)
        result = ""
        if ai_generator:
            try: result = str(ai_generator(prompt, "diagram", topic, language) or "")[:5000]
            except Exception: result = ""
        mermaid = ""; explanation = "AI output unavailable; safe starter flow generated from the topic label."
        if "flowchart" in result.lower() or "graph td" in result.lower() or "graph lr" in result.lower():
            pre, sep, post = result.partition("---EXPLANATION---")
            mermaid = pre.strip().replace("```mermaid", "").replace("```", "").strip()
            explanation = _clean_text(post, 1000) if sep else "AI-generated concept map; verify subject facts."
            mermaid = "\n".join(line for line in mermaid.splitlines() if not re.search(r"<\s*script|javascript:|<iframe|<img", line, re.I))[:4000]
        if not mermaid:
            label = re.sub(r"[^A-Za-z0-9 _-]", "", topic)[:40] or "Topic"
            mermaid = f'flowchart TD\n A["{label}"] --> B["Definition"]\n B --> C["Key components"]\n C --> D["Process or relationship"]\n D --> E["Example"]\n E --> F["Exam takeaway"]'
        return jsonify({"ok": True, "topic": topic, "mermaid": mermaid, "explanation": explanation,
                        "disclaimer": "AI-generated diagrams are study aids, not authority; check against your syllabus/textbook."})

    @bp.post("/api/study-intelligence/originality/check")
    def study_intelligence_originality():
        data = request.get_json(silent=True) or {}; text = _clean_text(data.get("text"), 12000)
        if len(text) < 30: return jsonify({"ok": False, "error": "Please provide at least 30 characters to compare."}), 400
        try:
            corpus = []
            pyq = _select("study_pyq_items", {"review_status": "eq.verified"}, columns="exam_name,year,subject,topic,question_stem,source_url", limit=250)
            for x in pyq:
                corpus.append({"label": f"Verified PYQ · {x.get('exam_name','')} {x.get('year','')} · {x.get('topic','')}", "text": x.get("question_stem", ""), "url": x.get("source_url", "")})
            ca = _select("study_current_affairs", {"review_status": "eq.verified"}, columns="title,summary,link", limit=150)
            for x in ca:
                corpus.append({"label": "PIB current-affairs item · " + str(x.get("title", "")), "text": (x.get("title", "") + " " + x.get("summary", "")), "url": x.get("link", "")})
            student = _student_key(data)
            cards = _select("study_flashcards", {"student_key": f"eq.{student}"}, columns="question,answer,subject,topic", limit=300) if student else []
            for x in cards:
                corpus.append({"label": "Your saved flashcard · " + str(x.get("topic", "")), "text": str(x.get("question", "")) + " " + str(x.get("answer", "")), "url": ""})
            scored = []
            for ref in corpus:
                reftext = (ref.get("text") or "").strip()
                if len(reftext) < 50: continue
                score = shingle_overlap(text, reftext, 5)
                if score >= 5:
                    scored.append({"source": ref["label"], "overlap_percent_of_your_text": score, "source_url": ref.get("url", "")})
            scored.sort(key=lambda x: -x["overlap_percent_of_your_text"])
            top = scored[:5]; best = top[0]["overlap_percent_of_your_text"] if top else 0
            if best >= 35: assessment = "Strong overlap with an internal indexed source; inspect and quote/attribute where appropriate."
            elif best >= 15: assessment = "Some overlap with an internal indexed source; review these passages."
            else: assessment = "No strong overlap detected in the current internal index. This does not prove originality."
            return jsonify({"ok": True, "assessment": assessment, "best_internal_overlap_percent": best,
                            "matches": top, "internal_sources_checked": len(corpus), "text_saved": False,
                            "limitations": "Not a web-wide plagiarism scan. Results depend on the limited current index and can miss copied material from unindexed sources."})
        except Exception as exc: return _json_error(exc, 503)

    app.register_blueprint(bp)


__all__ = ["register_study_intelligence", "allowed_source_url", "parse_youtube_video_id", "extractive_summary", "shingle_overlap", "rank_verified_topics", "sm2_next_review"]

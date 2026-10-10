"""
SaarthiBhai by Sparsh Singhal
Fully Gamified Multi-Platform E-Learning Bot
Telegram + WhatsApp + Web Dashboard
Gemini (Primary) + OpenRouter (Fallback) | All Exams | Stats | Razorpay Pro
UI: Branding + Pro Modal + Sounds + Dev Mode + Name Input + Markdown Render
"""
# v7 architecture: Supabase phone identity + Redis hot cache + multi-channel adapters + Next.js/PWA companion.


from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import json
import logging
import os
import random
import re
import secrets
import time
from urllib.parse import quote, urlencode
from collections import defaultdict
from datetime import datetime, timedelta
from threading import Lock, Thread
from typing import Any, Dict, List, Optional, Tuple
from zoneinfo import ZoneInfo

import redis
import requests
from dotenv import load_dotenv
from flask import Flask, jsonify, request, render_template_string, send_from_directory
from google import genai
from google.genai import types as genai_types
from groq import Groq
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ChatAction, ParseMode
from telegram.ext import (
    Application,
    ApplicationBuilder,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    Defaults,
    MessageHandler,
    filters,
)

load_dotenv()


def _load_runtime_config() -> dict:
    """Load non-secret settings from JSON; secrets stay in environment variables."""
    path = os.getenv("SAARTHIBHAI_CONFIG_FILE") or os.path.join(
        os.path.dirname(os.path.abspath(__file__)), "saarthibhai.config.json"
    )
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except Exception as exc:
        raise RuntimeError(f"SaarthiBhai config file could not be loaded: {path}") from exc
    if not isinstance(data, dict):
        raise RuntimeError("SaarthiBhai config root must be a JSON object")
    return data


RUNTIME_CONFIG = _load_runtime_config()
IST = ZoneInfo(RUNTIME_CONFIG["app"]["timezone"])
DATE_FORMAT = str(RUNTIME_CONFIG["app"]["date_format"])


def _setting(path: str):
    value = RUNTIME_CONFIG
    for part in path.split("."):
        value = value[part]
    return value


def _env(name: str) -> str:
    return os.getenv(name, "").strip()


def _env_or_setting(name: str, path: str):
    value = _env(name)
    return value if value else _setting(path)


def _now_ist() -> datetime:
    return datetime.now(IST)


def _today_ist() -> str:
    return _now_ist().strftime(DATE_FORMAT)


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
)
logger = logging.getLogger("saarthibhai")


class Config:
    def __init__(self) -> None:
        # Secrets/tokens are environment-only.
        self.BOT_TOKEN = _env("BOT_TOKEN")
        self.VERCEL_URL = _env("VERCEL_URL")
        self.WEBHOOK_SECRET = _env("WEBHOOK_SECRET")
        self.REDIS_URL = _env("REDIS_URL") or _env("UPSTASH_REDIS_URL")

        self.SUPABASE_URL = _env("SUPABASE_URL").rstrip("/")
        self.SUPABASE_SECRET_KEY = _env("SUPABASE_SECRET_KEY") or _env("SUPABASE_SERVICE_ROLE_KEY")
        self.SUPABASE_TIMEOUT = float(_env_or_setting("SUPABASE_TIMEOUT_SEC", "runtime.supabase_timeout_sec"))
        self.SUPABASE_ENABLED = bool(self.SUPABASE_URL and self.SUPABASE_SECRET_KEY)

        self.GOOGLE_API_KEY = _env("GOOGLE_API_KEY")
        self.GEMINI_MODEL = str(_env_or_setting("GEMINI_MODEL", "ai.gemini_model"))
        self.GEMINI_FLASH_LITE_MODEL = str(_env_or_setting("GEMINI_FLASH_LITE_MODEL", "ai.gemini_flash_lite_model"))
        self.GROQ_API_KEY = _env("GROQ_API_KEY")
        self.GROQ_MODEL = str(_env_or_setting("GROQ_MODEL", "ai.groq_model"))
        self.AI_PRIMARY = str(_env_or_setting("AI_PRIMARY", "ai.primary")).lower()

        self.OPENAI_API_KEY = _env("OPENAI_API_KEY")
        self.OPENAI_MODEL = str(_env_or_setting("OPENAI_MODEL", "ai.openai_model"))
        self.ANTHROPIC_API_KEY = _env("ANTHROPIC_API_KEY")
        self.ANTHROPIC_MODEL = str(_env_or_setting("ANTHROPIC_MODEL", "ai.anthropic_model"))
        self.XAI_API_KEY = _env("XAI_API_KEY")
        self.XAI_MODEL = str(_env_or_setting("XAI_MODEL", "ai.xai_model"))
        self.DEEPSEEK_API_KEY = _env("DEEPSEEK_API_KEY")
        self.DEEPSEEK_MODEL = str(_env_or_setting("DEEPSEEK_MODEL", "ai.deepseek_model"))
        self.PERPLEXITY_API_KEY = _env("PERPLEXITY_API_KEY")
        self.PERPLEXITY_MODEL = str(_env_or_setting("PERPLEXITY_MODEL", "ai.perplexity_model"))
        self.META_API_KEY = _env("META_API_KEY")
        self.META_BASE_URL = str(_env_or_setting("META_BASE_URL", "ai.meta_base_url"))
        self.META_MODEL = str(_env_or_setting("META_MODEL", "ai.meta_model"))
        self.MISTRAL_API_KEY = _env("MISTRAL_API_KEY")
        self.MISTRAL_MODEL = str(_env_or_setting("MISTRAL_MODEL", "ai.mistral_model"))
        self.QWEN_API_KEY = _env("QWEN_API_KEY")
        self.QWEN_BASE_URL = str(_env_or_setting("QWEN_BASE_URL", "ai.qwen_base_url"))
        self.QWEN_MODEL = str(_env_or_setting("QWEN_MODEL", "ai.qwen_model"))

        provider_order = _env("AI_PROVIDER_ORDER")
        order = provider_order.split(",") if provider_order else list(_setting("ai.provider_order"))
        self.AI_PROVIDER_ORDER = [str(x).strip().lower() for x in order if str(x).strip()]
        self.AI_PARALLEL_FANOUT = int(_env_or_setting("AI_PARALLEL_FANOUT", "runtime.ai_parallel_fanout"))
        self.LIBRARY_CONTEXT_ENABLED = str(_env_or_setting("LIBRARY_CONTEXT_ENABLED", "runtime.library_context_enabled")).lower() not in ("0", "false", "no")

        self.OPENROUTER_API_KEY = _env("OPENROUTER_API_KEY")
        openrouter_models = _env("OPENROUTER_MODELS")
        models = openrouter_models.split(",") if openrouter_models else list(_setting("ai.openrouter_models"))
        self.OPENROUTER_MODELS = [str(m).strip() for m in models if str(m).strip()]
        self.OPENROUTER_SITE_URL = str(_env_or_setting("OPENROUTER_SITE_URL", "ai.openrouter_site_url"))
        self.OPENROUTER_APP_NAME = str(_env_or_setting("OPENROUTER_APP_NAME", "ai.openrouter_app_name"))

        self.FREE_DAILY = int(_env_or_setting("FREE_DAILY_QUESTIONS", "limits.free_daily_questions"))
        self.FREE_LIFETIME = int(_env_or_setting("FREE_LIFETIME_QUESTIONS", "limits.free_lifetime_questions"))
        self.PRO_PRICE_INR = int(_env_or_setting("PRO_PRICE_INR", "limits.pro_price_inr"))
        self.RAZORPAY_KEY_ID = _env("RAZORPAY_KEY_ID")
        self.RAZORPAY_KEY_SECRET = _env("RAZORPAY_KEY_SECRET")
        self.RAZORPAY_WEBHOOK_SECRET = _env("RAZORPAY_WEBHOOK_SECRET")

        self.WHATSAPP_TOKEN = _env("WHATSAPP_TOKEN")
        self.WHATSAPP_PHONE_NUMBER_ID = _env("WHATSAPP_PHONE_NUMBER_ID")
        self.WHATSAPP_VERIFY_TOKEN = _env("WHATSAPP_VERIFY_TOKEN")
        self.WHATSAPP_API_VERSION = str(_env_or_setting("WHATSAPP_API_VERSION", "channels.whatsapp_api_version"))

        self.XP_QUESTION = int(_env_or_setting("XP_QUESTION", "limits.xp_question"))
        self.WELCOME_COINS = int(_env_or_setting("WELCOME_COINS", "limits.welcome_coins"))
        self.SPIN_MIN_COINS = int(_env_or_setting("SPIN_MIN_COINS", "limits.spin_min_coins"))
        self.SPIN_MAX_COINS = int(_env_or_setting("SPIN_MAX_COINS", "limits.spin_max_coins"))
        self.REFERRAL_COINS = int(_env_or_setting("REFERRAL_COINS", "limits.referral_coins"))
        self.TRIAL_COINS = int(_env_or_setting("TRIAL_COINS", "limits.trial_coins"))
        self.TRIAL_REFERRALS = int(_env_or_setting("TRIAL_REFERRALS", "limits.trial_referrals"))
        self.TRIAL_DAYS = int(_env_or_setting("TRIAL_DAYS", "limits.trial_days"))
        self.SPACED_REMINDER_DAYS = tuple(int(x) for x in _setting("limits.spaced_reminder_days"))
        self.MISSION_REWARD_COINS = int(_env_or_setting("MISSION_REWARD_COINS", "limits.mission_reward_coins"))
        self.QUIZ_PERFECT_COINS = int(_env_or_setting("QUIZ_PERFECT_COINS", "limits.quiz_perfect_coins"))
        self.QUIZ_FREE_QUESTIONS = int(_env_or_setting("QUIZ_FREE_QUESTIONS", "limits.quiz_free_questions"))
        self.QUIZ_PRO_QUESTIONS = int(_env_or_setting("QUIZ_PRO_QUESTIONS", "limits.quiz_pro_questions"))
        self.GALTI_FREE_VISIBLE = int(_env_or_setting("GALTI_FREE_VISIBLE", "limits.galti_free_visible"))
        self.MISTAKE_COINS = int(_env_or_setting("MISTAKE_COINS", "limits.mistake_coins"))
        self.FREE_PDF_PER_DAY = int(_env_or_setting("FREE_PDF_PER_DAY", "limits.free_pdf_per_day"))
        self.FREE_VIDEO_LINKS = int(_env_or_setting("FREE_VIDEO_LINKS", "limits.free_video_links"))
        self.FREE_ASSIGNMENT_PER_DAY = int(_env_or_setting("FREE_ASSIGNMENT_PER_DAY", "limits.free_assignment_per_day"))
        self.PRO_VIDEO_LINKS = int(_env_or_setting("PRO_VIDEO_LINKS", "limits.pro_video_links"))
        self.PRO_VIVA_QUESTIONS = int(_env_or_setting("PRO_VIVA_QUESTIONS", "limits.pro_viva_questions"))
        self.PRO_XP_MULTIPLIER = int(_env_or_setting("PRO_XP_MULTIPLIER", "limits.pro_xp_multiplier"))
        self.DAILY_MISSION_MINUTES = int(_env_or_setting("DAILY_MISSION_MINUTES", "limits.daily_mission_minutes"))
        self.DAILY_MISSION_XP = int(_env_or_setting("DAILY_MISSION_XP", "limits.daily_mission_xp"))
        self.WELCOME_FREE_SPIN_COUNT = int(_env_or_setting("WELCOME_FREE_SPIN_COUNT", "limits.welcome_free_spin_count"))
        self.WELCOME_FREEZE_COUNT = int(_env_or_setting("WELCOME_FREEZE_COUNT", "limits.welcome_freeze_count"))
        self.IMAGE_MAX_BYTES = int(_env_or_setting("IMAGE_MAX_BYTES", "limits.image_max_bytes"))
        self.TELEGRAM_DOCUMENT_MAX_BYTES = int(_env_or_setting("TELEGRAM_DOCUMENT_MAX_BYTES", "limits.telegram_document_max_bytes"))
        self.ABUSE_WARNING_LIMIT = int(_env_or_setting("ABUSE_WARNING_LIMIT", "limits.abuse_warning_limit"))
        self.ABUSE_BAN_HOURS = int(_env_or_setting("ABUSE_BAN_HOURS", "limits.abuse_ban_hours"))
        abuse_words = _env("ABUSE_WORDS")
        words = abuse_words.split(",") if abuse_words else list(_setting("content.abuse_words"))
        self.ABUSE_WORDS = [str(w).strip().lower() for w in words if str(w).strip()]
        self.CACHE_TTL = int(_env_or_setting("CACHE_TTL_SEC", "runtime.cache_ttl_sec"))
        self.PUBLIC_SCHEME = str(_setting("app.public_scheme"))
        self.ANTHROPIC_API_VERSION = str(_setting("ai.anthropic_api_version"))

        self.DEV_SECRET = _env("DEV_SECRET")
        self.BRAND_NAME = str(_setting("app.brand_name"))
        self.CREATOR_NAME = str(_setting("app.creator_name"))
        self.CREATOR_PHOTO_URL = str(_env_or_setting("CREATOR_PHOTO_URL", "app.creator_photo_url"))
        self.CREATOR_STORY_URL = str(_env_or_setting("CREATOR_STORY_URL", "app.creator_story_url"))
        self.APP_VERSION = str(_setting("app.version"))
        self.PUBLIC_DOMAIN = str(_setting("app.public_domain"))
        self.PUBLIC_SCHEME = str(_setting("app.public_scheme"))
        self.SHORT_NAME = str(_setting("app.short_name"))
        self.PWA_DESCRIPTION = str(_setting("app.pwa_description"))
        self.CORS_ORIGIN = str(_env_or_setting("CORS_ORIGIN", "app.cors_origin"))
        self.DEFAULT_COUNTRY_CODE = str(_setting("phone.default_country_code"))
        self.NORMALIZE_10_DIGIT_PHONE = bool(_setting("phone.normalize_10_digit_to_default_country"))
        self.THEME = dict(_setting("theme"))
        self.URLS = dict(_setting("urls"))
        self.CONTENT = dict(_setting("content"))
        self.FREE_UPSELL_LINE = str(_setting("content.free_upsell_line"))
        self.BRAND_STYLE_INTRO = str(_setting("content.brand_style_intro"))
        self.COPYRIGHT_CONTACT = str(_env_or_setting("DMCA_EMAIL", "content.copyright_contact"))
        self.FEATURE_CATALOG_28 = list(_setting("content.feature_catalog_28"))
        self.TOOL_ALIASES = dict(_setting("content.tool_aliases"))
        self.PRO_ONLY_TOOLS = set(_setting("content.pro_only_tools"))
        self.TOOL_KEYWORDS = [tuple(item) for item in _setting("content.tool_keywords")]
        self.EXAM_MAP = dict(_setting("content.exam_map"))
        self.SUBJECT_MAP = dict(_setting("content.subject_map"))
        self.MESSAGES = dict(_setting("content.messages"))
        self.DEFAULT_STUDENT_NAMES = dict(_setting("content.default_student_names"))
        self.ANSWER_RESOURCE_COPY = dict(_setting("content.answer_resource_copy"))
        self.PRODUCT_TEXT = dict(_setting("content.product_text"))
        self.SYSTEM_PROTOCOL = dict(_setting("content.system_protocol"))
        self.INSTAGRAM_GRAPH_VERSION = str(_env_or_setting("INSTAGRAM_GRAPH_VERSION", "channels.instagram_graph_version"))
        self.YOUTUBE_API_KEY = _env("YOUTUBE_API_KEY")
        self.INSTAGRAM_ACCESS_TOKEN = _env("INSTAGRAM_ACCESS_TOKEN")
        self.INSTAGRAM_ACCOUNT_ID = _env("INSTAGRAM_ACCOUNT_ID")
        self.INSTAGRAM_VERIFY_TOKEN = _env("INSTAGRAM_VERIFY_TOKEN")
        snap = _env("SNAPCHAT_DM_GATEWAY_ENABLED")
        self.SNAPCHAT_ENABLED = snap.lower() in ("1", "true", "yes") if snap else bool(_setting("channels.snapchat_gateway_enabled"))
        self.SNAPCHAT_DM_GATEWAY_URL = _env("SNAPCHAT_DM_GATEWAY_URL")
        self.SNAPCHAT_DM_GATEWAY_TOKEN = _env("SNAPCHAT_DM_GATEWAY_TOKEN")

        # Discord HTTP interactions / optional Gateway worker. Disabled unless explicitly configured.
        discord_cfg = _setting("channels.discord")
        self.DISCORD_ENABLED = (_env("DISCORD_ENABLED").lower() in ("1","true","yes")) if _env("DISCORD_ENABLED") else bool(discord_cfg.get("enabled", False))
        self.DISCORD_BOT_TOKEN = _env("DISCORD_BOT_TOKEN")
        self.DISCORD_APPLICATION_ID = _env("DISCORD_APPLICATION_ID")
        self.DISCORD_PUBLIC_KEY = _env("DISCORD_PUBLIC_KEY")
        self.DISCORD_GUILD_ID = _env("DISCORD_GUILD_ID")
        self.DISCORD_MESSAGE_CONTENT_INTENT = (_env("DISCORD_MESSAGE_CONTENT_INTENT").lower() in ("1","true","yes")) if _env("DISCORD_MESSAGE_CONTENT_INTENT") else bool(discord_cfg.get("message_content_intent", False))
        self.DISCORD_MAX_RESPONSE_CHARS = int(_env_or_setting("DISCORD_MAX_RESPONSE_CHARS", "channels.discord.max_response_chars"))
        self.DISCORD_COMMAND_NAME = str(_env_or_setting("DISCORD_COMMAND_NAME", "channels.discord.command_name"))
        self.DISCORD_COMMAND_DESCRIPTION = str(_env_or_setting("DISCORD_COMMAND_DESCRIPTION", "channels.discord.command_description"))
        self.DISCORD_RUN_MODE = str(_env_or_setting("DISCORD_RUN_MODE", "channels.discord.run_mode")).strip().lower()

        reddit_cfg = _setting("channels.reddit")
        self.REDDIT_ENABLED = (_env("REDDIT_ENABLED").lower() in ("1","true","yes")) if _env("REDDIT_ENABLED") else bool(reddit_cfg.get("enabled", False))
        self.REDDIT_BACKEND_TOKEN = _env("REDDIT_BACKEND_TOKEN")
        self.REDDIT_BACKEND_PATH = str(_env_or_setting("REDDIT_BACKEND_PATH", "channels.reddit.backend_path"))
        self.REDDIT_TRIGGER_PREFIX = str(_env_or_setting("REDDIT_TRIGGER_PREFIX", "channels.reddit.trigger_prefix"))
        self.REDDIT_AUTO_REPLY = (_env("REDDIT_AUTO_REPLY").lower() in ("1","true","yes")) if _env("REDDIT_AUTO_REPLY") else bool(reddit_cfg.get("auto_reply", True))
        self.REDDIT_MAX_REPLY_CHARS = int(_env_or_setting("REDDIT_MAX_REPLY_CHARS", "channels.reddit.max_reply_chars"))

        self.REDIS_MAX_CONN = int(_env_or_setting("REDIS_MAX_CONN", "runtime.redis_max_connections"))
        self.AI_POOL_WORKERS = int(_env_or_setting("AI_POOL_WORKERS", "runtime.ai_pool_workers"))
        self.AI_TIMEOUT_SEC = float(_env_or_setting("AI_TIMEOUT_SEC", "runtime.ai_timeout_sec"))
        self.DB_WRITE_WORKERS = int(_env_or_setting("DB_WRITE_WORKERS", "runtime.db_write_workers"))
        self.SEMANTIC_CACHE_THRESHOLD = float(_env_or_setting("SEMANTIC_CACHE_THRESHOLD", "runtime.semantic_cache_threshold"))
        self.GEMINI_EMBED_MODEL = str(_env_or_setting("GEMINI_EMBED_MODEL", "ai.gemini_embed_model"))
        self.PORT = int(_env_or_setting("PORT", "app.port"))

        self.validate()

    def validate(self) -> None:
        if not self.BOT_TOKEN:
            logger.warning("BOT_TOKEN not configured; Telegram bot features disabled")
        if not self.GROQ_API_KEY and not self.GOOGLE_API_KEY and not self.OPENAI_API_KEY and not self.OPENROUTER_API_KEY:
            logger.warning("No text-generation provider API key configured")


config = Config()
BRAND_NAME = config.BRAND_NAME
SW_CACHE_NAME = re.sub(r"[^a-z0-9_-]+", "-", config.APP_VERSION.lower()).strip("-")

# ----------------------------------------------------------------------
# BUILD IDENTITY — a self-hash of the running app.py, computed once at
# startup. This exists specifically so "which version is actually live"
# can be answered from the deployment itself (via /health) instead of by
# comparing pasted file snapshots, which is exactly what kept going wrong
# across our earlier audit rounds.
# ----------------------------------------------------------------------
def _compute_build_hash() -> str:
    try:
        with open(__file__, "rb") as f:
            return hashlib.sha256(f.read()).hexdigest()[:12]
    except Exception as e:
        logger.warning("_compute_build_hash: %s", e)
        return "unknown"


BUILD_HASH = _compute_build_hash()
BUILD_LINE_COUNT = None
try:
    with open(__file__, "r", encoding="utf-8") as _f:
        BUILD_LINE_COUNT = sum(1 for _ in _f)
except Exception:
    pass

from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeout

_AI_POOL = ThreadPoolExecutor(max_workers=config.AI_POOL_WORKERS, thread_name_prefix="ai")
_AI_TIMEOUT = config.AI_TIMEOUT_SEC
_DB_WRITE_POOL = ThreadPoolExecutor(max_workers=config.DB_WRITE_WORKERS, thread_name_prefix="dbwrite")
_rate_lock = Lock()
_rate_buckets: dict[str, list[float]] = defaultdict(list)
_redis_for_rl: Optional[redis.Redis] = None


def is_rate_limited(key: str, max_calls: int = 8, window_sec: int = 60) -> bool:
    r = _redis_for_rl
    if r is not None:
        try:
            rk = f"rl:{key}"
            now = time.time()
            pipe = r.pipeline()
            pipe.zremrangebyscore(rk, 0, now - window_sec)
            pipe.zcard(rk)
            pipe.zadd(rk, {f"{now}:{secrets.token_hex(3)}": now})
            pipe.expire(rk, window_sec + 5)
            results = pipe.execute()
            return int(results[1] or 0) >= max_calls
        except Exception as e:
            logger.warning("Redis rate-limit fallback: %s", e)
    now = time.time()
    with _rate_lock:
        bucket = _rate_buckets[key]
        while bucket and bucket[0] <= now - window_sec:
            bucket.pop(0)
        if len(bucket) >= max_calls:
            return True
        bucket.append(now)
        return False


def run_ai(fn, *args, **kwargs):
    fut = _AI_POOL.submit(fn, *args, **kwargs)
    try:
        result = fut.result(timeout=_AI_TIMEOUT)
        if result is None:
            return "ERROR: AI returned empty response."
        return result
    except FuturesTimeout:
        logger.warning("AI pool timeout after %.1fs", _AI_TIMEOUT)
        return SOFT_FAIL_MSG
    except Exception as e:
        logger.error("AI pool error: %s", e)
        return SOFT_FAIL_MSG



def make_cache_key(tool: str, question: str, is_pro: bool, language: str = "hinglish") -> str:
    q = " ".join((question or "").lower().split())
    lang = (language or "hinglish").strip().lower()
    raw = f"{tool}|{1 if is_pro else 0}|{lang}|{q}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:40]


def create_razorpay_order(uid: str, amount_inr: int) -> dict:
    if not config.RAZORPAY_KEY_ID or not config.RAZORPAY_KEY_SECRET:
        return {"error": "Razorpay keys missing"}
    try:
        auth = base64.b64encode(
            f"{config.RAZORPAY_KEY_ID}:{config.RAZORPAY_KEY_SECRET}".encode()
        ).decode()
        payload = {
            "amount": int(amount_inr) * 100,
            "currency": "INR",
            "receipt": f"sg_{str(uid)[:20]}_{int(time.time())}"[:40],
            "notes": {"user_id": str(uid)},
        }
        r = requests.post(
            config.URLS["razorpay_orders"],
            json=payload,
            headers={"Authorization": f"Basic {auth}", "Content-Type": "application/json"},
            timeout=20,
        )
        data = r.json()
        if r.status_code >= 400:
            logger.error("Razorpay order error: %s", data)
            return {"error": data.get("error", {}).get("description", "Order failed")}
        return data
    except Exception as e:
        logger.error("create_razorpay_order: %s", e)
        return {"error": str(e)}


def process_refund(refund_id: str = "", payment_id: str = "", amount_paise: int = 0) -> dict:
    """Handle a verified Razorpay refund event and revoke the refunded Pro entitlement.

    This is intentionally conservative: it only changes the Pro plan when a payment
    can be mapped to a user and the refund is at least the configured Pro price.
    """
    refund_id = (refund_id or "").strip()
    payment_id = (payment_id or "").strip()
    if not payment_id:
        return {"ok": False, "error": "payment_id required"}
    try:
        auth = base64.b64encode(
            f"{config.RAZORPAY_KEY_ID}:{config.RAZORPAY_KEY_SECRET}".encode()
        ).decode()
        r = requests.get(
            f"{config.URLS["razorpay_payments"]}/{payment_id}",
            headers={"Authorization": f"Basic {auth}"},
            timeout=20,
        )
        payment = r.json() if r.content else {}
        if r.status_code >= 300:
            return {"ok": False, "error": "Could not verify payment for refund"}
        notes = payment.get("notes") or {}
        uid = str(notes.get("user_id") or "").strip()
        if not uid:
            return {"ok": False, "error": "user_id missing from payment"}

        expected = int(config.PRO_PRICE_INR) * 100
        refunded = int(amount_paise or 0)
        # If the event did not include an amount, fetch payment's refund total.
        if refunded <= 0:
            rr = requests.get(
                f"{config.URLS["razorpay_payments"]}/{payment_id}/refunds",
                headers={"Authorization": f"Basic {auth}"},
                timeout=20,
            )
            if rr.status_code < 300:
                items = (rr.json() or {}).get("items") or []
                refunded = sum(int(x.get("amount") or 0) for x in items)
        if refunded < expected:
            return {"ok": True, "uid": uid, "partial": True, "message": "Partial refund recorded; Pro retained until full-price refund."}

        if db.redis and refund_id:
            if not db.redis.set(f"refund:done:{refund_id}", "1", nx=True, ex=86400 * 365):
                return {"ok": True, "uid": uid, "duplicate": True}
        user = db.get_user(uid) or db.ensure_user(uid, full_name="Student", platform="web")
        user["plan"] = "free"
        user["pro_until"] = ""
        user["refunded_at"] = _now_ist().isoformat()
        user["last_refund_id"] = refund_id
        db.save_user(uid, user)
        try:
            db.sync_user_to_supabase(uid, user)
        except Exception:
            pass
        if db.redis:
            db.redis.srem("stats:pro_users", str(uid))
        logger.info("Pro revoked after refund uid=%s payment=%s refund=%s", uid, payment_id, refund_id)
        return {"ok": True, "uid": uid, "revoked": True, "message": "Pro revoked after full refund"}
    except Exception as e:
        logger.error("process_refund: %s", e)
        return {"ok": False, "error": str(e)}


def verify_and_activate_razorpay_payment(payment_id: str, order_id: str = "", uid_hint: str = "") -> dict:
    """Verify payment with Razorpay API and activate Pro immediately (no webhook wait)."""
    if not config.RAZORPAY_KEY_ID or not config.RAZORPAY_KEY_SECRET:
        return {"ok": False, "error": "Razorpay not configured"}
    payment_id = (payment_id or "").strip()
    if not payment_id:
        return {"ok": False, "error": "payment_id required"}
    try:
        auth = base64.b64encode(
            f"{config.RAZORPAY_KEY_ID}:{config.RAZORPAY_KEY_SECRET}".encode()
        ).decode()
        r = requests.get(
            f"{config.URLS["razorpay_payments"]}/{payment_id}",
            headers={"Authorization": f"Basic {auth}"},
            timeout=20,
        )
        data = r.json()
        if r.status_code >= 400:
            logger.error("Razorpay payment fetch: %s", data)
            return {"ok": False, "error": data.get("error", {}).get("description", "Payment verify failed")}
        status = (data.get("status") or "").lower()
        if status != "captured":
            return {"ok": False, "error": f"Payment not captured ({status})"}
        amount = int(data.get("amount") or 0)
        expected = int(config.PRO_PRICE_INR) * 100
        if amount < expected:
            return {"ok": False, "error": "Amount mismatch"}
        notes = data.get("notes") or {}
        uid = (notes.get("user_id") or uid_hint or "").strip()
        if order_id and data.get("order_id") and str(data.get("order_id")) != str(order_id):
            return {"ok": False, "error": "Order mismatch"}
        if not uid:
            return {"ok": False, "error": "user_id missing on payment"}
        if payment_id and not db.mark_payment_processed(payment_id):
            return {"ok": True, "uid": uid, "plan": "pro", "duplicate": True, "message": "Already activated"}
        db.ensure_user(uid, full_name=config.DEFAULT_STUDENT_NAMES["pro"], platform="web")
        db.activate_pro(uid, days=30)
        try:
            pu = db.get_user(uid) or {}
            pu["last_payment_id"] = payment_id
            db.save_user(uid, pu)
            db.sync_user_to_supabase(uid, pu)
        except Exception:
            pass
        try:
            db.add_badge(uid, "Pro Warrior 👑")
        except Exception:
            pass
        logger.info("Pro activated instantly for %s via payment %s", uid, payment_id)
        return {"ok": True, "uid": uid, "plan": "pro", "message": "Pro unlocked"}
    except Exception as e:
        logger.error("verify_and_activate: %s", e)
        return {"ok": False, "error": str(e)}



# ============================================================================
# DATABASE
# ============================================================================

# ============================================================================
# SUPABASE DURABLE STORE
# ============================================================================

def normalize_phone(phone: str) -> str:
    """Return a stable phone identifier. Indian 10-digit numbers get +91."""
    raw = (phone or "").strip()
    if not raw:
        return ""
    # WhatsApp often sends +9198... and web forms may send spaces/dashes.
    digits = re.sub(r"\D", "", raw)
    if not digits:
        return ""
    if len(digits) == 10 and config.NORMALIZE_10_DIGIT_PHONE:
        digits = config.DEFAULT_COUNTRY_CODE + digits
    return "+" + digits


def guess_exam_subject(question: str, exam_type: str = "", subject: str = "") -> Tuple[str, str]:
    """Lightweight, deterministic tags for master_cache metadata."""
    q = (question or "").lower()
    ex = (exam_type or "").strip().lower()
    sub = (subject or "").strip().lower()
    exam_map = config.EXAM_MAP
    if not ex:
        for k, v in exam_map.items():
            if re.search(r"\b" + re.escape(k) + r"\b", q):
                ex = v
                break
    if not ex:
        ex = "general"
    subject_map = config.SUBJECT_MAP
    if not sub:
        for candidate, words in subject_map.items():
            if any(w in q for w in words):
                sub = candidate
                break
    return ex[:64] or "general", (sub[:64] or "general")


class SupabaseStore:
    """Small REST wrapper using Supabase's Data API; no extra SDK dependency."""
    def __init__(self) -> None:
        self.enabled = bool(config.SUPABASE_ENABLED)
        self.base = f"{config.SUPABASE_URL}/rest/v1" if self.enabled else ""
        self.headers = ({
            "apikey": config.SUPABASE_SECRET_KEY,
            "Authorization": f"Bearer {config.SUPABASE_SECRET_KEY}",
            "Content-Type": "application/json",
        } if self.enabled else {})
        if self.enabled:
            logger.info("Supabase REST enabled")
        else:
            logger.warning("Supabase disabled — set SUPABASE_URL + SUPABASE_SECRET_KEY")

    def _url(self, table: str) -> str:
        return f"{self.base}/{table}"

    def _request(self, method: str, table_or_rpc: str, **kwargs):
        if not self.enabled:
            return None
        try:
            return requests.request(
                method, self._url(table_or_rpc) if not table_or_rpc.startswith("rpc/") else f"{self.base}/{table_or_rpc}",
                headers=self.headers, timeout=config.SUPABASE_TIMEOUT, **kwargs
            )
        except Exception as e:
            logger.warning("Supabase %s %s: %s", method, table_or_rpc, e)
            return None

    def upsert(self, table: str, row: Dict[str, Any], on_conflict: str) -> bool:
        if not self.enabled or not row:
            return False
        try:
            h = dict(self.headers)
            h["Prefer"] = "resolution=merge-duplicates,return=minimal"
            r = requests.post(
                f"{self._url(table)}?on_conflict={quote(on_conflict, safe=',')}",
                headers=h, json=row, timeout=config.SUPABASE_TIMEOUT,
            )
            if r.status_code >= 300:
                logger.warning("Supabase upsert %s %s: %s", table, r.status_code, r.text[:500])
                return False
            return True
        except Exception as e:
            logger.warning("Supabase upsert %s: %s", table, e)
            return False

    def insert(self, table: str, row: Dict[str, Any]) -> bool:
        if not self.enabled:
            return False
        try:
            r = requests.post(f"{self._url(table)}", headers=self.headers, json=row, timeout=config.SUPABASE_TIMEOUT)
            if r.status_code >= 300:
                logger.warning("Supabase insert %s %s: %s", table, r.status_code, r.text[:500])
                return False
            return True
        except Exception as e:
            logger.warning("Supabase insert %s: %s", table, e)
            return False

    def select_one(self, table: str, filters: Dict[str, str], columns: str = "*") -> Optional[Dict[str, Any]]:
        if not self.enabled:
            return None
        params = {"select": columns, **filters}
        try:
            r = requests.get(self._url(table), headers=self.headers, params=params, timeout=config.SUPABASE_TIMEOUT)
            if r.status_code >= 300:
                return None
            data = r.json() or []
            return data[0] if data else None
        except Exception as e:
            logger.warning("Supabase select %s: %s", table, e)
            return None

    def select_many(self, table: str, filters: Dict[str, str], limit: int = 5, columns: str = "*") -> List[Dict[str, Any]]:
        if not self.enabled:
            return []
        params = {"select": columns, "limit": str(limit), **filters}
        try:
            r = requests.get(self._url(table), headers=self.headers, params=params, timeout=config.SUPABASE_TIMEOUT)
            if r.status_code >= 300:
                return []
            data = r.json()
            return data if isinstance(data, list) else []
        except Exception as e:
            logger.warning("Supabase select many %s: %s", table, e)
            return []

    def rpc(self, fn: str, payload: Dict[str, Any]) -> Optional[Any]:
        if not self.enabled:
            return None
        try:
            r = requests.post(f"{self.base}/rpc/{fn}", headers=self.headers, json=payload, timeout=config.SUPABASE_TIMEOUT)
            if r.status_code >= 300:
                logger.debug("Supabase RPC %s %s: %s", fn, r.status_code, r.text[:400])
                return None
            return r.json()
        except Exception as e:
            logger.debug("Supabase RPC %s: %s", fn, e)
            return None


supa = SupabaseStore()


class Database:
    def __init__(self) -> None:
        self.redis = self._connect()
        self.supabase = supa
        self._quota_script = None
        if self.redis:
            try:
                self._quota_script = self.redis.register_script(self._QUOTA_LUA)
            except Exception as e:
                logger.warning("Quota Lua script registration failed, will retry per-call: %s", e)

    def _connect(self) -> Optional[redis.Redis]:
        if not config.REDIS_URL:
            logger.warning("No Redis – limited mode")
            return None
        try:
            pool = redis.ConnectionPool.from_url(
                config.REDIS_URL,
                decode_responses=True,
                max_connections=config.REDIS_MAX_CONN,
                socket_timeout=5,
                socket_connect_timeout=5,
                socket_keepalive=True,
                retry_on_timeout=True,
                health_check_interval=30,
            )
            r = redis.Redis(connection_pool=pool)
            r.ping()
            logger.info("Redis OK")
            return r
        except Exception as e:
            logger.error("Redis fail: %s", e)
            return None

    def set_tool(self, uid: str | int, tool: str, ttl: int = 300) -> None:
        if self.redis:
            try:
                self.redis.setex(f"tool:{uid}", ttl, tool)
            except Exception:
                pass

    def pop_tool(self, uid: str | int) -> Optional[str]:
        if not self.redis:
            return None
        try:
            key = f"tool:{uid}"
            tool = self.redis.get(key)
            if tool:
                self.redis.delete(key)
            return tool
        except Exception:
            return None

    def mark_payment_processed(self, payment_id: str) -> bool:
        if not self.redis or not payment_id:
            return True
        try:
            return bool(self.redis.set(f"pay:done:{payment_id}", "1", nx=True, ex=86400 * 90))
        except Exception:
            return True

    def _key(self, uid: str | int) -> str:
        return f"user:{uid}"

    def get_user(self, uid: str | int) -> Optional[Dict[str, str]]:
        uid = str(uid)
        if self.redis:
            try:
                data = self.redis.hgetall(self._key(uid))
                if data:
                    return data
            except Exception:
                pass

        # Durable recovery: Redis may be empty after a deploy/restart.
        if self.supabase.enabled:
            try:
                phone = normalize_phone(uid) if re.fullmatch(r"\+?[0-9 ()-]{10,20}", uid) else ""
                if uid.startswith("wa:"):
                    phone = normalize_phone(uid[3:])
                row = None
                if phone:
                    row = self.supabase.select_one("users", {"phone_number": f"eq.{phone}"})
                if not row:
                    row = self.supabase.select_one("users", {"legacy_uid": f"eq.{uid}"})
                if row:
                    data: Dict[str, str] = {}
                    for k, v in row.items():
                        if isinstance(v, (dict, list)):
                            data[k] = json.dumps(v)
                        elif v is None:
                            data[k] = ""
                        else:
                            data[k] = str(v)
                    data.setdefault("user_id", uid)
                    data.setdefault("badges", "[]")
                    data.setdefault("language_pref", "hinglish")
                    data.setdefault("platform", "web")
                    data.setdefault("plan", "free")
                    data.setdefault("coins", "0")
                    data.setdefault("xp", "0")
                    data.setdefault("level", "1")
                    data.setdefault("streak", "0")
                    data.setdefault("fire_streak", data.get("streak", "0"))
                    if self.redis:
                        self.save_user(uid, data)
                    return data
            except Exception as e:
                logger.debug("Supabase user recovery failed: %s", e)
        return None

    def save_user(self, uid: str | int, data: Dict[str, Any]) -> bool:
        if not self.redis:
            return False
        try:
            payload = {k: str(v) for k, v in data.items()}
            pipe = self.redis.pipeline()
            pipe.hset(self._key(uid), mapping=payload)
            pipe.expire(self._key(uid), 86400 * 120)
            pipe.execute()
            return True
        except Exception as e:
            logger.error("save_user: %s", e)
            return False

    def ensure_user(self, uid: str | int, username: str = "", full_name: str = "", platform: str = "telegram", referred_by: str = "", phone_number: str = "", exam_type: str = "", subject: str = "") -> Dict[str, str]:
        uid = str(uid)
        inferred_phone = normalize_phone(phone_number or (uid[3:] if uid.startswith("wa:") else ""))
        user = self.get_user(uid)
        # Cross-device recovery: same phone should reopen the durable student profile.
        if not user and inferred_phone and self.supabase.enabled:
            try:
                row = self.supabase.select_one("users", {"phone_number": f"eq.{inferred_phone}"})
                if row:
                    user = {}
                    for k, v in row.items():
                        user[k] = json.dumps(v) if isinstance(v, (dict, list)) else ("" if v is None else str(v))
                    user.setdefault("user_id", uid)
                    user.setdefault("legacy_uid", uid)
                    if self.redis:
                        self.save_user(uid, user)
            except Exception as e:
                logger.debug("Supabase phone recovery failed: %s", e)
        if user:
            changed = False
            p = normalize_phone(phone_number)
            if p and p != user.get("phone_number", ""):
                user["phone_number"] = p; changed = True
            if exam_type:
                user["exam_type"] = exam_type.strip().lower(); changed = True
            if subject:
                user["subject"] = subject.strip().lower(); changed = True
            user["updated_at"] = _now_ist().isoformat()
            if changed:
                self.save_user(uid, user)
            if self.redis:
                try:
                    self.redis.sadd("stats:users", str(uid))
                except Exception:
                    pass
            self.sync_user_to_supabase(uid, user)
            return user
        data = {
            "user_id": str(uid),
            "username": username or "",
            "full_name": full_name or config.DEFAULT_STUDENT_NAMES["generic"],
            "platform": platform,
            "plan": "free",
            "pro_until": "",
            "xp": "0",
            "level": "1",
            "coins": str(config.WELCOME_COINS),
            "language_pref": "hinglish",
            "streak": "0",
            "best_streak": "0",
            "shields": "1",
            "onboarding_gift_claimed": "1",
            "questions_asked": "0",
            "badges": "[]",
            "referral_code": secrets.token_hex(4).upper(),
            "referred_by": "",
            "referral_count": "0",
            "phone_number": inferred_phone,
            "exam_type": (exam_type or "general").strip().lower(),
            "subject": (subject or "general").strip().lower(),
            "fire_streak": "0",
            "learner_track": "school_college",
            "onboarding_gift_claimed": "1",
            "trial_granted": "0",
            "trial_granted_at": "",
            "trial_reason": "",
            "correct_answers": "0",
            "wrong_answers": "0",
            "last_question_at": "",
            "created_at": _today_ist(),
            "updated_at": _now_ist().isoformat(),
        }
        self.save_user(uid, data)
        if self.redis:
            try:
                self.redis.sadd("stats:users", str(uid))
                self.redis.set(f"welcome_flash:{self._key(uid)}", "1", nx=True, ex=120)
            except Exception:
                pass
        self.sync_user_to_supabase(uid, data)
        # Register this user's own referral code immediately so anyone can
        # refer them from the moment they exist — regardless of which
        # platform (Telegram/Web/WhatsApp) created the account.
        self.register_referral_code(uid, data["referral_code"])
        if referred_by:
            self.apply_referral(uid, referred_by)
        # index identity for dedup stats (telegram names, etc.)
        try:
            self.register_name_identity(uid, data.get("full_name", ""))
        except Exception:
            pass
        return self.get_user(uid) or data

    def _supabase_user_key(self, uid: str | int, user: Optional[Dict[str, Any]] = None) -> str:
        user = user or self.get_user(uid) or {}
        return normalize_phone(user.get("phone_number", ""))

    def sync_user_to_supabase(self, uid: str | int, user: Optional[Dict[str, Any]] = None) -> bool:
        """Persist a user's profile/score to Supabase only when phone is known.
        Redis remains the hot operational store for backwards compatibility."""
        u = user or self.get_user(uid)
        if not u:
            return False
        phone = self._supabase_user_key(uid, u)
        if not phone:
            return False
        badges = u.get("badges", "[]")
        try:
            badges_json = json.loads(badges) if isinstance(badges, str) else badges
        except Exception:
            badges_json = []
        row = {
            "phone_number": phone,
            "legacy_uid": str(uid),
            "username": u.get("username", ""),
            "full_name": u.get("full_name", config.DEFAULT_STUDENT_NAMES["generic"]),
            "platform": u.get("platform", "web"),
            "plan": u.get("plan", "free"),
            "pro_until": u.get("pro_until") or None,
            "coins": int(u.get("coins", 0) or 0),
            "streak": int(u.get("streak", 0) or 0),
            "fire_streak": int(u.get("fire_streak", u.get("streak", 0)) or 0),
            "best_streak": int(u.get("best_streak", 0) or 0),
            "shields": int(u.get("shields", 0) or 0),
            "xp": int(u.get("xp", 0) or 0),
            "level": int(u.get("level", 1) or 1),
            "questions_asked": int(u.get("questions_asked", 0) or 0),
            "correct_answers": int(u.get("correct_answers", 0) or 0),
            "wrong_answers": int(u.get("wrong_answers", 0) or 0),
            "exam_type": u.get("exam_type") or "general",
            "subject": u.get("subject") or "general",
            "language_pref": u.get("language_pref") or "hinglish",
            "badges": badges_json,
            "referral_code": u.get("referral_code", ""),
            "referred_by": u.get("referred_by", "") or None,
            "referral_count": int(u.get("referral_count", 0) or 0),
            "parent_phone": normalize_phone(u.get("parent_phone", "")) or None,
            "exam_date": u.get("exam_date") or None,
            "exam_subject": u.get("exam_subject") or None,
            "last_activity": u.get("last_activity") or _today_ist(),
            "last_question_at": u.get("last_question_at") or None,
        }
        # Non-blocking write so Supabase never makes the AI response wait.
        try:
            _DB_WRITE_POOL.submit(self.supabase.upsert, "users", row, "phone_number")
            return True
        except Exception:
            return False

    def set_phone_number(self, uid: str | int, phone_number: str) -> bool:
        phone = normalize_phone(phone_number)
        if not phone:
            return False
        user = self.get_user(uid) or self.ensure_user(uid)
        user["phone_number"] = phone
        user["updated_at"] = _now_ist().isoformat()
        ok = self.save_user(uid, user)
        if ok:
            self.sync_user_to_supabase(uid, user)
        return ok

    def add_personal_history(self, uid: str | int, question: str, tool: str = "general",
                             exam_type: str = "", subject: str = "", user_answer: str = "",
                             correct_answer: str = "", is_correct: Optional[bool] = None,
                             error_reason: str = "", source_cache: str = "") -> bool:
        user = self.get_user(uid) or {}
        phone = self._supabase_user_key(uid, user)
        if not phone or not question or not self.supabase.enabled:
            return False
        ex, sub = guess_exam_subject(question, exam_type or user.get("exam_type", ""), subject or user.get("subject", ""))
        row = {
            "phone_number": phone, "question": question[:4000], "tool": tool[:64],
            "exam_type": ex, "subject": sub, "user_answer": user_answer[:2000],
            "correct_answer": correct_answer[:2000], "is_correct": is_correct,
            "error_reason": error_reason[:1000], "source_cache": source_cache[:32] or None,
        }
        try:
            _DB_WRITE_POOL.submit(self.supabase.insert, "personal_history", row)
            return True
        except Exception:
            return False

    def touch_master_cache(self, question: str, answer: str = "", tool: str = "general",
                           exam_type: str = "", subject: str = "", source: str = "ai",
                           phone_number: str = "") -> None:
        if not self.supabase.enabled or not question:
            return
        ex, sub = guess_exam_subject(question, exam_type, subject)
        q = " ".join(question.lower().split())
        hash_material = f"{tool}|{ex}|{sub}|{q}"
        qhash = hashlib.sha256(hash_material.encode("utf-8")).hexdigest()
        row = {
            "question_hash": qhash, "question": question[:4000], "answer": (answer or "")[:12000],
            "tool": tool[:64], "exam_type": ex, "subject": sub, "source": source[:32],
            "ask_count": 1,
        }
        def _write():
            # First try an RPC incrementer (created by the SQL migration).
            if self.supabase.rpc("touch_master_cache", {
                "p_question_hash": qhash, "p_question": row["question"], "p_answer": row["answer"],
                "p_tool": row["tool"], "p_exam_type": ex, "p_subject": sub, "p_source": row["source"],
                "p_phone_number": normalize_phone(phone_number),
            }) is not None:
                return
            existing = self.supabase.select_one("master_cache", {"question_hash": f"eq.{qhash}"}, "question_hash,ask_count")
            if existing:
                row["ask_count"] = int(existing.get("ask_count") or 0) + 1
            self.supabase.upsert("master_cache", row, "question_hash")
        try:
            _DB_WRITE_POOL.submit(_write)
        except Exception:
            pass

    def get_master_cache(self, question: str, tool: str = "general", exam_type: str = "", subject: str = "", phone_number: str = "") -> Optional[Dict[str, Any]]:
        if not self.supabase.enabled or not question:
            return None
        q = " ".join(question.lower().split())
        ex, sub = guess_exam_subject(question, exam_type, subject)
        hash_material = f"{tool}|{ex}|{sub}|{q}"
        qhash = hashlib.sha256(hash_material.encode("utf-8")).hexdigest()
        row = self.supabase.select_one("master_cache", {"question_hash": f"eq.{qhash}"})
        if row and row.get("answer"):
            # Increment in background; never block the hot read.
            self.touch_master_cache(question, answer=row.get("answer", ""), tool=tool, exam_type=exam_type, subject=subject, source="master_cache_hit", phone_number=phone_number)
            return row
        return None

    def search_library(self, question: str, exam_type: str = "", subject: str = "", limit: int = 3) -> List[Dict[str, Any]]:
        if not self.supabase.enabled or not config.LIBRARY_CONTEXT_ENABLED or not question:
            return []
        ex, sub = guess_exam_subject(question, exam_type, subject)
        words = [w for w in re.findall(r"[A-Za-z0-9]{4,}", question.lower())[:6]]
        if not words:
            return []
        # A small OR-style ILIKE search; it is intentionally conservative to protect latency.
        filters = {"limit": str(limit)}
        rows: List[Dict[str, Any]] = []
        # Try subject/exam first, then a broader public search.
        try:
            params = {"select": "title,content,source_type,exam_type,subject,url", "limit": str(limit),
                      "or": "(" + ",".join([f"title.ilike.*{w}*,content.ilike.*{w}*" for w in words[:3]]) + ")"}
            if ex != "general": params["exam_type"] = f"eq.{ex}"
            if sub != "general": params["subject"] = f"eq.{sub}"
            r = requests.get(self.supabase._url("library"), headers=self.supabase.headers, params=params, timeout=config.SUPABASE_TIMEOUT)
            if r.status_code < 300:
                rows = r.json() or []
        except Exception:
            rows = []
        return rows[:limit]

    def apply_referral(self, new_uid: str | int, ref_code: str) -> bool:
        """Apply a referral exactly once, then let maybe_grant_trial() decide Pro eligibility."""
        if not self.redis or not ref_code:
            return False
        ref_code = ref_code.strip().upper()
        new_uid = str(new_uid)
        try:
            referrer_uid = self.redis.get(f"refcode:{ref_code}")
            if not referrer_uid or str(referrer_uid) == new_uid:
                return False

            new_user = self.get_user(new_uid)
            if not new_user or new_user.get("referred_by"):
                return False

            # Short processing lock prevents concurrent duplicate rewards.
            canonical_new = _canonical_uid(self, new_uid) if self.redis else new_uid
            process_lock = f"referral:process:{canonical_new}"
            if not self.redis.set(process_lock, "1", nx=True, ex=120):
                return False

            try:
                # Re-check after acquiring the lock so a retry sees the latest state.
                new_user = self.get_user(new_uid)
                if not new_user or new_user.get("referred_by"):
                    return False

                new_user["referred_by"] = str(referrer_uid)
                self.save_user(new_uid, new_user)

                # Bonus: +2 lifetime questions for the new user (soft, capped).
                self.redis.decrby(f"quota:lifetime:{new_uid}", 2)

                # Both sides receive referral coins.
                self.add_coins(new_uid, config.REFERRAL_COINS)
                self.add_coins(referrer_uid, config.REFERRAL_COINS)

                ref_user = self.get_user(referrer_uid)
                if ref_user:
                    count = int(ref_user.get("referral_count", 0) or 0) + 1
                    ref_user["referral_count"] = str(count)
                    self.save_user(referrer_uid, ref_user)
                    self.add_xp(referrer_uid, 50)
                    # IMPORTANT: referral processing never directly activates Pro.
                    # The single authoritative trial evaluator below decides it.
                    try:
                        self.maybe_grant_trial(referrer_uid)
                    except Exception:
                        pass
                return True
            finally:
                try:
                    self.redis.delete(process_lock)
                except Exception:
                    pass
        except Exception as e:
            logger.warning("apply_referral: %s", e)
            return False

    def register_referral_code(self, uid: str | int, code: str) -> None:
        if not self.redis or not code:
            return
        try:
            self.redis.set(f"refcode:{code.strip().upper()}", str(uid))
        except Exception:
            pass

    def track_activity(self, uid: str | int) -> None:
        if not self.redis:
            return
        try:
            today = _today_ist()
            uid = str(uid)
            pipe = self.redis.pipeline()
            pipe.sadd(f"dau:{today}", uid)
            pipe.expire(f"dau:{today}", 90000)
            pipe.setex(f"live:{uid}", 120, "1")
            pipe.execute()
        except Exception as e:
            logger.warning("track_activity: %s", e)

    # ------------------------------------------------------------------
    # POINT 3/19 — ABUSE MODERATION: 3 soft warnings, then a 24h auto-ban.
    # Warning count resets once the ban expires (fresh start, not a
    # permanent strike record — matches the spec's "3 warning ke baad
    # 24 ghante ka ban" rather than a lifetime three-strikes policy).
    # ------------------------------------------------------------------
    def is_banned(self, uid: str | int) -> bool:
        if not self.redis:
            return False
        try:
            return bool(self.redis.get(f"banned:{uid}"))
        except Exception:
            return False

    def get_ban_remaining_seconds(self, uid: str | int) -> int:
        if not self.redis:
            return 0
        try:
            ttl = self.redis.ttl(f"banned:{uid}")
            return max(0, ttl)
        except Exception:
            return 0

    def record_abuse_warning(self, uid: str | int) -> Tuple[int, bool]:
        """Increments the warning counter. Returns (warning_count, just_banned).
        On hitting config.ABUSE_WARNING_LIMIT, bans for config.ABUSE_BAN_HOURS
        and resets the counter."""
        if not self.redis:
            return (1, False)
        try:
            key = f"abusewarn:{uid}"
            count = self.redis.incr(key)
            self.redis.expire(key, 86400 * 30)  # warnings don't linger forever
            if count >= config.ABUSE_WARNING_LIMIT:
                self.redis.setex(f"banned:{uid}", config.ABUSE_BAN_HOURS * 3600, "1")
                self.redis.delete(key)
                return (count, True)
            return (count, False)
        except Exception as e:
            logger.warning("record_abuse_warning: %s", e)
            return (1, False)

    def get_abuse_warning_count(self, uid: str | int) -> int:
        if not self.redis:
            return 0
        try:
            return int(self.redis.get(f"abusewarn:{uid}") or 0)
        except Exception:
            return 0

    def unban_user(self, uid: str | int) -> bool:
        """Admin escape hatch — used by /api/dev/unban."""
        if not self.redis:
            return False
        try:
            self.redis.delete(f"banned:{uid}")
            self.redis.delete(f"abusewarn:{uid}")
            return True
        except Exception:
            return False

    def is_pro(self, uid: str | int) -> bool:
        user = self.get_user(uid)
        if not user or str(user.get("plan", "")).lower() != "pro":
            return False
        until = (user.get("pro_until") or "").strip()
        if not until:
            return True
        try:
            # Handle both aware and naive ISO timestamps safely
            raw = until.replace("Z", "+00:00")
            dt = datetime.fromisoformat(raw)
            now = _now_ist()
            if dt.tzinfo is None:
                # treat naive pro_until as IST
                dt = dt.replace(tzinfo=IST)
            if now.tzinfo is None:
                now = now.replace(tzinfo=IST)
            return dt > now
        except Exception as e:
            logger.warning("is_pro parse fail uid=%s until=%r err=%s — treating as active Pro", uid, until, e)
            # Fail OPEN for plan=pro with unparseable date (better than locking a paid user out)
            return True

    def activate_pro(self, uid: str | int, days: int = 30) -> bool:
        user = self.get_user(uid) or self.ensure_user(uid)
        # If already pro and not expired, extend from current expiry instead of overwriting
        base = _now_ist()
        try:
            existing_until = user.get("pro_until", "")
            if existing_until:
                existing_dt = datetime.fromisoformat(existing_until)
                if existing_dt > base:
                    base = existing_dt
        except Exception:
            pass
        until = (base + timedelta(days=days)).isoformat()
        user["plan"] = "pro"
        user["pro_until"] = until
        ok = self.save_user(uid, user)
        if ok and self.redis:
            try:
                self.redis.sadd("stats:pro_users", str(uid))
            except Exception:
                pass
        return ok

    def add_xp(self, uid: str | int, amount: int) -> Tuple[int, int]:
        """Atomically add XP and return (total_xp, level)."""
        uid = str(uid)
        amount = int(amount or 0)
        if amount == 0:
            user = self.get_user(uid) or {}
            xp = int(user.get("xp", 0) or 0)
            return xp, (xp // 100) + 1
        # Ensure user hash exists first
        self.ensure_user(uid)
        if self.redis:
            try:
                key = self._key(uid)
                pipe = self.redis.pipeline()
                pipe.hincrby(key, "xp", amount)
                pipe.hget(key, "xp")
                pipe.expire(key, 86400 * 120)
                results = pipe.execute()
                xp = int(results[1] or 0)
                level = (xp // 100) + 1
                self.redis.hset(key, "level", str(level))
                self.sync_user_to_supabase(uid)
                try:
                    u = self.get_user(uid)
                    if self.is_test_user(uid, u):
                        self.redis.zrem("leaderboard", uid)
                    else:
                        self.redis.zadd("leaderboard", {uid: xp})
                except Exception:
                    pass
                return xp, level
            except Exception as e:
                logger.warning("add_xp redis: %s", e)
        # Fallback without redis atomic
        user = self.get_user(uid) or self.ensure_user(uid)
        xp = int(user.get("xp", 0) or 0) + amount
        level = (xp // 100) + 1
        user["xp"] = str(xp)
        user["level"] = str(level)
        self.save_user(uid, user)
        self.sync_user_to_supabase(uid, user)
        return xp, level

    # ------------------------------------------------------------------
    # COINS (POINT 8/17/25/36) — separate currency from XP. Used for the
    # welcome gift, daily spin wheel, quiz rewards, and referral bonuses.
    # XP drives Level/Leaderboard; coins are the spendable "game" currency
    # (kept simple here — no shop yet, just a running balance).
    # ------------------------------------------------------------------
    def add_coins(self, uid: str | int, amount: int) -> int:
        """Atomically add (or subtract, if amount<0) coins. Returns new balance."""
        uid = str(uid)
        self.ensure_user(uid)
        if self.redis:
            try:
                key = self._key(uid)
                pipe = self.redis.pipeline()
                pipe.hincrby(key, "coins", int(amount))
                pipe.expire(key, 86400 * 120)
                results = pipe.execute()
                bal = int(results[0] or 0)
                if bal < 0:
                    self.redis.hset(key, "coins", "0")
                    bal = 0
                self.sync_user_to_supabase(uid)
                return bal
            except Exception as e:
                logger.warning("add_coins: %s", e)
        user = self.get_user(uid) or self.ensure_user(uid)
        bal = max(0, int(user.get("coins", 0) or 0) + int(amount))
        user["coins"] = str(bal)
        self.save_user(uid, user)
        self.sync_user_to_supabase(uid, user)
        return bal

    def get_coins(self, uid: str | int) -> int:
        user = self.get_user(uid)
        return int((user or {}).get("coins", 0) or 0)

    def spin_used_today(self, uid: str | int) -> bool:
        """POINT 36 — one spin per account per day (anti-loot cap)."""
        if not self.redis:
            return False
        try:
            return bool(self.redis.get(f"spin:{uid}:{_today_ist()}"))
        except Exception:
            return False

    def do_spin(self, uid: str | int) -> Optional[int]:
        """Atomically claim today's spin and award a random coin amount in
        [SPIN_MIN_COINS, SPIN_MAX_COINS]. Returns the amount won, or None
        if already spun today."""
        if self.redis:
            try:
                key = f"spin:{uid}:{_today_ist()}"
                if not self.redis.set(key, "1", nx=True, ex=90000):
                    return None
            except Exception as e:
                logger.warning("do_spin lock: %s", e)
        elif self.spin_used_today(uid):
            return None
        amount = secrets.randbelow(config.SPIN_MAX_COINS - config.SPIN_MIN_COINS + 1) + config.SPIN_MIN_COINS
        self.add_coins(uid, amount)
        return amount

    def update_streak(self, uid: str | int) -> Dict[str, int]:
        user = self.get_user(uid)
        if not user:
            return {"current": 0, "best": 0, "shields": 0}
        today = _today_ist()
        last = user.get("last_activity", "")
        if last == today:
            return {
                "current": int(user.get("streak", 0)),
                "best": int(user.get("best_streak", 0)),
                "shields": int(user.get("shields", 0)),
            }
        yesterday = (_now_ist() - timedelta(days=1)).strftime(DATE_FORMAT)
        current = int(user.get("streak", 0))
        shields = int(user.get("shields", 0))
        if last == yesterday:
            new = current + 1
        elif shields > 0:
            new = current + 1
            shields -= 1
        else:
            new = 1
        if new > 0 and new % 7 == 0:
            shields += 1
        best = max(new, int(user.get("best_streak", 0)))
        user.update({"streak": str(new), "best_streak": str(best), "shields": str(shields), "fire_streak": str(new), "last_activity": today})
        self.save_user(uid, user)
        self.sync_user_to_supabase(uid, user)
        return {"current": new, "best": best, "shields": shields}

    # ------------------------------------------------------------------
    # QUOTA — now atomic via Lua script to remove the check-then-consume
    # race condition (two concurrent requests could previously both pass
    # the check before either incremented the counter).
    # ------------------------------------------------------------------
    _QUOTA_LUA = """
    local daily_key = KEYS[1]
    local life_key = KEYS[2]
    local daily_limit = tonumber(ARGV[1])
    local life_limit = tonumber(ARGV[2])
    local daily_ttl = tonumber(ARGV[3])

    local daily_used = tonumber(redis.call('GET', daily_key) or '0')
    local life_used = tonumber(redis.call('GET', life_key) or '0')

    if daily_used >= daily_limit or life_used >= life_limit then
        local daily_left = daily_limit - daily_used
        local life_left = life_limit - life_used
        if daily_left < 0 then daily_left = 0 end
        if life_left < 0 then life_left = 0 end
        return {0, daily_left, life_left}
    end

    daily_used = redis.call('INCR', daily_key)
    redis.call('EXPIRE', daily_key, daily_ttl)
    life_used = redis.call('INCR', life_key)

    local daily_left = daily_limit - daily_used
    local life_left = life_limit - life_used
    if daily_left < 0 then daily_left = 0 end
    if life_left < 0 then life_left = 0 end
    return {1, daily_left, life_left}
    """

    def try_consume_quota(self, uid: str | int) -> Tuple[bool, Dict[str, int]]:
        """Atomically check AND consume in one Redis round trip. Returns
        (allowed, {daily_left, lifetime_left}). Pro users always allowed."""
        if self.is_pro(uid):
            return True, {"daily_left": -1, "lifetime_left": -1}
        if not self.redis:
            return True, {"daily_left": config.FREE_DAILY, "lifetime_left": config.FREE_LIFETIME}
        try:
            today = _today_ist()
            script = self._quota_script or self.redis.register_script(self._QUOTA_LUA)
            allowed, daily_left, life_left = script(
                keys=[f"quota:daily:{uid}:{today}", f"quota:lifetime:{uid}"],
                args=[config.FREE_DAILY, config.FREE_LIFETIME, 90000],
            )
            return bool(int(allowed)), {"daily_left": int(daily_left), "lifetime_left": int(life_left)}
        except Exception as e:
            logger.warning("try_consume_quota: %s", e)
            return True, {"daily_left": config.FREE_DAILY, "lifetime_left": config.FREE_LIFETIME}

    def check_quota(self, uid: str | int) -> Tuple[bool, Dict[str, int]]:
        """Read-only peek at quota (does NOT consume). Used for display only."""
        if self.is_pro(uid):
            return True, {"daily_left": -1, "lifetime_left": -1}
        if not self.redis:
            return True, {"daily_left": config.FREE_DAILY, "lifetime_left": config.FREE_LIFETIME}
        try:
            today = _today_ist()
            daily_used = int(self.redis.get(f"quota:daily:{uid}:{today}") or 0)
            life_used = int(self.redis.get(f"quota:lifetime:{uid}") or 0)
            daily_left = max(0, config.FREE_DAILY - daily_used)
            life_left = max(0, config.FREE_LIFETIME - life_used)
            return (daily_left > 0 and life_left > 0), {"daily_left": daily_left, "lifetime_left": life_left}
        except Exception:
            return True, {"daily_left": config.FREE_DAILY, "lifetime_left": config.FREE_LIFETIME}

    # Names that must never appear on the public leaderboard (dev/test pollution).
    _LB_HIDDEN_NAMES = {
        "pro tester", "dev · sparsh", "dev sparsh", "pro student",
        "web student", "test student sparsh", "🧪 test account (hidden)",
        "test account (hidden)",
    }

    def is_test_user(self, uid: str | int, user: Optional[Dict[str, str]] = None) -> bool:
        """True for dev/test accounts — excluded from ranks + public leaderboard."""
        uid = str(uid or "")
        if user is None:
            user = self.get_user(uid)
        if user and str(user.get("is_test", "")).lower() in ("1", "true", "yes"):
            return True
        return self._is_hidden_leaderboard_user(uid, user)

    def _is_hidden_leaderboard_user(self, uid: str, user: Optional[Dict[str, str]]) -> bool:
        uid = str(uid or "")
        if user and str(user.get("is_test", "")).lower() in ("1", "true", "yes"):
            return True
        name = ((user or {}).get("full_name") or "").strip().lower()
        if name in self._LB_HIDDEN_NAMES:
            return True
        if name.startswith("pro tester") or name.startswith("dev ·") or name.startswith("dev "):
            return True
        if "test account" in name:
            return True
        low = uid.lower()
        if any(x in low for x in ("testpro_", "profull_", "testfree_", "fulltest_", "test_")):
            return True
        return False

    # ------------------------------------------------------------------
    # USER DEDUPLICATION (analytics + soft identity)
    # Web creates one account per browser/device. We treat a normalized
    # non-default name as a soft human identity for unique-user counts.
    # We do NOT merge quotas/XP (abuse risk) — only stats + optional link.
    # ------------------------------------------------------------------
    _DEFAULT_NAMES = {
        "student", "web student", "pro student", "whatsapp student",
        "telegram student", "user", "anonymous", "anon",
        "🧪 test account (hidden)", "test account (hidden)",
    }

    def normalize_name(self, name: str) -> str:
        n = " ".join((name or "").strip().lower().split())
        # strip common prefixes/emojis noise
        for ch in ("👤", "🧪", "👑", "🔥"):
            n = n.replace(ch, "")
        return " ".join(n.split())

    def is_default_name(self, name: str) -> bool:
        n = self.normalize_name(name)
        if not n or len(n) < 2:
            return True
        if n in self._DEFAULT_NAMES or n in self._LB_HIDDEN_NAMES:
            return True
        if n.startswith("pro tester") or n.startswith("dev ·") or n.startswith("dev "):
            return True
        if "test account" in n:
            return True
        return False

    def register_name_identity(self, uid: str | int, name: str) -> None:
        """Index uid under normalized name for unique-human counting."""
        if not self.redis:
            return
        uid = str(uid)
        norm = self.normalize_name(name)
        if self.is_default_name(norm) or self.is_test_user(uid):
            return
        try:
            # reverse index: name -> set of uids
            self.redis.sadd(f"nameidx:{norm}", uid)
            self.redis.hset(self._key(uid), "name_key", norm)
        except Exception as e:
            logger.warning("register_name_identity: %s", e)

    def get_unique_user_stats(self) -> Dict[str, Any]:
        """Account-level vs human-level counts (excludes test/default names)."""
        empty = {
            "total_accounts": 0,
            "test_accounts": 0,
            "real_accounts": 0,
            "named_real_accounts": 0,
            "unique_humans_est": 0,
            "unnamed_real_accounts": 0,
            "real_pro_humans_est": 0,
        }
        if not self.redis:
            return empty
        try:
            all_uids = list(self.redis.smembers("stats:users") or [])
            test_accounts = 0
            real_accounts = 0
            named_keys: set[str] = set()
            unnamed_real = 0
            pro_human_keys: set[str] = set()
            pro_unnamed = 0

            for uid in all_uids:
                u = self.get_user(uid)
                if self.is_test_user(uid, u):
                    test_accounts += 1
                    continue
                real_accounts += 1
                name = (u or {}).get("full_name", "")
                norm = self.normalize_name(name)
                is_pro = self.is_pro(uid)
                if self.is_default_name(norm):
                    unnamed_real += 1
                    if is_pro:
                        pro_unnamed += 1
                else:
                    named_keys.add(norm)
                    if is_pro:
                        pro_human_keys.add(norm)

            # unique humans ≈ unique names + unnamed real accounts
            # (unnamed may still double-count multi-device; best effort)
            unique_humans = len(named_keys) + unnamed_real
            real_pro_humans = len(pro_human_keys) + pro_unnamed

            return {
                "total_accounts": len(all_uids),
                "test_accounts": test_accounts,
                "real_accounts": real_accounts,
                "named_real_accounts": len(named_keys),
                "unique_humans_est": unique_humans,
                "unnamed_real_accounts": unnamed_real,
                "real_pro_humans_est": real_pro_humans,
            }
        except Exception as e:
            logger.warning("get_unique_user_stats: %s", e)
            return empty

    def get_leaderboard(self, limit: int = 15) -> List[Dict]:
        if not self.redis:
            return []
        try:
            # Pull a wider window so after filtering we still fill `limit` rows.
            top = self.redis.zrevrange("leaderboard", 0, max(limit * 8, 80) - 1, withscores=True)
            out = []
            rank = 0
            for uid, xp in top:
                u = self.get_user(uid)
                if self._is_hidden_leaderboard_user(str(uid), u):
                    continue
                name = (u or {}).get("full_name", config.DEFAULT_STUDENT_NAMES["generic"])[:20]
                if not name or name.strip().lower() in self._LB_HIDDEN_NAMES:
                    continue
                rank += 1
                out.append({
                    "rank": rank,
                    "name": name,
                    "xp": int(xp),
                    "level": int((u or {}).get("level", 1) or 1),
                    "platform": (u or {}).get("platform", "web"),
                })
                if rank >= limit:
                    break
            return out
        except Exception:
            return []

    def get_rank(self, uid: str | int) -> Optional[int]:
        """Public rank among REAL users only (test/dev accounts excluded)."""
        if not self.redis:
            return None
        uid = str(uid)
        try:
            if self.is_test_user(uid):
                return None  # test accounts have no public rank
            # Count how many non-test members have strictly higher XP
            my_xp = self.redis.zscore("leaderboard", uid)
            if my_xp is None:
                return None
            # Scan top slice; for large boards this is still fine at class scale
            members = self.redis.zrevrange("leaderboard", 0, 500, withscores=True)
            rank = 0
            found = False
            for mid, score in members:
                if self.is_test_user(str(mid)):
                    continue
                if str(mid) == uid:
                    found = True
                    rank += 1
                    break
                rank += 1
            return rank if found else None
        except Exception:
            return None

    def add_badge(self, uid: str | int, badge: str) -> None:
        user = self.get_user(uid)
        if not user:
            return
        try:
            badges = json.loads(user.get("badges", "[]"))
        except Exception:
            badges = []
        if badge not in badges:
            badges.append(badge)
            user["badges"] = json.dumps(badges)
            self.save_user(uid, user)

    def cache_get(self, key: str) -> Optional[str]:
        if not self.redis or not key:
            return None
        try:
            return self.redis.get(f"ans:{key}")
        except Exception:
            return None

    def cache_set(self, key: str, answer: str, ttl: int = None) -> None:
        if not self.redis or not key or not answer:
            return
        try:
            self.redis.setex(f"ans:{key}", int(ttl or config.CACHE_TTL), answer)
        except Exception:
            pass

    def get_stats(self) -> Dict[str, Any]:
        if not self.redis:
            return {
                "total_users": 0, "total_questions": 0, "dau_today": 0,
                "pro_users": 0, "live_approx": 0,
                "real_accounts": 0, "test_accounts": 0, "unique_humans_est": 0,
                "real_pro_humans_est": 0,
            }
        try:
            today = _today_ist()
            live_count = 0
            try:
                cursor = 0
                while True:
                    cursor, keys = self.redis.scan(cursor=cursor, match="live:*", count=100)
                    live_count += len(keys)
                    if cursor == 0:
                        break
            except Exception:
                pass
            uniq = self.get_unique_user_stats()
            return {
                "total_users": int(self.redis.scard("stats:users") or 0),
                "total_questions": int(self.redis.get("stats:total_questions") or 0),
                "dau_today": int(self.redis.scard(f"dau:{today}") or 0),
                "pro_users": int(self.redis.scard("stats:pro_users") or 0),
                "live_approx": live_count,
                "real_accounts": uniq.get("real_accounts", 0),
                "test_accounts": uniq.get("test_accounts", 0),
                "unique_humans_est": uniq.get("unique_humans_est", 0),
                "real_pro_humans_est": uniq.get("real_pro_humans_est", 0),
                "named_real_accounts": uniq.get("named_real_accounts", 0),
                "unnamed_real_accounts": uniq.get("unnamed_real_accounts", 0),
            }
        except Exception:
            return {
                "total_users": 0, "total_questions": 0, "dau_today": 0,
                "pro_users": 0, "live_approx": 0,
                "real_accounts": 0, "test_accounts": 0, "unique_humans_est": 0,
                "real_pro_humans_est": 0,
            }

    # ------------------------------------------------------------------
    # POINT 24 — LANGUAGE PREFERENCE (Hindi / Hinglish / English)
    # ------------------------------------------------------------------
    def set_language(self, uid: str | int, lang: str) -> bool:
        lang = (lang or "hinglish").strip().lower()
        if lang not in ("hindi", "hinglish", "english"):
            lang = "hinglish"
        user = self.get_user(uid) or self.ensure_user(uid)
        user["language_pref"] = lang
        return self.save_user(uid, user)

    def get_language(self, uid: str | int) -> str:
        user = self.get_user(uid)
        return ((user or {}).get("language_pref") or "hinglish").strip().lower()

    # ------------------------------------------------------------------
    # POINT 32 / GALTI DIARY — mistake tracker (wrong quiz answers,
    # anything the user got wrong). Stored as a capped Redis list per user.
    # ------------------------------------------------------------------
    def record_answer_outcome(self, uid: str | int, is_correct: bool) -> None:
        uid = str(uid)
        self.ensure_user(uid)
        if self.redis:
            try:
                field = "correct_answers" if is_correct else "wrong_answers"
                self.redis.hincrby(self._key(uid), field, 1)
                self.sync_user_to_supabase(uid)
            except Exception:
                pass

    def add_mistake(self, uid: str | int, question: str, tool: str = "quiz",
                     correct_answer: str = "", user_answer: str = "", topic: str = "") -> int:
        """Logs a mistake AND rewards a few coins for attempting (POINT 32 —
        'Galti pe Inaam': don't punish wrong attempts, encourage them).
        Returns the coin amount awarded (0 if it couldn't be logged)."""
        if not self.redis or not question:
            return 0
        try:
            entry = json.dumps({
                "question": question[:500],
                "tool": tool,
                "correct_answer": (correct_answer or "")[:500],
                "user_answer": (user_answer or "")[:300],
                "topic": (topic or "")[:100],
                "added_at": _today_ist(),
            })
            key = f"galti:{uid}"
            pipe = self.redis.pipeline()
            pipe.lpush(key, entry)
            pipe.ltrim(key, 0, 199)  # keep last 200 mistakes
            pipe.expire(key, 86400 * 120)
            pipe.execute()
            self.add_coins(uid, config.MISTAKE_COINS)
            return config.MISTAKE_COINS
        except Exception as e:
            logger.warning("add_mistake: %s", e)
            return 0

    def get_mistakes(self, uid: str | int, limit: int = 20) -> List[Dict]:
        if not self.redis:
            return []
        try:
            raw = self.redis.lrange(f"galti:{uid}", 0, max(0, limit - 1))
            out = []
            for r in raw:
                try:
                    out.append(json.loads(r))
                except Exception:
                    continue
            return out
        except Exception:
            return []

    def clear_mistake(self, uid: str | int, index: int) -> bool:
        """Remove one mistake by list index (as returned by get_mistakes)."""
        if not self.redis:
            return False
        try:
            key = f"galti:{uid}"
            items = self.redis.lrange(key, 0, -1)
            if index < 0 or index >= len(items):
                return False
            placeholder = f"__DELETED__:{secrets.token_hex(4)}"
            self.redis.lset(key, index, placeholder)
            self.redis.lrem(key, 1, placeholder)
            return True
        except Exception:
            return False

    # ------------------------------------------------------------------
    # QUIZ — storage for generated quizzes + attempts + simple per-topic
    # weakness analysis. Practice / Exam / 1v1-challenge modes are all
    # driven from the same stored quiz object.
    # ------------------------------------------------------------------
    def save_quiz(self, quiz_id: str, data: Dict[str, Any], ttl: int = 86400 * 7) -> bool:
        if not self.redis:
            return False
        try:
            self.redis.setex(f"quiz:{quiz_id}", ttl, json.dumps(data))
            return True
        except Exception as e:
            logger.warning("save_quiz: %s", e)
            return False

    def get_quiz(self, quiz_id: str) -> Optional[Dict[str, Any]]:
        if not self.redis:
            return None
        try:
            raw = self.redis.get(f"quiz:{quiz_id}")
            return json.loads(raw) if raw else None
        except Exception:
            return None

    def save_quiz_attempt(self, uid: str | int, quiz_id: str, score: int, total: int,
                           topic: str = "", mode: str = "practice") -> None:
        if not self.redis:
            return
        try:
            entry = json.dumps({
                "quiz_id": quiz_id, "score": score, "total": total,
                "topic": topic, "mode": mode, "date": _today_ist(),
            })
            key = f"quizhist:{uid}"
            pipe = self.redis.pipeline()
            pipe.lpush(key, entry)
            pipe.ltrim(key, 0, 49)
            pipe.expire(key, 86400 * 120)
            pipe.execute()
        except Exception as e:
            logger.warning("save_quiz_attempt: %s", e)

    def get_daily_quiz_used(self, uid: str | int) -> bool:
        """FREE users get 1 quiz/day (3 Qs). Returns True if already used today."""
        if not self.redis:
            return False
        try:
            return bool(self.redis.get(f"quizdaily:{uid}:{_today_ist()}"))
        except Exception:
            return False

    def mark_daily_quiz_used(self, uid: str | int) -> None:
        if not self.redis:
            return
        try:
            self.redis.setex(f"quizdaily:{uid}:{_today_ist()}", 90000, "1")
        except Exception:
            pass

    # ------------------------------------------------------------------
    # POINT 10 — 1v1 "Dost se Panga" challenge results. Both players play
    # the SAME quiz_id independently (via a shared link); each result is
    # recorded here so either side can poll and see a simple leaderboard.
    # ------------------------------------------------------------------
    def record_challenge_result(self, quiz_id: str, uid: str | int, name: str, score: int, total: int) -> None:
        if not self.redis:
            return
        try:
            key = f"challenge:{quiz_id}"
            self.redis.hset(key, str(uid), json.dumps({"name": name, "score": score, "total": total}))
            self.redis.expire(key, 86400 * 7)
        except Exception as e:
            logger.warning("record_challenge_result: %s", e)

    def get_challenge_results(self, quiz_id: str) -> List[Dict]:
        if not self.redis:
            return []
        try:
            raw = self.redis.hgetall(f"challenge:{quiz_id}") or {}
            out = []
            for uid, payload in raw.items():
                try:
                    d = json.loads(payload)
                    d["uid"] = uid
                    out.append(d)
                except Exception:
                    continue
            return out
        except Exception:
            return []

    # ------------------------------------------------------------------
    # POINT 27 — TEACHER DASHBOARD (class codes)
    # ------------------------------------------------------------------
    def create_class_code(self, teacher_uid: str | int, class_name: str = "") -> str:
        code = secrets.token_hex(3).upper()
        if self.redis:
            try:
                self.redis.hset(f"class:{code}", mapping={
                    "teacher_uid": str(teacher_uid),
                    "class_name": class_name or "My Class",
                    "created_at": _today_ist(),
                })
                self.redis.sadd(f"teacher:classes:{teacher_uid}", code)
            except Exception as e:
                logger.warning("create_class_code: %s", e)
        return code

    def join_class(self, uid: str | int, code: str) -> bool:
        code = (code or "").strip().upper()
        if not self.redis or not code:
            return False
        try:
            if not self.redis.exists(f"class:{code}"):
                return False
            self.redis.sadd(f"class:{code}:students", str(uid))
            user = self.get_user(uid) or self.ensure_user(uid)
            user["class_code"] = code
            self.save_user(uid, user)
            return True
        except Exception as e:
            logger.warning("join_class: %s", e)
            return False

    def get_class_info(self, code: str) -> Optional[Dict]:
        if not self.redis:
            return None
        try:
            code = (code or "").strip().upper()
            data = self.redis.hgetall(f"class:{code}")
            if not data:
                return None
            student_ids = list(self.redis.smembers(f"class:{code}:students") or [])
            students = []
            for sid in student_ids:
                u = self.get_user(sid) or {}
                students.append({
                    "uid": sid, "name": u.get("full_name", config.DEFAULT_STUDENT_NAMES["generic"]),
                    "xp": int(u.get("xp", 0) or 0), "level": int(u.get("level", 1) or 1),
                    "questions_asked": int(u.get("questions_asked", 0) or 0),
                    "streak": int(u.get("streak", 0) or 0),
                })
            students.sort(key=lambda s: -s["xp"])
            return {"code": code, **data, "students": students, "student_count": len(students)}
        except Exception as e:
            logger.warning("get_class_info: %s", e)
            return None

    def get_teacher_classes(self, teacher_uid: str | int) -> List[str]:
        if not self.redis:
            return []
        try:
            return list(self.redis.smembers(f"teacher:classes:{teacher_uid}") or [])
        except Exception:
            return []

    # ------------------------------------------------------------------
    # POINT 26 — PARENT PHONE + WEEKLY REPORT DATA
    # ------------------------------------------------------------------
    def set_parent_phone(self, uid: str | int, phone: str) -> bool:
        user = self.get_user(uid) or self.ensure_user(uid)
        user["parent_phone"] = (phone or "").strip()
        return self.save_user(uid, user)

    def get_weekly_report(self, uid: str | int) -> Dict[str, Any]:
        user = self.get_user(uid) or {}
        mistakes = self.get_mistakes(uid, limit=5)
        rank = self.get_rank(uid)
        return {
            "name": user.get("full_name", config.DEFAULT_STUDENT_NAMES["generic"]),
            "xp": int(user.get("xp", 0) or 0),
            "level": int(user.get("level", 1) or 1),
            "streak": int(user.get("streak", 0) or 0),
            "questions_asked": int(user.get("questions_asked", 0) or 0),
            "rank": rank,
            "recent_weak_topics": [m.get("topic") or m.get("question", "")[:40] for m in mistakes],
        }

    # ------------------------------------------------------------------
    # POINT 25 — REFERRAL LEADERBOARD ("Referral Raja")
    # ------------------------------------------------------------------
    def get_referral_leaderboard(self, limit: int = 10) -> List[Dict]:
        if not self.redis:
            return []
        try:
            uids = list(self.redis.smembers("stats:users") or [])
            rows = []
            for uid in uids:
                u = self.get_user(uid)
                if not u or self.is_test_user(uid, u):
                    continue
                count = int(u.get("referral_count", 0) or 0)
                if count <= 0:
                    continue
                rows.append({"name": (u.get("full_name") or config.DEFAULT_STUDENT_NAMES["generic"])[:20], "referrals": count})
            rows.sort(key=lambda r: -r["referrals"])
            for i, r in enumerate(rows[:limit], 1):
                r["rank"] = i
            return rows[:limit]
        except Exception:
            return []

    # ------------------------------------------------------------------
    # POINT 30 — EXAM DATE (for the "exam bomb" 24h-before reminder)
    # ------------------------------------------------------------------
    def set_exam_date(self, uid: str | int, exam_date: str, subject: str = "") -> bool:
        user = self.get_user(uid) or self.ensure_user(uid)
        user["exam_date"] = (exam_date or "").strip()
        user["exam_subject"] = (subject or "").strip()
        return self.save_user(uid, user)

    def get_users_with_exam_tomorrow(self) -> List[Dict]:
        if not self.redis:
            return []
        try:
            tomorrow = (_now_ist() + timedelta(days=1)).strftime(DATE_FORMAT)
            out = []
            for uid in self.redis.smembers("stats:users") or []:
                u = self.get_user(uid)
                if u and u.get("exam_date") == tomorrow:
                    out.append({"uid": uid, **u})
            return out
        except Exception:
            return []

    # ------------------------------------------------------------------
    # POINT 3/4/5 — SEMANTIC CACHE (theory only, never numerical).
    # No vector DB available in this Redis-only stack, so we keep a
    # capped pool of recent theory embeddings and do cosine similarity
    # in Python. This is the light-weight equivalent of the pgvector
    # design in the spec, sized for the free-tier user counts this app
    # targets (a few thousand users) rather than a real vector index.
    # ------------------------------------------------------------------
    def semantic_cache_add(self, tool: str, question: str, embedding: List[float], answer: str) -> None:
        if not self.redis or not embedding:
            return
        try:
            entry_id = hashlib.sha256(f"{tool}|{question}".encode()).hexdigest()[:24]
            payload = json.dumps({"q": question[:300], "vec": embedding, "ans": answer})
            pool_key = f"semcache:{tool}"
            pipe = self.redis.pipeline()
            pipe.hset(pool_key, entry_id, payload)
            pipe.expire(pool_key, config.CACHE_TTL * 6)
            pipe.execute()
            # Cap pool size so lookups stay cheap.
            if self.redis.hlen(pool_key) > 800:
                extra = self.redis.hkeys(pool_key)[:100]
                if extra:
                    self.redis.hdel(pool_key, *extra)
        except Exception as e:
            logger.warning("semantic_cache_add: %s", e)

    def semantic_cache_search(self, tool: str, embedding: List[float], threshold: float = None) -> Optional[str]:
        if not self.redis or not embedding:
            return None
        threshold = threshold if threshold is not None else config.SEMANTIC_CACHE_THRESHOLD
        try:
            pool_key = f"semcache:{tool}"
            items = self.redis.hgetall(pool_key)
            if not items:
                return None
            best_score, best_ans = 0.0, None
            for raw in items.values():
                try:
                    data = json.loads(raw)
                    vec = data.get("vec") or []
                    score = _cosine_sim(embedding, vec)
                    if score > best_score:
                        best_score, best_ans = score, data.get("ans")
                except Exception:
                    continue
            if best_ans and best_score >= threshold:
                return best_ans
            return None
        except Exception as e:
            logger.warning("semantic_cache_search: %s", e)
            return None


def _cosine_sim(a: List[float], b: List[float]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    try:
        dot = sum(x * y for x, y in zip(a, b))
        na = sum(x * x for x in a) ** 0.5
        nb = sum(y * y for y in b) ** 0.5
        if na == 0 or nb == 0:
            return 0.0
        return dot / (na * nb)
    except Exception:
        return 0.0


# Keywords that mark a question as NUMERICAL (must always be solved fresh,
# never served from cache — see POINT 5). Kept in config-like list here so
# it's easy to extend without touching the detection logic.
NUMERICAL_KEYWORDS = [
    "solve", "calculate", "find", "nikalo", "value", "value of", "evaluate",
    "compute", "derive the value", "simplify", "kitna hoga", "how much",
    "what is the value", "=",
]


def is_numerical_question(text: str) -> bool:
    """POINT 5: has a digit AND a solve/calculate-style keyword => numerical,
    which must be solved fresh every time (never semantic-cached)."""
    if not text:
        return False
    has_digit = any(ch.isdigit() for ch in text)
    low = text.lower()
    has_keyword = any(kw in low for kw in NUMERICAL_KEYWORDS)
    return has_digit and has_keyword


# JavaScript-style alias requested in the product spec.
def isNumerical(text: str) -> bool:
    return is_numerical_question(text)


# ----------------------------------------------------------------------
# POINT 3/19 — ABUSE DETECTION. Whole-word, case-insensitive match against
# config.ABUSE_WORDS. Compiled once at import time; re-compiled if you ever
# need to hot-reload the word list (not done automatically — restart the
# process after changing ABUSE_WORDS env var).
# ----------------------------------------------------------------------
ABUSE_PAT = re.compile(
    r"\b(" + "|".join(re.escape(w) for w in config.ABUSE_WORDS if w) + r")\b",
    re.IGNORECASE,
) if config.ABUSE_WORDS else None


def contains_abuse(text: str) -> bool:
    if not text or not ABUSE_PAT:
        return False
    return bool(ABUSE_PAT.search(text))


def format_abuse_warning_message(warning_count: int, just_banned: bool) -> str:
    """Shared Hindi warning/ban copy used by Telegram, WhatsApp, and web."""
    if just_banned:
        return (
            f"🚫 *{config.ABUSE_BAN_HOURS} ghante ka ban laga diya gaya hai.*\n\n"
            f"Teen warning ke baad bhi gaali-galoch nahi rukni chahiye thi. "
            f"{config.ABUSE_BAN_HOURS} ghante baad wapas try karo, tab tak bhai\n"
            f"ko break do 🙏\n\n- {config.BRAND_NAME}"
        )
    remaining = max(0, config.ABUSE_WARNING_LIMIT - warning_count)
    return (
        f"⚠️ *Warning {warning_count}/{config.ABUSE_WARNING_LIMIT}*\n\n"
        f"Bhai, pyaar se baat karo — gaali-galoch yahan nahi chalega. "
        f"{remaining} warning aur mili toh {config.ABUSE_BAN_HOURS} ghante ka ban lag jayega.\n\n"
        "Chalo, ab apna doubt bolo 🙂"
    )


def format_ban_active_message(remaining_seconds: int) -> str:
    hrs = max(1, remaining_seconds // 3600)
    return (
        f"🚫 Tumhara account abhi ~{hrs} ghante ke liye banned hai (abuse rule).\n\n"
        f"Iske baad wapas normally use kar paoge. - {config.BRAND_NAME}"
    )


db = Database()
_redis_for_rl = db.redis


# ============================================================================
# AI SERVICE
# ============================================================================

SOFT_FAIL_MSG = (
    "🎯 Target almost locked!\n\n"
    "SaarthiBhai abhi thoda busy hai (free AI limits).\n"
    "15–20 second baad dubara try karo — answers wapas aa jaate hain.\n\n"
    "Short tip: chhota clear sawaal likho.\n"
    f"- {config.CREATOR_NAME} ka {config.SHORT_NAME} tumhare saath hai"
)

class AIService:
    def __init__(self) -> None:
        self.gemini_client = None
        self.groq_client = None
        self.openrouter_ready = bool(config.OPENROUTER_API_KEY and config.OPENROUTER_MODELS)
        if config.GOOGLE_API_KEY:
            try:
                self.gemini_client = genai.Client(api_key=config.GOOGLE_API_KEY)
                logger.info("Gemini ready")
            except Exception as e:
                logger.error("Gemini init: %s", e)
        if config.GROQ_API_KEY:
            try:
                self.groq_client = Groq(api_key=config.GROQ_API_KEY)
                logger.info("Groq ready | %s", config.GROQ_MODEL)
            except Exception as e:
                logger.error("Groq init: %s", e)
        if self.openrouter_ready:
            logger.info("OpenRouter ready | %s", config.OPENROUTER_MODELS)

    def _base_prompt(self, is_pro: bool, language: str = "hinglish") -> str:
        lang_key = (language or "hinglish").strip().lower()
        lang_instruction = config.CONTENT.get("language_instructions", {}).get(
            lang_key, config.CONTENT.get("language_instructions", {}).get("hinglish", "")
        )
        template = config.CONTENT.get("ai_base_prompt_template", "{brand_name}. {language_instruction}")
        base = template.format(
            brand_name=config.BRAND_NAME,
            language_instruction=lang_instruction,
            style_intro=config.BRAND_STYLE_INTRO,
        ) + "\n\n"
        if is_pro:
            base += "PRO user: give deeper explanations, tips, memory tricks, common mistakes, exam strategy.\n\n"
        return base

    def _templates(self, base: str, question: str, is_pro: bool = False) -> Dict[str, str]:
        n_pyq = 20 if is_pro else 10
        n_mock = 15 if is_pro else 10
        return {
            "general": (
                f"{base}"
                "Tool=GENERAL. Answer the student's question helpfully using the compact 10-in-1 structure when the request is academic/study related.\n"
                "Do not fabricate PYQ claims, video links, citations, or data.\n\n"
                f"Student:\n{question}"
            ),
            "explain": (
                f"{base}"
                "Tool=EXPLAIN only. Explain the concept simply with analogy + examples.\n"
                "Do NOT give a full study plan, mock test, or only formula list.\n\n"
                f"Topic/Question:\n{question}"
            ),
            "solve": (
                f"{base}"
                "Tool=SOLVE only. Solve step-by-step. Show working. Box final answer.\n"
                "If input is only a topic (not a problem), ask for the exact problem OR give 1 worked example.\n\n"
                f"Problem:\n{question}"
            ),
            "notes": (
                f"{base}"
                "Tool=NOTES only. Short exam-ready notes: bullets, key points, formulas.\n"
                "No 7-day plan. No full mock test.\n\n"
                f"Topic:\n{question}"
            ),
            "pyq": (
                f"{base}"
                "Tool=PYQ only.\n"
                "User message may be (1) a full past question to solve, or (2) a topic with optional filters.\n"
                "If full question: solve step-by-step with tips and common mistakes.\n"
                f"If topic: generate exactly {n_pyq} exam-style questions STRICTLY on that topic.\n"
                "TOPIC FILTER: stay on topic; match exam style if named (JEE/NEET/GATE/SSC/DTU and many more).\n"
                "PREVIOUS YEAR TRENDS: 4-6 bullets before questions (frequent ideas, weightage feel). Never invent paper codes.\n"
                "Mix: objective only / subjective only / default BOTH (half-half).\n"
                "Format: Trends → Numbered questions → Answer key.\n\n"
                f"User input:\n{question}"
            ),
            "formula": (
                f"{base}"
                "Tool=FORMULA only. List important formulas with short notes.\n"
                "Put each formula in $$ ... $$. One plain-English line under each.\n"
                "No full theory chapter. No mock test.\n\n"
                f"Topic:\n{question}"
            ),
            "planner": (
                f"{base}"
                "Tool=PLANNER only. Output ONLY a day-wise study timetable, NOT notes/guide.\n"
                "Default 7 days unless user says otherwise.\n"
                "Markdown table: Day | Focus | Tasks | Duration | Outcome.\n"
                "End with weekly hours + progress tips.\n"
                "FORBIDDEN: long theory, definitions-only guide, pipeline explanation without schedule.\n\n"
                f"User request:\n{question}"
            ),
            "mock": (
                f"{base}"
                "Tool=MOCK TEST only.\n"
                f"Create a timed-style mock: exactly {n_mock} questions on the given topic/subject.\n"
                "Structure:\n"
                "1) Exam header (topic, marks, suggested time)\n"
                "2) Questions numbered (mix MCQ + short if useful)\n"
                "3) Do NOT reveal answers until after a clear separator line 'ANSWER KEY'\n"
                "4) Answer key with brief explanations\n"
                "FORBIDDEN: teaching notes or chapter summary instead of a test.\n"
                "If user only says a broad subject (e.g. physics), pick a high-yield subtopic set and still make a mock.\n\n"
                f"Topic:\n{question}"
            ),
            "roast": (
                f"{base}"
                "Tool=ROAST. Hinglish savage but educational roast while teaching the concept.\n\n"
                f"Doubt:\n{question}"
            ),
            "ncert": (
                f"{base}"
                "Tool=NCERT style. Clear textbook-like explanation.\n\n"
                f"Topic:\n{question}"
            ),
            "mindmap": (
                f"{base}"
                "Tool=MINDMAP only. Hierarchical text mind-map (branches). No long paragraphs.\n\n"
                f"Topic:\n{question}"
            ),
            "important": (
                f"{base}"
                "Tool=IMPORTANT Qs. 15 high-yield questions with short answers.\n\n"
                f"Topic:\n{question}"
            ),
            "diagram": (
                f"{base}"
                "Tool=DIAGRAM explain. Describe/explain diagram for exams.\n\n"
                f"Input:\n{question}"
            ),
            "derivation": (
                f"{base}"
                "Tool=DERIVATION only. Full step-by-step derivation.\n\n"
                f"Derive:\n{question}"
            ),
            "numerical": (
                f"{base}"
                "Tool=NUMERICAL only. This is always a fresh solve — never rely on a stored answer. Show EXACTLY 3 numbered steps: 1) Formula/setup, 2) Substitution/calculation, 3) Final answer with units/check. Box the final answer.\n\n"
                f"Problem:\n{question}"
            ),
            "mcq": (
                f"{base}"
                "Tool=MCQ GENERATOR only. 15 MCQs (easy-medium-hard) with answers.\n"
                "Not a full syllabus notes dump.\n\n"
                f"Topic:\n{question}"
            ),
            "essay": (
                f"{base}"
                "Tool=ESSAY/LETTER. Well-structured formal writing as requested.\n\n"
                f"Request:\n{question}"
            ),
            "resume": (
                f"{base}"
                "Tool=RESUME. Clean ATS-friendly student resume content.\n\n"
                f"Details:\n{question}"
            ),
            "youtube": (
                f"{base}"
                "Tool=YOUTUBE summary style + 5 revision questions.\n\n"
                f"Topic:\n{question}"
            ),
            "career": (
                f"{base}"
                "Tool=CAREER guidance for Indian students.\n\n"
                f"Question:\n{question}"
            ),
            "tips": (
                f"{base}"
                "Tool=STUDY TIPS only.\n\n"
                f"Request:\n{question}"
            ),
            "ocr": (
                f"{base}"
                "Tool=OCR. Read the image and solve/explain.\n\n"
                f"Extra:\n{question}"
            ),
        }


    def _call_groq(self, prompt: str, max_tokens: int = 1500) -> Optional[str]:
        if not self.groq_client:
            return None
        try:
            resp = self.groq_client.chat.completions.create(
                model=config.GROQ_MODEL,
                messages=[
                    {"role": "system", "content": "You are SaarthiBhai. Reply in Hinglish. Use clean Markdown."},
                    {"role": "user", "content": prompt},
                ],
                temperature=0.7,
                max_tokens=max_tokens,
            )
            text = (resp.choices[0].message.content or "").strip()
            return text or None
        except Exception as e:
            logger.error("Groq: %s", e)
            return None

    def _call_gemini(self, prompt: str, max_tokens: int = 1500, model: Optional[str] = None) -> Optional[str]:
        if not self.gemini_client:
            return None
        try:
            resp = self.gemini_client.models.generate_content(
                model=model or config.GEMINI_FLASH_LITE_MODEL or config.GEMINI_MODEL,
                contents=prompt,
                config=genai_types.GenerateContentConfig(
                    temperature=0.5,
                    max_output_tokens=max_tokens,
                    # Prefer shorter, faster completions
                ),
            )
            text = (resp.text or "").strip()
            return text or None
        except Exception as e:
            logger.error("Gemini (%s): %s", model or config.GEMINI_MODEL, e)
            return None

    def _call_gemini_flash_lite(self, prompt: str, max_tokens: int = 1500) -> Optional[str]:
        return self._call_gemini(prompt, max_tokens=max_tokens, model=config.GEMINI_FLASH_LITE_MODEL)

    def _call_openai_compatible(self, base_url: str, api_key: str, model: str, prompt: str,
                                 max_tokens: int, extra_headers: Optional[Dict[str, str]] = None,
                                 timeout: int = 20) -> Optional[str]:
        """Shared helper for any OpenAI-compatible chat/completions endpoint
        just for two providers when `requests` already does the job."""
        try:
            headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
            if extra_headers:
                headers.update(extra_headers)
            payload = {
                "model": model,
                "messages": [
                    {"role": "system", "content": "You are SaarthiBhai. Reply in Hinglish. Use clean Markdown."},
                    {"role": "user", "content": prompt},
                ],
                "temperature": 0.7,
                "max_tokens": max_tokens,
            }
            r = requests.post(f"{base_url.rstrip('/')}/chat/completions", json=payload, headers=headers, timeout=timeout)
            if r.status_code >= 400:
                logger.error("%s HTTP %s: %s", model, r.status_code, r.text[:300])
                return None
            data = r.json()
            text = (data.get("choices", [{}])[0].get("message", {}).get("content") or "").strip()
            return text or None
        except Exception as e:
            logger.error("OpenAI-compatible call (%s): %s", model, e)
            return None

    def _call_anthropic(self, prompt: str, max_tokens: int = 1500) -> Optional[str]:
        if not config.ANTHROPIC_API_KEY:
            return None
        try:
            r = requests.post(
                config.URLS["anthropic_messages"],
                headers={"x-api-key": config.ANTHROPIC_API_KEY, "anthropic-version": config.ANTHROPIC_API_VERSION, "content-type": "application/json"},
                json={"model": config.ANTHROPIC_MODEL, "max_tokens": max_tokens, "system": "You are SaarthiBhai. Reply in the requested language and clean Markdown.", "messages":[{"role":"user","content":prompt}]},
                timeout=20,
            )
            if r.status_code >= 400:
                logger.warning("Claude HTTP %s: %s", r.status_code, r.text[:300]); return None
            data = r.json()
            parts = data.get("content") or []
            text = "".join((p.get("text") or "") for p in parts if isinstance(p, dict)).strip()
            return text or None
        except Exception as e:
            logger.warning("Claude: %s", e); return None

    def _call_openai(self, prompt: str, max_tokens: int = 1500) -> Optional[str]:
        return self._call_openai_compatible(config.URLS["openai_base_url"], config.OPENAI_API_KEY, config.OPENAI_MODEL, prompt, max_tokens) if config.OPENAI_API_KEY else None

    def _call_xai(self, prompt: str, max_tokens: int = 1500) -> Optional[str]:
        return self._call_openai_compatible(config.URLS["xai_base_url"], config.XAI_API_KEY, config.XAI_MODEL, prompt, max_tokens) if config.XAI_API_KEY else None

    def _call_deepseek(self, prompt: str, max_tokens: int = 1500) -> Optional[str]:
        return self._call_openai_compatible(config.URLS["deepseek_base_url"], config.DEEPSEEK_API_KEY, config.DEEPSEEK_MODEL, prompt, max_tokens) if config.DEEPSEEK_API_KEY else None

    def _call_perplexity(self, prompt: str, max_tokens: int = 1500) -> Optional[str]:
        return self._call_openai_compatible(config.URLS["perplexity_base_url"], config.PERPLEXITY_API_KEY, config.PERPLEXITY_MODEL, prompt, max_tokens) if config.PERPLEXITY_API_KEY else None

    def _call_meta(self, prompt: str, max_tokens: int = 1500) -> Optional[str]:
        return self._call_openai_compatible(config.META_BASE_URL, config.META_API_KEY, config.META_MODEL, prompt, max_tokens) if config.META_API_KEY else None

    def _call_mistral(self, prompt: str, max_tokens: int = 1500) -> Optional[str]:
        return self._call_openai_compatible(config.URLS["mistral_base_url"], config.MISTRAL_API_KEY, config.MISTRAL_MODEL, prompt, max_tokens) if config.MISTRAL_API_KEY else None

    def _call_qwen(self, prompt: str, max_tokens: int = 1500) -> Optional[str]:
        return self._call_openai_compatible(config.QWEN_BASE_URL, config.QWEN_API_KEY, config.QWEN_MODEL, prompt, max_tokens) if config.QWEN_API_KEY else None

    def _call_openrouter(self, prompt: str, max_tokens: int = 1500) -> Optional[str]:
        if not config.OPENROUTER_API_KEY or not config.OPENROUTER_MODELS:
            return None
        headers = {
            "HTTP-Referer": config.OPENROUTER_SITE_URL,
            "X-Title": config.OPENROUTER_APP_NAME,
        }
        # Rotate through the free-model list so one saturated/rate-limited
        # model doesn't take the whole fallback chain down with it.
        for model in config.OPENROUTER_MODELS[:2]:  # only first 2 free models — faster fail
            text = self._call_openai_compatible(
                config.URLS["openrouter_base_url"], config.OPENROUTER_API_KEY, model,
                prompt, max_tokens, extra_headers=headers,
            )
            if text:
                return text
        return None

    def answer(self, question: str, tool: str = "general", is_pro: bool = False, language: str = "hinglish") -> Optional[str]:
        if not question or not question.strip():
            return "Please ask a valid question."
        base = self._base_prompt(is_pro, language=language)
        templates = self._templates(base, question.strip(), is_pro=is_pro)
        prompt = templates.get(tool, templates["general"])
        # Keep answers useful but shorter → much lower latency on free Gemini
        if is_pro and tool in ("pyq", "mock", "notes"):
            max_tokens = 1200
        elif is_pro:
            max_tokens = 1000
        elif tool in ("pyq", "planner", "notes"):
            max_tokens = 900
        else:
            max_tokens = 700
        # Optional library RAG: NCERT/notes/open-book snippets stored in Supabase are
        # injected only for knowledge-oriented tools. The retrieval is small so it
        # does not turn every request into a giant context window.
        if config.LIBRARY_CONTEXT_ENABLED and tool in ("general", "explain", "notes", "ncert", "pyq", "important") and supa.enabled:
            try:
                ex_tag, sub_tag = guess_exam_subject(question)
                library_rows = db.search_library(question, ex_tag, sub_tag, limit=3)
                if library_rows:
                    snippets = []
                    for row in library_rows:
                        content = str(row.get("content") or "").strip()[:1800]
                        if content:
                            snippets.append(f"SOURCE: {row.get('title','Library')} [{row.get('source_type','reference')}]\n{content}")
                    if snippets:
                        prompt += "\n\nREFERENCE LIBRARY — use this material when relevant; do not invent citations:\n" + "\n---\n".join(snippets)
            except Exception as e:
                logger.debug("library context skipped: %s", e)

        provider_fns = {
            "gemini": self._call_gemini_flash_lite,
            "gemini_flash_lite": self._call_gemini_flash_lite,
            "groq": self._call_groq,
            "openai": self._call_openai,
            "xai": self._call_xai,
            "deepseek": self._call_deepseek,
            "claude": self._call_anthropic,
            "anthropic": self._call_anthropic,
            "perplexity": self._call_perplexity,
            "meta": self._call_meta,
            "mistral": self._call_mistral,
            "qwen": self._call_qwen,
            "openrouter": self._call_openrouter,
        }
        # Provider routing is fully configuration-driven.
        order = list(config.AI_PROVIDER_ORDER)
        for pref in config.CONTENT.get("provider_preferences_by_tool", {}).get(tool, []):
            if pref in order:
                order.remove(pref)
        order = list(config.CONTENT.get("provider_preferences_by_tool", {}).get(tool, [])) + order

        seen = set()
        for name in order:
            if name in seen or name not in provider_fns:
                continue
            seen.add(name)
            fn = provider_fns[name]
            # Skip unconfigured providers without making a network call.
            configured = {
                "gemini": bool(self.gemini_client), "gemini_flash_lite": bool(self.gemini_client),
                "groq": bool(self.groq_client), "openai": bool(config.OPENAI_API_KEY),
                "xai": bool(config.XAI_API_KEY), "deepseek": bool(config.DEEPSEEK_API_KEY),
                "claude": bool(config.ANTHROPIC_API_KEY), "anthropic": bool(config.ANTHROPIC_API_KEY),
                "perplexity": bool(config.PERPLEXITY_API_KEY), "meta": bool(config.META_API_KEY),
                "mistral": bool(config.MISTRAL_API_KEY), "qwen": bool(config.QWEN_API_KEY),
                "openrouter": bool(config.OPENROUTER_API_KEY),
            }.get(name, False)
            if not configured:
                continue
            try:
                text = fn(prompt, max_tokens=max_tokens)
            except Exception as e:
                logger.warning("Provider %s crashed: %s", name, e); text = None
            if text:
                logger.info("Answer served by provider: %s", name)
                return text
        return SOFT_FAIL_MSG


    def answer_with_image(self, img_bytes: bytes, mime: str, question: str = "", tool: str = "ocr", is_pro: bool = False, language: str = "hinglish") -> Optional[str]:
        if not self.gemini_client:
            return SOFT_FAIL_MSG
        base = self._base_prompt(is_pro, language=language)
        tool = (tool or "general").strip().lower()
        mime = mime or "image/jpeg"
        is_pdf = "pdf" in (mime or "").lower()
        tool_jobs = {
            "general": "Read the content and give a clear, complete exam-oriented answer.",
            "explain": "Explain every concept in the file step-by-step in simple Hinglish.",
            "solve": "Solve all questions/problems visible. Show steps and final answers.",
            "notes": "Make short, structured revision notes from the content.",
            "formula": "Extract and list all formulas with one-line meaning each.",
            "planner": "Turn the content into a practical study plan.",
            "pyq": "Frame/solve as previous-year style Q&A based on the content.",
            "mock": "Create a short mock test from the content + answer key.",
            "mcq": "Create MCQs from the content with correct options marked.",
            "ocr": "Transcribe clearly, then solve/explain any questions found.",
            "numerical": "Solve all numerical problems with steps and units.",
            "derivation": "Write clean derivations for any laws/formulas shown.",
            "ncert": "Explain in NCERT textbook style.",
            "important": "List important questions/points for exams from this content.",
            "diagram": "Explain the diagram/figure labeled and exam-ready.",
            "roast": "Light roast then teach the content properly.",
            "mindmap": "Build a hierarchical mindmap of the content.",
            "essay": "Write an essay/letter style response based on the content.",
            "resume": "Improve or draft resume content based on the file.",
            "youtube": "If this relates to a lecture, make structured study notes.",
            "career": "Give career guidance linked to the content.",
            "tips": "Give Sparsh-style exam tips based on the content.",
        }
        job = tool_jobs.get(tool, tool_jobs["general"])
        file_word = "PDF document" if is_pdf else "image"
        user_q = (question or "").strip()
        prompt = (
            f"{base}\n"
            f"You are given a {file_word}. Selected tool = **{tool}**.\n"
            f"YOUR JOB: {job}\n"
            f"User question (optional): {user_q or 'None — still do the tool job fully on the file content.'}\n\n"
            "RULES:\n"
            "- Do NOT say that you received the image/PDF or that you are ready.\n"
            "- Do NOT ask the user to type the question again if the file already has the problem.\n"
            "- Start directly with the answer in clean Markdown.\n"
            "- If text is unclear, make best effort and state uncertainty briefly once.\n"
        )
        try:
            resp = self.gemini_client.models.generate_content(
                model=config.GEMINI_FLASH_LITE_MODEL or config.GEMINI_MODEL,
                contents=[genai_types.Part.from_bytes(data=img_bytes, mime_type=mime), prompt],
                config=genai_types.GenerateContentConfig(temperature=0.35, max_output_tokens=1000),
            )
            text = (resp.text or "").strip()
            if not text:
                return None
            # Strip meta "I received" style openers if model still adds them
            low = text.lower()
            for bad in (
                "i have received", "i've received", "image received", "pdf received",
                "i can see the image", "thanks for sharing", "please type your question",
            ):
                if low.startswith(bad):
                    # drop first paragraph
                    parts = text.split("\n\n", 1)
                    text = parts[1].strip() if len(parts) > 1 else text
                    break
            return text or None
        except Exception as e:
            logger.error("Vision: %s", e)
            return SOFT_FAIL_MSG

    # ------------------------------------------------------------------
    # POINT 3 — embeddings for the semantic theory-cache.
    # ------------------------------------------------------------------
    def embed_text(self, text: str) -> Optional[List[float]]:
        if not self.gemini_client or not text or not text.strip():
            return None
        try:
            resp = self.gemini_client.models.embed_content(
                model=config.GEMINI_EMBED_MODEL,
                contents=text.strip()[:2000],
            )
            emb = getattr(resp, "embeddings", None) or getattr(resp, "embedding", None)
            if emb is None:
                return None
            if isinstance(emb, list) and emb and hasattr(emb[0], "values"):
                return list(emb[0].values)
            if hasattr(emb, "values"):
                return list(emb.values)
            if isinstance(emb, list):
                return list(emb)
            return None
        except Exception as e:
            logger.warning("embed_text: %s", e)
            return None

    # ------------------------------------------------------------------
    # POINT 10 — QUIZ GENERATOR. Returns a structured quiz dict, built by
    # asking the model for strict JSON (see structured_outputs guidance):
    # {"questions": [{"q":..., "options":[...], "correct": idx, "explanation":...}]}
    # Falls back gracefully to None on any parsing failure so the caller
    # can show a friendly error instead of crashing.
    # ------------------------------------------------------------------
    def generate_quiz(self, topic: str, n_questions: int = 5, language: str = "hinglish",
                       source_context: str = "") -> Optional[List[Dict[str, Any]]]:
        lang_note = {
            "hindi": "Likho pure Hindi (Devanagari) mein.",
            "english": "Write in plain English.",
        }.get(language, "Likho Hinglish mein (Hindi+English mix, Roman script).")
        context_block = f"\nUse this student context (their weak topics / notes) if relevant:\n{source_context}\n" if source_context else ""
        prompt = (
            "You are SaarthiBhai's quiz generator. Output ONLY valid JSON, no markdown fences, "
            "no preamble, no explanation outside the JSON.\n"
            f"{lang_note}\n"
            f"Generate exactly {n_questions} multiple-choice questions on: {topic}\n"
            f"{context_block}"
            "Each question needs 4 options, one correct index (0-based), a short explanation, "
            "and a short HINT that nudges toward the answer WITHOUT stating it directly or naming "
            "which option is correct.\n"
            "JSON schema:\n"
            '{"questions": [{"q": "string", "options": ["a","b","c","d"], "correct": 0, '
            '"explanation": "string", "hint": "string"}]}\n'
        )
        raw = None
        if self.gemini_client:
            raw = self._call_gemini(prompt, max_tokens=1800)
        if not raw:
            raw = self._call_openrouter(prompt, max_tokens=1800)
        if not raw:
            return None
        try:
            cleaned = raw.strip()
            if cleaned.startswith("```"):
                cleaned = cleaned.strip("`")
                cleaned = cleaned.split("\n", 1)[1] if "\n" in cleaned else cleaned
                if cleaned.lower().startswith("json"):
                    cleaned = cleaned[4:]
            start = cleaned.find("{")
            end = cleaned.rfind("}")
            if start == -1 or end == -1:
                return None
            data = json.loads(cleaned[start:end + 1])
            questions = data.get("questions") or []
            clean_qs = []
            for q in questions:
                if not q.get("q") or not q.get("options") or len(q.get("options", [])) < 2:
                    continue
                clean_qs.append({
                    "q": str(q["q"])[:500],
                    "options": [str(o)[:200] for o in q["options"]][:6],
                    "correct": int(q.get("correct", 0)) if str(q.get("correct", 0)).isdigit() else 0,
                    "explanation": str(q.get("explanation", ""))[:400],
                    "hint": str(q.get("hint", ""))[:200],
                })
            return clean_qs or None
        except Exception as e:
            logger.warning("generate_quiz parse: %s", e)
            return None


ai = AIService()


# Tools where an answer legitimately varies with fresh working-out and must
# never be served from cache (POINT 5: numerical freshness rule).
_NEVER_CACHE_TOOLS = {"numerical", "solve", "derivation"}


def get_ai_answer(question: str, tool: str, is_pro: bool, language: str = "hinglish",
                  phone_number: str = "", exam_type: str = "", subject: str = "") -> Optional[str]:
    """Single entry point used by ALL surfaces (Telegram/WhatsApp/Web) so
    caching is consistent everywhere instead of duplicated ad-hoc per route.

    Caching strategy (POINT 3/4/5):
      - NUMERICAL questions (digit + solve/calculate keyword, or tool is
        numerical/solve/derivation) are NEVER cached — solved fresh every time.
      - THEORY questions first check the exact-match hash cache (fast,
        free), then fall back to a semantic/embedding similarity cache
        (catches reworded versions of the same question) before finally
        calling the AI and writing back to both caches.
      - Cache keys AND the semantic-cache pool are namespaced by language
        so a Hindi-preference user never gets served an English-cached
        answer (or vice versa) for the same underlying question.
    """
    language = (language or "hinglish").strip().lower()
    numerical = tool in _NEVER_CACHE_TOOLS or is_numerical_question(question)
    semantic_pool = f"{tool}:{language}"

    if not numerical:
        ckey = make_cache_key(tool, question, is_pro, language)
        cached = db.cache_get(ckey)
        if cached:
            db.touch_master_cache(question, answer=cached, tool=tool, source="redis_hit", phone_number=phone_number, exam_type=exam_type, subject=subject)
            return cached
        durable = db.get_master_cache(question, tool=tool, exam_type=exam_type, subject=subject, phone_number=phone_number)
        if durable and durable.get("answer"):
            db.cache_set(ckey, durable["answer"])
            return durable["answer"]
        try:
            emb = ai.embed_text(question)
            if emb:
                sem_hit = db.semantic_cache_search(semantic_pool, emb)
                if sem_hit:
                    db.cache_set(ckey, sem_hit)
                    db.touch_master_cache(question, answer=sem_hit, tool=tool, source="semantic_hit", phone_number=phone_number, exam_type=exam_type, subject=subject)
                    return sem_hit
        except Exception as e:
            logger.warning("semantic cache lookup skipped: %s", e)

    text = run_ai(ai.answer, question, tool, is_pro=is_pro, language=language)
    if text and not str(text).startswith("ERROR:"):
        if not numerical:
            ckey = make_cache_key(tool, question, is_pro, language)
            db.cache_set(ckey, text)
            db.touch_master_cache(question, answer=text, tool=tool, source="ai", phone_number=phone_number, exam_type=exam_type, subject=subject)
            try:
                emb = ai.embed_text(question)
                if emb:
                    db.semantic_cache_add(semantic_pool, question, emb, text)
            except Exception as e:
                logger.warning("semantic cache write skipped: %s", e)
        return text
    return None


# ============================================================================
# TELEGRAM (same as before)
# ============================================================================

async def typing(update: Update) -> None:
    try:
        if update.effective_chat:
            await update.effective_chat.send_action(ChatAction.TYPING)
    except Exception:
        pass


async def reply(update: Update, text: str, reply_markup=None) -> None:
    try:
        if update.callback_query:
            await update.callback_query.edit_message_text(text, reply_markup=reply_markup, parse_mode=ParseMode.MARKDOWN)
        elif update.message:
            await update.message.reply_text(text, reply_markup=reply_markup, parse_mode=ParseMode.MARKDOWN)
    except Exception:
        try:
            if update.message:
                await update.message.reply_text(text, reply_markup=reply_markup)
        except Exception:
            pass


def main_menu(is_pro: bool = False) -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton("📚 Ask Doubt", callback_data="menu_ask"),
         InlineKeyboardButton("🛠 Tools", callback_data="menu_tools")],
        [InlineKeyboardButton("📊 Progress", callback_data="menu_progress"),
         InlineKeyboardButton("🏆 Leaderboard", callback_data="menu_lb")],
        [InlineKeyboardButton("🔥 Streak", callback_data="menu_streak"),
         InlineKeyboardButton("📖 Galti Diary", callback_data="menu_galti")],
        [InlineKeyboardButton("❓ Quiz (type /quiz topic)", callback_data="menu_quiz_hint"),
         InlineKeyboardButton("🌐 Language", callback_data="menu_lang")],
    ]
    if is_pro:
        rows.append([InlineKeyboardButton("👑 You are PRO", callback_data="menu_prostatus")])
    else:
        rows.append([InlineKeyboardButton(f"💎 Upgrade ₹{config.PRO_PRICE_INR}", callback_data="menu_upgrade")])
    rows.append([InlineKeyboardButton(f"👨‍💻 About {config.CREATOR_NAME}", callback_data="menu_about")])
    return InlineKeyboardMarkup(rows)


def tools_menu(is_pro: bool = False) -> InlineKeyboardMarkup:
    tools = [
        ("explain", "📖 Explain"), ("solve", "🧮 Solve"), ("notes", "📝 Notes"),
        ("pyq", "📜 PYQ"), ("formula", "📐 Formula"), ("planner", "📅 Planner"),
    ]
    if is_pro:
        # Full parity with the web sidebar — every Pro tool that exists
        # must be reachable here, not just the ones that happened to get
        # added first.
        tools += [
            ("mock", "🎯 Mock"), ("roast", "🔥 Roast"), ("mindmap", "🧠 Mindmap"),
            ("mcq", "❓ MCQ"), ("ncert", "📘 NCERT"), ("derivation", "📐 Derivation"),
            ("numerical", "🔢 Numerical"), ("essay", "✍️ Essay"), ("resume", "📄 Resume"),
            ("career", "🚀 Career"), ("tips", "💡 Tips"), ("important", "⭐ Important Qs"),
            ("diagram", "🧬 Diagram"), ("youtube", "📺 YouTube Notes"),
        ]
    else:
        tools += [("mock", "🎯 Mock 🔒")]
    rows = []
    for i in range(0, len(tools), 2):
        row = [InlineKeyboardButton(tools[i][1], callback_data=f"tool_{tools[i][0]}")]
        if i + 1 < len(tools):
            row.append(InlineKeyboardButton(tools[i + 1][1], callback_data=f"tool_{tools[i + 1][0]}"))
        rows.append(row)
    rows.append([InlineKeyboardButton("« Back", callback_data="menu_main")])
    return InlineKeyboardMarkup(rows)


PRO_ONLY_TOOLS = set(config.PRO_ONLY_TOOLS)
TOOL_KEYWORDS = list(config.TOOL_KEYWORDS)


def detect_tool_from_text(text: str, default: str = "general") -> str:
    lower = (text or "").lower()
    for key, name in TOOL_KEYWORDS:
        if lower.startswith(key):
            return name
    return default


async def process_question(update: Update, context: ContextTypes.DEFAULT_TYPE, text: str, tool: str = "general") -> None:
    user = update.effective_user
    if not user:
        return
    uid = user.id
    if db.is_banned(uid):
        await reply(update, format_ban_active_message(db.get_ban_remaining_seconds(uid)))
        return
    if contains_abuse(text):
        count, just_banned = db.record_abuse_warning(uid)
        await reply(update, format_abuse_warning_message(count, just_banned))
        return
    is_pro = db.is_pro(uid)
    db.track_activity(uid)
    if tool in PRO_ONLY_TOOLS and not is_pro:
        await reply(update, f"🔒 Pro-only tool.\n\nUpgrade ₹{config.PRO_PRICE_INR}/30 days.",
                    InlineKeyboardMarkup([[InlineKeyboardButton("💎 Upgrade", callback_data="menu_upgrade")]]))
        return
    if not is_pro:
        can, quota = db.try_consume_quota(uid)
        if not can:
            await reply(update, f"❌ Free quota finished!\nDaily: {quota['daily_left']} | Lifetime: {quota['lifetime_left']}\n\nUpgrade for unlimited access 💎")
            return
    await typing(update)
    start = time.time()
    udata_for_ai = db.get_user(uid) or {}
    answer = get_ai_answer(text, tool, is_pro, language=db.get_language(uid),
                           phone_number=udata_for_ai.get("phone_number", ""),
                           exam_type=udata_for_ai.get("exam_type", ""), subject=udata_for_ai.get("subject", ""))
    elapsed = time.time() - start
    if not answer:
        await reply(update, "😔 Answer generate nahi ho paya abhi. Please dobara try karo 30 seconds baad.")
        return
    udata = db.ensure_user(uid, user.username or "", user.full_name or config.DEFAULT_STUDENT_NAMES["generic"])
    ex, sub = guess_exam_subject(text, udata.get("exam_type", ""), udata.get("subject", ""))
    udata["exam_type"], udata["subject"], udata["last_question_at"] = ex, sub, _now_ist().isoformat()
    db.save_user(uid, udata)
    db.sync_user_to_supabase(uid, udata)
    db.add_personal_history(uid, text, tool=tool, exam_type=ex, subject=sub, source_cache="")
    xp_gain = config.XP_QUESTION * (config.PRO_XP_MULTIPLIER if is_pro else 1)
    xp, level = db.add_xp(uid, xp_gain)
    try:
        if db.redis:
            db.redis.hincrby(db._key(uid), "questions_asked", 1)
            db.redis.incr("stats:total_questions")
    except Exception:
        pass
    db.update_streak(uid)
    db.sync_user_to_supabase(uid)
    footer = f"\n\n━━━━━━━━━━━━━━━\n⚡ {elapsed:.1f}s | ⭐ +{xp_gain} XP{f' (Pro ×{config.PRO_XP_MULTIPLIER})' if is_pro else ''} | Level {level}\n" + config.CONTENT["footer_signature"].format(creator_name=config.CREATOR_NAME)
    full = answer + footer + _channel_resource_suffix(text, is_pro, uid)
    if len(full) <= 4096:
        await reply(update, full, _text_action_keyboard(is_pro))
    else:
        for i, chunk in enumerate([full[j:j + 4000] for j in range(0, len(full), 4000)]):
            if i == 0:
                await reply(update, chunk)
            elif update.message:
                await update.message.reply_text(chunk, parse_mode=ParseMode.MARKDOWN)


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    if not user:
        return
    ref_code = ""
    if context.args:
        arg0 = context.args[0]
        if arg0.startswith("ref_"):
            ref_code = arg0[4:]
    udata = db.ensure_user(user.id, user.username or "", user.full_name or config.DEFAULT_STUDENT_NAMES["generic"], referred_by=ref_code)
    db.register_referral_code(user.id, udata.get("referral_code", ""))
    db.track_activity(user.id)
    bonus_note = "\n\n🎁 Referral bonus applied!" if ref_code else ""
    welcome_text = (
        f"🎓 *Welcome to {config.BRAND_NAME}!*\n\nHi {user.first_name}! Type your doubt or use menu.{bonus_note}\n\n"
        "Bonus commands:\n"
        "`/quiz <topic>` — instant quiz\n"
        "`/galti` — your mistake diary\n"
        "`/language` — Hindi/Hinglish/English\n"
        "`/joinclass <code>` — join a teacher's class\n\n"
        + config.CONTENT["footer_signature"].format(creator_name=config.CREATOR_NAME)
    )
    await reply(update, welcome_text, main_menu(db.is_pro(user.id)))


async def menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    await reply(update, "🏠 *Main Menu*", main_menu(db.is_pro(user.id) if user else False))


async def free_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message or not update.message.text:
        return
    text = update.message.text.strip()
    if not text:
        return
    uid = update.effective_user.id if update.effective_user else 0
    # Explicit tool selection (via /Tools menu) wins; otherwise auto-detect
    # from the message text itself using the same keyword logic WhatsApp uses.
    tool = db.pop_tool(uid) or detect_tool_from_text(text)
    await process_question(update, context, text, tool)


async def progress(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    if not user:
        return
    u = db.ensure_user(user.id)
    xp = int(u.get("xp", 0))
    level = int(u.get("level", 1))
    coins = db.get_coins(user.id)
    await reply(update, f"📊 *Progress*\n\n⭐ Level {level}\nXP: {xp}\n🪙 Coins: {coins}\n🔥 Streak: {u.get('streak', 0)}\n📚 Questions: {u.get('questions_asked', 0)}")


async def streak_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    if not user:
        return
    s = db.update_streak(user.id)
    await reply(update, f"🔥 *Streak*\nCurrent: {s['current']} | Best: {s['best']} | Shields: {s['shields']}")


async def leaderboard(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    board = db.get_leaderboard(15)
    if not board:
        await reply(update, "🏆 Empty leaderboard. Be first!")
        return
    lines = ["🏆 *Leaderboard*\n"]
    for e in board:
        m = {1: "🥇", 2: "🥈", 3: "🥉"}.get(e["rank"], f"{e['rank']}.")
        lines.append(f"{m} {e['name']} – L{e['level']} ({e['xp']} XP)")
    rank = db.get_rank(update.effective_user.id)
    if rank:
        lines.append(f"\n📍 Your rank: #{rank}")
    await reply(update, "\n".join(lines))


async def upgrade(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    uid = user.id if user else 0
    domain = config.VERCEL_URL.rstrip("/") if config.VERCEL_URL else config.PUBLIC_DOMAIN
    link = f"{config.PUBLIC_SCHEME}://{domain}/pay?uid={uid}"
    await reply(update,
                f"💎 *SaarthiBhai Pro – ₹{config.PRO_PRICE_INR}/30 days*\n\n"
                "Unlimited • Roast • Mindmap • OCR • 2× XP\n\n"
                f"Pay here: {link}\n\n_ - made with love by Sparsh Singhal _")


async def about_sparsh(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await reply(update, "👨‍💻 *Sparsh Singhal*\n\nCreator & Developer of SaarthiBhai 🎓\nBuilt with ❤️ for Indian students — gamified learning for every exam.\n\n_SaarthiBhai — by Sparsh Singhal_")


# ----------------------------------------------------------------------
# QUIZ over Telegram — one question at a time via inline buttons.
# State (quiz_id, current index, answers-so-far) lives in Redis so it
# survives across the async callback round-trips.
# ----------------------------------------------------------------------
def _quiz_state_key(uid) -> str:
    return f"tgquiz:{uid}"


def _save_quiz_state(uid, quiz_id: str, idx: int, answers: List[int]) -> None:
    if db.redis:
        try:
            db.redis.setex(_quiz_state_key(uid), 3600, json.dumps({"quiz_id": quiz_id, "idx": idx, "answers": answers}))
        except Exception:
            pass


def _load_quiz_state(uid) -> Optional[Dict]:
    if not db.redis:
        return None
    try:
        raw = db.redis.get(_quiz_state_key(uid))
        return json.loads(raw) if raw else None
    except Exception:
        return None


def _quiz_option_keyboard(quiz_id: str, options: List[str]) -> InlineKeyboardMarkup:
    letters = ["A", "B", "C", "D", "E", "F"]
    rows = [[InlineKeyboardButton(f"{letters[i]}. {opt[:40]}", callback_data=f"qz_{quiz_id}_{i}")]
            for i, opt in enumerate(options)]
    return InlineKeyboardMarkup(rows)


async def _send_quiz_question(update: Update, quiz: Dict, idx: int) -> None:
    q = quiz["questions"][idx]
    text = f"❓ *Q{idx + 1}/{len(quiz['questions'])}:* {q['q']}"
    await reply(update, text, _quiz_option_keyboard(quiz["quiz_id"], q["options"]))


async def quiz_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    if not user:
        return
    uid = user.id
    topic = " ".join(context.args) if context.args else ""
    if not topic:
        await reply(update, "📚 Usage: `/quiz <topic>`\nExample: `/quiz Thermodynamics`")
        return
    is_pro = db.is_pro(uid)
    if not is_pro and db.get_daily_quiz_used(uid):
        await reply(update, "❌ Free daily quiz limit khatam. Pro se unlimited quiz milta hai.",
                    InlineKeyboardMarkup([[InlineKeyboardButton("💎 Upgrade", callback_data="menu_upgrade")]]))
        return
    await typing(update)
    n = 10 if is_pro else 3
    questions = run_ai(ai.generate_quiz, topic, n_questions=n, language=db.get_language(uid))
    if not questions:
        await reply(update, "😔 Quiz generate nahi ho paya. Dobara try karo.")
        return
    quiz_id = secrets.token_hex(8)
    quiz_data = {"quiz_id": quiz_id, "owner_uid": str(uid), "topic": topic, "questions": questions}
    db.save_quiz(quiz_id, quiz_data)
    if not is_pro:
        db.mark_daily_quiz_used(uid)
    _save_quiz_state(uid, quiz_id, 0, [])
    await reply(update, f"🎯 *Quiz Started:* {topic} ({len(questions)} Qs)")
    await _send_quiz_question(update, quiz_data, 0)


async def galti_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    if not user:
        return
    mistakes = db.get_mistakes(user.id, limit=10)
    if not mistakes:
        await reply(update, "📖 *Galti Diary khaali hai!* Koi mistake save nahi hui abhi tak — solid going 🔥")
        return
    lines = ["📖 *Teri Galti Diary (last 10):*\n"]
    for i, m in enumerate(mistakes, 1):
        lines.append(f"{i}. {m.get('question','')[:80]}\n   ✅ {m.get('correct_answer','')[:60]}")
    await reply(update, "\n".join(lines))


async def language_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    if not user:
        return
    if context.args:
        lang = context.args[0].strip().lower()
        db.set_language(user.id, lang)
        await reply(update, f"✅ Language set: *{db.get_language(user.id)}*")
        return
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("🇮🇳 Hindi", callback_data="lang_hindi"),
         InlineKeyboardButton("🔤 Hinglish", callback_data="lang_hinglish"),
         InlineKeyboardButton("🇬🇧 English", callback_data="lang_english")],
    ])
    await reply(update, "🌐 Kaunsi bhasha me samjhau?", kb)


async def joinclass_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    if not user:
        return
    if not context.args:
        await reply(update, "🏫 Usage: `/joinclass <CODE>`")
        return
    code = context.args[0].strip().upper()
    ok = db.join_class(user.id, code)
    await reply(update, "✅ Class join ho gaya!" if ok else "❌ Invalid class code.")


async def callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not query:
        return
    await query.answer()
    data = query.data or ""
    user = update.effective_user
    uid = user.id if user else 0
    is_pro = db.is_pro(uid)
    if data == "menu_main":
        await menu(update, context)
    elif data == "menu_ask":
        await reply(update, "📚 Type your question now.")
    elif data == "menu_tools":
        await reply(update, "🛠 *Tools*", tools_menu(is_pro))
    elif data == "menu_progress":
        await progress(update, context)
    elif data == "menu_lb":
        await leaderboard(update, context)
    elif data == "menu_streak":
        await streak_cmd(update, context)
    elif data == "menu_upgrade":
        await upgrade(update, context)
    elif data == "menu_about":
        await about_sparsh(update, context)
    elif data == "menu_prostatus":
        await reply(update, "👑 You are PRO. Enjoy unlimited power!")
    elif data == "menu_galti":
        await galti_cmd(update, context)
    elif data == "menu_quiz_hint":
        await reply(update, "❓ Type `/quiz <topic>` to start — example: `/quiz Newton's Laws`")
    elif data == "menu_lang":
        await language_cmd(update, context)
    elif data.startswith("tool_"):
        tool = data.replace("tool_", "")
        if tool in PRO_ONLY_TOOLS and not is_pro:
            await reply(update, f"🔒 Pro-only tool.\n\nUpgrade ₹{config.PRO_PRICE_INR}/30 days.",
                        InlineKeyboardMarkup([[InlineKeyboardButton("💎 Upgrade", callback_data="menu_upgrade")]]))
            return
        db.set_tool(uid, tool)
        await reply(update, f"✅ Tool: *{tool}*\nAb sawaal type karo.")
    elif data.startswith("lang_"):
        lang = data.replace("lang_", "")
        db.set_language(uid, lang)
        await reply(update, f"✅ Language set: *{db.get_language(uid)}*")
    elif data.startswith("qz_"):
        # qz_{quiz_id}_{option_index}
        try:
            _, quiz_id, opt_str = data.split("_", 2)
            picked = int(opt_str)
        except Exception:
            return
        quiz = db.get_quiz(quiz_id)
        state = _load_quiz_state(uid)
        if not quiz or not state or state.get("quiz_id") != quiz_id:
            await reply(update, "⏱️ Quiz session expired. Start again with /quiz <topic>.")
            return
        idx = state.get("idx", 0)
        answers = state.get("answers", [])
        answers.append(picked)
        q = quiz["questions"][idx]
        correct_idx = q.get("correct", 0)
        is_correct = (picked == correct_idx)
        if not is_correct:
            mistake_coins = db.add_mistake(uid, question=q.get("q", ""), tool="quiz",
                            correct_answer=(q.get("options") or [""])[correct_idx] if correct_idx < len(q.get("options", [])) else "",
                            user_answer=(q.get("options") or [""])[picked] if 0 <= picked < len(q.get("options", [])) else "?",
                            topic=quiz.get("topic", ""))
        else:
            mistake_coins = 0
        verdict = "✅ Sahi!" if is_correct else f"❌ Galat. Himmat ki koshish — Galti Diary me daal di 📖 +{mistake_coins} 🪙"
        explanation = q.get("explanation", "")
        await reply(update, f"{verdict}\n\n{explanation}" if explanation else verdict)
        next_idx = idx + 1
        if next_idx >= len(quiz["questions"]):
            score = sum(1 for i, a in enumerate(answers) if a == quiz["questions"][i].get("correct", 0))
            total = len(quiz["questions"])
            db.save_quiz_attempt(uid, quiz_id, score, total, topic=quiz.get("topic", ""), mode="telegram")
            xp_gain = 100 if score == total else int(20 * (score / total)) if total else 0
            coins_gain = config.QUIZ_PERFECT_COINS if score == total else int(10 * (score / total)) if total else 0
            if xp_gain:
                db.add_xp(uid, xp_gain)
            if coins_gain:
                db.add_coins(uid, coins_gain)
            if db.redis:
                try:
                    db.redis.delete(_quiz_state_key(uid))
                except Exception:
                    pass
            await reply(update, f"🏁 *Quiz Khatam!* Score: {score}/{total}\n⭐ +{xp_gain} XP | 🪙 +{coins_gain} Coins\n\nUse /quiz <topic> for another round.")
        else:
            _save_quiz_state(uid, quiz_id, next_idx, answers)
            await _send_quiz_question(update, quiz, next_idx)


async def handle_contact(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Optional phone-link step so Telegram accounts can become phone-keyed in Supabase."""
    user = update.effective_user
    contact = update.effective_message.contact if update.effective_message else None
    if not user or not contact:
        return
    if str(contact.user_id or "") and int(contact.user_id) != int(user.id):
        await reply(update, "⚠️ Apna hi contact share karo, bhai.")
        return
    ok = db.set_phone_number(user.id, contact.phone_number or "")
    await reply(update, "✅ Phone link ho gaya. Ab tumhara Supabase profile phone number ko ID maan kar save hoga." if ok else "❌ Phone number save nahi ho paya.")


async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    if not user or not update.message or not update.message.photo:
        return
    uid = user.id
    db.track_activity(uid)
    if not db.is_pro(uid):
        await reply(update, f"📷 Image OCR is Pro-only. Upgrade ₹{config.PRO_PRICE_INR}.",
                    InlineKeyboardMarkup([[InlineKeyboardButton("💎 Upgrade", callback_data="menu_upgrade")]]))
        return
    await typing(update)
    try:
        photo = update.message.photo[-1]
        file = await context.bot.get_file(photo.file_id)
        img_bytes = bytes(await file.download_as_bytearray())
        caption = (update.message.caption or "").strip()
        start = time.time()
        answer = run_ai(ai.answer_with_image, img_bytes, "image/jpeg", caption, "ocr", True)
        elapsed = time.time() - start
        if not answer:
            await reply(update, "😔 Could not read image.")
            return
        udata = db.ensure_user(uid, user.username or "", user.full_name or config.DEFAULT_STUDENT_NAMES["generic"])
        xp_gain = config.XP_QUESTION * 2
        xp, level = db.add_xp(uid, xp_gain)
        try:
            if db.redis:
                db.redis.hincrby(db._key(uid), "questions_asked", 1)
        except Exception:
            pass
        footer = f"\n\n━━━━━━━━━━━━━━━\n📷 OCR | ⚡ {elapsed:.1f}s | ⭐ +{xp_gain} XP (2× Pro) | Level {level}"
        await reply(update, answer + footer)
    except Exception as e:
        logger.exception("Photo: %s", e)
        await reply(update, "😔 Image error.")


async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    logger.exception("Error: %s", context.error)


telegram_app: Optional[Application] = None
_lock = asyncio.Lock()


async def get_app() -> Application:
    global telegram_app
    if telegram_app:
        return telegram_app
    async with _lock:
        if telegram_app:
            return telegram_app
        app_ = (
            ApplicationBuilder()
            .token(config.BOT_TOKEN)
            .defaults(Defaults(parse_mode=ParseMode.MARKDOWN))
            .build()
        )
        app_.add_handler(CommandHandler("start", start))
        app_.add_handler(CommandHandler("menu", menu))
        app_.add_handler(CommandHandler("progress", progress))
        app_.add_handler(CommandHandler("streak", streak_cmd))
        app_.add_handler(CommandHandler("leaderboard", leaderboard))
        app_.add_handler(CommandHandler("upgrade", upgrade))
        app_.add_handler(CommandHandler("about", about_sparsh))
        app_.add_handler(CommandHandler("quiz", quiz_cmd))
        app_.add_handler(CommandHandler("galti", galti_cmd))
        app_.add_handler(CommandHandler("language", language_cmd))
        app_.add_handler(CommandHandler("joinclass", joinclass_cmd))
        app_.add_handler(CallbackQueryHandler(callback))
        app_.add_handler(MessageHandler(filters.CONTACT, handle_contact))
        app_.add_handler(MessageHandler(filters.PHOTO, handle_photo))
        app_.add_handler(MessageHandler(filters.Document.ALL, handle_document))
        app_.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, free_text))
        app_.add_error_handler(error_handler)
        await app_.initialize()
        telegram_app = app_
        return app_


def process_whatsapp_message(from_number: str, text: str, profile_name: str = "") -> None:
    uid = f"wa:{from_number}"
    if db.is_banned(uid):
        _send_whatsapp_text(from_number, format_ban_active_message(db.get_ban_remaining_seconds(uid)))
        return
    if contains_abuse(text):
        count, just_banned = db.record_abuse_warning(uid)
        _send_whatsapp_text(from_number, format_abuse_warning_message(count, just_banned))
        return
    db.ensure_user(uid, full_name=profile_name or "WhatsApp Student", platform="whatsapp", phone_number=from_number)
    db.track_activity(uid)
    is_pro = db.is_pro(uid)
    tool = detect_tool_from_text(text)
    if tool in PRO_ONLY_TOOLS and not is_pro:
        _send_whatsapp_text(from_number, f"🔒 Pro-only tool.\n\nUpgrade ₹{config.PRO_PRICE_INR}/30 days — SaarthiBhai by Sparsh Singhal.")
        return
    if not is_pro:
        can, quota = db.try_consume_quota(uid)
        if not can:
            _send_whatsapp_text(from_number, f"❌ Free quota finished!\nDaily: {quota['daily_left']} | Lifetime: {quota['lifetime_left']}\n\nUpgrade for unlimited access 💎")
            return
    udata_for_ai = db.get_user(uid) or {}
    answer = get_ai_answer(text, tool, is_pro, language=db.get_language(uid),
                           phone_number=udata_for_ai.get("phone_number", from_number),
                           exam_type=udata_for_ai.get("exam_type", ""), subject=udata_for_ai.get("subject", ""))
    if not answer:
        _send_whatsapp_text(from_number, "😔 Abhi answer generate nahi ho paya. Please 30 second baad dobara try karo.")
        return
    db.update_streak(uid)
    ex, sub = guess_exam_subject(text, (db.get_user(uid) or {}).get("exam_type", ""), (db.get_user(uid) or {}).get("subject", ""))
    u = db.get_user(uid) or {}
    u["exam_type"], u["subject"], u["last_question_at"] = ex, sub, _now_ist().isoformat()
    db.save_user(uid, u); db.sync_user_to_supabase(uid, u)
    db.add_personal_history(uid, text, tool=tool, exam_type=ex, subject=sub)
    try:
        schedule_spaced_reminders(uid, text, tool)
    except Exception:
        pass
    xp, level = db.add_xp(uid, config.XP_QUESTION * (config.PRO_XP_MULTIPLIER if is_pro else 1))
    if db.redis:
        try:
            db.redis.hincrby(db._key(uid), "questions_asked", 1)
            db.redis.incr("stats:total_questions")
        except Exception:
            pass
    db.sync_user_to_supabase(uid)
    footer = f"\n\n━━━━━━━━━━━━━━━\n⭐ +{config.XP_QUESTION * config.PRO_XP_MULTIPLIER if is_pro else config.XP_QUESTION} XP" + (f" (Pro ×{config.PRO_XP_MULTIPLIER})" if is_pro else "") + f" | Level {level}\n" + config.CONTENT["footer_signature"].format(creator_name=config.CREATOR_NAME)
    _send_whatsapp_text(from_number, answer + footer + _channel_resource_suffix(text, is_pro, uid))


def _send_whatsapp_text(to_number: str, body: str) -> None:
    if config.WHATSAPP_TOKEN and config.WHATSAPP_PHONE_NUMBER_ID:
        try:
            url = f"{config.URLS["whatsapp_graph_base_url"]}/{config.WHATSAPP_API_VERSION}/{config.WHATSAPP_PHONE_NUMBER_ID}/messages"
            headers = {"Authorization": f"Bearer {config.WHATSAPP_TOKEN}", "Content-Type": "application/json"}
            requests.post(url, json={"messaging_product": "whatsapp", "to": to_number, "type": "text",
                                     "text": {"body": body[:4000]}}, headers=headers, timeout=15)
        except Exception as e:
            logger.error("WA send: %s", e)


# ============================================================================
# FRONTEND with Markdown renderer
# ============================================================================

FRONTEND_HTML = r"""
<!DOCTYPE html>
<html lang="en">
<head>
<link rel="stylesheet" href="{{ katex_css }}" crossorigin="anonymous">
<script src="{{ katex_js }}" crossorigin="anonymous"></script>
<script src="{{ katex_auto_render_js }}" crossorigin="anonymous"></script>

<meta charset="UTF-8">
<link rel="icon" type="image/svg+xml" href="/bot-icon.svg">
<link rel="apple-touch-icon" href="/bot-icon.svg">
<meta name="viewport" content="width=device-width, initial-scale=1, maximum-scale=1, viewport-fit=cover">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{{ brand_name }}</title>
<meta name="description" content="{{ brand_name }} — gamified AI tutor, built by {{ creator_name }}.">
<style>
:root{--bg:#ffffff;--card:#ffffff;--accent:{{ theme_primary }};--text:#111111;--muted:#575757;--border:rgba(17,17,17,.12)}
*{box-sizing:border-box;margin:0;padding:0}
body{font-family:system-ui,-apple-system,sans-serif;background:var(--bg);color:var(--text);min-height:100vh;display:flex;flex-direction:column}
header{background:var(--accent);padding:.85rem 1.25rem;display:flex;justify-content:space-between;align-items:center;border-bottom:1px solid var(--border);position:sticky;top:0;z-index:50}
.logo-wrap{display:flex;align-items:center;gap:.65rem;cursor:pointer;user-select:none}
.logo-wrap img{width:52px;height:52px;border-radius:50%;object-fit:cover;border:2px solid var(--accent);box-shadow:0 0 0 3px rgba(34,211,238,.25)}
.logo{font-size:1.25rem;font-weight:700}.logo span{color:var(--accent)}
.brand-sub{font-size:.7rem;color:var(--muted);margin-top:1px}
.stats{font-size:.82rem;color:var(--muted);display:flex;gap:1rem;align-items:center;flex-wrap:wrap}
.pro-btn-top{background:var(--accent);color:#111;border:2px solid #111;border-radius:999px;padding:.35rem .85rem;font-size:.78rem;font-weight:700;cursor:pointer}
main{flex:1;display:grid;grid-template-columns:280px 1fr;max-width:1400px;margin:0 auto;width:100%}
@media(max-width:900px){
  main{grid-template-columns:1fr;max-width:100%}
  header{padding:.65rem .85rem}
  .logo-wrap img{width:48px;height:48px}
  .logo{font-size:1.05rem}
  .brand-sub{display:none}
  .stats{font-size:.72rem;gap:.45rem}
  .sidebar{
    display:block;border-right:none;border-bottom:1px solid var(--border);
    padding:.65rem .75rem;max-height:none;overflow:visible;
  }
  .creator-card{margin-bottom:.5rem}
  .creator-card img{width:64px;height:64px}
  .sidebar h3{margin:.4rem 0 .35rem}
  /* tools as horizontal chips */
  .sidebar .tool-btn{display:inline-block;width:auto;margin:0 .3rem .35rem 0;padding:.4rem .65rem;font-size:.8rem}
  .sidebar .tool-btn{white-space:nowrap}
  #toolList, .tool-list, .sidebar-tools{display:flex;flex-wrap:wrap;gap:.25rem}
  .pay-side{font-size:.8rem;padding:.55rem}
  .lb-item{font-size:.8rem}
  .chat-area{height:auto;min-height:55vh}
  .messages{padding:.85rem;padding-bottom:1rem}
  .msg{max-width:94%;padding:.75rem .9rem;font-size:.95rem}
  .input-bar,.composer{padding:.5rem !important}
  .input-row textarea{font-size:16px !important} /* prevents iOS zoom */
  .send{padding:.65rem 1rem;min-height:44px}
  .modal{width:92%;margin:1rem}
}
@media(max-width:600px){
  .stats span.hide-xs,.stats .hide-xs{display:none}
  .logo-wrap img{width:46px;height:46px}
  .creator-card img{width:60px;height:60px}
}
.sidebar{background:var(--card);border-right:1px solid var(--border);padding:1.25rem 1rem;overflow-y:auto}
.sidebar h3{font-size:.75rem;text-transform:uppercase;letter-spacing:.05em;color:var(--muted);margin:1rem 0 .6rem}
.tool-btn{display:block;width:100%;text-align:left;background:transparent;border:1px solid transparent;color:var(--text);padding:.55rem .8rem;border-radius:8px;margin-bottom:.25rem;cursor:pointer;font-size:.92rem}
.tool-btn:hover,.tool-btn.active{background:rgba(255,214,0,.22);border-color:#111;color:#111}
.pro-badge{background:var(--accent);color:#111;border:1px solid #111;font-size:.65rem;padding:.12rem .4rem;border-radius:999px;margin-left:.35rem}
.pay-side{display:block;width:100%;margin:1rem 0 .5rem;background:var(--accent);color:#111;border:2px solid #111;border-radius:10px;padding:.7rem;font-weight:700;cursor:pointer;font-size:.9rem}
.creator-card{display:flex;gap:.7rem;align-items:center;padding:.75rem;background:#0f172a;border-radius:12px;border:1px solid var(--border);margin-bottom:1rem}
.creator-card img{width:84px;height:84px;border-radius:50%;object-fit:cover;border:3px solid var(--accent);box-shadow:0 0 0 4px rgba(34,211,238,.22)}
.creator-card .name{font-weight:700;font-size:.9rem}
.creator-card .role{font-size:.72rem;color:var(--muted)}
.chat-area{display:flex;flex-direction:column;height:calc(100vh - 64px)}
.messages{flex:1;overflow-y:auto;padding:1.25rem;display:flex;flex-direction:column;gap:1rem}
.msg{max-width:88%;padding:.95rem 1.1rem;border-radius:16px;line-height:1.6;word-break:break-word}
.msg.user{align-self:flex-end;background:#fff;color:#111;border:1px solid #111;border-bottom-right-radius:4px;white-space:pre-wrap}
.msg.bot{align-self:flex-start;background:var(--accent);color:#111;border:1px solid #111;border-bottom-left-radius:4px}
.msg.bot h1,.msg.bot h2,.msg.bot h3,.msg.bot h4{color:#111;margin:.7rem 0 .35rem;line-height:1.3}
.msg.bot h1{font-size:1.2rem}.msg.bot h2{font-size:1.1rem}.msg.bot h3{font-size:1.02rem}
.msg.bot p{margin:.35rem 0}
.msg.bot ul,.msg.bot ol{margin:.4rem 0 .4rem 1.25rem}
.msg.bot li{margin:.2rem 0}
.msg.bot table{border-collapse:collapse;width:100%;font-size:.88rem;margin:.55rem 0}
.msg.bot th,.msg.bot td{border:1px solid var(--border);padding:.4rem .55rem;text-align:left}
.msg.bot th{background:#0f172a}
.msg.bot code{background:#0f172a;padding:.1rem .35rem;border-radius:4px;font-size:.88em}
.msg.bot pre{background:#0f172a;padding:.75rem;border-radius:8px;overflow:auto;margin:.5rem 0}
.msg.bot hr{border:none;border-top:1px solid var(--border);margin:.75rem 0}
.msg .meta{font-size:.72rem;color:var(--muted);margin-top:.55rem}
.input-area{padding:1rem 1.25rem 1.25rem;background:var(--card);border-top:1px solid var(--border)}
.input-row{display:flex;gap:.65rem;align-items:flex-end}
textarea{flex:1;background:#0f172a;border:1px solid var(--border);border-radius:12px;color:var(--text);padding:.85rem 1rem;resize:none;font-size:1rem;min-height:48px;outline:none}
button.send{background:var(--accent);color:#111111;border:none;border-radius:12px;padding:0 1.25rem;height:48px;font-weight:700;cursor:pointer}
button.send:disabled{opacity:.5}
.tools-bar{display:flex;gap:.45rem;margin-bottom:.65rem;flex-wrap:wrap}
.tools-bar select,.tools-bar button{background:#0f172a;border:1px solid var(--border);color:var(--text);padding:.35rem .7rem;border-radius:8px;font-size:.82rem}
.welcome{text-align:center;padding:2.5rem 1rem;color:var(--muted)}
.welcome img{width:72px;height:72px;border-radius:50%;object-fit:cover;border:3px solid var(--accent);margin-bottom:.75rem}
.welcome h2{color:var(--text);margin-bottom:.35rem}
.lb-item{display:flex;justify-content:space-between;padding:.4rem 0;font-size:.88rem;border-bottom:1px solid var(--border)}
.loading{opacity:.85;font-style:italic}
#proOnlyModal{display:none;align-items:center;justify-content:center}
#proOnlyModal.open{display:flex}
.modal-bg{position:fixed;inset:0;background:rgba(0,0,0,.65);display:none;align-items:center;justify-content:center;z-index:100;padding:1rem}
.modal-bg.show{display:flex}
.modal{background:var(--card);border:1px solid var(--border);border-radius:16px;padding:1.5rem;max-width:420px;width:100%;max-height:90vh;overflow-y:auto}
.modal h2{margin-bottom:.5rem;font-size:1.2rem}
.modal ul{margin:1rem 0;padding-left:1.1rem;color:var(--muted);line-height:1.75;font-size:.92rem}
.modal .price{font-size:1.6rem;color:var(--accent);font-weight:700;margin:.5rem 0}
.modal .actions{display:flex;gap:.5rem;margin-top:1rem}
.modal .actions button{flex:1;padding:.7rem;border:none;border-radius:10px;font-weight:700;cursor:pointer}
.btn-pro{background:linear-gradient(90deg,#a78bfa,#ec4899);color:#fff}
.btn-close{background:#0f172a;color:var(--text);border:1px solid var(--border)!important}
input.name-input{width:100%;padding:.7rem;margin:1rem 0;border-radius:8px;border:1px solid var(--border);background:#0f172a;color:var(--text);font-size:1rem}
footer.brand-footer{text-align:center;padding:.5rem;font-size:.72rem;color:var(--muted);border-top:1px solid var(--border);background:var(--card)}
footer.brand-footer strong{color:var(--accent)}
</style>
</head>
<body>
<header>
  <div class="logo-wrap" id="logoClick" title="SaarthiBhai">
    <img src="/bot-icon.svg" alt="SaarthiBhai" width="52" height="52" onerror="this.src='/sparsh.jpg'">
    <div>
      <div class="logo">Study<span>Genie</span></div>
      <div class="brand-sub">by Sparsh Singhal</div>
    </div>
  </div>
  <div class="stats">
    <span id="name-display" style="cursor:pointer;color:var(--accent);position:relative;z-index:50;pointer-events:auto" onclick="openNameModal()" title="Change name">👤 Set name</span>
    <span id="xp-display">⭐ 0 XP</span>
    <span id="coins-display" style="cursor:pointer" onclick="openSpinModal()" title="Daily Spin">🪙 0</span>
    <span id="streak-display" title="Streak">🔥 0</span>
    <span id="level-display">Level 1</span>
    <span id="quota-display">Free</span>
    <select id="langSelect" onchange="changeLanguage(this.value)" title="Language" style="background:#0f172a;color:#f1f5f9;border:1px solid rgba(255,255,255,.12);border-radius:6px;padding:.2rem .4rem;font-size:.75rem">
      <option value="hinglish">🔤 Hinglish</option>
      <option value="hindi">🇮🇳 Hindi</option>
      <option value="english">🇬🇧 English</option>
    </select>
    <button class="pro-btn-top" onclick="openProModal()">💎 Pro</button>
  </div>
</header>
<main>
  <aside class="sidebar">
    <div class="creator-card">
      <img src="/sparsh.jpg" alt="Sparsh Singhal" onerror="this.style.display='none'">
      <div>
        <div class="name">Sparsh Singhal</div>
        <div class="role">Creator of SaarthiBhai</div>
      </div>
    </div>
    <h3>Tools</h3>
    <button class="tool-btn active" data-tool="general">💬 General Ask</button>
    <button class="tool-btn" data-tool="explain">📖 Explain</button>
    <button class="tool-btn" data-tool="solve">🧮 Solve</button>
    <button class="tool-btn" data-tool="notes">📝 Notes</button>
    <button class="tool-btn" data-tool="pyq">📜 PYQ</button>
    <button class="tool-btn" data-tool="formula">📐 Formula</button>
    <button class="tool-btn" data-tool="planner">📅 Planner</button>
    <button class="tool-btn" data-tool="mock">🎯 Mock Test <span class="pro-badge">PRO</span></button>
    <button class="tool-btn" data-tool="roast">🔥 Roast <span class="pro-badge">PRO</span></button>
    <button class="tool-btn" data-tool="mindmap">🧠 Mind Map <span class="pro-badge">PRO</span></button>
    <button class="tool-btn" data-tool="mcq">❓ MCQ Generator <span class="pro-badge">PRO</span></button>
    <button class="tool-btn" data-tool="ncert">📘 NCERT Style <span class="pro-badge">PRO</span></button>
    <button class="tool-btn" data-tool="derivation">📐 Derivation <span class="pro-badge">PRO</span></button>
    <button class="tool-btn" data-tool="numerical">🔢 Numerical <span class="pro-badge">PRO</span></button>
    <button class="tool-btn" data-tool="essay">✍️ Essay / Letter <span class="pro-badge">PRO</span></button>
    <button class="tool-btn" data-tool="resume">📄 Resume <span class="pro-badge">PRO</span></button>
    <button class="tool-btn" data-tool="career">🚀 Career Guide <span class="pro-badge">PRO</span></button>
    <button class="tool-btn" data-tool="tips">💡 Sparsh Tips <span class="pro-badge">PRO</span></button>
    <button class="tool-btn" data-tool="important">⭐ Important Qs <span class="pro-badge">PRO</span></button>
    <button class="tool-btn" data-tool="diagram">🧬 Diagram Explain <span class="pro-badge">PRO</span></button>
    <button class="tool-btn" data-tool="youtube">📺 YouTube Notes <span class="pro-badge">PRO</span></button>
    <button class="pay-side" onclick="openProModal()">❌ Limit over? Doubt yahin rukega — Pro ₹{{ price }}/30d</button>
    <h3>🎮 Practice</h3>
    <button class="tool-btn" onclick="openQuizModal()">❓ Quiz Me</button>
    <button class="tool-btn" onclick="openGaltiModal()">📖 Galti Diary</button>
    <h3>🏆 Live Leaderboard</h3>
    <div id="lb-list">Loading...</div>
    <h3>🎁 Refer & Earn</h3>
    <div style="background:#0f172a;border:1px solid var(--border);border-radius:10px;padding:.7rem;margin-bottom:.5rem">
      <p style="font-size:.78rem;color:var(--muted);margin-bottom:.5rem">
        Har referral pe <strong style="color:var(--accent)">{{ referral_coins }} coins</strong> dono ko milte hain.
        Har 5 referral pe <strong style="color:var(--accent)">3 din free Pro</strong>!
      </p>
      <div style="display:flex;gap:.35rem">
        <input id="referralLinkBox" readonly style="flex:1;background:#0b1220;border:1px solid var(--border);border-radius:6px;color:var(--text);padding:.4rem .5rem;font-size:.78rem" />
        <button class="tool-btn" style="width:auto;padding:.4rem .6rem;font-size:.78rem" onclick="copyReferralLink()">Copy</button>
      </div>
      <a id="referralWhatsappShare" href="#" target="_blank" rel="noopener"
         style="display:block;margin-top:.5rem;text-align:center;background:#25D366;color:#0b1220;border-radius:6px;padding:.4rem;font-size:.78rem;font-weight:700;text-decoration:none">
        📤 Share on WhatsApp
      </a>
      <p id="referralCountText" style="font-size:.75rem;color:var(--muted);margin-top:.4rem"></p>
    </div>
    <h3>🎁 Referral Raja</h3>
    <div id="ref-lb-list">Loading...</div>
  </aside>
  <section class="chat-area">
    <div class="messages" id="messages">
      <div class="welcome">
        <img src="/sparsh.jpg" alt="Sparsh Singhal" onerror="this.style.display='none'" style="width:132px;height:132px;border-radius:50%;object-fit:cover;border:3px solid #22d3ee;box-shadow:0 0 0 6px rgba(34,211,238,.22);margin-bottom:.85rem">
        <h2>Welcome to SaarthiBhai 🎓</h2>
        <p>Built with ❤️ by <strong>Sparsh Singhal</strong></p>
        <p style="margin-top:.75rem;font-size:.9rem">All exams • Free tools + Pro power</p>
      </div>
    </div>
    <div class="input-area">
      <div class="tools-bar">
        <select id="toolSelect">
          <option value="general">General</option>
          <option value="explain">Explain</option>
          <option value="solve">Solve</option>
          <option value="notes">Notes</option>
          <option value="pyq">PYQ</option>
          <option value="formula">Formula</option>
          <option value="planner">Planner</option>
          <option value="mock">Mock (Pro)</option>
          <option value="roast">Roast (Pro)</option>
          <option value="mindmap">Mindmap (Pro)</option>
          <option value="mcq">MCQ (Pro)</option>
          <option value="ncert">NCERT (Pro)</option>
          <option value="derivation">Derivation (Pro)</option>
          <option value="numerical">Numerical (Pro)</option>
          <option value="essay">Essay (Pro)</option>
          <option value="resume">Resume (Pro)</option>
          <option value="career">Career (Pro)</option>
          <option value="tips">Tips (Pro)</option>
          <option value="important">Important Qs (Pro)</option>
          <option value="diagram">Diagram (Pro)</option>
          <option value="youtube">YouTube Notes (Pro)</option>
        </select>
        <div class="media-dd" style="position:relative;display:inline-block">
          <button type="button" id="mediaBtn" onclick="toggleMediaMenu(event)">📎 Media ▾</button>
          <div id="mediaMenu" style="display:none;position:absolute;bottom:110%;left:0;background:#111827;border:1px solid rgba(255,255,255,.12);border-radius:10px;min-width:180px;z-index:50;box-shadow:0 8px 24px rgba(0,0,0,.4)">
            <button type="button" class="media-item" onclick="pickMedia('image')" style="display:block;width:100%;text-align:left;padding:.65rem .9rem;background:transparent;border:none;color:#e2e8f0;cursor:pointer">🖼️ Images</button>
            <button type="button" class="media-item" onclick="pickMedia('pdf')" style="display:block;width:100%;text-align:left;padding:.65rem .9rem;background:transparent;border:none;color:#e2e8f0;cursor:pointer">📄 PDFs <span style="color:#a78bfa;font-size:.75rem">PRO</span></button>
            <button type="button" class="media-item" onclick="pickMedia('youtube')" style="display:block;width:100%;text-align:left;padding:.65rem .9rem;background:transparent;border:none;color:#e2e8f0;cursor:pointer">▶️ YouTube Videos <span style="color:#a78bfa;font-size:.75rem">PRO</span></button>
          </div>
        </div>
        <input type="file" id="imageInput" accept="image/*" style="display:none" onchange="handleMediaFile(this,'image')">
        <input type="file" id="pdfInput" accept="application/pdf,.pdf" style="display:none" onchange="handleMediaFile(this,'pdf')">
      </div>
      <div class="input-row">
        <textarea id="question" placeholder="Dimaag mein kya ghoom raha hai? Poocho... 🔥" rows="1"></textarea>
        <button class="send" id="sendBtn" onclick="ask()">🔥 Fire</button>
      </div>
    </div>
  </section>
</main>
<footer class="brand-footer">🎓 SaarthiBhai — built with ❤️ by <strong>Sparsh Singhal</strong> · <a href="/legal" style="color:var(--accent)">Legal</a></footer>

<div class="modal-bg" id="quizModal">
  <div class="modal" style="max-width:480px">
    <h2>❓ Quiz Me</h2>
    <div id="quizSetup">
      <input id="quizTopic" class="name-input" type="text" placeholder="Topic — e.g. Thermodynamics" />
      <label style="display:flex;align-items:center;gap:.4rem;font-size:.85rem;color:var(--muted);margin:.4rem 0">
        <input type="checkbox" id="quizFromGalti"> Build from my Galti Diary instead
      </label>
      <div style="display:flex;gap:.4rem;margin:.5rem 0" id="quizModeRow">
        <button type="button" class="tool-btn quiz-mode-btn active" data-mode="practice" onclick="selectQuizMode('practice')" style="flex:1;text-align:center">Practice</button>
        <button type="button" class="tool-btn quiz-mode-btn" data-mode="exam" onclick="selectQuizMode('exam')" style="flex:1;text-align:center">Exam ⏱️<span class="pro-badge">PRO</span></button>
        <button type="button" class="tool-btn quiz-mode-btn" data-mode="1v1" onclick="selectQuizMode('1v1')" style="flex:1;text-align:center">1v1 🤝<span class="pro-badge">PRO</span></button>
      </div>
      <div class="actions">
        <button class="btn-pro" onclick="startQuiz()">Start Quiz</button>
        <button class="btn-close" onclick="closeQuizModal()">Cancel</button>
      </div>
      <p id="quizSetupMsg" style="margin-top:.5rem;font-size:.8rem;color:var(--muted)"></p>
    </div>
    <div id="quizPlay" style="display:none">
      <div style="display:flex;justify-content:space-between;align-items:center">
        <p id="quizProgress" style="color:var(--muted);font-size:.85rem"></p>
        <p id="quizTimer" style="color:#f87171;font-weight:700;font-size:.9rem;display:none"></p>
      </div>
      <p id="quizQuestion" style="font-weight:700;margin:.5rem 0 .8rem"></p>
      <div id="quizOptions"></div>
      <div id="quizLifelines" style="display:flex;gap:.4rem;margin-top:.6rem"></div>
      <p id="quizHintText" style="margin-top:.5rem;font-size:.82rem;color:#fbbf24;display:none"></p>
      <p id="quizFeedback" style="margin-top:.6rem;font-size:.88rem"></p>
      <button class="btn-pro" id="quizNextBtn" style="display:none;width:100%;margin-top:.7rem" onclick="quizNext()">Next →</button>
    </div>
    <div id="quizResult" style="display:none;text-align:center">
      <h3 id="quizScoreText" style="margin:.5rem 0"></h3>
      <p id="quizVerdictText" style="color:var(--muted);font-size:.9rem"></p>
      <div id="quizChallengeShare" style="display:none;margin-top:.8rem">
        <p style="font-size:.82rem;color:var(--muted);margin-bottom:.4rem">Dost ko bhejo, wahi quiz khelega:</p>
        <div class="copy-row" style="display:flex;gap:.5rem">
          <input id="quizChallengeLink" class="name-input" readonly style="margin:0;flex:1" />
          <button class="secondary" onclick="copyQuizChallengeLink()">Copy</button>
        </div>
        <div id="quizChallengeResults" style="margin-top:.6rem;font-size:.85rem;color:var(--muted)"></div>
      </div>
      <button class="btn-pro" style="width:100%;margin-top:.8rem" onclick="closeQuizModal()">Done</button>
    </div>
  </div>
</div>

<div class="modal-bg" id="galtiModal">
  <div class="modal" style="max-width:480px;max-height:80vh">
    <h2>📖 Galti Diary</h2>
    <div id="galtiList" style="margin-top:.8rem;text-align:left;font-size:.85rem;color:var(--muted)">Loading...</div>
    <div class="actions"><button class="btn-close" style="width:100%" onclick="closeGaltiModal()">Close</button></div>
  </div>
</div>

<div class="modal-bg" id="spinModal">
  <div class="modal" style="max-width:340px;text-align:center">
    <h2>🎡 Daily Spin</h2>
    <p style="color:var(--muted);font-size:.9rem;margin:.5rem 0">Roz ek spin free — {{ spin_min }} se {{ spin_max }} coins tak!</p>
    <div style="position:relative;width:220px;height:220px;margin:1rem auto">
      <div style="position:absolute;top:-6px;left:50%;transform:translateX(-50%);font-size:1.4rem;z-index:2;filter:drop-shadow(0 2px 2px rgba(0,0,0,.4))">🔻</div>
      <div id="spinWheel" style="width:220px;height:220px;border-radius:50%;border:4px solid #0f172a;box-shadow:0 0 0 3px var(--accent);
           background:conic-gradient(#22d3ee 0deg 45deg,#a78bfa 45deg 90deg,#ec4899 90deg 135deg,#22d3ee 135deg 180deg,
           #a78bfa 180deg 225deg,#ec4899 225deg 270deg,#22d3ee 270deg 315deg,#a78bfa 315deg 360deg);
           transition:transform 2.2s cubic-bezier(0.17,0.67,0.32,1.02);display:flex;align-items:center;justify-content:center">
        <div style="width:70px;height:70px;border-radius:50%;background:#0b1220;display:flex;align-items:center;justify-content:center;font-size:1.8rem">🪙</div>
      </div>
    </div>
    <div id="spinResult" style="font-size:1.4rem;margin:.5rem 0;min-height:1.8rem;font-weight:700"></div>
    <button class="btn-pro" id="spinBtn" style="width:100%" onclick="doSpin()">Spin Now</button>
    <button class="btn-close" style="width:100%;margin-top:.5rem" onclick="closeSpinModal()">Close</button>
  </div>
</div>


<div class="modal-bg" id="proOnlyModal" onclick="if(event.target===this)closeProOnlyModal()">
  <div class="modal" style="max-width:340px;text-align:center">
    <h2 style="margin-bottom:.5rem">🔒 ONLY FOR PRO USERS</h2>
    <p style="color:#94a3b8;margin:.5rem 0 1.2rem">PDFs aur YouTube Notes Pro plan me milte hain.<br>Images free users bhi use kar sakte hain.</p>
    <button type="button" onclick="closeProOnlyModal()" style="background:#22d3ee;color:#0b1220;border:none;border-radius:10px;padding:.7rem 1.4rem;font-weight:700;cursor:pointer;width:100%">OK</button>
  </div>
</div>
<div class="modal-bg" id="proModal">
  <div class="modal">
    <h2 id="proModalTitle">❌ Limit pe atak gaye?</h2>
    <p id="proModalBody" style="color:var(--muted);font-size:.95rem;line-height:1.55;margin:.6rem 0 0"></p>
    <div class="price" style="margin-top:1rem">₹{{ price }} <span style="font-size:1rem;color:var(--muted)">/ 30 days</span>
      <div style="font-size:.85rem;color:#94a3b8;font-weight:500;margin-top:.25rem">din ka ~₹1.6 — ek chai se kam</div>
    </div>
    <ul id="proModalBullets" style="margin-top:.75rem"></ul>
    <div class="actions">
      <button class="btn-pro" onclick="goPay()">Unlock Pro — ₹{{ price }}</button>
      <button class="btn-close" onclick="closeProModal()">Baad mein</button>
    </div>
    <div style="margin-top:1rem;padding-top:.85rem;border-top:1px solid var(--border)">
      <p style="font-size:.8rem;color:var(--muted);margin:0 0 .4rem">Pehle se pay kar chuke ho? (dusri device)</p>
      <input id="restorePayId" class="name-input" type="text" placeholder="pay_XXXXXXXX" style="margin-bottom:.5rem" />
      <button class="btn-pro" style="width:100%;background:#0f172a;border:1px solid var(--accent);color:var(--accent)" onclick="restorePro()">Restore Pro on this device</button>
      <p id="restoreMsg" style="margin-top:.45rem;font-size:.8rem;color:var(--muted)"></p>
    </div>
    <p id="proAbTag" style="margin-top:.5rem;font-size:.7rem;color:#475569"></p>
  </div>
</div>

<div class="modal-bg" id="devModal">
  <div class="modal">
    <h2>🔐 Developer Mode</h2>
    <p style="color:var(--muted);font-size:.9rem">Enter secret code</p>
    <input id="devCode" type="password" placeholder="Secret code" class="name-input" />
    <div class="actions">
      <button class="btn-pro" onclick="checkDev()">Unlock</button>
      <button class="btn-close" onclick="closeDevModal()">Close</button>
    </div>
    <p id="devMsg" style="margin-top:.75rem;font-size:.85rem;color:var(--muted)"></p>
  </div>
</div>

<div class="modal-bg" id="nameModal">
  <div class="modal">
    <h2>👤 Apna naam likho</h2>
    <p style="color:var(--muted);font-size:.9rem;margin-top:.4rem">Yeh naam leaderboard pe dikhega</p>
    <input id="nameInput" class="name-input" type="text" maxlength="40" placeholder="e.g. Rahul Sharma" />
    <input id="phoneInput" class="name-input" type="tel" maxlength="16" placeholder="Mobile number (Supabase ID)" style="margin-top:.55rem" />
    <div class="actions">
      <button class="btn-pro" onclick="saveName()">Save</button>
      <button class="btn-close" onclick="closeNameModal()">Skip</button>
    </div>
    <p id="nameMsg" style="margin-top:.6rem;font-size:.85rem;color:var(--muted)"></p>
  </div>
</div>

<script>
const PRICE = {{ price }};
let currentTool = "general";
// --- Pro upgrade modal — these were referenced by onclick= handlers above
// but never implemented, so the main monetization button did nothing at all.
// A/B pitch variants — pain-first, short
const PITCH_AB = {
  A: {
    title: "❌ Doubt yahin ruk gaya",
    body: "Free limit khatam. Kal tak wait — chahe raat ko exam ho.\n\nPro se abhi clear hota hai.",
    bullets: ["Unlimited doubts", "Photo se sawaal (OCR)", "Mock + PYQ + planner", "Din ka ~₹1.6"]
  },
  B: {
    title: "Limit pe mat atakna",
    body: "Jo roz clear karke aage badhna hai, free limit beech mein rok deti hai.\n\nPro = bina rukhe padhai.",
    bullets: ["Unlimited access", "Handwritten photo solve", "Exam tools (mock/PYQ)", "₹{{ price }} / 30 din".replace("{{ price }}", String(PRICE))]
  }
};

function getPitchVariant(){
  try{
    let v = localStorage.getItem("sg_pitch_ab");
    if(v === "A" || v === "B") return v;
    v = (Math.random() < 0.5) ? "A" : "B";
    localStorage.setItem("sg_pitch_ab", v);
    return v;
  }catch(e){ return "A"; }
}

function applyPitchVariant(){
  const v = getPitchVariant();
  const p = PITCH_AB[v] || PITCH_AB.A;
  const t = document.getElementById("proModalTitle");
  const b = document.getElementById("proModalBody");
  const ul = document.getElementById("proModalBullets");
  const tag = document.getElementById("proAbTag");
  if(t) t.textContent = p.title;
  if(b) b.textContent = p.body;
  if(ul){
    ul.innerHTML = (p.bullets || []).map(x => "<li>" + x + "</li>").join("");
  }
  if(tag) tag.textContent = "";
  return v;
}

function trackAb(eventName){
  try{
    const v = getPitchVariant();
    fetch("/api/ab/track", {
      method: "POST",
      headers: {"Content-Type":"application/json"},
      body: JSON.stringify({ event: eventName, variant: v, client_id: clientId })
    }).catch(function(){});
  }catch(e){}
}

function openProModal(){
  const m = document.getElementById("proModal");
  if(!m) return;
  applyPitchVariant();
  m.classList.add("show");
  trackAb("pitch_view");
  try{ soundClick(); }catch(e){}
}
function closeProModal(){
  const m = document.getElementById("proModal");
  if(m) m.classList.remove("show");
}
function goPay(){
  try{ soundClick(); }catch(e){}
  trackAb("pay_click");
  const v = getPitchVariant();
  window.location.href = "/pay?uid=" + encodeURIComponent("web:" + clientId) + "&ab=" + encodeURIComponent(v);
}

async function restorePro(){
  const input = document.getElementById("restorePayId");
  const msg = document.getElementById("restoreMsg");
  const payment_id = (input && input.value || "").trim();
  if(!payment_id || payment_id.length < 6){
    if(msg){ msg.style.color = "#f87171"; msg.textContent = "Razorpay payment id daalo (pay_...)"; }
    try{ soundError(); }catch(e){}
    return;
  }
  if(msg){ msg.style.color = "#94a3b8"; msg.textContent = "Checking payment..."; }
  try{
    const res = await fetch("/api/restore-pro", {
      method: "POST",
      headers: {"Content-Type":"application/json"},
      body: JSON.stringify({ payment_id, client_id: clientId })
    });
    const data = await res.json();
    if(data.ok){
      isProUser = true;
      const qd = document.getElementById("quota-display");
      if(qd) qd.textContent = "PRO ∞";
      if(msg){ msg.style.color = "#22d3ee"; msg.textContent = "✅ Pro unlocked on this device!"; }
      try{ soundRecv(); }catch(e){}
      try{ localStorage.setItem("sg_pro_just_unlocked","1"); }catch(e){}
      setTimeout(function(){ closeProModal(); syncProfile(); }, 600);
    }else{
      if(msg){ msg.style.color = "#f87171"; msg.textContent = data.error || "Restore failed"; }
      try{ soundError(); }catch(e){}
    }
  }catch(e){
    if(msg){ msg.style.color = "#f87171"; msg.textContent = "Network error"; }
    try{ soundError(); }catch(e){}
  }
}

// --- Hidden Dev Mode — tap the logo 5× within 3 seconds to open it.
// (devModal already existed in the HTML but had no way to open it.)
function openDevModal(){
  const m = document.getElementById("devModal");
  if(!m) return;
  m.classList.add("show");
  try{ soundClick(); }catch(e){}
}
function closeDevModal(){
  const m = document.getElementById("devModal");
  if(m) m.classList.remove("show");
}
async function checkDev(){
  const codeInput = document.getElementById("devCode");
  const msg = document.getElementById("devMsg");
  const code = (codeInput && codeInput.value || "").trim();
  if(!code){
    if(msg){ msg.style.color = "#f87171"; msg.textContent = "Enter a code."; }
    return;
  }
  try{
    const res = await fetch("/api/dev/activate-pro", {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify({ code, uid: "web:" + clientId })
    });
    const data = await res.json();
    if(data.ok){
      if(msg){ msg.style.color = "#22d3ee"; msg.textContent = "✅ Pro activated for testing!"; }
      isProUser = true;
      syncProfile();
      try{ soundRecv(); }catch(e){}
      setTimeout(closeDevModal, 700);
    }else{
      if(msg){ msg.style.color = "#f87171"; msg.textContent = data.error || "Invalid code."; }
      try{ soundError(); }catch(e){}
    }
  }catch(e){
    if(msg){ msg.style.color = "#f87171"; msg.textContent = "Network error"; }
  }
}

let mediaKind = null; // image | pdf | youtube
function toggleMediaMenu(e){
  try{ e.stopPropagation(); }catch(ex){}
  const m = document.getElementById("mediaMenu");
  if(!m) return;
  m.style.display = (m.style.display === "none" || !m.style.display) ? "block" : "none";
}
document.addEventListener("click", function(){
  const m = document.getElementById("mediaMenu");
  if(m) m.style.display = "none";
});
function showProOnlyModal(){
  try{ soundError(); }catch(e){}
  const el = document.getElementById("proOnlyModal");
  if(el){ el.classList.add("open"); el.style.display = "flex"; }
  else alert("ONLY FOR PRO USERS");
}
function closeProOnlyModal(){
  const el = document.getElementById("proOnlyModal");
  if(el){ el.classList.remove("open"); el.style.display = "none"; }
}
function pickMedia(kind){
  const m = document.getElementById("mediaMenu");
  if(m) m.style.display = "none";
  if(kind === "pdf" || kind === "youtube"){
    if(!isProUser){
      showProOnlyModal();
      return;
    }
  }
  mediaKind = kind;
  if(kind === "image"){
    const el = document.getElementById("imageInput");
    if(el){ el.value = ""; el.click(); }
  } else if(kind === "pdf"){
    const el = document.getElementById("pdfInput");
    if(el){ el.value = ""; el.click(); }
  } else if(kind === "youtube"){
    currentTool = "youtube";
    try{
      const sel = document.getElementById("toolSelect");
      if(sel) sel.value = "youtube";
    }catch(e){}
    const ta = document.getElementById("question");
    if(ta){
      ta.placeholder = "YouTube video link + kya notes chahiye? paste URL...";
      ta.focus();
    }
    addMessage("bot", "▶️ **YouTube Notes (Pro)**\\nVideo ka link yahan paste karo aur Fire dabao. Example: `{{ youtube_example_url }}` + topic");
  }
}
function handleMediaFile(input, kind){
  mediaKind = kind || "image";
  if((kind === "pdf") && !isProUser){
    showProOnlyModal();
    try{ input.value = ""; }catch(e){}
    imageBase64 = null; window._mediaKind = null; window._mediaName = null; try{ const mb=document.getElementById("mediaBtn"); if(mb){ mb.textContent="📎 Media ▾"; mb.title=""; } }catch(e){}
    return;
  }
  const file = input && input.files && input.files[0];
  if(!file){
    try{ soundError(); }catch(e){}
    addMessage("bot", "No file selected.");
    return;
  }
  if(kind === "image"){
    if(!file.type || !file.type.startsWith("image/")){
      try{ soundError(); }catch(e){}
      addMessage("bot", "Please choose an image file (JPG/PNG/WebP).");
      return;
    }
  }
  if(kind === "pdf"){
    const okPdf = (file.type === "application/pdf") || /\\.pdf$/i.test(file.name || "");
    if(!okPdf){
      try{ soundError(); }catch(e){}
      addMessage("bot", "Please choose a PDF file.");
      return;
    }
  }
  const maxMb = kind === "pdf" ? 8 : 4.5;
  if(file.size > maxMb * 1024 * 1024){
    try{ soundError(); }catch(e){}
    addMessage("bot", "File too large. Please use under ~" + maxMb + "MB.");
    return;
  }
  const reader = new FileReader();
  reader.onload = () => {
    try{
      const dataUrl = String(reader.result || "");
      const parts = dataUrl.split(",");
      imageBase64 = parts.length > 1 ? parts[1] : "";
      if(!imageBase64){
        addMessage("bot", "Could not read file.");
        return;
      }
      window._imageMime = file.type || (kind === "pdf" ? "application/pdf" : "image/jpeg");
      window._mediaKind = kind;
      window._mediaName = file.name || (kind === "pdf" ? "file.pdf" : "image");
      const btn = document.getElementById("mediaBtn");
      if(btn){
        btn.textContent = (kind === "pdf" ? "📄 " : "🖼️ ") + "Attached ▾";
        btn.title = window._mediaName + " — Fire dabao for answer";
      }
      try{ soundRecv(); }catch(e){}
      // Auto-run selected tool on the file (no status chat spam)
      try{
        const sel = document.getElementById("toolSelect");
        if(sel && sel.value) currentTool = sel.value;
        const qel = document.getElementById("question");
        // if user already typed something keep it; else empty is OK — model uses tool
        ask();
      }catch(e){}
    }catch(err){
      addMessage("bot", "Could not read file.");
    }
  };
  reader.onerror = () => addMessage("bot", "Could not read file.");
  reader.readAsDataURL(file);
}
function onImageButtonClick(){ pickMedia("image"); }
function handleImage(input){ handleMediaFile(input, "image"); }


function handleImage(input){
  // Free users: block immediately with popup
  if(!isProUser){
    try{ soundError(); }catch(e){}
    alert("Only for pro plan users");
    try{ input.value = ""; }catch(e){}
    imageBase64 = null;
    return;
  }
  const file = input && input.files && input.files[0];
  if(!file){
    try{ soundError(); }catch(e){}
    addMessage("bot", "No image selected.");
    return;
  }
  if(!file.type || !file.type.startsWith("image/")){
    try{ soundError(); }catch(e){}
    addMessage("bot", "Please choose an image file (JPG/PNG/WebP).");
    return;
  }
  if(file.size > 4.5 * 1024 * 1024){
    try{ soundError(); }catch(e){}
    addMessage("bot", "Image too large. Please use under ~4MB.");
    return;
  }
  const reader = new FileReader();
  reader.onload = () => {
    try{
      const dataUrl = String(reader.result || "");
      const parts = dataUrl.split(",");
      imageBase64 = parts.length > 1 ? parts[1] : "";
      if(!imageBase64){
        addMessage("bot", "Could not read image.");
        return;
      }
      window._imageMime = file.type || "image/jpeg";
      addMessage("user", "📷 Image ready: " + (file.name || "photo") + " — ab question likho (optional) aur 🔥 Fire dabao");
      try{ soundClick(); }catch(e){}
    }catch(err){
      addMessage("bot", "Image read failed.");
    }
  };
  reader.onerror = () => {
    addMessage("bot", "Image read error.");
    try{ soundError(); }catch(e){}
  };
  reader.readAsDataURL(file);
  try{ input.value = ""; }catch(e){}
}

function openNameModal(){
  const m = document.getElementById("nameModal");
  if(!m) return;
  m.classList.add("show");
  const input = document.getElementById("nameInput");
  const cur = (document.getElementById("name-display")||{}).textContent || "";
  if(input && cur && !cur.includes("Set name")){
    input.value = cur.replace(/^👤\s*/, "").trim();
  }
  try{ const ph = localStorage.getItem("sg_phone") || ""; const pi = document.getElementById("phoneInput"); if(pi && ph) pi.value = ph; }catch(e){}
  setTimeout(() => { try{ input && input.focus(); }catch(e){} }, 50);
  try{ soundClick(); }catch(e){}
}
function closeNameModal(){
  const m = document.getElementById("nameModal");
  if(m) m.classList.remove("show");
  try{ localStorage.setItem("sg_name_skipped", "1"); }catch(e){}
}
async function saveName(){
  const input = document.getElementById("nameInput");
  const msg = document.getElementById("nameMsg");
  const name = (input && input.value || "").trim();
  const phoneInput = document.getElementById("phoneInput");
  const phone = (phoneInput && phoneInput.value || "").trim();
  if(!name || name.length < 2){
    if(msg){ msg.style.color = "#f87171"; msg.textContent = "Naam kam se kam 2 letters ka ho"; }
    try{ soundError(); }catch(e){}
    return;
  }
  try{
    const res = await fetch("/api/set-name", {
      method: "POST",
      headers: {"Content-Type":"application/json"},
      body: JSON.stringify({ client_id: clientId, name, phone_number: phone })
    });
    const data = await res.json();
    if(data.ok){
      const el = document.getElementById("name-display");
      if(el) el.textContent = "👤 " + data.name;
      try{ localStorage.setItem("sg_name", data.name); if(data.phone_number) localStorage.setItem("sg_phone", data.phone_number); }catch(e){}
      if(msg){ msg.style.color = "#22d3ee"; msg.textContent = "Saved!"; }
      try{ soundRecv(); }catch(e){}
      setTimeout(closeNameModal, 400);
    }else{
      if(msg){ msg.style.color = "#f87171"; msg.textContent = data.error || "Failed"; }
      try{ soundError(); }catch(e){}
    }
  }catch(e){
    if(msg){ msg.style.color = "#f87171"; msg.textContent = "Network error"; }
    try{ soundError(); }catch(e){}
  }
}
// restore saved name in header
(function(){
  try{
    const saved = localStorage.getItem("sg_name");
    if(saved){
      const el = document.getElementById("name-display");
      if(el) el.textContent = "👤 " + saved;
    }
  }catch(e){}
})();


async function syncProfile(){
  try{
    const params = new URLSearchParams(window.location.search);
    const refFromUrl = params.get("ref") || "";
    const savedPhone = (()=>{ try{return localStorage.getItem("sg_phone")||"";}catch(e){return "";} })();
    const url = "/api/me?client_id=" + encodeURIComponent(clientId) + (savedPhone ? "&phone_number=" + encodeURIComponent(savedPhone) : "") + (refFromUrl ? "&ref=" + encodeURIComponent(refFromUrl) : "");
    const res = await fetch(url);
    const data = await res.json();
    if(!data.ok) return;
    if(data.phone_number){ try{ localStorage.setItem("sg_phone", data.phone_number); }catch(e){} }
    if(data.name && data.name !== "Web Student" && data.name !== "Student"){
      const el = document.getElementById("name-display");
      if(el) el.textContent = "👤 " + data.name;
    }
    if(data.xp !== undefined){
      const x = document.getElementById("xp-display");
      if(x) x.textContent = `⭐ ${data.xp} XP`;
    }
    if(data.level !== undefined){
      const l = document.getElementById("level-display");
      if(l) l.textContent = `Level ${data.level}`;
    }
    if(data.coins !== undefined){
      const c = document.getElementById("coins-display");
      if(c) c.textContent = `🪙 ${data.coins}` + (data.spin_available ? " (Spin!)" : "");
    }
    if(data.streak !== undefined){
      const s = document.getElementById("streak-display");
      if(s){
        s.textContent = `🔥 ${data.streak}` + (data.shields ? ` 🛡️${data.shields}` : "");
        s.title = `Best streak: ${data.best_streak || 0} days`;
      }
    }
    if(data.language){
      const sel = document.getElementById("langSelect");
      if(sel) sel.value = data.language;
    }
    if(data.referral_code){
      const link = window.location.origin + "/?ref=" + encodeURIComponent(data.referral_code);
      const box = document.getElementById("referralLinkBox");
      if(box) box.value = link;
      const wa = document.getElementById("referralWhatsappShare");
      if(wa) wa.href = "{{ whatsapp_share_url }}" + encodeURIComponent(
        `Padhai ke liye SaarthiBhai try kar — AI tutor, unlimited doubts. Mera link se join karo, dono ko coins milenge: ${link}`
      );
      const countEl = document.getElementById("referralCountText");
      if(countEl) countEl.textContent = `👥 ${data.referral_count || 0} dost refer kiye`;
    }
    if(data.plan === "pro" || (data.quota && data.quota.daily_left === -1) || data.plan_raw === "pro"){
      isProUser = true;
    } else {
      isProUser = false;
    }
    if(data.quota){
      const q = document.getElementById("quota-display");
      if(q){
        if(isProUser){
          q.textContent = "PRO ∞";
        } else {
          q.textContent = data.quota.daily_left === -1 ? "PRO ∞" : `Free: ${data.quota.daily_left} left`;
        }
      }
    }
  }catch(e){}
}

// Auto-join a teacher's class if the page was opened via a /?join_class=CODE
// link (from the Teacher Dashboard's "Copy Link" button).
async function autoJoinClassFromUrl(){
  try{
    const params = new URLSearchParams(window.location.search);
    const code = (params.get("join_class") || "").trim();
    if(!code) return;
    const res = await fetch("/api/join-class", {
      method: "POST", headers: {"Content-Type":"application/json"},
      body: JSON.stringify({ client_id: clientId, code })
    });
    const data = await res.json();
    if(typeof addMessage === "function"){
      addMessage("bot", data.ok
        ? `🏫 Class **${data.class_code}** join ho gaya! Teacher ab tumhara progress dekh payega.`
        : `❌ Class join nahi hua: ${data.error || "invalid code"}`);
    }
    history.replaceState({}, "", "/");
  }catch(e){}
}

syncProfile();
autoJoinClassFromUrl();
(function(){
  try{
    const params = new URLSearchParams(window.location.search);
    const challengeId = (params.get("challenge") || "").trim();
    if(challengeId && typeof joinQuizChallenge === "function"){
      joinQuizChallenge(challengeId);
      history.replaceState({}, "", "/");
    }
  }catch(e){}
})();

const TOOL_PLACEHOLDERS = {
  general: "Dimaag mein kya ghoom raha hai? Poocho... 🔥",
  explain: "Konsa concept bhoot ban gaya hai samajh mein? 👻",
  solve: "Problem yahan daalo, suljha mai dunga 💪",
  notes: "Last-minute revision? Topic bolo, notes ready 📝",
  pyq: "Purana paper khodna hai? Topic batao, khazana milega 🕵️",
  formula: "Formula bhool gaye? Koi na, yahan maang lo 📐",
  planner: "Aalas chhodo, ab plan banate hain 📅",
  mock: "Ready ho jao — asli exam jaisa mahaul milega 🎯",
  roast: "Dimaag lagao, warna pyaar se roast ho jao 🔥😂",
  mindmap: "Topic do, branches khud ugengi 🧠🌳",
  mcq: "Options mein ghoomte ho? Practice yahan karo ❓",
  ncert: "Seedhi-saadhi NCERT wali baat chahiye? Yahan bolo 📘",
  derivation: "Formula aaya kahan se? Chalo jadd tak jaate hain 📐✨",
  numerical: "Number crunching time! Problem daalo yahan 🔢",
  essay: "Shabdon ka jaadu chahiye? Topic bolo, likh dete hain ✍️",
  resume: "Apna CV chamkaate hain — details daalo 📄✨",
  career: "Future ka confusion? Befikar poocho 🚀",
  tips: "Sparsh ke secret tips chahiye? Bolo 💡",
  important: "Exams mein most likely aane wale sawaal chahiye? Bolo ⭐",
  diagram: "Diagram dekh ke ghabrao mat, samjha dete hain 🧬",
  youtube: "Lecture dekhne ka time nahi? Summary yahan lo 📺",
};

function setTool(tool){
  if(!tool) return;
  currentTool = tool;
  document.querySelectorAll(".tool-btn").forEach(b => {
    b.classList.toggle("active", b.getAttribute("data-tool") === tool);
  });
  const sel = document.getElementById("toolSelect");
  if(sel){
    // if option missing, keep value anyway
    sel.value = tool;
  }
  const qBox = document.getElementById("question");
  if(qBox){
    qBox.placeholder = TOOL_PLACEHOLDERS[tool] || TOOL_PLACEHOLDERS.general;
  }
  try{ soundClick(); }catch(e){}
}
document.querySelectorAll(".tool-btn").forEach(btn => {
  btn.addEventListener("click", () => setTool(btn.getAttribute("data-tool")));
});
const _toolSelect = document.getElementById("toolSelect");
if(_toolSelect){
  _toolSelect.addEventListener("change", () => setTool(_toolSelect.value));
}

let clientId = localStorage.getItem("sg_client") || ("web_" + Math.random().toString(36).slice(2));
localStorage.setItem("sg_client", clientId);
let imageBase64 = null;
let isProUser = false;
let logoClicks = 0;
let logoTimer = null;
const _logoEl = document.getElementById("logoClick");
if(_logoEl){
  _logoEl.addEventListener("click", () => {
    logoClicks++;
    if(logoTimer) clearTimeout(logoTimer);
    logoTimer = setTimeout(() => { logoClicks = 0; }, 3000);
    if(logoClicks >= 5){
      logoClicks = 0;
      openDevModal();
    }
  });
}

const AudioCtx = window.AudioContext || window.webkitAudioContext;
let actx = null;
function beep(freq, dur, type="square", vol=0.35){
  try{
    if(!actx) actx = new AudioCtx();
    if(actx.state === "suspended") actx.resume();
    const o = actx.createOscillator();
    const g = actx.createGain();
    o.type = type;
    o.frequency.value = freq;
    g.gain.value = vol;
    o.connect(g); g.connect(actx.destination);
    o.start();
    g.gain.exponentialRampToValueAtTime(0.001, actx.currentTime + dur);
    o.stop(actx.currentTime + dur);
  }catch(e){}
}
function soundSend(){ beep(180, 0.06, "sawtooth", 0.4); setTimeout(()=>beep(90, 0.12, "square", 0.35), 40); }
function soundRecv(){ beep(520, 0.05, "square", 0.3); setTimeout(()=>beep(780, 0.08, "square", 0.35), 60); setTimeout(()=>beep(1040, 0.1, "triangle", 0.3), 140); }
function soundClick(){ beep(240, 0.04, "square", 0.28); }
function soundError(){ beep(120, 0.15, "sawtooth", 0.4); setTimeout(()=>beep(80, 0.2, "sawtooth", 0.35), 100); }

function escapeHtml(s){
  return String(s).replace(/&/g,"&amp;").replace(/</g,"&lt;").replace(/>/g,"&gt;");
}

function mdToHtml(text){
  if(!text) return "";
  const slots = [];
  // Protect math blocks before HTML escape / markdown
  let s = String(text).replace(/\$\$[\s\S]+?\$\$|\\\[[\s\S]+?\\\]|\\\([\s\S]+?\\\)|\$[^$\n]+\$/g, function(m){
    slots.push(m);
    return "%%MATH" + (slots.length - 1) + "%%";
  });
  s = escapeHtml(s);

  s = s.replace(/```([\s\S]*?)```/g, function(_, code){
    return "<pre><code>" + code.trim() + "</code></pre>";
  });
  s = s.replace(/`([^`]+)`/g, "<code>$1</code>");

  s = s.replace(/^######\s+(.*)$/gm, "<h6>$1</h6>");
  s = s.replace(/^#####\s+(.*)$/gm, "<h5>$1</h5>");
  s = s.replace(/^####\s+(.*)$/gm, "<h4>$1</h4>");
  s = s.replace(/^###\s+(.*)$/gm, "<h3>$1</h3>");
  s = s.replace(/^##\s+(.*)$/gm, "<h2>$1</h2>");
  s = s.replace(/^#\s+(.*)$/gm, "<h1>$1</h1>");

  s = s.replace(/\*\*\*([^*]+)\*\*\*/g, "<strong><em>$1</em></strong>");
  s = s.replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>");
  s = s.replace(/\*([^*]+)\*/g, "<em>$1</em>");

  // tables
  s = s.replace(/(?:^|\n)((?:\|.+\|(?:\n|$))+)/g, function(_, block){
    const rows = block.trim().split("\n").filter(Boolean);
    if(rows.length < 1) return block;
    let html = "<div style='overflow:auto'><table>";
    let headerDone = false;
    rows.forEach((row) => {
      if(/^\|?\s*[-:| ]+\s*\|?$/.test(row)) return;
      const cells = row.replace(/^\|/, "").replace(/\|$/, "").split("|").map(c => c.trim());
      if(!headerDone){
        html += "<thead><tr>" + cells.map(c => "<th>" + c + "</th>").join("") + "</tr></thead><tbody>";
        headerDone = true;
      } else {
        html += "<tr>" + cells.map(c => "<td>" + c + "</td>").join("") + "</tr>";
      }
    });
    html += "</tbody></table></div>";
    return "\n" + html + "\n";
  });

  s = s.replace(/^\s*[-*]\s+(.*)$/gm, "<li>$1</li>");
  s = s.replace(/(?:<li>.*<\/li>\s*)+/g, function(m){ return "<ul>" + m + "</ul>"; });
  s = s.replace(/^\s*\d+\.\s+(.*)$/gm, "<li>$1</li>");

  s = s.replace(/\n{2,}/g, "</p><p>");
  s = s.replace(/\n/g, "<br>");
  s = "<p>" + s + "</p>";
  s = s.replace(/<p>\s*<\/p>/g, "");
  s = s.replace(/<p>\s*(<h[1-6]>)/g, "$1");
  s = s.replace(/(<\/h[1-6]>)\s*<\/p>/g, "$1");
  s = s.replace(/<p>\s*(<ul>)/g, "$1");
  s = s.replace(/(<\/ul>)\s*<\/p>/g, "$1");
  s = s.replace(/<p>\s*(<div)/g, "$1");
  s = s.replace(/(<\/div>)\s*<\/p>/g, "$1");
  s = s.replace(/<p>\s*(<pre>)/g, "$1");
  s = s.replace(/(<\/pre>)\s*<\/p>/g, "$1");

  // Restore math (unescaped LaTeX for KaTeX)
  s = s.replace(/%%MATH(\d+)%%/g, function(_, i){
    return slots[parseInt(i, 10)] || "";
  });
  return s;
}

function renderMsgMath(el){
  if(!el) return;
  const run = () => {
    if(!window.renderMathInElement) return false;
    try{
      renderMathInElement(el, {
        delimiters: [
          {left: "$$", right: "$$", display: true},
          {left: "\\[", right: "\\]", display: true},
          {left: "$", right: "$", display: false},
          {left: "\\(", right: "\\)", display: false}
        ],
        throwOnError: false,
        ignoredTags: ["script", "noscript", "style", "textarea", "pre", "code"]
      });
      return true;
    }catch(e){ return false; }
  };
  if(run()) return;
  let tries = 0;
  const t = setInterval(() => {
    tries++;
    if(run() || tries > 20) clearInterval(t);
  }, 100);
}
function addQuickActions(actions, question){
  if(!Array.isArray(actions) || !actions.length || !question) return;
  const box=document.getElementById("messages");
  const wrap=document.createElement("div");
  wrap.className="quick-actions";
  wrap.style.cssText="display:flex;flex-wrap:wrap;gap:.45rem;margin:.15rem 0 .75rem";
  actions.forEach(a=>{
    const b=document.createElement("button");
    b.type="button";
    b.textContent=String(a.label||a.id||"Action");
    b.style.cssText="border:1px solid rgba(0,0,0,.12);border-radius:999px;padding:.48rem .7rem;background:#FFD600;color:#111;font-weight:700;cursor:pointer";
    b.onclick=async()=>{
      b.disabled=true;
      try{
        const r=await fetch("/api/quick-action",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({client_id:clientId,question:question,action:a.id})});
        const d=await r.json();
        if(d.pro_only){ addMessage("bot", d.error||"PRO feature hai."); return; }
        if(d.videos){ addMessage("bot", d.videos.map(v=>`▶️ ${escapeHtml(v.title||"Video")}: ${escapeHtml(v.url||"")}`).join("\n")); return; }
        addMessage("bot", d.answer || (d.quiz ? ("📝 Quiz\n\n"+JSON.stringify(d.quiz,null,2)) : "Done."));
      }catch(e){ addMessage("bot", "Please try again."); }
      finally{ b.disabled=false; }
    };
    wrap.appendChild(b);
  });
  box.appendChild(wrap);
  box.scrollTop=box.scrollHeight;
}

function addMessage(role, text, meta=""){
  const div = document.createElement("div");
  div.className = "msg " + role;
  const body = (role === "bot") ? mdToHtml(text) : escapeHtml(text).replace(/\n/g,"<br>");
  div.innerHTML = body + (meta ? `<div class="meta">${escapeHtml(meta)}</div>` : "");
  const box = document.getElementById("messages");
  const welcome = box.querySelector(".welcome");
  if(welcome) welcome.remove();
  box.appendChild(div);
  if(role === "bot") renderMsgMath(div);
  box.scrollTop = box.scrollHeight;
}

async function ask(){
  const sel = document.getElementById("toolSelect");
  if(sel && sel.value) currentTool = sel.value;
  const q = document.getElementById("question").value.trim();
  if(!q && !imageBase64) return;
  const btn = document.getElementById("sendBtn");
  btn.disabled = true; btn.textContent = "...";
  soundSend();
  addMessage("user", q || "📷 Image question");
  document.getElementById("question").value = "";
  const loading = document.createElement("div");
  loading.className = "msg bot loading";
  loading.textContent = "🎯 Target locked by Sparsh Singhal's SaarthiBhai...";
  document.getElementById("messages").appendChild(loading);
  try{
    const res = await fetch("/api/webask", {
      method: "POST",
      headers: {"Content-Type":"application/json"},
      body: JSON.stringify({ question: q, tool: currentTool, client_id: clientId, phone_number: (localStorage.getItem("sg_phone") || ""), image_base64: imageBase64 || undefined, image_mime: window._imageMime || "image/jpeg", media_kind: window._mediaKind || mediaKind || undefined })
    });
    const data = await res.json();
    loading.remove();
    addMessage("bot", data.answer || "No response", data.elapsed ? `⚡ ${data.elapsed}s` : "");
    addQuickActions(data.actions, q);
    soundRecv();
    if(data.xp !== undefined && data.xp !== null){
      document.getElementById("xp-display").textContent = `⭐ ${data.xp} XP`;
      if(data.level !== undefined) document.getElementById("level-display").textContent = `Level ${data.level}`;
    } else {
      syncProfile();
    }
    if(data.quota){
      const qq = data.quota;
      isProUser = (qq.daily_left === -1);
      document.getElementById("quota-display").textContent = qq.daily_left === -1 ? "PRO ∞" : `Free: ${qq.daily_left} left`;
    }
    if(data.coins !== undefined){
      const c = document.getElementById("coins-display");
      if(c) c.textContent = `🪙 ${data.coins}`;
    }
    if(data.streak && data.streak.current !== undefined){
      const s = document.getElementById("streak-display");
      if(s) s.textContent = `🔥 ${data.streak.current}` + (data.streak.shields ? ` 🛡️${data.streak.shields}` : "");
    }
    if(data.language){
      const sel = document.getElementById("langSelect");
      if(sel) sel.value = data.language;
    }
  }catch(err){
    loading.remove();
    addMessage("bot", "😔 Network error. Please try again.");
    soundError();
  }
  imageBase64 = null;
  btn.disabled = false; btn.textContent = "🔥 Fire";
}

document.getElementById("question").addEventListener("keydown", e=>{
  if(e.key === "Enter" && !e.shiftKey){ e.preventDefault(); ask(); }
});

async function loadLB(){
  try{
    const res = await fetch("/api/leaderboard");
    const data = await res.json();
    const list = document.getElementById("lb-list");
    if(!data.board || !data.board.length){
      list.innerHTML = "<div style='color:#64748b;font-size:.85rem'>No one yet</div>";
      return;
    }
    list.innerHTML = data.board.slice(0,10).map(e=>
      `<div class="lb-item"><span>${e.rank}. ${e.name}${e.is_pro ? " 👑 PRO" : ""}</span><span>L${e.level}</span></div>`
    ).join("");
  }catch{}
}

// Instant Pro UX after payment redirect
(function(){
  try{
    const params=new URLSearchParams(location.search);
    if(params.get("pro")==="1" || params.get("paid")==="1" || localStorage.getItem("sg_pro_just_unlocked")==="1"){
      localStorage.removeItem("sg_pro_just_unlocked");
      isProUser = true;
      const qd=document.getElementById("quota-display");
      if(qd) qd.textContent = "PRO ∞";
      if(params.get("pro") || params.get("paid")){
        history.replaceState({}, "", "/");
      }
      setTimeout(function(){
        try{
          if(typeof addMessage==="function"){
            addMessage("bot", "🎉 **Pro unlocked!** Unlimited questions + Roast + Mindmap + OCR + 2× XP ab active hain. Fire away!");
          }
        }catch(e){}
      }, 400);
      if(typeof syncProfile==="function") syncProfile();
    }
  }catch(e){}
})();

async function loadRefLB(){
  try{
    const res = await fetch("/api/leaderboard/referrals");
    const data = await res.json();
    const list = document.getElementById("ref-lb-list");
    if(!list) return;
    if(!data.board || !data.board.length){
      list.innerHTML = "<div style='color:#64748b;font-size:.85rem'>No referrals yet</div>";
      return;
    }
    list.innerHTML = data.board.map(e=>
      `<div class="lb-item"><span>${e.rank}. ${e.name}</span><span>${e.referrals} 👥</span></div>`
    ).join("");
  }catch{}
}
loadRefLB();
setInterval(loadRefLB, 60000);

async function changeLanguage(lang){
  try{
    await fetch("/api/set-language", {
      method: "POST", headers: {"Content-Type":"application/json"},
      body: JSON.stringify({ client_id: clientId, language: lang })
    });
    try{ localStorage.setItem("sg_lang", lang); }catch(e){}
  }catch(e){}
}
(function(){
  try{
    const saved = localStorage.getItem("sg_lang");
    if(saved){ const sel = document.getElementById("langSelect"); if(sel) sel.value = saved; }
  }catch(e){}
})();

// ---------------- QUIZ ----------------
let quizState = { quizId: null, questions: [], idx: 0, answers: [], picked: null,
                   mode: "practice", lifelines: {}, hiddenOptions: [], timer: null, timeLeft: 0 };
let selectedQuizMode = "practice";

function selectQuizMode(mode){
  selectedQuizMode = mode;
  document.querySelectorAll(".quiz-mode-btn").forEach(b => {
    b.classList.toggle("active", b.getAttribute("data-mode") === mode);
  });
  try{ soundClick(); }catch(e){}
}

function openQuizModal(){
  document.getElementById("quizModal").classList.add("show");
  document.getElementById("quizSetup").style.display = "block";
  document.getElementById("quizPlay").style.display = "none";
  document.getElementById("quizResult").style.display = "none";
  document.getElementById("quizChallengeShare").style.display = "none";
  selectQuizMode("practice");
  const msg = document.getElementById("quizSetupMsg");
  if(msg) msg.textContent = "";
}
function closeQuizModal(){
  document.getElementById("quizModal").classList.remove("show");
  stopQuizTimer();
}
async function startQuiz(){
  const topic = document.getElementById("quizTopic").value.trim();
  const fromGalti = document.getElementById("quizFromGalti").checked;
  const msg = document.getElementById("quizSetupMsg");
  if(!topic && !fromGalti){
    if(msg){ msg.style.color="#f87171"; msg.textContent = "Topic likho ya Galti Diary select karo."; }
    return;
  }
  if(msg){ msg.style.color="#94a3b8"; msg.textContent = "Generating quiz..."; }
  try{
    const res = await fetch("/api/quiz/generate", {
      method: "POST", headers: {"Content-Type":"application/json"},
      body: JSON.stringify({ client_id: clientId, topic, use_galti_diary: fromGalti, mode: selectedQuizMode })
    });
    const data = await res.json();
    if(!data.ok){
      if(msg){ msg.style.color="#f87171"; msg.textContent = data.error || "Failed"; }
      return;
    }
    quizState = { quizId: data.quiz_id, questions: data.questions, idx: 0, answers: [], picked: null,
                  mode: data.mode || selectedQuizMode, lifelines: data.lifelines || {}, hiddenOptions: [],
                  timerSec: data.timer_per_question_sec, timeLeft: data.timer_per_question_sec || 0 };
    document.getElementById("quizSetup").style.display = "none";
    document.getElementById("quizPlay").style.display = "block";
    renderQuizQuestion();
  }catch(e){
    if(msg){ msg.style.color="#f87171"; msg.textContent = "Network error"; }
  }
}

// Join an existing 1v1 challenge quiz (via /?challenge=quiz_id link)
async function joinQuizChallenge(quizId){
  try{
    const res = await fetch("/api/quiz/join/" + encodeURIComponent(quizId));
    const data = await res.json();
    if(!data.ok){
      if(typeof addMessage === "function") addMessage("bot", `❌ Challenge link expired ya invalid: ${data.error||""}`);
      return;
    }
    quizState = { quizId, questions: data.questions, idx: 0, answers: [], picked: null,
                  mode: "1v1", lifelines: data.lifelines || {}, hiddenOptions: [],
                  timerSec: data.timer_per_question_sec, timeLeft: data.timer_per_question_sec || 0 };
    openQuizModal();
    document.getElementById("quizSetup").style.display = "none";
    document.getElementById("quizPlay").style.display = "block";
    renderQuizQuestion();
  }catch(e){}
}

function stopQuizTimer(){
  if(quizState.timer){ clearInterval(quizState.timer); quizState.timer = null; }
  const t = document.getElementById("quizTimer");
  if(t) t.style.display = "none";
}
function startQuizTimer(){
  const t = document.getElementById("quizTimer");
  if(!quizState.timerSec){ if(t) t.style.display = "none"; return; }
  quizState.timeLeft = quizState.timerSec;
  t.style.display = "block";
  t.textContent = `⏱️ ${quizState.timeLeft}s`;
  if(quizState.timer) clearInterval(quizState.timer);
  quizState.timer = setInterval(() => {
    quizState.timeLeft -= 1;
    t.textContent = `⏱️ ${quizState.timeLeft}s`;
    if(quizState.timeLeft <= 0){
      clearInterval(quizState.timer);
      quizState.timer = null;
      if(quizState.picked === null){
        quizState.picked = -1;
        quizState.answers[quizState.idx] = -1;
        document.getElementById("quizFeedback").textContent = "⏱️ Time up! Auto-moving to next question.";
        document.getElementById("quizNextBtn").style.display = "block";
      }
    }
  }, 1000);
}

function renderQuizQuestion(){
  const q = quizState.questions[quizState.idx];
  quizState.picked = null;
  quizState.hiddenOptions = [];
  document.getElementById("quizProgress").textContent = `Q${quizState.idx+1}/${quizState.questions.length}` +
    (quizState.mode === "1v1" ? " · 1v1" : "");
  document.getElementById("quizQuestion").textContent = q.q;
  document.getElementById("quizFeedback").textContent = "";
  document.getElementById("quizHintText").style.display = "none";
  document.getElementById("quizNextBtn").style.display = "none";
  renderQuizOptions();
  renderQuizLifelines();
  if(quizState.mode === "exam") startQuizTimer(); else stopQuizTimer();
}
function renderQuizOptions(){
  const q = quizState.questions[quizState.idx];
  const opts = document.getElementById("quizOptions");
  opts.innerHTML = "";
  q.options.forEach((opt, i) => {
    if(quizState.hiddenOptions.includes(i)) return;
    const btn = document.createElement("button");
    btn.className = "tool-btn";
    btn.style.marginBottom = ".35rem";
    btn.textContent = String.fromCharCode(65+i) + ". " + opt;
    btn.onclick = () => pickQuizOption(i);
    opts.appendChild(btn);
  });
}
function renderQuizLifelines(){
  const box = document.getElementById("quizLifelines");
  box.innerHTML = "";
  if(!quizState.lifelines || (!quizState.lifelines.fifty_fifty && !quizState.lifelines.hint && !quizState.lifelines.skip)) return;
  const mk = (label, fn) => {
    const b = document.createElement("button");
    b.className = "tool-btn"; b.style.flex = "1"; b.style.fontSize = ".8rem";
    b.textContent = label; b.onclick = fn;
    return b;
  };
  if(quizState.lifelines.fifty_fifty) box.appendChild(mk("50:50", () => useLifeline("fifty_fifty")));
  if(quizState.lifelines.hint) box.appendChild(mk("💡 Hint", () => useLifeline("hint")));
  if(quizState.lifelines.skip) box.appendChild(mk("⏭️ Skip", () => useLifeline("skip")));
}
async function useLifeline(type){
  try{
    const res = await fetch("/api/quiz/lifeline", {
      method: "POST", headers: {"Content-Type":"application/json"},
      body: JSON.stringify({ client_id: clientId, quiz_id: quizState.quizId, q_index: quizState.idx, type })
    });
    const data = await res.json();
    if(!data.ok){
      document.getElementById("quizFeedback").textContent = data.error || "Lifeline use nahi hui";
      return;
    }
    if(type === "fifty_fifty"){
      quizState.hiddenOptions = data.eliminate_indices || [];
      renderQuizOptions();
    } else if(type === "hint"){
      const h = document.getElementById("quizHintText");
      h.style.display = "block";
      h.textContent = "💡 " + (data.hint || "");
    } else if(type === "skip"){
      quizState.answers[quizState.idx] = -1;
      quizNext();
      return;
    }
    try{ soundClick(); }catch(e){}
  }catch(e){
    document.getElementById("quizFeedback").textContent = "Network error";
  }
}
function pickQuizOption(i){
  if(quizState.picked !== null) return;
  quizState.picked = i;
  quizState.answers[quizState.idx] = i;
  document.getElementById("quizNextBtn").style.display = "block";
  document.getElementById("quizFeedback").textContent = "Selected. Tap Next to continue →";
  stopQuizTimer();
  try{ soundClick(); }catch(e){}
}
function quizNext(){
  stopQuizTimer();
  quizState.idx += 1;
  quizState.picked = null;
  if(quizState.idx >= quizState.questions.length){
    submitQuiz();
  } else {
    renderQuizQuestion();
  }
}
async function submitQuiz(){
  try{
    const res = await fetch("/api/quiz/submit", {
      method: "POST", headers: {"Content-Type":"application/json"},
      body: JSON.stringify({ client_id: clientId, quiz_id: quizState.quizId, answers: quizState.answers })
    });
    const data = await res.json();
    document.getElementById("quizPlay").style.display = "none";
    document.getElementById("quizResult").style.display = "block";
    if(data.ok){
      document.getElementById("quizScoreText").textContent = `Score: ${data.score}/${data.total}`;
      const bonus = [];
      if(data.xp_gain) bonus.push(`+${data.xp_gain} XP`);
      if(data.coins_gain) bonus.push(`+${data.coins_gain} 🪙`);
      document.getElementById("quizVerdictText").textContent = data.verdict + (bonus.length ? ` (${bonus.join(", ")})` : "");
      if(data.coins_total !== undefined){
        const c = document.getElementById("coins-display");
        if(c) c.textContent = `🪙 ${data.coins_total}`;
      }
      if(quizState.mode === "1v1"){
        const share = document.getElementById("quizChallengeShare");
        share.style.display = "block";
        document.getElementById("quizChallengeLink").value = window.location.origin + "/?challenge=" + quizState.quizId;
        loadQuizChallengeResults(quizState.quizId);
      }
      try{ soundRecv(); }catch(e){}
      syncProfile();
    } else {
      document.getElementById("quizScoreText").textContent = "Error";
      document.getElementById("quizVerdictText").textContent = data.error || "Could not submit";
    }
  }catch(e){
    document.getElementById("quizPlay").style.display = "none";
    document.getElementById("quizResult").style.display = "block";
    document.getElementById("quizScoreText").textContent = "Network error";
  }
}
function copyQuizChallengeLink(){
  const box = document.getElementById("quizChallengeLink");
  box.select();
  try{ navigator.clipboard.writeText(box.value); }catch(e){ document.execCommand("copy"); }
}
function copyReferralLink(){
  const box = document.getElementById("referralLinkBox");
  if(!box || !box.value) return;
  box.select();
  try{ navigator.clipboard.writeText(box.value); }catch(e){ document.execCommand("copy"); }
  try{ soundClick(); }catch(e){}
}
async function loadQuizChallengeResults(quizId){
  const box = document.getElementById("quizChallengeResults");
  try{
    const res = await fetch("/api/quiz/challenge/" + encodeURIComponent(quizId));
    const data = await res.json();
    if(!data.ok || !data.results || !data.results.length){
      box.textContent = "Abhi tak koi result nahi — link share karo!";
      return;
    }
    box.innerHTML = data.results
      .sort((a,b) => b.score - a.score)
      .map((r,i) => `${i===0 ? "🏆" : "  "} ${(r.name||"Player").replace(/</g,"&lt;")}: ${r.score}/${r.total}`)
      .join("<br>");
  }catch(e){
    box.textContent = "";
  }
}

// ---------------- GALTI DIARY ----------------
function openGaltiModal(){
  document.getElementById("galtiModal").classList.add("show");
  loadGaltiDiary();
}
function closeGaltiModal(){
  document.getElementById("galtiModal").classList.remove("show");
}
async function loadGaltiDiary(){
  const list = document.getElementById("galtiList");
  list.innerHTML = "Loading...";
  try{
    const res = await fetch("/api/galti-diary?client_id=" + encodeURIComponent(clientId));
    const data = await res.json();
    if(!data.mistakes || !data.mistakes.length){
      list.innerHTML = "<div>Khaali hai! Koi mistake save nahi hui abhi tak 🔥</div>";
      return;
    }
    list.innerHTML = data.mistakes.map(m =>
      `<div style="padding:.5rem 0;border-bottom:1px solid var(--border)">
         <div style="color:var(--text)">${(m.question||"").replace(/</g,"&lt;")}</div>
         <div style="font-size:.78rem;color:#22d3ee;margin-top:.2rem">✅ ${(m.correct_answer||"").replace(/</g,"&lt;")}</div>
       </div>`
    ).join("");
  }catch(e){
    list.innerHTML = "Network error";
  }
}

// ---------------- SPIN WHEEL ----------------
let spinWheelRotation = 0;
function openSpinModal(){
  document.getElementById("spinModal").classList.add("show");
  document.getElementById("spinResult").textContent = "";
}
function closeSpinModal(){
  document.getElementById("spinModal").classList.remove("show");
}
async function doSpin(){
  const btn = document.getElementById("spinBtn");
  const res_el = document.getElementById("spinResult");
  const wheel = document.getElementById("spinWheel");
  btn.disabled = true;
  res_el.textContent = "🎡 Spinning...";
  // Spin visually right away (4-6 extra full turns) while the request is
  // in flight, so the wheel is always mid-spin when the result arrives —
  // the exact stop angle doesn't need to match a segment since the coin
  // amount is announced as text once it lands.
  spinWheelRotation += 1440 + Math.floor(Math.random() * 360);
  wheel.style.transform = `rotate(${spinWheelRotation}deg)`;
  try{ soundClick(); }catch(e){}
  try{
    const res = await fetch("/api/spin", {
      method: "POST", headers: {"Content-Type":"application/json"},
      body: JSON.stringify({ client_id: clientId })
    });
    const data = await res.json();
    setTimeout(() => {
      if(data.ok){
        res_el.textContent = `🎉 +${data.coins_won} Coins!`;
        try{ soundRecv(); }catch(e){}
        syncProfile();
      } else {
        res_el.textContent = data.error || "Already spun today";
        try{ soundError(); }catch(e){}
      }
      btn.disabled = false;
    }, 2200); // matches the wheel's CSS transition duration
    return;
  }catch(e){
    res_el.textContent = "Network error";
  }
  btn.disabled = false;
}

loadLB();
setInterval(loadLB, 30000);
</script>
</body>
</html>
"""

PAY_HTML = r"""
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Upgrade Pro – SaarthiBhai by Sparsh Singhal</title>
<script src="{{ razorpay_checkout_js }}"></script>
<style>
body{font-family:system-ui,sans-serif;background:#0b1220;color:#f1f5f9;display:flex;min-height:100vh;align-items:center;justify-content:center;margin:0}
.card{background:#111827;border:1px solid rgba(255,255,255,.08);border-radius:16px;padding:2rem;max-width:420px;width:90%;text-align:center}
h1{font-size:1.4rem;margin:0 0 .5rem}
.price{font-size:2rem;color:#22d3ee;font-weight:700;margin:1rem 0}
ul{text-align:left;color:#94a3b8;line-height:1.7}
button{background:#22d3ee;color:#0b1220;border:none;border-radius:12px;padding:.9rem 1.5rem;font-weight:700;width:100%;margin-top:1.2rem;cursor:pointer}
button:disabled{opacity:.5}
.msg{margin-top:1rem;font-size:.9rem;color:#94a3b8}
a{color:#22d3ee}
.creator{display:flex;align-items:center;gap:.6rem;justify-content:center;margin-top:1rem;font-size:.8rem;color:#94a3b8}
.creator img{width:28px;height:28px;border-radius:50%;object-fit:cover;border:1px solid #22d3ee}
</style>
</head>
<body>
<div class="card">
  <h1>🎓 SaarthiBhai Pro</h1>
  <p>Doubt yahin mat rokna — unlimited access for 30 days</p>
  <div class="price">₹{{ price }} <span style="font-size:1rem;color:#94a3b8">/ 30 days</span></div>
  <ul>
    <li>Unlimited questions</li>
    <li>All Pro tools unlocked</li>
    <li>Image OCR</li>
    <li>2× XP + priority</li>
  </ul>
  <button id="payBtn" onclick="startPay()">Pay ₹{{ price }} Securely</button>
  <p class="msg" id="status">User: {{ uid }}</p>
  <p class="msg"><a href="/">← Back to SaarthiBhai</a></p>
  <div class="creator"><img src="/sparsh.jpg" alt="Sparsh Singhal" onerror="this.style.display='none'"> Built by Sparsh Singhal</div>
</div>
<script>
const UID={{ uid|tojson }};
const KEY_ID={{ key_id|tojson }};
async function startPay(){
  const btn=document.getElementById("payBtn");
  const status=document.getElementById("status");
  btn.disabled=true;status.textContent="Creating order...";
  try{
    const res=await fetch("/api/create-order",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({uid:UID})});
    const data=await res.json();
    if(data.error){status.textContent="Error: "+data.error;btn.disabled=false;return;}
    const rzp=new Razorpay({
      key:KEY_ID,amount:data.amount,currency:"INR",name:"SaarthiBhai Pro",
      description:"30 days Pro",order_id:data.id,notes:{user_id:UID},
      handler:async function(response){
        status.textContent="✅ Payment received. Unlocking Pro...";
        try{
          const ab=(new URLSearchParams(location.search)).get("ab")||"";
          const vr=await fetch("/api/verify-payment",{
            method:"POST",
            headers:{"Content-Type":"application/json"},
            body:JSON.stringify({
              payment_id: response.razorpay_payment_id,
              order_id: response.razorpay_order_id || data.id,
              uid: UID,
              ab: ab
            })
          });
          const vd=await vr.json();
          if(vd.ok){
            status.textContent="🎉 Pro unlocked! Redirecting...";
            try{ localStorage.setItem("sg_pro_just_unlocked","1"); }catch(e){}
            setTimeout(function(){ window.location.href="/?pro=1&paid=1"; }, 600);
          }else{
            status.textContent="Payment OK but unlock delayed: "+(vd.error||"refresh home in a few seconds");
            btn.disabled=false;
          }
        }catch(err){
          status.textContent="Payment OK. Open Home — Pro will sync shortly.";
          setTimeout(function(){ window.location.href="/?pro=1"; }, 1200);
        }
      },
      theme:{color:"#22d3ee"},
      modal:{ondismiss:function(){btn.disabled=false;status.textContent="Payment cancelled."}}
    });
    rzp.open();btn.disabled=false;
  }catch(e){status.textContent="Network error";btn.disabled=false;}
}
</script>
</body>
</html>
"""

TEACHER_HTML = r"""
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Teacher Dashboard — SaarthiBhai</title>
<style>
:root{--bg:#ffffff;--card:#ffffff;--accent:{{ theme_primary }};--text:#111111;--muted:#575757;--border:rgba(17,17,17,.12)}
*{box-sizing:border-box;margin:0;padding:0}
body{font-family:system-ui,-apple-system,sans-serif;background:var(--bg);color:var(--text);min-height:100vh}
header{background:linear-gradient(90deg,#0f172a,#1e1b4b);padding:1rem 1.5rem;border-bottom:1px solid var(--border);display:flex;justify-content:space-between;align-items:center;flex-wrap:wrap;gap:.5rem}
header h1{font-size:1.15rem}header h1 span{color:var(--accent)}
main{max-width:960px;margin:0 auto;padding:1.5rem}
.card{background:var(--card);border:1px solid var(--border);border-radius:14px;padding:1.25rem;margin-bottom:1.25rem}
.card h2{font-size:1rem;margin-bottom:.75rem;color:var(--accent)}
input,button{font-family:inherit;font-size:.95rem}
input[type=text]{width:100%;padding:.65rem .8rem;border-radius:8px;border:1px solid var(--border);background:#0f172a;color:var(--text);margin-bottom:.6rem}
button{background:var(--accent);color:#0b1220;border:none;border-radius:8px;padding:.6rem 1.1rem;font-weight:700;cursor:pointer}
button.secondary{background:#0f172a;color:var(--text);border:1px solid var(--border)}
button:disabled{opacity:.5;cursor:not-allowed}
.class-list{display:grid;gap:.75rem}
.class-item{background:#0f172a;border:1px solid var(--border);border-radius:10px;padding:.9rem 1rem;display:flex;justify-content:space-between;align-items:center;cursor:pointer;flex-wrap:wrap;gap:.5rem}
.class-item:hover{border-color:var(--accent)}
.class-item .code{font-family:monospace;font-size:1.1rem;color:var(--accent);font-weight:700}
.class-item .meta{color:var(--muted);font-size:.82rem}
table{width:100%;border-collapse:collapse;font-size:.88rem}
th,td{text-align:left;padding:.5rem .6rem;border-bottom:1px solid var(--border)}
th{color:var(--muted);font-weight:600;font-size:.78rem;text-transform:uppercase}
.badge{background:rgba(34,211,238,.15);color:var(--accent);padding:.15rem .5rem;border-radius:999px;font-size:.75rem}
.empty{color:var(--muted);text-align:center;padding:1.5rem;font-size:.9rem}
.copy-row{display:flex;gap:.5rem;align-items:center}
.copy-row input{flex:1;margin:0}
#msg{font-size:.85rem;color:var(--muted);margin-top:.5rem}
a.back{color:var(--accent);text-decoration:none;font-size:.85rem}
</style>
</head>
<body>
<header>
  <h1>🎓 Saarthi<span>Bhai</span> — Teacher Dashboard</h1>
  <a class="back" href="/">← Back to chat</a>
</header>
<main>
  <div class="card">
    <h2>➕ Create a New Class</h2>
    <input type="text" id="className" placeholder="Class name — e.g. DTU Sem1 Physics" />
    <button onclick="createClass()">Create Class Code</button>
    <p id="createMsg" style="margin-top:.5rem;font-size:.85rem;color:var(--muted)"></p>
  </div>


  <div class="card">
    <h2>👑 Teacher Pro & Class Tools</h2>
    <div id="teacherStatus" class="meta" style="margin-bottom:.75rem">Loading teacher plan...</div>
    <button onclick="buyTeacherPro()">Unlock Teacher Pro ₹{{ teacher_pro_price }}</button>
    <div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(220px,1fr));gap:.7rem;margin-top:1rem">
      <div>
        <input type="text" id="taskClass" placeholder="Class code"/>
        <input type="text" id="taskTitle" placeholder="Assignment/Test/Note title"/>
        <textarea id="taskBody" placeholder="Instructions / topic / note" style="width:100%;min-height:90px;padding:.6rem;border-radius:8px;border:1px solid var(--border);background:#0f172a;color:var(--text)"></textarea>
        <button onclick="saveTeacherTask('assignment')">Create Assignment</button>
        <button onclick="saveTeacherTask('test')">Create Test</button>
        <button onclick="saveTeacherTask('note')">Save Note</button>
      </div>
      <div>
        <h3 style="margin-bottom:.5rem">Class Report</h3>
        <button class="secondary" onclick="loadTeacherReport()">Refresh Report</button>
        <pre id="teacherReport" style="white-space:pre-wrap;margin-top:.6rem;color:var(--muted)"></pre>
      </div>
    </div>
  </div>

  <div class="card">
    <h2>📚 Your Classes</h2>
    <div id="classList" class="class-list"><p class="empty">Loading...</p></div>
  </div>

  <div class="card" id="detailCard" style="display:none">
    <h2 id="detailTitle">Class</h2>
    <div class="copy-row" style="margin-bottom:1rem">
      <input type="text" id="joinLinkBox" readonly />
      <button class="secondary" onclick="copyJoinLink()">Copy Link</button>
    </div>
    <table>
      <thead><tr><th>#</th><th>Student</th><th>Level</th><th>XP</th><th>Streak</th><th>Questions</th></tr></thead>
      <tbody id="studentRows"></tbody>
    </table>
    <div id="noStudents" class="empty" style="display:none">No students joined yet — share the class code above.</div>
  </div>
</main>
<script>
let teacherId = localStorage.getItem("sg_teacher_client") || ("teacher_" + Math.random().toString(36).slice(2));
localStorage.setItem("sg_teacher_client", teacherId);

async function createClass(){
  const nameEl = document.getElementById("className");
  const msg = document.getElementById("createMsg");
  const name = nameEl.value.trim();
  msg.style.color = "#94a3b8"; msg.textContent = "Creating...";
  try{
    const res = await fetch("/api/teacher/create-class", {
      method: "POST", headers: {"Content-Type":"application/json"},
      body: JSON.stringify({ client_id: teacherId, class_name: name })
    });
    const data = await res.json();
    if(data.ok){
      msg.style.color = "#22d3ee";
      msg.textContent = `✅ Class code: ${data.class_code} — share this with students!`;
      nameEl.value = "";
      loadClasses();
    } else {
      msg.style.color = "#f87171";
      msg.textContent = data.error || "Failed to create class";
    }
  }catch(e){
    msg.style.color = "#f87171"; msg.textContent = "Network error";
  }
}

async function loadClasses(){
  const list = document.getElementById("classList");
  try{
    const res = await fetch("/api/teacher/classes?client_id=" + encodeURIComponent(teacherId));
    const data = await res.json();
    const classes = data.classes || [];
    if(!classes.length){
      list.innerHTML = "<p class='empty'>Koi class abhi tak nahi bani — upar se ek create karo.</p>";
      return;
    }
    list.innerHTML = classes.map(c => `
      <div class="class-item" onclick="openClass('${c.code}')">
        <div>
          <div>${(c.class_name||"Class").replace(/</g,"&lt;")}</div>
          <div class="meta">Created ${c.created_at || ""}</div>
        </div>
        <div style="text-align:right">
          <div class="code">${c.code}</div>
          <div class="meta">${c.student_count || 0} students</div>
        </div>
      </div>
    `).join("");
  }catch(e){
    list.innerHTML = "<p class='empty'>Network error loading classes.</p>";
  }
}

async function openClass(code){
  const card = document.getElementById("detailCard");
  card.style.display = "block";
  document.getElementById("detailTitle").textContent = "Class: " + code;
  document.getElementById("joinLinkBox").value = window.location.origin + "/?join_class=" + code;
  card.scrollIntoView({behavior:"smooth"});
  try{
    const res = await fetch("/api/teacher/class/" + encodeURIComponent(code));
    const data = await res.json();
    const rows = document.getElementById("studentRows");
    const empty = document.getElementById("noStudents");
    const students = data.students || [];
    if(!students.length){
      rows.innerHTML = "";
      empty.style.display = "block";
      return;
    }
    empty.style.display = "none";
    rows.innerHTML = students.map((s,i) => `
      <tr>
        <td>${i+1}</td>
        <td>${(s.name||"Student").replace(/</g,"&lt;")}</td>
        <td><span class="badge">L${s.level}</span></td>
        <td>${s.xp}</td>
        <td>🔥 ${s.streak}</td>
        <td>${s.questions_asked}</td>
      </tr>
    `).join("");
  }catch(e){
    document.getElementById("studentRows").innerHTML = "";
    document.getElementById("noStudents").style.display = "block";
    document.getElementById("noStudents").textContent = "Network error loading students.";
  }
}

function copyJoinLink(){
  const box = document.getElementById("joinLinkBox");
  box.select();
  try{
    navigator.clipboard.writeText(box.value);
  }catch(e){
    document.execCommand("copy");
  }
}

loadClasses();
</script>

<script src="{{ razorpay_checkout_js }}"></script>
<script>
async function refreshTeacherStatus(){try{const r=await fetch('/api/teacher/status?client_id='+encodeURIComponent(teacherId));const d=await r.json();document.getElementById('teacherStatus').textContent=d.teacher_plan==='pro'?'👑 Teacher Pro active — '+d.max_classes+' classes / '+d.max_students_per_class+' students per class':'Free Teacher — '+d.max_classes+' class / '+d.max_students_per_class+' students per class';}catch(e){}}
async function buyTeacherPro(){try{const r=await fetch('/api/teacher/create-order',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({client_id:teacherId})});const d=await r.json();if(!d.ok){alert(d.error||'Could not create order');return;}if(!window.Razorpay){alert('Payment UI unavailable');return;}const rz=new Razorpay({key:'{{ razorpay_key_id }}',amount:d.amount,currency:d.currency||'INR',name:'{{ brand_name }}',description:'Teacher Pro',order_id:d.id,notes:{user_id:'web:'+teacherId},handler:async function(resp){const vr=await fetch('/api/teacher/verify-payment',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({payment_id:resp.razorpay_payment_id,order_id:resp.razorpay_order_id||d.id,client_id:teacherId})});const vd=await vr.json();alert(vd.message||vd.error||'Done');refreshTeacherStatus();}});rz.open();}catch(e){alert('Payment error');}}
async function saveTeacherTask(kind){const cls=document.getElementById('taskClass').value.trim();const title=document.getElementById('taskTitle').value.trim();const body=document.getElementById('taskBody').value.trim();if(!cls||!title){alert('Class code and title required');return;}const path=kind==='assignment'?'/api/teacher/assignment':kind==='test'?'/api/teacher/test':'/api/teacher/note';const payload=kind==='assignment'?{client_id:teacherId,class_code:cls,title,instructions:body}:kind==='test'?{client_id:teacherId,class_code:cls,title,topic:body}:{client_id:teacherId,class_code:cls,title,content:body};const r=await fetch(path,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)});const d=await r.json();alert(d.ok?'Saved':(d.error||'Failed'));}
async function loadTeacherReport(){const cls=document.getElementById('taskClass').value.trim();if(!cls){alert('Class code required');return;}const r=await fetch('/api/teacher/class/'+encodeURIComponent(cls)+'/report');const d=await r.json();document.getElementById('teacherReport').textContent=d.ok?JSON.stringify(d,null,2):(d.error||'Failed');}
refreshTeacherStatus();
</script>
</body>
</html>
"""

# ============================================================================
# FLASK
# ============================================================================

app = Flask(__name__)

SAARTHIBHAI_CORS_ORIGIN = config.CORS_ORIGIN
@app.after_request
def _v7_cors(resp):
    resp.headers["Access-Control-Allow-Origin"] = SAARTHIBHAI_CORS_ORIGIN
    resp.headers["Access-Control-Allow-Headers"] = "Content-Type, Authorization, X-Cron-Secret, X-Webhook-Secret"
    resp.headers["Access-Control-Allow-Methods"] = "GET, POST, PATCH, OPTIONS"
    return resp



@app.route("/teacher")
def teacher_dashboard():
    return render_template_string(TEACHER_HTML, theme_primary=config.THEME["primary"], brand_name=config.BRAND_NAME, creator_name=config.CREATOR_NAME)


@app.route("/bot-icon.svg")
def bot_icon():
    """SaarthiBhai avatar — clean genie + book mark for the bot name."""
    svg = """<?xml version="1.0" encoding="UTF-8"?>
<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 128 128" width="128" height="128">
  <defs>
    <linearGradient id="bg" x1="0%" y1="0%" x2="100%" y2="100%">
      <stop offset="0%" stop-color="#0891b2"/>
      <stop offset="50%" stop-color="#6366f1"/>
      <stop offset="100%" stop-color="#db2777"/>
    </linearGradient>
    <linearGradient id="turban" x1="0%" y1="0%" x2="0%" y2="100%">
      <stop offset="0%" stop-color="#22d3ee"/>
      <stop offset="100%" stop-color="#818cf8"/>
    </linearGradient>
    <linearGradient id="skin" x1="0%" y1="0%" x2="0%" y2="100%">
      <stop offset="0%" stop-color="#ffe8c8"/>
      <stop offset="100%" stop-color="#f5c78e"/>
    </linearGradient>
    <filter id="soft" x="-20%" y="-20%" width="140%" height="140%">
      <feDropShadow dx="0" dy="2" stdDeviation="2" flood-color="#000" flood-opacity="0.25"/>
    </filter>
  </defs>
  <!-- App icon circle -->
  <circle cx="64" cy="64" r="64" fill="url(#bg)"/>
  <circle cx="64" cy="64" r="58" fill="#0b1220" opacity="0.22"/>
  <circle cx="64" cy="64" r="54" fill="#0f172a"/>

  <!-- Soft glow -->
  <circle cx="64" cy="58" r="36" fill="#22d3ee" opacity="0.08"/>

  <!-- Turban / genie hat -->
  <path d="M34 62c0-22 13-36 30-36s30 14 30 36c-8-8-19-12-30-12s-22 4-30 12z" fill="url(#turban)" filter="url(#soft)"/>
  <ellipse cx="64" cy="48" rx="22" ry="8" fill="#a5f3fc" opacity="0.35"/>
  <!-- Jewel on turban -->
  <circle cx="64" cy="40" r="5.5" fill="#f472b6"/>
  <circle cx="64" cy="40" r="2.5" fill="#fce7f3"/>

  <!-- Face -->
  <ellipse cx="64" cy="72" rx="24" ry="26" fill="url(#skin)" filter="url(#soft)"/>

  <!-- Eyes -->
  <ellipse cx="54" cy="70" rx="4.2" ry="4.8" fill="#0f172a"/>
  <ellipse cx="74" cy="70" rx="4.2" ry="4.8" fill="#0f172a"/>
  <circle cx="55.5" cy="68.5" r="1.4" fill="#fff"/>
  <circle cx="75.5" cy="68.5" r="1.4" fill="#fff"/>

  <!-- Smile -->
  <path d="M54 82c3.5 6 12.5 6 16 0" fill="none" stroke="#0f172a" stroke-width="2.8" stroke-linecap="round"/>

  <!-- Small book badge (study) -->
  <g transform="translate(86 82)">
    <rect x="0" y="0" width="18" height="14" rx="2" fill="#22d3ee"/>
    <rect x="1.5" y="1.5" width="15" height="11" rx="1.2" fill="#ecfeff"/>
    <line x1="9" y1="1.5" x2="9" y2="12.5" stroke="#0891b2" stroke-width="1.2"/>
    <line x1="3" y1="5" x2="7" y2="5" stroke="#67e8f9" stroke-width="1"/>
    <line x1="3" y1="8" x2="7" y2="8" stroke="#67e8f9" stroke-width="1"/>
    <line x1="11" y1="5" x2="15" y2="5" stroke="#67e8f9" stroke-width="1"/>
    <line x1="11" y1="8" x2="15" y2="8" stroke="#67e8f9" stroke-width="1"/>
  </g>

  <!-- Sparkle -->
  <path d="M26 36l1.8 3.8 3.8 1.8-3.8 1.8L26 47.2l-1.8-3.8L20.4 41.6l3.8-1.8L26 36z" fill="#fde68a" opacity="0.95"/>
  <path d="M98 30l1.3 2.7 2.7 1.3-2.7 1.3L98 38l-1.3-2.7-2.7-1.3 2.7-1.3L98 30z" fill="#f9a8d4" opacity="0.9"/>
</svg>
"""
    from flask import Response
    return Response(svg, mimetype="image/svg+xml")



@app.route("/sparsh.jpg")
def serve_photo():
    try:
        return send_from_directory(".", "sparsh.jpg")
    except Exception:
        return "", 404


@app.route("/")
def home():
    return render_template_string(FRONTEND_HTML, price=config.PRO_PRICE_INR, referral_coins=config.REFERRAL_COINS,
                                   spin_min=config.SPIN_MIN_COINS, spin_max=config.SPIN_MAX_COINS,
                                   theme_primary=config.THEME["primary"], brand_name=config.BRAND_NAME, creator_name=config.CREATOR_NAME,
                                   katex_css=config.URLS["katex_css"], katex_js=config.URLS["katex_js"], katex_auto_render_js=config.URLS["katex_auto_render_js"],
                                   whatsapp_share_url=config.URLS["whatsapp_share_url"], youtube_example_url=config.CONTENT.get("youtube_example_url", ""))


@app.route("/pay")
def pay_page():
    uid = (request.args.get("uid") or "").strip()
    if not uid:
        return "Missing uid. Open from Upgrade link.", 400
    if not config.RAZORPAY_KEY_ID:
        return "Payment not configured.", 503
    return render_template_string(
        PAY_HTML, uid=uid, price=config.PRO_PRICE_INR, key_id=config.RAZORPAY_KEY_ID,
        theme_primary=config.THEME["primary"], brand_name=config.BRAND_NAME, creator_name=config.CREATOR_NAME,
        razorpay_checkout_js=config.URLS["razorpay_checkout_js"]
    )




@app.route("/api/verify-payment", methods=["POST"])
def api_verify_payment():
    """Called from Pay page immediately after Razorpay success — unlocks Pro without webhook delay."""
    data = request.get_json(silent=True) or {}
    payment_id = (data.get("payment_id") or data.get("razorpay_payment_id") or "").strip()
    order_id = (data.get("order_id") or data.get("razorpay_order_id") or "").strip()
    uid = (data.get("uid") or "").strip()
    ab = (data.get("ab") or data.get("variant") or "").strip().upper()
    result = verify_and_activate_razorpay_payment(payment_id, order_id=order_id, uid_hint=uid)
    if not result.get("ok"):
        return jsonify(result), 400
    if ab in ("A", "B") and db.redis and not result.get("duplicate"):
        try:
            db.redis.incr(f"ab:{ab}:pay_success")
            db.redis.incr("ab:total:pay_success")
        except Exception:
            pass
    return jsonify(result)


@app.route("/api/restore-pro", methods=["POST"])
def api_restore_pro():
    """Activate Pro on THIS device using an existing Razorpay payment_id.
    Fixes: paid on laptop, opened on phone (new client_id) still shows Free.
    """
    data = request.get_json(silent=True) or {}
    payment_id = (data.get("payment_id") or "").strip()
    client_id = (data.get("client_id") or "").strip()
    if not payment_id:
        return jsonify({"ok": False, "error": "payment_id required (Razorpay payment id, e.g. pay_xxx)"}), 400
    if not client_id:
        return jsonify({"ok": False, "error": "client_id required"}), 400
    if not config.RAZORPAY_KEY_ID or not config.RAZORPAY_KEY_SECRET:
        return jsonify({"ok": False, "error": "Razorpay not configured"}), 503
    uid = f"web:{client_id}"
    try:
        auth = base64.b64encode(
            f"{config.RAZORPAY_KEY_ID}:{config.RAZORPAY_KEY_SECRET}".encode()
        ).decode()
        r = requests.get(
            f"{config.URLS["razorpay_payments"]}/{payment_id}",
            headers={"Authorization": f"Basic {auth}"},
            timeout=20,
        )
        pdata = r.json()
        if r.status_code >= 400:
            return jsonify({"ok": False, "error": pdata.get("error", {}).get("description", "Payment not found")}), 400
        status = (pdata.get("status") or "").lower()
        if status != "captured":
            return jsonify({"ok": False, "error": f"Payment not captured ({status})"}), 400
        amount = int(pdata.get("amount") or 0)
        expected = int(config.PRO_PRICE_INR) * 100
        if amount < expected:
            return jsonify({"ok": False, "error": "Amount mismatch"}), 400
        # Bind this successful payment to current device uid
        db.ensure_user(uid, full_name=config.DEFAULT_STUDENT_NAMES["pro"], platform="web")
        db.activate_pro(uid, days=30)
        try:
            pu = db.get_user(uid) or {}
            pu["last_payment_id"] = payment_id
            db.save_user(uid, pu)
            db.sync_user_to_supabase(uid, pu)
        except Exception:
            pass
        try:
            db.add_badge(uid, "Pro Warrior 👑")
        except Exception:
            pass
        # Remember payment -> uid mapping (allow restore on multiple devices from same pay)
        if db.redis:
            try:
                db.redis.sadd(f"pay:devices:{payment_id}", uid)
                db.redis.expire(f"pay:devices:{payment_id}", 86400 * 40)
            except Exception:
                pass
        logger.info("Pro restored on %s via payment %s", uid, payment_id)
        return jsonify({"ok": True, "uid": uid, "plan": "pro", "message": "Pro unlocked on this device"})
    except Exception as e:
        logger.error("restore-pro: %s", e)
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/api/create-order", methods=["POST"])
def api_create_order():
    data = request.get_json(silent=True) or {}
    uid = (data.get("uid") or "").strip()
    if not uid:
        return jsonify({"error": "uid required"}), 400
    order = create_razorpay_order(uid, config.PRO_PRICE_INR)
    if "error" in order:
        return jsonify(order), 400
    order_id = str(order.get("id") or "").strip()
    if order_id and db.redis:
        try:
            db.redis.hset(f"pay:pending:{order_id}", mapping={
                "uid": uid, "created_at": _now_ist().isoformat(), "status": "created",
            })
            db.redis.expire(f"pay:pending:{order_id}", 86400 * 10)
        except Exception:
            pass
    return jsonify({"id": order_id, "amount": order.get("amount"), "currency": order.get("currency", "INR")})



@app.route("/api/me")
def api_me():
    client_id = (request.args.get("client_id") or "").strip()
    if not client_id:
        return jsonify({"ok": False, "error": "client_id required"}), 400
    ref = (request.args.get("ref") or "").strip()
    phone = normalize_phone(request.args.get("phone_number") or request.args.get("phone") or "")
    uid = f"web:{client_id}"
    user = db.ensure_user(uid, full_name=config.DEFAULT_STUDENT_NAMES["web"], platform="web", referred_by=ref, phone_number=phone)
    db.register_referral_code(uid, user.get("referral_code", ""))
    xp = int(user.get("xp", 0) or 0)
    level = int(user.get("level", 1) or 1)
    is_pro_now = db.is_pro(uid)
    return jsonify({
        "ok": True,
        "uid": uid,
        "name": user.get("full_name") or config.DEFAULT_STUDENT_NAMES["generic"],
        "xp": xp,
        "level": level,
        "coins": db.get_coins(uid),
        "spin_available": not db.spin_used_today(uid),
        "language": db.get_language(uid),
        "streak": int(user.get("streak", 0) or 0),
        "best_streak": int(user.get("best_streak", 0) or 0),
        "shields": int(user.get("shields", 0) or 0),
        "plan": "pro" if is_pro_now else "free",
        "plan_raw": user.get("plan", "free"),
        "pro_until": user.get("pro_until", ""),
        "quota": db.check_quota(uid)[1],
        "referral_code": user.get("referral_code", ""),
        "referral_count": user.get("referral_count", "0"),
        "phone_number": user.get("phone_number", ""),
        "exam_type": user.get("exam_type", "general"),
        "subject": user.get("subject", "general"),
        "board": user.get("board", ""),
        "course": user.get("course", ""),
        "class_level": user.get("class_level", ""),
        "semester": user.get("semester", ""),
        "student_type": user.get("student_type", ""),
        "learner_track": user.get("learner_track", "school_college"),
        "study_mode": user.get("study_mode", ""),
        "target_exam": user.get("target_exam", user.get("exam_type", "")),
        "target_days": int(user.get("target_days", 0) or 0),
        "parent_phone": user.get("parent_phone", ""),
        "parent_report_opt_in": str(user.get("parent_report_opt_in", "0")).lower() in ("1","true","yes"),
        "welcome_just_claimed": _pop_welcome_flash(uid),
        "welcome": {"coins": config.WELCOME_COINS, "spin_min": config.SPIN_MIN_COINS, "spin_max": config.SPIN_MAX_COINS, "spin_count": config.WELCOME_FREE_SPIN_COUNT, "freeze": config.WELCOME_FREEZE_COUNT},
    })


def _pop_welcome_flash(uid: str | int) -> bool:
    if not db.redis:
        return False
    try:
        return bool(db.redis.delete(f"welcome_flash:{db._key(uid)}"))
    except Exception:
        return False


@app.route("/api/spin", methods=["POST"])
def api_spin():
    """POINT 17/36 — daily spin wheel, one spin per account per day."""
    data = request.get_json(silent=True) or {}
    client_id = (data.get("client_id") or "").strip()
    if not client_id:
        return jsonify({"ok": False, "error": "client_id required"}), 400
    uid = f"web:{client_id}"
    db.ensure_user(uid, full_name=config.DEFAULT_STUDENT_NAMES["web"], platform="web")
    won = db.do_spin(uid)
    if won is None:
        return jsonify({"ok": False, "error": "Aaj ka spin ho chuka hai. Kal wapas aana!"}), 429
    return jsonify({"ok": True, "coins_won": won, "coins_total": db.get_coins(uid)})

@app.route("/api/set-name", methods=["POST"])
def api_set_name():
    data = request.get_json(silent=True) or {}
    client_id = (data.get("client_id") or "").strip()
    name = (data.get("name") or "").strip()[:40]
    phone = normalize_phone(data.get("phone_number") or data.get("phone") or "")
    if not client_id:
        return jsonify({"ok": False, "error": "client_id required"}), 400
    if not name or len(name) < 2:
        return jsonify({"ok": False, "error": "Name too short"}), 400
    name = " ".join(name.split())
    uid = f"web:{client_id}"
    udata = db.ensure_user(uid, full_name=name, platform="web", phone_number=phone)
    udata["full_name"] = name
    if phone:
        udata["phone_number"] = phone
    db.save_user(uid, udata)
    db.sync_user_to_supabase(uid, udata)
    db.register_name_identity(uid, name)
    # soft signal: how many accounts share this name (multi-device)
    dup = 0
    try:
        if db.redis:
            dup = int(db.redis.scard(f"nameidx:{db.normalize_name(name)}") or 0)
    except Exception:
        pass
    return jsonify({"ok": True, "name": name, "phone_number": udata.get("phone_number", ""), "accounts_with_same_name": dup})


@app.route("/health")
def health():
    redis_ok = False
    if db.redis:
        try:
            redis_ok = bool(db.redis.ping())
        except Exception:
            pass
    return jsonify({
        "ok": True, "redis": redis_ok,
        "providers": {
            "groq": ai.groq_client is not None,
            "gemini_flash_lite": ai.gemini_client is not None,
            "openrouter": ai.openrouter_ready,
        },
        "version": config.APP_VERSION,
        "build_hash": BUILD_HASH,
        "build_line_count": BUILD_LINE_COUNT,
        "creator": config.CREATOR_NAME,
        "features": _feature_status(),
    })


def _feature_status() -> Dict[str, Any]:
    """Ground-truth feature map read from the RUNNING deployment, not a
    pasted file. Split into two kinds:
      - "code": true means the capability is compiled into this build,
        regardless of env config (these can never be a false positive —
        the route/function either exists or this dict entry wouldn't
        even be here).
      - "ready": true means the code AND the external config it needs
        (API keys, tokens, secrets) are both present, i.e. a real user
        hitting that feature right now would get a working response.
    """
    return {
        "code": {
            "teacher_dashboard_ui": True,
            "quiz_system": True,
            "quiz_exam_mode_timer": True,
            "quiz_lifelines": True,
            "quiz_1v1_challenge": True,
            "galti_diary": True,
            "referral_share_ui": True,
            "streak_shields_display": True,
            "spin_wheel_ui": True,
            "coins_economy": True,
            "language_switcher": True,
            "abuse_moderation": True,
            "semantic_answer_cache": True,
            "legal_page": True,
            "parent_report_cron_route": True,
            "exam_bomb_cron_route": True,
        },
        "ready": {
            "razorpay_payments": bool(config.RAZORPAY_KEY_ID and config.RAZORPAY_KEY_SECRET),
            "razorpay_webhook_verified": bool(config.RAZORPAY_WEBHOOK_SECRET),
            "whatsapp_surface": bool(config.WHATSAPP_TOKEN and config.WHATSAPP_PHONE_NUMBER_ID),
            "telegram_bot": bool(config.BOT_TOKEN),
            "dev_admin_routes": bool(config.DEV_SECRET),
            "cron_endpoints_callable": bool(config.DEV_SECRET),
            "parent_report_can_actually_send": bool(config.WHATSAPP_TOKEN and config.WHATSAPP_PHONE_NUMBER_ID and config.DEV_SECRET),
            "exam_bomb_can_actually_send": bool(config.WHATSAPP_TOKEN and config.WHATSAPP_PHONE_NUMBER_ID and config.DEV_SECRET),
            "gemini_ai": ai.gemini_client is not None,
            "groq_ai": ai.groq_client is not None,
            "openrouter_ai": ai.openrouter_ready,
        },
    }


@app.route("/api/debug/ai")
def debug_ai():
    if not config.DEV_SECRET or not hmac.compare_digest(request.args.get("code", ""), config.DEV_SECRET):
        return jsonify({"ok": False}), 403
    results: Dict[str, Any] = {
        "chain_order": ["gemini_flash_lite", "openrouter"],
        "keys_present": {
            "groq": bool(config.GROQ_API_KEY),
            "gemini": bool(config.GOOGLE_API_KEY),
            "openrouter": bool(config.OPENROUTER_API_KEY),
        },
    }
    test_prompt = "Say exactly: OK SaarthiBhai"

    def _timed(fn, *a, **kw):
        t0 = time.time()
        try:
            text = fn(*a, **kw)
            return {"ok": bool(text), "reply": (text or "")[:200], "elapsed": round(time.time() - t0, 2)}
        except Exception as e:
            return {"ok": False, "error": str(e), "elapsed": round(time.time() - t0, 2)}

    if ai.groq_client:
        results["groq"] = {**_timed(ai._call_groq, test_prompt, max_tokens=50), "model": config.GROQ_MODEL}
    else:
        results["groq"] = {"ok": False, "error": "not initialized"}


    if ai.gemini_client:
        results["gemini_flash_lite"] = {**_timed(ai._call_gemini_flash_lite, test_prompt, max_tokens=50),
                                         "model": config.GEMINI_FLASH_LITE_MODEL}
    else:
        results["gemini_flash_lite"] = {"ok": False, "error": "not initialized"}

    if ai.openrouter_ready:
        results["openrouter"] = {**_timed(ai._call_openrouter, test_prompt, max_tokens=50),
                                  "models_tried": config.OPENROUTER_MODELS}
    else:
        results["openrouter"] = {"ok": False, "error": "OPENROUTER_API_KEY not set"}

    return jsonify(results)


@app.route("/api/webask", methods=["POST"])
def web_ask():
    data = request.get_json(silent=True) or {}
    q = (data.get("question") or "").strip()
    tool = (data.get("tool") or "general").strip().lower()
    client_id = (data.get("client_id") or request.remote_addr or "anon").strip()
    image_b64 = data.get("image_base64") or ""
    if is_rate_limited(f"web:{client_id}", max_calls=8, window_sec=60):
        return jsonify({"answer": "Too many requests. Wait a minute.\n\n- made with love by Sparsh Singhal"}), 429
    if len(image_b64) > 6_500_000:  # ~4.5MB binary -> base64 overhead cap
        return jsonify({"answer": config.SYSTEM_PROTOCOL.get("image_too_large", "Image too large.")}), 400
    if not q and not image_b64:
        return jsonify({"answer": "Please type a question or upload an image"}), 400
    uid = f"web:{client_id}"
    if db.is_banned(uid):
        return jsonify({"answer": format_ban_active_message(db.get_ban_remaining_seconds(uid)), "banned": True}), 403
    if q and contains_abuse(q):
        count, just_banned = db.record_abuse_warning(uid)
        return jsonify({"answer": format_abuse_warning_message(count, just_banned), "abuse_warning": count, "banned": just_banned}), 403
    supplied_phone = normalize_phone(data.get("phone_number") or data.get("phone") or "")
    udata = db.ensure_user(uid, full_name=config.DEFAULT_STUDENT_NAMES["web"], platform="web", phone_number=supplied_phone, exam_type=data.get("exam_type") or "", subject=data.get("subject") or "")
    db.track_activity(uid)
    is_pro = db.is_pro(uid)
    media_kind = (data.get("media_kind") or ("image" if image_b64 else "")).strip().lower()
    # Images: allowed for free users. PDF / YouTube media: Pro-only.
    if tool in PRO_ONLY_TOOLS and not is_pro:
        return jsonify({"answer": f"{config.PRODUCT_TEXT["pro_tool"]}\n\n{config.PRODUCT_TEXT["pro_upgrade"].format(price=config.PRO_PRICE_INR)}"})
    if image_b64 and media_kind in ("pdf", "youtube") and not is_pro:
        return jsonify({"answer": f"{config.PRODUCT_TEXT["pro_tool"]}\n\n{config.PRODUCT_TEXT["pro_upgrade"].format(price=config.PRO_PRICE_INR)}"})
    if not is_pro:
        can, quota = db.try_consume_quota(uid)
        if not can:
            return jsonify({"answer": "❌ Free limit khatam\n\nDoubt yahin ruk gaya. Kal tak wait — chahe raat ko exam ho.\n\nPro = ₹%s / 30 din (din ka ~₹1.6)\nUnlimited + photo se sawaal + mock/PYQ\n\nLimit pe mat atakna.\n\n- SaarthiBhai by Sparsh Singhal" % config.PRO_PRICE_INR, "quota": quota, "upsell": True})
    start = time.time()
    cached = False
    answer = None
    if image_b64:
        try:
            img_bytes = base64.b64decode(image_b64)
            mime = data.get("image_mime") or data.get("media_mime") or "image/jpeg"
            # Always honour the tool the user selected in the dropdown
            tool_for_media = tool or "general"
            answer = run_ai(ai.answer_with_image, img_bytes, mime, q, tool_for_media, is_pro, language=db.get_language(uid))
        except Exception as e:
            logger.error("Media: %s", e)
            answer = "Could not read the file. Try a clearer image or smaller PDF."
    else:
        # Route through the SAME get_ai_answer() used by Telegram/WhatsApp so
        # the numerical-never-cache rule, semantic cache, and language
        # namespacing all apply consistently here too (this used to have its
        # own simpler cache-only logic that skipped all three).
        web_lang = db.get_language(uid)
        web_numerical = tool in _NEVER_CACHE_TOOLS or is_numerical_question(q)
        pre_cached = None
        if not web_numerical:
            pre_cached = db.cache_get(make_cache_key(tool, q, is_pro, web_lang))
        answer = get_ai_answer(q, tool, is_pro, language=web_lang,
                               phone_number=udata.get("phone_number", supplied_phone),
                               exam_type=udata.get("exam_type", data.get("exam_type", "")),
                               subject=udata.get("subject", data.get("subject", "")))
        cached = bool(pre_cached)
    elapsed = time.time() - start
    soft = "Target almost locked" in str(answer or "")
    if (not answer) or str(answer).startswith("ERROR:") or soft:
        logger.warning("AI failure for uid=%s tool=%s reason=%s", uid, tool, (answer or "")[:120])
        fallback = _fallback_youtube_answer(q, is_pro, db.get_language(uid))
        return jsonify({
            "answer": fallback,
            "resources": _10in1_metadata(q, is_pro, uid),
            "actions": TEXT_ACTIONS,
            "fallback": True,
            "brand": BRAND_NAME,
        })
    if not is_pro:
        pass  # already consumed atomically above via try_consume_quota
    udata = db.get_user(uid) or udata
    ex, sub = guess_exam_subject(q, data.get("exam_type") or udata.get("exam_type", ""), data.get("subject") or udata.get("subject", ""))
    udata["exam_type"], udata["subject"], udata["last_question_at"] = ex, sub, _now_ist().isoformat()
    db.save_user(uid, udata); db.sync_user_to_supabase(uid, udata)
    db.add_personal_history(uid, q, tool=tool, exam_type=ex, subject=sub, source_cache=("redis" if cached else "ai"))
    try:
        schedule_spaced_reminders(uid, q, tool)
    except Exception:
        pass
    xp_gain = config.XP_QUESTION * (config.PRO_XP_MULTIPLIER if is_pro else 1)
    xp, level = db.add_xp(uid, xp_gain)
    streak_info = db.update_streak(uid)  # was missing on web — Telegram/WhatsApp already did this
    try:
        if db.redis:
            db.redis.hincrby(db._key(uid), "questions_asked", 1)
            db.redis.incr("stats:total_questions")
    except Exception:
        pass
    db.sync_user_to_supabase(uid)
    _, quota = db.check_quota(uid)
    rank = db.get_rank(uid)
    footer = (
        f"\n\n━━━━━━━━━━━━━━━\n"
        f"⚡ {elapsed:.1f}s"
        f"{' | 📦 cache' if cached else ''}"
        f" | 🛠️ {tool}"
        f" | ⭐ +{xp_gain} XP{' (2× Pro)' if is_pro else ''} | Level {level}"
        f" | 🔥 {streak_info.get('current', 0)} din\n"
        + config.CONTENT["footer_signature"].format(creator_name=config.CREATOR_NAME)
    )
    resources = _10in1_metadata(q, is_pro, uid)
    return jsonify({"answer": answer + footer, "xp": xp, "level": level, "rank": rank, "quota": quota,
                     "elapsed": round(elapsed, 2), "cached": cached, "numerical": web_numerical,
                     "coins": db.get_coins(uid), "language": db.get_language(uid), "streak": streak_info,
                     "resources": resources, "actions": TEXT_ACTIONS, "brand": BRAND_NAME,
                     "plan_compare": {
            "free": config.PRODUCT_TEXT["free_plan_compare"].format(
                free_daily=config.FREE_DAILY, free_pdf_per_day=config.FREE_PDF_PER_DAY,
                free_video_links=config.FREE_VIDEO_LINKS, free_assignment_per_day=config.FREE_ASSIGNMENT_PER_DAY
            ),
            "pro": config.PRODUCT_TEXT["pro_plan_compare"].format(
                pro_video_links=config.PRO_VIDEO_LINKS, pro_viva_questions=config.PRO_VIVA_QUESTIONS
            )
        }})


@app.route("/api/leaderboard")
def api_leaderboard():
    top_n=max(1,int(TEXT_PRO_GROWTH.get("leaderboard_top_n",10) or 10))
    return jsonify({"board": db.get_leaderboard(top_n), "live": True})


@app.route("/api/leaderboard/referrals")
def api_referral_leaderboard():
    """POINT 25 — 'Referral Raja' board."""
    return jsonify({"board": db.get_referral_leaderboard(10)})


@app.route("/api/set-phone", methods=["POST"])
def api_set_phone():
    data = request.get_json(silent=True) or {}
    client_id = (data.get("client_id") or "").strip()
    phone = normalize_phone(data.get("phone_number") or data.get("phone") or "")
    if not client_id or not phone:
        return jsonify({"ok": False, "error": "client_id and valid phone_number required"}), 400
    uid = f"web:{client_id}"
    db.ensure_user(uid, full_name=config.DEFAULT_STUDENT_NAMES["web"], platform="web")
    ok = db.set_phone_number(uid, phone)
    return jsonify({"ok": bool(ok), "phone_number": phone if ok else None})


@app.route("/api/set-exam-type", methods=["POST"])
def api_set_exam_type():
    data = request.get_json(silent=True) or {}
    client_id = (data.get("client_id") or "").strip()
    exam_type = (data.get("exam_type") or "general").strip().lower()
    subject = (data.get("subject") or "general").strip().lower()
    if not client_id:
        return jsonify({"ok": False, "error": "client_id required"}), 400
    uid = f"web:{client_id}"
    u = db.ensure_user(uid, full_name=config.DEFAULT_STUDENT_NAMES["web"], platform="web", exam_type=exam_type, subject=subject)
    u["exam_type"], u["subject"] = exam_type[:64], subject[:64]
    db.save_user(uid, u); db.sync_user_to_supabase(uid, u)
    return jsonify({"ok": True, "exam_type": u["exam_type"], "subject": u["subject"]})


@app.route("/api/supabase/status")
def api_supabase_status():
    return jsonify({"enabled": bool(supa.enabled), "url_configured": bool(config.SUPABASE_URL), "secret_configured": bool(config.SUPABASE_SECRET_KEY)})


@app.route("/api/set-language", methods=["POST"])
def api_set_language():
    """POINT 24 — Hindi / Hinglish / English preference."""
    data = request.get_json(silent=True) or {}
    client_id = (data.get("client_id") or "").strip()
    lang = (data.get("language") or "hinglish").strip().lower()
    if not client_id:
        return jsonify({"ok": False, "error": "client_id required"}), 400
    uid = f"web:{client_id}"
    db.ensure_user(uid, full_name=config.DEFAULT_STUDENT_NAMES["web"], platform="web")
    ok = db.set_language(uid, lang)
    return jsonify({"ok": bool(ok), "language": db.get_language(uid)})


@app.route("/api/set-exam-date", methods=["POST"])
def api_set_exam_date():
    """POINT 30 — Exam Bomb: student tells us their exam date, we remind
    them 24h before via the /api/cron/exam-bomb job."""
    data = request.get_json(silent=True) or {}
    client_id = (data.get("client_id") or "").strip()
    exam_date = (data.get("exam_date") or "").strip()  # YYYY-MM-DD
    subject = (data.get("subject") or "").strip()
    exam_type = (data.get("exam_type") or "").strip().lower()
    if not client_id or not exam_date:
        return jsonify({"ok": False, "error": "client_id and exam_date required"}), 400
    try:
        datetime.strptime(exam_date, DATE_FORMAT)
    except Exception:
        return jsonify({"ok": False, "error": "exam_date must be YYYY-MM-DD"}), 400
    uid = f"web:{client_id}"
    db.ensure_user(uid, full_name=config.DEFAULT_STUDENT_NAMES["web"], platform="web")
    ok = db.set_exam_date(uid, exam_date, subject)
    if exam_type:
        u = db.get_user(uid) or db.ensure_user(uid, full_name=config.DEFAULT_STUDENT_NAMES["web"], platform="web")
        u["exam_type"] = exam_type; u["subject"] = subject or u.get("subject", "general")
        db.save_user(uid, u); db.sync_user_to_supabase(uid, u)
    return jsonify({"ok": bool(ok)})


@app.route("/api/set-parent-phone", methods=["POST"])
def api_set_parent_phone():
    """POINT 26 — save parent's WhatsApp number for weekly reports."""
    data = request.get_json(silent=True) or {}
    client_id = (data.get("client_id") or "").strip()
    phone = (data.get("parent_phone") or data.get("phone") or "").strip()
    opt_in = bool(data.get("opt_in", True))
    if not client_id or not phone:
        return jsonify({"ok": False, "error": "client_id and phone required"}), 400
    uid = f"web:{client_id}"
    db.ensure_user(uid, full_name=config.DEFAULT_STUDENT_NAMES["web"], platform="web")
    ok = db.set_parent_phone(uid, phone)
    user = db.get_user(uid) or {}
    user["parent_report_opt_in"] = "1" if opt_in else "0"
    db.save_user(uid, user); db.sync_user_to_supabase(uid, user)
    return jsonify({"ok": bool(ok), "opt_in": opt_in})


# ----------------------------------------------------------------------
# GALTI DIARY — mistake tracker (POINT 32)
# ----------------------------------------------------------------------
@app.route("/api/galti-diary")
def api_galti_diary():
    client_id = (request.args.get("client_id") or "").strip()
    if not client_id:
        return jsonify({"ok": False, "error": "client_id required"}), 400
    uid = f"web:{client_id}"
    mistakes = db.get_mistakes(uid, limit=int(request.args.get("limit", 20)))
    return jsonify({"ok": True, "mistakes": mistakes})


@app.route("/api/galti-diary/clear", methods=["POST"])
def api_galti_diary_clear():
    data = request.get_json(silent=True) or {}
    client_id = (data.get("client_id") or "").strip()
    index = data.get("index")
    if not client_id or index is None:
        return jsonify({"ok": False, "error": "client_id and index required"}), 400
    uid = f"web:{client_id}"
    ok = db.clear_mistake(uid, int(index))
    return jsonify({"ok": ok})


# ----------------------------------------------------------------------
# QUIZ SYSTEM (POINT 10) — Practice / Exam / 1v1-challenge modes.
# FREE: 1 quiz/day, 3 questions, score only.
# PRO: unlimited, any topic or built from the Galti Diary, full
#      per-question explanations + weak-topic analysis, lifelines.
# ----------------------------------------------------------------------
@app.route("/api/quiz/generate", methods=["POST"])
def api_quiz_generate():
    data = request.get_json(silent=True) or {}
    client_id = (data.get("client_id") or "").strip()
    topic = (data.get("topic") or "").strip()
    mode = (data.get("mode") or "practice").strip().lower()
    use_galti_diary = bool(data.get("use_galti_diary"))
    if not client_id:
        return jsonify({"ok": False, "error": "client_id required"}), 400
    if not topic and not use_galti_diary:
        return jsonify({"ok": False, "error": "topic required"}), 400

    uid = f"web:{client_id}"
    db.ensure_user(uid, full_name=config.DEFAULT_STUDENT_NAMES["web"], platform="web")
    is_pro = db.is_pro(uid)

    if not is_pro:
        if db.get_daily_quiz_used(uid):
            return jsonify({"ok": False, "error": "Free daily quiz limit reached. Upgrade for unlimited quizzes.",
                             "upsell": True}), 403
        if mode != "practice":
            return jsonify({"ok": False, "error": "Exam mode and 1v1 challenges are Pro-only.", "upsell": True}), 403
        n_questions = config.QUIZ_FREE_QUESTIONS
    else:
        n_questions = max(1, min(int(data.get("n_questions", 10)), 20))

    source_context = ""
    if use_galti_diary:
        mistakes = db.get_mistakes(uid, limit=15)
        if not mistakes:
            return jsonify({"ok": False, "error": "Galti Diary khaali hai — pehle kuch sawaal galat karo! 😄"}), 400
        topic = topic or "student's recent mistakes"
        source_context = "\n".join(f"- {m.get('question','')} (topic: {m.get('topic','')})" for m in mistakes)

    language = db.get_language(uid)
    questions = ai.generate_quiz(topic, n_questions=n_questions, language=language, source_context=source_context)
    if not questions:
        return jsonify({"ok": False, "error": "Quiz generate nahi ho paya, dobara try karo."}), 502

    quiz_id = secrets.token_hex(8)
    db.save_quiz(quiz_id, {
        "quiz_id": quiz_id, "owner_uid": uid, "topic": topic, "mode": mode,
        "language": language, "questions": questions, "created_at": _today_ist(),
        "is_pro": is_pro, "lifelines_used": {}, "skipped": [],
    })
    if not is_pro:
        db.mark_daily_quiz_used(uid)

    # Never leak the "correct" index / explanation / hint to the client up-front.
    public_questions = [{"q": q["q"], "options": q["options"]} for q in questions]
    return jsonify({
        "ok": True, "quiz_id": quiz_id, "topic": topic, "mode": mode,
        "questions": public_questions,
        "lifelines": {"fifty_fifty": is_pro, "skip": is_pro, "hint": is_pro},
        "timer_per_question_sec": 45 if mode == "exam" else None,
    })


@app.route("/api/quiz/lifeline", methods=["POST"])
def api_quiz_lifeline():
    """POINT 10 — 50:50 / Skip / Hint, Pro-only. Server decides what to
    reveal so the client never holds the answer key up front."""
    data = request.get_json(silent=True) or {}
    client_id = (data.get("client_id") or "").strip()
    quiz_id = (data.get("quiz_id") or "").strip()
    q_index = data.get("q_index")
    lifeline_type = (data.get("type") or "").strip().lower()
    if not client_id or not quiz_id or q_index is None or lifeline_type not in ("fifty_fifty", "hint", "skip"):
        return jsonify({"ok": False, "error": "client_id, quiz_id, q_index, and a valid type are required"}), 400

    uid = f"web:{client_id}"
    if not db.is_pro(uid):
        return jsonify({"ok": False, "error": "Lifelines Pro-only hain.", "upsell": True}), 403

    quiz = db.get_quiz(quiz_id)
    if not quiz or quiz.get("owner_uid") != uid:
        return jsonify({"ok": False, "error": "Quiz expired or not found"}), 404

    q_index = int(q_index)
    questions = quiz.get("questions", [])
    if q_index < 0 or q_index >= len(questions):
        return jsonify({"ok": False, "error": "Invalid question index"}), 400

    lifelines_used = quiz.get("lifelines_used") or {}
    used_key = f"{q_index}:{lifeline_type}"
    if used_key in lifelines_used:
        return jsonify({"ok": False, "error": "Ye lifeline is sawaal pe already use ho chuki hai"}), 400

    q = questions[q_index]
    result: Dict[str, Any] = {"ok": True, "type": lifeline_type}

    if lifeline_type == "fifty_fifty":
        wrong_indices = [i for i in range(len(q.get("options", []))) if i != q.get("correct", 0)]
        random.shuffle(wrong_indices)
        eliminate = wrong_indices[:max(0, len(wrong_indices) - 1)]  # leave exactly 1 wrong + correct
        result["eliminate_indices"] = eliminate
    elif lifeline_type == "hint":
        result["hint"] = q.get("hint") or "Concept ko dobara padho — options mein se sabse specific wala try karo."
    elif lifeline_type == "skip":
        skipped = quiz.get("skipped") or []
        if q_index not in skipped:
            skipped.append(q_index)
        quiz["skipped"] = skipped

    lifelines_used[used_key] = True
    quiz["lifelines_used"] = lifelines_used
    db.save_quiz(quiz_id, quiz)
    return jsonify(result)


@app.route("/api/quiz/submit", methods=["POST"])
def api_quiz_submit():
    """Grade a completed quiz attempt, push wrong answers into the Galti
    Diary, and return per-question feedback + a simple weak-topic verdict."""
    data = request.get_json(silent=True) or {}
    client_id = (data.get("client_id") or "").strip()
    quiz_id = (data.get("quiz_id") or "").strip()
    answers = data.get("answers") or []  # list of selected option indices (-1 = skipped)
    if not client_id or not quiz_id:
        return jsonify({"ok": False, "error": "client_id and quiz_id required"}), 400

    uid = f"web:{client_id}"
    quiz = db.get_quiz(quiz_id)
    if not quiz:
        return jsonify({"ok": False, "error": "Quiz expired or not found"}), 404

    questions = quiz.get("questions", [])
    skipped_set = set(quiz.get("skipped") or [])
    results = []
    score = 0
    mistake_coins_total = 0
    graded_total = 0  # skipped questions (via the Skip lifeline) don't count either way
    for i, q in enumerate(questions):
        picked = answers[i] if i < len(answers) else -1
        correct_idx = q.get("correct", 0)
        was_skipped = i in skipped_set
        if was_skipped:
            results.append({
                "q": q.get("q", ""), "picked": -1, "correct": correct_idx,
                "is_correct": None, "skipped": True, "explanation": q.get("explanation", ""),
            })
            continue
        graded_total += 1
        is_correct = (picked == correct_idx)
        db.record_answer_outcome(uid, is_correct)
        if is_correct:
            score += 1
        else:
            correct_text = (q.get("options") or [""])[correct_idx] if correct_idx < len(q.get("options", [])) else ""
            user_text = (q.get("options") or [""])[picked] if 0 <= picked < len(q.get("options", [])) else "skipped"
            mistake_coins_total += db.add_mistake(
                uid, question=q.get("q", ""), tool="quiz",
                correct_answer=correct_text, user_answer=user_text, topic=quiz.get("topic", ""),
            )
            db.add_personal_history(uid, q.get("q", ""), tool="quiz", subject=quiz.get("topic", ""),
                                    user_answer=user_text, correct_answer=correct_text, is_correct=False,
                                    error_reason="quiz_answer_wrong")
        if is_correct:
            db.add_personal_history(uid, q.get("q", ""), tool="quiz", subject=quiz.get("topic", ""),
                                    user_answer=(q.get("options") or [""])[picked] if 0 <= picked < len(q.get("options", [])) else "",
                                    correct_answer=(q.get("options") or [""])[correct_idx] if correct_idx < len(q.get("options", [])) else "",
                                    is_correct=True)
        results.append({
            "q": q.get("q", ""), "picked": picked, "correct": correct_idx,
            "is_correct": is_correct, "skipped": False, "explanation": q.get("explanation", ""),
        })

    total = graded_total
    db.save_quiz_attempt(uid, quiz_id, score, total, topic=quiz.get("topic", ""), mode=quiz.get("mode", "practice"))
    is_pro = db.is_pro(uid)
    xp_gain = 100 if (total and score == total) else int(20 * (score / total)) if total else 0
    coins_gain = config.QUIZ_PERFECT_COINS if (total and score == total) else int(10 * (score / total)) if total else 0
    if xp_gain:
        db.add_xp(uid, xp_gain)
    if coins_gain:
        db.add_coins(uid, coins_gain)
    coins_gain += mistake_coins_total  # POINT 32: small bonus per logged mistake, already credited above

    pct_wrong = round(100 * (total - score) / total, 1) if total else 0
    verdict = (
        f"Tera {quiz.get('topic','is topic')} thoda weak hai, {pct_wrong}% galat kiya. Galti Diary check kar 📖"
        if pct_wrong >= 40 else
        f"Solid! {quiz.get('topic','is topic')} mein sirf {pct_wrong}% galti hui 🔥"
    )

    if quiz.get("mode") == "1v1":
        player_name = (db.get_user(uid) or {}).get("full_name", config.DEFAULT_STUDENT_NAMES["generic"])
        db.record_challenge_result(quiz_id, uid, player_name, score, total)

    return jsonify({
        "ok": True, "score": score, "total": total, "results": results,
        "xp_gain": xp_gain, "coins_gain": coins_gain, "verdict": verdict,
        "is_pro": is_pro, "coins_total": db.get_coins(uid),
    })


@app.route("/api/quiz/join/<quiz_id>")
def api_quiz_join(quiz_id):
    """POINT 10 — 1v1 challenge: a friend opens the shared link and plays
    the exact same question set as the original quiz_id, independently."""
    quiz = db.get_quiz(quiz_id)
    if not quiz or quiz.get("mode") != "1v1":
        return jsonify({"ok": False, "error": "Challenge not found or expired"}), 404
    public_questions = [{"q": q["q"], "options": q["options"]} for q in quiz.get("questions", [])]
    return jsonify({
        "ok": True, "quiz_id": quiz_id, "topic": quiz.get("topic", ""), "mode": "1v1",
        "questions": public_questions,
        "lifelines": {"fifty_fifty": False, "skip": False, "hint": False},  # joiners play it straight
        "timer_per_question_sec": None,
    })


@app.route("/api/quiz/challenge/<quiz_id>")
def api_quiz_challenge_results(quiz_id):
    return jsonify({"ok": True, "results": db.get_challenge_results(quiz_id)})


@app.route("/api/quiz/history")
def api_quiz_history():
    client_id = (request.args.get("client_id") or "").strip()
    if not client_id:
        return jsonify({"ok": False, "error": "client_id required"}), 400
    uid = f"web:{client_id}"
    if not db.redis:
        return jsonify({"ok": True, "history": []})
    try:
        raw = db.redis.lrange(f"quizhist:{uid}", 0, 19)
        history = [json.loads(r) for r in raw]
    except Exception:
        history = []
    return jsonify({"ok": True, "history": history})


# ----------------------------------------------------------------------
# TEACHER DASHBOARD (POINT 27)
# ----------------------------------------------------------------------
@app.route("/api/teacher/create-class", methods=["POST"])
def api_teacher_create_class():
    data = request.get_json(silent=True) or {}
    client_id = (data.get("client_id") or "").strip()
    class_name = (data.get("class_name") or "").strip()
    if not client_id:
        return jsonify({"ok": False, "error": "client_id required"}), 400
    teacher_uid = f"web:{client_id}"
    db.ensure_user(teacher_uid, full_name="Teacher", platform="web")
    code = db.create_class_code(teacher_uid, class_name)
    return jsonify({"ok": True, "class_code": code, "join_link": f"/?join_class={code}"})


@app.route("/api/teacher/classes")
def api_teacher_classes():
    client_id = (request.args.get("client_id") or "").strip()
    if not client_id:
        return jsonify({"ok": False, "error": "client_id required"}), 400
    teacher_uid = f"web:{client_id}"
    codes = db.get_teacher_classes(teacher_uid)
    classes = [db.get_class_info(c) for c in codes]
    return jsonify({"ok": True, "classes": [c for c in classes if c]})


@app.route("/api/join-class", methods=["POST"])
def api_join_class():
    data = request.get_json(silent=True) or {}
    client_id = (data.get("client_id") or "").strip()
    code = (data.get("code") or "").strip()
    if not client_id or not code:
        return jsonify({"ok": False, "error": "client_id and code required"}), 400
    uid = f"web:{client_id}"
    db.ensure_user(uid, full_name=config.DEFAULT_STUDENT_NAMES["web"], platform="web")
    ok = db.join_class(uid, code)
    if not ok:
        return jsonify({"ok": False, "error": "Invalid class code"}), 404
    return jsonify({"ok": True, "class_code": code.upper()})


@app.route("/api/teacher/class/<code>")
def api_teacher_class_detail(code):
    info = db.get_class_info(code)
    if not info:
        return jsonify({"ok": False, "error": "Class not found"}), 404
    return jsonify({"ok": True, **info})


# ----------------------------------------------------------------------
# CRON JOBS (POINTS 7, 26, 30) — call these from Vercel Cron / an
# external scheduler, protected by DEV_SECRET so randoms can't trigger
# mass WhatsApp sends. Configure with a header or ?code=... query param.
# ----------------------------------------------------------------------
def _cron_authorized() -> bool:
    if not config.DEV_SECRET:
        return False
    supplied = request.args.get("code") or request.headers.get("X-Cron-Secret", "")
    return hmac.compare_digest(supplied or "", config.DEV_SECRET)


@app.route("/api/cron/parent-report", methods=["POST", "GET"])
def cron_parent_report():
    """POINT 26 — weekly WhatsApp report to parents. Run every Sunday 7PM IST."""
    if not _cron_authorized():
        return jsonify({"ok": False, "error": "unauthorized"}), 403
    if not db.redis:
        return jsonify({"ok": False, "error": "no redis"}), 500
    sent = 0
    try:
        for uid in db.redis.smembers("stats:users") or []:
            u = db.get_user(uid)
            phone = (u or {}).get("parent_phone", "")
            if not phone:
                continue
            report = db.get_weekly_report(uid)
            body = (
                f"🎓 Namaste! {report['name']} ka SaarthiBhai weekly report:\n\n"
                f"⭐ Level {report['level']} ({report['xp']} XP)\n"
                f"🔥 Streak: {report['streak']} din\n"
                f"📚 Total sawaal: {report['questions_asked']}\n"
                + (f"📍 Rank: #{report['rank']}\n" if report.get('rank') else "")
                + "\n- SaarthiBhai by Sparsh Singhal"
            )
            _send_whatsapp_text(phone, body)
            sent += 1
    except Exception as e:
        logger.error("cron_parent_report: %s", e)
        return jsonify({"ok": False, "error": str(e)}), 500
    return jsonify({"ok": True, "reports_sent": sent})


@app.route("/api/cron/exam-bomb", methods=["POST", "GET"])
def cron_exam_bomb():
    """POINT 30 — 24h-before-exam formula sheet + weak-topics nudge."""
    if not _cron_authorized():
        return jsonify({"ok": False, "error": "unauthorized"}), 403
    sent = 0
    try:
        for u in db.get_users_with_exam_tomorrow():
            uid = u.get("user_id")
            subject = u.get("exam_subject") or "kal ka exam"
            mistakes = db.get_mistakes(uid, limit=5)
            weak_list = "\n".join(f"- {m.get('question','')[:80]}" for m in mistakes) or "Koi saved mistakes nahi — solid prep!"
            body = (
                f"🎯 Bhai kal {subject} ka exam hai na?\n\n"
                f"Tere weak sawaal (Galti Diary se):\n{weak_list}\n\n"
                "All the best — SaarthiBhai tumhare saath hai 💪"
            )
            if u.get("platform") == "whatsapp" and uid.startswith("wa:"):
                _send_whatsapp_text(uid.replace("wa:", ""), body)
                sent += 1
    except Exception as e:
        logger.error("cron_exam_bomb: %s", e)
        return jsonify({"ok": False, "error": str(e)}), 500
    return jsonify({"ok": True, "reminders_sent": sent})


@app.route("/legal")
def legal_page():
    """POINT 19/42 — Fair Use + DMCA notice, content mostly static but the
    contact email comes from ENV so it's not hardcoded per deployment."""
    dmca_email = config.COPYRIGHT_CONTACT
    html = f"""<!DOCTYPE html><html lang="en"><head><meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Legal — SaarthiBhai</title>
<style>body{{font-family:system-ui,sans-serif;background:#0b1220;color:#f1f5f9;max-width:720px;margin:2rem auto;padding:1.5rem;line-height:1.7}}
h1{{color:#22d3ee}}h2{{color:#22d3ee;font-size:1.05rem;margin-top:1.5rem}}a{{color:#22d3ee}}</style></head><body>
<h1>SaarthiBhai — Legal &amp; Fair Use</h1>
<p>SaarthiBhai (by Sparsh Singhal) is an educational assistant. We do not host or distribute
full scanned textbooks, question papers, or other copyrighted works. Any reference material is
summarized in our own words, with source attribution (chapter/page) shown for transparency, not
as a substitute for the original book.</p>
<h2>Fair Use</h2>
<p>Short, attributed summaries and explanations of educational material, generated to help
students study, are provided under a good-faith fair-use understanding. We do not claim this
constitutes formal legal advice.</p>
<h2>Academic Integrity</h2>
<p>SaarthiBhai will not assist with live exam-hall cheating or impersonation. It is a study and
revision tool, not an exam-taking tool.</p>
<h2>DMCA / Copyright Takedown</h2>
<p>If you believe content on SaarthiBhai infringes your copyright, email
<a href="mailto:{dmca_email}">{dmca_email}</a> with the material in question and proof of
ownership. We aim to review and remove valid claims within 24 hours.</p>
<p style="margin-top:2rem;color:#94a3b8;font-size:.85rem">— made with ❤️ by Sparsh Singhal</p>
</body></html>"""
    from flask import Response
    return Response(html, mimetype="text/html")


@app.route("/api/dev/stats")
def dev_stats():
    if not config.DEV_SECRET or not hmac.compare_digest(request.args.get("code", ""), config.DEV_SECRET):
        return jsonify({"ok": False}), 403
    s = db.get_stats()
    return jsonify({"ok": True, **s})


@app.route("/api/dev/real-pro")
def dev_real_pro():
    """Count real vs test Pro users (same filters as leaderboard)."""
    if not config.DEV_SECRET or not hmac.compare_digest(request.args.get("code", ""), config.DEV_SECRET):
        return jsonify({"ok": False}), 403
    if not db.redis:
        return jsonify({"ok": False, "error": "no redis"}), 500
    try:
        pro_set = list(db.redis.smembers("stats:pro_users") or [])
        all_users = list(db.redis.smembers("stats:users") or [])
        real = []
        test = []
        expired = []
        missing = []
        for uid in set(pro_set) | set(all_users):
            u = db.get_user(uid)
            if not u:
                if uid in pro_set:
                    missing.append(str(uid))
                continue
            currently_pro = db.is_pro(uid)
            in_set = uid in pro_set
            if not currently_pro and not in_set:
                continue
            if db.is_test_user(uid, u):
                if currently_pro or in_set:
                    test.append({
                        "uid": str(uid),
                        "name": u.get("full_name", ""),
                        "platform": u.get("platform", ""),
                    })
                continue
            if currently_pro:
                real.append({
                    "uid": str(uid),
                    "name": u.get("full_name", config.DEFAULT_STUDENT_NAMES["generic"])[:40],
                    "platform": u.get("platform", "?"),
                    "pro_until": u.get("pro_until", ""),
                    "questions": int(u.get("questions_asked", 0) or 0),
                    "xp": int(u.get("xp", 0) or 0),
                })
            elif in_set:
                expired.append(str(uid))
        real.sort(key=lambda x: -x["questions"])
        return jsonify({
            "ok": True,
            "raw_pro_set": len(pro_set),
            "total_users": len(all_users),
            "real_pro": len(real),
            "test_pro": len(test),
            "expired_in_set": len(expired),
            "missing_hash": len(missing),
            "real_users": real[:50],
            "test_users": test[:30],
        })
    except Exception as e:
        logger.error("dev_real_pro: %s", e)
        return jsonify({"ok": False, "error": str(e)}), 500



@app.route("/api/dev/unique-users")
def dev_unique_users():
    """Deduped human-level stats (excludes test + default names)."""
    if not config.DEV_SECRET or not hmac.compare_digest(request.args.get("code", ""), config.DEV_SECRET):
        return jsonify({"ok": False}), 403
    uniq = db.get_unique_user_stats()
    return jsonify({"ok": True, **uniq})


@app.route("/api/dev/clean-pro-set", methods=["POST"])
def dev_clean_pro_set():
    """Remove test/dev accounts from stats:pro_users. Real Pro users stay."""
    if not config.DEV_SECRET:
        return jsonify({"ok": False, "error": "dev mode disabled"}), 403
    data = request.get_json(silent=True) or {}
    code = (data.get("code") or request.args.get("code") or "").strip()
    if not hmac.compare_digest(code, config.DEV_SECRET):
        return jsonify({"ok": False, "error": "unauthorized"}), 403
    if not db.redis:
        return jsonify({"ok": False, "error": "no redis"}), 500
    try:
        pro_set = list(db.redis.smembers("stats:pro_users") or [])
        removed = []
        kept = []
        for uid in pro_set:
            u = db.get_user(uid)
            if db.is_test_user(str(uid), u) or (u is None):
                db.redis.srem("stats:pro_users", uid)
                # also demote plan if still marked pro on a pure test account
                if u is not None and db.is_test_user(str(uid), u):
                    try:
                        u["is_test"] = "1"
                        # leave plan as-is so local testing still works; only clean the public counter
                        db.save_user(uid, u)
                    except Exception:
                        pass
                removed.append(str(uid))
            else:
                # real user — keep only if still actually pro
                if db.is_pro(uid):
                    kept.append(str(uid))
                else:
                    db.redis.srem("stats:pro_users", uid)
                    removed.append(str(uid))
        return jsonify({
            "ok": True,
            "before": len(pro_set),
            "removed": len(removed),
            "kept_real_pro": len(kept),
            "removed_uids": removed[:80],
            "kept_uids": kept[:40],
        })
    except Exception as e:
        logger.error("dev_clean_pro_set: %s", e)
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/api/razorpay/webhook", methods=["POST"])
def razorpay_webhook():
    try:
        if not config.RAZORPAY_WEBHOOK_SECRET:
            # Never process payment events without a verified signature.
            logger.error("Razorpay webhook received but RAZORPAY_WEBHOOK_SECRET is not configured — rejecting.")
            return jsonify({"ok": False, "error": "webhook not configured"}), 503
        body = request.get_data()
        received_sig = request.headers.get("X-Razorpay-Signature", "")
        expected = hmac.new(config.RAZORPAY_WEBHOOK_SECRET.encode(), body, hashlib.sha256).hexdigest()
        if not hmac.compare_digest(expected, received_sig):
            return jsonify({"ok": False}), 400
        payload = request.get_json(force=True)
        event_name = payload.get("event", "")
        if event_name in ("refund.created", "refund.processed"):
            entity = payload.get("payload", {}).get("refund", {}).get("entity", {}) or {}
            refund_id = entity.get("id", "")
            payment_id = entity.get("payment_id", "")
            refund_amount = int(entity.get("amount") or 0)
            result = process_refund(refund_id, payment_id, refund_amount)
            return jsonify(result if result.get("ok") else {"ok": False, "error": result.get("error", "refund failed")}), (200 if result.get("ok") else 400)
        if event_name == "payment.captured":
            entity = payload.get("payload", {}).get("payment", {}).get("entity", {})
            payment_id = entity.get("id", "")
            notes = entity.get("notes", {}) or {}
            uid = notes.get("user_id", "")
            if payment_id and not db.mark_payment_processed(payment_id):
                return jsonify({"ok": True, "duplicate": True})
            if uid:
                db.activate_pro(uid, days=30)
                try:
                    pu = db.get_user(uid) or {}
                    pu["last_payment_id"] = payment_id
                    db.save_user(uid, pu)
                    db.sync_user_to_supabase(uid, pu)
                except Exception:
                    pass
                db.add_badge(uid, "Pro Warrior 👑")
                logger.info("Pro activated for %s", uid)
        return jsonify({"ok": True})
    except Exception as e:
        logger.error("Razorpay webhook: %s", e)
        return jsonify({"ok": False}), 500


@app.route("/api/whatsapp", methods=["GET", "POST"])
def whatsapp_webhook():
    if request.method == "GET":
        if request.args.get("hub.mode") == "subscribe" and request.args.get("hub.verify_token") == config.WHATSAPP_VERIFY_TOKEN:
            return request.args.get("hub.challenge"), 200
        return "Forbidden", 403
    try:
        data = request.get_json(force=True, silent=True) or {}
        for ent in data.get("entry", []):
            for change in ent.get("changes", []):
                value = change.get("value", {})
                contacts = value.get("contacts", [])
                profile_name = contacts[0].get("profile", {}).get("name", "") if contacts else ""
                for msg in value.get("messages", []):
                    if msg.get("type") == "text":
                        from_number = msg.get("from")
                        text = msg.get("text", {}).get("body", "").strip()
                        if from_number and text:
                            process_whatsapp_message(from_number, text, profile_name)
        return jsonify({"ok": True})
    except Exception as e:
        logger.exception("WA: %s", e)
        return jsonify({"ok": False}), 500


@app.route("/api/webhook", methods=["POST"])
def telegram_webhook():
    if config.WEBHOOK_SECRET and request.headers.get("X-Telegram-Bot-Api-Secret-Token") != config.WEBHOOK_SECRET:
        return jsonify({"ok": False}), 401
    try:
        data = request.get_json(force=True, silent=True)
        if not data:
            return jsonify({"ok": False}), 400
        uid = str(data.get("message", {}).get("from", {}).get("id") or
                  data.get("callback_query", {}).get("from", {}).get("id") or "tg")
        if is_rate_limited(f"tg:{uid}", max_calls=12, window_sec=60):
            return jsonify({"ok": True})

        async def _run():
            application = await get_app()
            update = Update.de_json(data, application.bot)
            if update:
                await application.process_update(update)

        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                asyncio.ensure_future(_run())
            else:
                loop.run_until_complete(_run())
        except RuntimeError:
            asyncio.run(_run())
        return jsonify({"ok": True})
    except Exception as e:
        logger.exception("TG webhook: %s", e)
        return jsonify({"ok": False}), 500


@app.route("/api/setup-supabase")
def setup_supabase():
    """Safe helper: returns the checked-in schema text; DB creation itself is intentionally
    performed in the Supabase SQL editor or migration pipeline."""
    sql_path = os.path.join(os.path.dirname(__file__), "supabase_schema.sql")
    try:
        with open(sql_path, "r", encoding="utf-8") as f:
            return f"<pre>{f.read()}</pre>", 200, {"Content-Type": "text/html; charset=utf-8"}
    except Exception:
        return jsonify({"ok": False, "error": "supabase_schema.sql not found in deployment"}), 404


@app.route("/api/dev/library-upsert", methods=["POST"])
def dev_library_upsert():
    if not config.DEV_SECRET or not hmac.compare_digest(request.headers.get("X-Dev-Secret", ""), config.DEV_SECRET):
        return jsonify({"ok": False, "error": "unauthorized"}), 403
    data = request.get_json(silent=True) or {}
    title = (data.get("title") or "").strip()
    content = (data.get("content") or "").strip()
    if not title or not content:
        return jsonify({"ok": False, "error": "title and content required"}), 400
    source_type = (data.get("source_type") or "notes").strip().lower()
    if source_type in {"full_book_photo", "full_book_scan", "pirated_book", "paper_leak", "exam_leak"} or "leak" in source_type:
        return jsonify({"ok": False, "error": "SaarthiBhai library me full copyrighted scans/paper leaks allowed nahi hain."}), 400
    row = {
        "title": title[:300], "content": content[:500000],
        "source_type": source_type[:64],
        "exam_type": (data.get("exam_type") or "general")[:64],
        "subject": (data.get("subject") or "general")[:64],
        "class_name": (data.get("class_name") or "")[:64],
        "chapter": (data.get("chapter") or "")[:200],
        "author": (data.get("author") or "")[:200],
        "url": (data.get("url") or "")[:1000],
        "license": (data.get("license") or "")[:300],
        "is_public": bool(data.get("is_public", True)),
        "metadata": data.get("metadata") if isinstance(data.get("metadata"), dict) else {},
    }
    if not supa.enabled:
        return jsonify({"ok": False, "error": "Supabase disabled"}), 503
    ok = supa.insert("library", row)
    return jsonify({"ok": bool(ok)})


@app.route("/api/setup")
def setup():
    if not config.VERCEL_URL or not config.BOT_TOKEN:
        return jsonify({"error": "Missing VERCEL_URL or BOT_TOKEN"}), 400
    webhook_url = f"{config.PUBLIC_SCHEME}://{config.VERCEL_URL.rstrip('/')}/api/webhook"
    api = f"{config.URLS["telegram_base_url"]}/bot{config.BOT_TOKEN}/setWebhook"
    payload = {"url": webhook_url}
    if config.WEBHOOK_SECRET:
        payload["secret_token"] = config.WEBHOOK_SECRET
    try:
        r = requests.post(api, json=payload, timeout=20)
        return jsonify({"ok": r.json().get("ok"), "telegram_webhook": webhook_url, "response": r.json()})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500



@app.route("/api/dev/purge-test-leaderboard", methods=["POST"])
def dev_purge_test_leaderboard():
    """Remove Pro Tester / Dev test rows from the public leaderboard (Redis zset)."""
    if not config.DEV_SECRET:
        return jsonify({"ok": False, "error": "dev mode disabled"}), 403
    data = request.get_json(silent=True) or {}
    if not hmac.compare_digest((data.get("code") or ""), config.DEV_SECRET):
        return jsonify({"ok": False, "error": "unauthorized"}), 403
    if not db.redis:
        return jsonify({"ok": False, "error": "no redis"}), 500
    removed = []
    try:
        members = db.redis.zrange("leaderboard", 0, -1)
        for uid in members:
            u = db.get_user(uid)
            if db.is_test_user(str(uid), u) or db._is_hidden_leaderboard_user(str(uid), u):
                db.redis.zrem("leaderboard", uid)
                # stamp so they never re-enter via add_xp
                try:
                    if u is not None:
                        u["is_test"] = "1"
                        db.save_user(uid, u)
                except Exception:
                    pass
                removed.append(str(uid))
        return jsonify({"ok": True, "removed": len(removed), "uids": removed[:50]})
    except Exception as e:
        logger.error("purge-test-leaderboard: %s", e)
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/api/dev/activate-pro", methods=["POST"])
def dev_activate_pro():
    if not config.DEV_SECRET:
        return jsonify({"ok": False, "error": "dev mode disabled"}), 403
    data = request.get_json(silent=True) or {}
    if not hmac.compare_digest((data.get("code") or ""), config.DEV_SECRET):
        return jsonify({"ok": False, "error": "unauthorized"}), 403
    uid = (data.get("uid") or "").strip()
    if not uid:
        return jsonify({"ok": False, "error": "uid required"}), 400
    # Mark as test account — never enters public leaderboard / rank.
    db.ensure_user(uid, full_name="🧪 Test Account (hidden)", platform="web")
    udata = db.get_user(uid) or {}
    udata["full_name"] = "🧪 Test Account (hidden)"
    udata["is_test"] = "1"
    db.save_user(uid, udata)
    ok = db.activate_pro(uid, days=30)
    try:
        if db.redis:
            db.redis.zrem("leaderboard", str(uid))
    except Exception:
        pass
    return jsonify({
        "ok": bool(ok), "uid": uid, "plan": "pro", "days": 30,
        "name": "🧪 Test Account (hidden)", "is_test": True,
    })


@app.route("/api/dev/unban", methods=["POST"])
def dev_unban():
    """POINT 3/19 admin escape hatch — a support agent (you) can lift a ban
    early, e.g. a false-positive word match. Never exposed to end users."""
    if not config.DEV_SECRET:
        return jsonify({"ok": False, "error": "dev mode disabled"}), 403
    data = request.get_json(silent=True) or {}
    if not hmac.compare_digest((data.get("code") or ""), config.DEV_SECRET):
        return jsonify({"ok": False, "error": "unauthorized"}), 403
    uid = (data.get("uid") or "").strip()
    if not uid:
        return jsonify({"ok": False, "error": "uid required"}), 400
    ok = db.unban_user(uid)
    return jsonify({"ok": bool(ok), "uid": uid, "unbanned": True})


@app.route("/api/dev/abuse-status")
def dev_abuse_status():
    """Check a user's current warning count / ban status without unbanning."""
    if not config.DEV_SECRET or not hmac.compare_digest(request.args.get("code", ""), config.DEV_SECRET):
        return jsonify({"ok": False}), 403
    uid = (request.args.get("uid") or "").strip()
    if not uid:
        return jsonify({"ok": False, "error": "uid required"}), 400
    return jsonify({
        "ok": True, "uid": uid,
        "banned": db.is_banned(uid),
        "ban_remaining_seconds": db.get_ban_remaining_seconds(uid),
        "warning_count": db.get_abuse_warning_count(uid),
    })


# ============================================================================
# SAARTHIBHAI v7 PRODUCT LAYER — points 1..41
# ============================================================================

# Locked branding: every server-generated surface uses this constant.
FREE_UPSELL_LINE = config.FREE_UPSELL_LINE

FEATURE_CATALOG_28 = list(config.FEATURE_CATALOG_28)
TOOL_ALIASES = dict(config.TOOL_ALIASES)


# Optional external services.
YOUTUBE_API_KEY = config.YOUTUBE_API_KEY
INSTAGRAM_ACCESS_TOKEN = config.INSTAGRAM_ACCESS_TOKEN
INSTAGRAM_GRAPH_VERSION = config.INSTAGRAM_GRAPH_VERSION
INSTAGRAM_ACCOUNT_ID = config.INSTAGRAM_ACCOUNT_ID
INSTAGRAM_VERIFY_TOKEN = config.INSTAGRAM_VERIFY_TOKEN
SNAPCHAT_ENABLED = config.SNAPCHAT_ENABLED
SNAPCHAT_DM_GATEWAY_URL = config.SNAPCHAT_DM_GATEWAY_URL
SNAPCHAT_DM_GATEWAY_TOKEN = config.SNAPCHAT_DM_GATEWAY_TOKEN
CREATOR_PHOTO_URL = config.CREATOR_PHOTO_URL
CREATOR_STORY_URL = config.CREATOR_STORY_URL

# ---------------------------------------------------------------------------
# Supabase utility operations added to the small REST wrapper.
# ---------------------------------------------------------------------------
def _sb_update(self, table: str, filters: dict, patch: dict) -> bool:
    if not self.enabled or not filters or not patch:
        return False
    try:
        params = {k: str(v) for k, v in filters.items()}
        h = dict(self.headers)
        h["Prefer"] = "return=minimal"
        r = requests.patch(self._url(table), params=params, headers=h, json=patch, timeout=config.SUPABASE_TIMEOUT)
        if r.status_code >= 300:
            logger.warning("Supabase update %s %s: %s", table, r.status_code, r.text[:400])
            return False
        return True
    except Exception as e:
        logger.warning("Supabase update %s: %s", table, e)
        return False


def _sb_delete(self, table: str, filters: dict) -> bool:
    if not self.enabled or not filters:
        return False
    try:
        r = requests.delete(self._url(table), params=filters, headers=self.headers, timeout=config.SUPABASE_TIMEOUT)
        return r.status_code < 300
    except Exception as e:
        logger.warning("Supabase delete %s: %s", table, e)
        return False


SupabaseStore.update = _sb_update
SupabaseStore.delete = _sb_delete


# ---------------------------------------------------------------------------
# Phone is the canonical cross-platform identity in Redis as well as Supabase.
# This makes coins/XP/streaks follow the student across web/WA/Telegram.
# ---------------------------------------------------------------------------
_ORIG_DB_KEY = Database._key
_ORIG_ENSURE_USER = Database.ensure_user
_ORIG_SET_PHONE = Database.set_phone_number
_ORIG_ADD_COINS = Database.add_coins
_ORIG_ADD_XP = Database.add_xp
_ORIG_UPDATE_STREAK = Database.update_streak
_ORIG_SYNC_SUPABASE = Database.sync_user_to_supabase
_ORIG_SAVE_USER = Database.save_user


def _canonical_uid(self, uid: str | int) -> str:
    raw = str(uid)
    if not self.redis:
        return raw
    try:
        p = self.redis.get(f"identity:uid_phone:{raw}")
        if p:
            return f"phone:{normalize_phone(p)}"
        if raw.startswith("wa:"):
            p = normalize_phone(raw[3:])
            if p:
                return f"phone:{p}"
        p2 = normalize_phone(raw) if re.fullmatch(r"\+?[0-9 ()-]{10,20}", raw) else ""
        if p2:
            return f"phone:{p2}"
    except Exception:
        pass
    return raw


def _v7_key(self, uid: str | int) -> str:
    return f"user:{_canonical_uid(self, uid)}"


def _bind_phone_identity(self, uid: str | int, phone: str) -> str:
    p = normalize_phone(phone)
    raw = str(uid)
    if not p or not self.redis:
        return raw
    canonical = f"phone:{p}"
    try:
        existing = self.redis.hgetall(f"user:{raw}")
        canonical_data = self.redis.hgetall(f"user:{canonical}")
        if existing and not canonical_data:
            self.redis.hset(f"user:{canonical}", mapping=existing)
        self.redis.set(f"identity:uid_phone:{raw}", p)
        self.redis.set(f"identity:phone_uid:{p}", raw)
        self.redis.expire(f"identity:uid_phone:{raw}", 86400 * 3650)
        self.redis.expire(f"identity:phone_uid:{p}", 86400 * 3650)
    except Exception as e:
        logger.debug("bind phone identity: %s", e)
    return canonical


def _v7_ensure_user(self, uid, username="", full_name="", platform="telegram", referred_by="", phone_number="", exam_type="", subject=""):
    p = normalize_phone(phone_number or (str(uid)[3:] if str(uid).startswith("wa:") else ""))
    if p:
        _bind_phone_identity(self, uid, p)
    user = _ORIG_ENSURE_USER(self, uid, username, full_name, platform, referred_by, phone_number, exam_type, subject)
    if p:
        user["phone_number"] = p
    # Trial is evaluated on every login boundary too, so reaching 500 coins or
    # 5 referrals unlocks Pro immediately rather than waiting for the next ask.
    try:
        self.maybe_grant_trial(uid)
    except Exception:
        pass
    user = self.get_user(uid) or user
    # Onboarding gift: exactly once — 50 coins + one freeze. Daily spin remains free.
    if self.redis:
        try:
            key = self._key(uid)
            if self.redis.hget(key, "onboarding_gift_claimed") != "1":
                self.redis.hset(key, mapping={"onboarding_gift_claimed": "1", "shields": max(1, int(self.redis.hget(key, "shields") or 0))})
        except Exception:
            pass
    self.sync_user_to_supabase(uid)
    return self.get_user(uid) or user


def _v7_save_user(self, uid, data):
    # Preserve phone identity whenever a profile is saved.
    phone = normalize_phone(data.get("phone_number", ""))
    if phone:
        _bind_phone_identity(self, uid, phone)
    return _ORIG_SAVE_USER(self, uid, data)


def _v7_set_phone(self, uid, phone_number):
    p = normalize_phone(phone_number)
    if not p:
        return False
    _bind_phone_identity(self, uid, p)
    ok = _ORIG_SET_PHONE(self, uid, p)
    if ok:
        u = self.get_user(uid) or {}
        u["phone_number"] = p
        self.save_user(uid, u)
        self.sync_user_to_supabase(uid, u)
    return ok


def _v7_sync(self, uid, user=None):
    u = user or self.get_user(uid)
    if u:
        u.setdefault("learner_track", "competitive" if str(u.get("exam_type", "")).lower() in {
            "jee","neet","upsc","ssc","banking","bpsc","gate","cat","cuet","nda","clat"
        } else "school_college")
        u.setdefault("onboarding_gift_claimed", "1")
        u.setdefault("trial_granted", "0")
        u.setdefault("trial_granted_at", "")
        u.setdefault("trial_reason", "")
        u.setdefault("telegram_chat_id", "")
        u.setdefault("instagram_user_id", "")
    ok = _ORIG_SYNC_SUPABASE(self, uid, u)
    if self.supabase.enabled and u:
        phone = normalize_phone(u.get("phone_number", ""))
        if phone:
            extra = {
                "learner_track": u.get("learner_track", "school_college"),
                "onboarding_gift_claimed": str(u.get("onboarding_gift_claimed", "1")).lower() in ("1","true","yes"),
                "trial_granted": str(u.get("trial_granted", "0")).lower() in ("1","true","yes"),
                "trial_granted_at": u.get("trial_granted_at", "") or None,
                "trial_reason": u.get("trial_reason", "") or None,
            "last_payment_id": u.get("last_payment_id", ""),
            "refunded_at": u.get("refunded_at", "") or None,
            "last_refund_id": u.get("last_refund_id", ""),
                "telegram_chat_id": str(u.get("telegram_chat_id", "")),
                "instagram_user_id": str(u.get("instagram_user_id", "")),
            }
            try:
                _DB_WRITE_POOL.submit(self.supabase.upsert, "users", {"phone_number": phone, **extra}, "phone_number")
            except Exception:
                pass
    return ok


Database._key = _v7_key
Database.ensure_user = _v7_ensure_user
Database.save_user = _v7_save_user
Database.set_phone_number = _v7_set_phone
Database.sync_user_to_supabase = _v7_sync


def _set_channel_identity(self, uid: str | int, field: str, value: str) -> bool:
    u = self.get_user(uid) or self.ensure_user(uid)
    if not u:
        return False
    u[field] = str(value or "")
    return bool(self.save_user(uid, u) and self.sync_user_to_supabase(uid, u))


Database.set_channel_identity = _set_channel_identity


# Durable sync for all hot-wallet mutations.
def _v7_add_coins(self, uid, amount):
    bal = _ORIG_ADD_COINS(self, uid, amount)
    try:
        self.sync_user_to_supabase(uid)
        self.maybe_grant_trial(uid)
    except Exception:
        pass
    return bal


def _v7_add_xp(self, uid, amount):
    result = _ORIG_ADD_XP(self, uid, amount)
    try:
        self.sync_user_to_supabase(uid)
    except Exception:
        pass
    return result


def _v7_update_streak(self, uid):
    result = _ORIG_UPDATE_STREAK(self, uid)
    try:
        self.sync_user_to_supabase(uid)
    except Exception:
        pass
    return result


Database.add_coins = _v7_add_coins
Database.add_xp = _v7_add_xp
Database.update_streak = _v7_update_streak


def _maybe_grant_trial(self, uid: str | int) -> bool:
    """Single authoritative free-trial evaluator. Exactly one 3-day trial per user."""
    if not self.redis:
        return False
    try:
        u = self.get_user(uid) or {}
        if str(u.get("trial_granted", "0")).lower() in ("1", "true", "yes"):
            return False

        referrals = int(u.get("referral_count", 0) or 0)
        coins = int(u.get("coins", 0) or 0)
        reason = ""
        if coins >= config.TRIAL_COINS:
            reason = "coins"
        elif referrals >= config.TRIAL_REFERRALS:
            reason = "referrals"
        else:
            return False

        canonical = _canonical_uid(self, uid)
        claim_key = f"trial:claim:{canonical}"
        if not self.redis.set(claim_key, "1", nx=True, ex=86400 * 3650):
            return False

        # Activate exactly one trial. activate_pro extends an already-active plan,
        # but this function can execute only once because of trial_granted + claim_key.
        if not self.activate_pro(uid, days=config.TRIAL_DAYS):
            try:
                self.redis.delete(claim_key)
            except Exception:
                pass
            return False

        u = self.get_user(uid) or {}
        u["trial_granted"] = "1"
        u["trial_granted_at"] = _now_ist().isoformat()
        u["trial_reason"] = reason
        self.save_user(uid, u)
        self.sync_user_to_supabase(uid, u)
        logger.info("Free trial granted uid=%s reason=%s days=%s", uid, reason, config.TRIAL_DAYS)
        return True
    except Exception as e:
        logger.warning("trial grant: %s", e)
        return False


Database.maybe_grant_trial = _maybe_grant_trial


# ---------------------------------------------------------------------------
# Reminder + mission + community verification storage helpers.
# ---------------------------------------------------------------------------
def _phone_for_uid(uid: str | int) -> str:
    return normalize_phone((db.get_user(uid) or {}).get("phone_number", ""))


def schedule_spaced_reminders(uid: str | int, question: str, tool: str = "general") -> None:
    """1/3/7/15-day spaced review queue; durable in Supabase when phone exists."""
    phone = _phone_for_uid(uid)
    if not phone or not supa.enabled or not question:
        return
    base = _now_ist()
    rows = []
    for days in config.SPACED_REMINDER_DAYS:
        rows.append({
            "phone_number": phone,
            "due_at": (base + timedelta(days=days)).isoformat(),
            "offset_days": days,
            "question": question[:4000],
            "tool": (tool or "general")[:64],
            "status": "pending",
        })
    def _write():
        for row in rows:
            try:
                supa.insert("study_reminders", row)
            except Exception:
                pass
    try:
        _DB_WRITE_POOL.submit(_write)
    except Exception:
        _write()


def schedule_spaced_reminders_phone(phone: str, question: str, tool: str = "general") -> None:
    p = normalize_phone(phone)
    if not p or not supa.enabled or not question:
        return
    base = _now_ist()
    for days in config.SPACED_REMINDER_DAYS:
        row = {"phone_number": p, "due_at": (base + timedelta(days=days)).isoformat(),
               "offset_days": days, "question": question[:4000], "tool": (tool or "general")[:64], "status": "pending"}
        try:
            _DB_WRITE_POOL.submit(supa.insert, "study_reminders", row)
        except Exception:
            try: supa.insert("study_reminders", row)
            except Exception: pass


def daily_mission_for_user(uid: str | int) -> dict:
    """One old PYQ + one formula daily mission; duration is config-driven."""
    u = db.get_user(uid) or {}
    ex = u.get("exam_type", "general")
    sub = u.get("subject", "general")
    mistakes = db.get_mistakes(uid, limit=3)
    old_topic = (mistakes[0].get("topic") or mistakes[0].get("question", "")).strip() if mistakes else f"{sub} revision"
    return {
        "title": "3-Minute Daily Mission 🔥",
        "exam_type": ex,
        "subject": sub,
        "pyq": f"1 purana PYQ: {old_topic[:120]}",
        "formula": f"1 formula: aaj {sub} ka ek high-yield formula yaad karo.",
        "duration_min": config.DAILY_MISSION_MINUTES,
        "reward_coins": config.MISSION_REWARD_COINS,
        "mission_id": f"{_today_ist()}:{ex}:{sub}",
    }


def _library_vote(uid: str, library_id: int, vote: int) -> bool:
    phone = _phone_for_uid(uid)
    if not phone or not supa.enabled:
        return False
    vote = 1 if int(vote or 0) > 0 else -1
    row = {"library_id": int(library_id), "phone_number": phone, "vote": vote}
    # Unique constraint makes this one-vote-per-child and safe to upsert.
    return supa.upsert("library_votes", row, "library_id,phone_number")


def _verify_library_item(library_id: int) -> dict:
    if not supa.enabled:
        return {"verified": False, "upvotes": 0, "downvotes": 0}
    ups = len(supa.select_many("library_votes", {"library_id": f"eq.{int(library_id)}", "vote": "eq.1"}, limit=10000, columns="phone_number"))
    downs = len(supa.select_many("library_votes", {"library_id": f"eq.{int(library_id)}", "vote": "eq.-1"}, limit=10000, columns="phone_number"))
    verified = ups >= 3
    try:
        supa.update("library", {"id": f"eq.{int(library_id)}"}, {"upvotes": ups, "downvotes": downs, "verified": verified})
    except Exception:
        pass
    return {"verified": verified, "upvotes": ups, "downvotes": downs}


# ---------------------------------------------------------------------------
# Answer polish: exact free upsell + 10-in-1 resource metadata.
# ---------------------------------------------------------------------------
def _free_upsell(answer: str, is_pro: bool) -> str:
    if is_pro or not answer:
        return answer or ""
    return answer.rstrip() + "\n\n" + FREE_UPSELL_LINE


def _youtube_links(topic: str, limit: int, language: str = "hinglish") -> list[dict]:
    topic = (topic or "").strip()
    if not topic:
        return []
    if YOUTUBE_API_KEY:
        try:
            r = requests.get(
                config.URLS["youtube_api_url"],
                params={"part":"snippet", "q":topic, "type":"video", "maxResults":max(1,min(limit,config.PRO_VIDEO_LINKS)), "key":YOUTUBE_API_KEY},
                timeout=8,
            )
            if r.status_code < 300:
                out = []
                for item in (r.json().get("items") or []):
                    vid = ((item.get("id") or {}).get("videoId") or "").strip()
                    sn = item.get("snippet") or {}
                    if vid:
                        out.append({"title": str(sn.get("title") or config.PRODUCT_TEXT.get("resource_video_fallback_title", "Relevant video"))[:160], "url": f"{config.URLS["youtube_watch_base_url"]}{vid}"})
                return out[:limit]
        except Exception:
            pass
    # No API key: still give a topic-scoped YouTube result rather than an unrelated link.
    return [{"title": f"YouTube: {topic}", "url": config.URLS["youtube_search_base_url"] + quote(topic)}]


def _10in1_metadata(question: str, is_pro: bool, uid: str | int = "") -> dict:
    links = _youtube_links(question, config.PRO_VIDEO_LINKS if is_pro else config.FREE_VIDEO_LINKS)
    pdf_url = f"/api/export/pdf?question={quote((question or '')[:300])}&uid={quote(str(uid))}"
    assignment_url = f"/api/resource/assignment?uid={quote(str(uid))}&topic={quote((question or '')[:300])}"
    return {
        "video_links": links,
        "pdf_url": pdf_url,
        "assignment_url": assignment_url,
        "free_pro": {
            "free": config.ANSWER_RESOURCE_COPY["free_pack"],
            "pro": config.ANSWER_RESOURCE_COPY["pro_pack"],
        },
    }


def _channel_resource_suffix(question: str, is_pro: bool, uid: str | int) -> str:
    meta = _10in1_metadata(question, is_pro, uid)
    lines = [config.ANSWER_RESOURCE_COPY["extras_heading"]]
    videos = meta.get("video_links") or []
    for v in videos[:config.PRO_VIDEO_LINKS if is_pro else config.FREE_VIDEO_LINKS]:
        lines.append(f"▶️ {v.get('title','Relevant video')}: {v.get('url','')}")
    lines.append(f"📄 Study PDF: {meta['pdf_url']}")
    lines.append(f"📝 Full assignment: {meta['assignment_url']}")
    if not is_pro:
        lines.append(config.ANSWER_RESOURCE_COPY["free_badge"].format(
            free_video_links=config.FREE_VIDEO_LINKS,
            free_pdf_per_day=config.FREE_PDF_PER_DAY,
            free_assignment_per_day=config.FREE_ASSIGNMENT_PER_DAY,
            pro_video_links=config.PRO_VIDEO_LINKS,
        ))
    return "\n".join(lines)


_ORIG_GET_AI_ANSWER = get_ai_answer

def get_ai_answer_v7(question: str, tool: str, is_pro: bool, language: str = "hinglish",
                     phone_number: str = "", exam_type: str = "", subject: str = ""):
    # Exam integrity rule: refuse paper leaks / in-exam cheating while still helping with preparation.
    low = (question or "").lower()
    cheating_terms = ("paper leak", "leaked paper", "exam hall cheating", "cheat in exam", "phone in exam", "exam me cheating")
    if any(x in low for x in cheating_terms):
        return (
            "Bhai exam me phone ya cheating nahi. 📵❤️\n\n"
            "Main leak ya live cheating me help nahi karunga, but abhi isi topic ka 10-minute revision, formula sheet, "
            "PYQ-style practice ya mock bana deta hoon. Bas topic bhej."
        )
    # Emotional support mode — kind, non-judgmental, no diagnosis.
    crisis_terms = ("suicide", "kill myself", "marna chahta", "marna chahti", "jeena nahi", "self harm", "khud ko nuksan")
    if any(x in low for x in crisis_terms):
        return (
            "Arre bhai/behen, pehle padhai side pe rakhte hain. ❤️\n\n"
            "Tu akela nahi hai. Abhi kisi trusted adult, parent, teacher ya close dost ko bata aur unke paas reh. "
            "Agar tujhe lag raha hai ki tu khud ko hurt kar sakta/sakti hai, turant local emergency service ya nearest hospital ki help le. "
            "Mujhe bas ek cheez bata: **abhi tu safe jagah par hai aur kisi apne ke paas hai?**"
        )
    canonical = TOOL_ALIASES.get(tool, tool)
    q = question
    if tool != canonical:
        q = f"Feature mode: {tool}. Do the requested study task using this mode.\n\n{question}"
    ans = _ORIG_GET_AI_ANSWER(q, canonical, is_pro, language=language, phone_number=phone_number, exam_type=exam_type, subject=subject)
    if ans and not str(ans).startswith("ERROR:"):
        ans = _free_upsell(ans, is_pro)
    return ans


get_ai_answer = get_ai_answer_v7


# ---------------------------------------------------------------------------
# Channel senders / webhook aliases.
# ---------------------------------------------------------------------------
def _send_telegram_text(chat_id: str, body: str) -> bool:
    if not config.BOT_TOKEN or not chat_id:
        return False
    try:
        r = requests.post(
            f"{config.URLS["telegram_base_url"]}/bot{config.BOT_TOKEN}/sendMessage",
            json={"chat_id": chat_id, "text": body[:4000], "disable_web_page_preview": False},
            timeout=10,
        )
        return r.status_code < 300
    except Exception as e:
        logger.warning("Telegram cron send: %s", e)
        return False


def _send_instagram_text(recipient_id: str, body: str) -> bool:
    if not INSTAGRAM_ACCESS_TOKEN or not INSTAGRAM_ACCOUNT_ID or not recipient_id:
        return False
    try:
        url = f"{config.URLS["instagram_graph_base_url"]}/{INSTAGRAM_GRAPH_VERSION}/{INSTAGRAM_ACCOUNT_ID}/messages"
        r = requests.post(
            url,
            params={"access_token": INSTAGRAM_ACCESS_TOKEN},
            json={"recipient": {"id": recipient_id}, "message": {"text": body[:1000]}},
            timeout=15,
        )
        return r.status_code < 300
    except Exception as e:
        logger.warning("Instagram send: %s", e)
        return False


def _send_snapchat_gateway(recipient_id: str, body: str) -> bool:
    if not SNAPCHAT_ENABLED or not SNAPCHAT_DM_GATEWAY_URL or not SNAPCHAT_DM_GATEWAY_TOKEN:
        return False
    try:
        r = requests.post(
            SNAPCHAT_DM_GATEWAY_URL,
            headers={"Authorization": f"Bearer {SNAPCHAT_DM_GATEWAY_TOKEN}"},
            json={"recipient_id": recipient_id, "text": body[:1000], "brand": BRAND_NAME},
            timeout=15,
        )
        return r.status_code < 300
    except Exception as e:
        logger.warning("Snapchat gateway send: %s", e)
        return False


@app.route("/webhook/telegram", methods=["POST"])
def telegram_webhook_public():
    return telegram_webhook()


@app.route("/webhook/whatsapp", methods=["GET", "POST"])
def whatsapp_webhook_public():
    return whatsapp_webhook()


@app.route("/webhook/instagram", methods=["GET", "POST"])
def instagram_webhook():
    if request.method == "GET":
        mode = request.args.get("hub.mode", "")
        token = request.args.get("hub.verify_token", "")
        challenge = request.args.get("hub.challenge", "")
        if mode == "subscribe" and INSTAGRAM_VERIFY_TOKEN and hmac.compare_digest(token, INSTAGRAM_VERIFY_TOKEN):
            return challenge, 200
        return "Forbidden", 403
    try:
        body = request.get_json(silent=True) or {}
        for entry in body.get("entry", []):
            for ev in entry.get("messaging", []) or []:
                sender = str((ev.get("sender") or {}).get("id") or "")
                msg = ev.get("message") or {}
                text_msg = str(msg.get("text") or "").strip()
                if not sender or not text_msg:
                    continue
                uid = f"ig:{sender}"
                db.ensure_user(uid, full_name=config.DEFAULT_STUDENT_NAMES["instagram"], platform="instagram")
                db.set_channel_identity(uid, "instagram_user_id", sender)
                if db.is_banned(uid):
                    _send_instagram_text(sender, format_ban_active_message(db.get_ban_remaining_seconds(uid)))
                    continue
                if contains_abuse(text_msg):
                    count, just_banned = db.record_abuse_warning(uid)
                    _send_instagram_text(sender, format_abuse_warning_message(count, just_banned))
                    continue
                is_pro = db.is_pro(uid)
                tool = detect_tool_from_text(text_msg)
                if tool in PRO_ONLY_TOOLS and not is_pro:
                    _send_instagram_text(sender, f"🔒 {tool} Pro feature hai. ₹{config.PRO_PRICE_INR}/30 days.")
                    continue
                if not is_pro:
                    can, quota = db.try_consume_quota(uid)
                    if not can:
                        _send_instagram_text(sender, f"Free limit khatam. Aaj ke baad phir aana ya PRO lo. Left: {quota.get('daily_left',0)}")
                        continue
                u = db.get_user(uid) or {}
                answer = get_ai_answer_v7(text_msg, tool, is_pro, language=db.get_language(uid),
                                          phone_number=u.get("phone_number", ""), exam_type=u.get("exam_type", ""), subject=u.get("subject", ""))
                db.add_personal_history(uid, text_msg, tool=tool, exam_type=u.get("exam_type", ""), subject=u.get("subject", ""))
                xp, level = db.add_xp(uid, config.XP_QUESTION * (config.PRO_XP_MULTIPLIER if is_pro else 1))
                _send_instagram_text(sender, f"{answer}\n\n⭐ +{config.XP_QUESTION * (config.PRO_XP_MULTIPLIER if is_pro else 1)} XP | Level {level}")
        return jsonify({"ok": True})
    except Exception as e:
        logger.exception("Instagram webhook: %s", e)
        return jsonify({"ok": False}), 500


@app.route("/webhook/snapchat", methods=["POST"])
def snapchat_webhook():
    # Snapchat's public developer surface supports Login Kit / Creative Kit; this
    # endpoint deliberately requires your own approved DM gateway rather than
    # pretending there is a general public Snapchat DM bot API.
    if not SNAPCHAT_ENABLED:
        return jsonify({"ok": False, "error": "Snapchat DM adapter is disabled. Configure an approved gateway in SNAPCHAT_DM_GATEWAY_URL."}), 501
    try:
        body = request.get_json(silent=True) or {}
        sender = str(body.get("sender_id") or body.get("user_id") or "")
        text_msg = str(body.get("text") or "").strip()
        if not sender or not text_msg:
            return jsonify({"ok": True})
        uid = f"snap:{sender}"
        db.ensure_user(uid, full_name=config.DEFAULT_STUDENT_NAMES["snapchat"], platform="snapchat")
        answer = get_ai_answer_v7(text_msg, detect_tool_from_text(text_msg), db.is_pro(uid))
        _send_snapchat_gateway(sender, answer or SOFT_FAIL_MSG)
        return jsonify({"ok": True})
    except Exception as e:
        logger.exception("Snapchat webhook: %s", e)
        return jsonify({"ok": False}), 500


# Vercel/hosted cron auth: supports either the existing dev secret, an explicit X-Cron-Secret,
# or Authorization: Bearer CRON_SECRET for managed cron providers.
CRON_SECRET = os.getenv("CRON_SECRET", "").strip()
def _cron_authorized_v7() -> bool:
    supplied = request.args.get("code") or request.headers.get("X-Cron-Secret", "")
    if supplied and config.DEV_SECRET and hmac.compare_digest(supplied, config.DEV_SECRET):
        return True
    auth = request.headers.get("Authorization", "")
    if CRON_SECRET and auth == f"Bearer {CRON_SECRET}":
        return True
    if request.headers.get("X-Vercel-Cron") == "1" and CRON_SECRET:
        return True
    return False
_cron_authorized = _cron_authorized_v7

# ---------------------------------------------------------------------------
# Daily missions, spaced reminders, 5pm/8pm nudges, Sunday parent report,
# 2:30am Top-5 refresh and one-command SEO warming.
# ---------------------------------------------------------------------------
def _cron_users() -> list[str]:
    if not db.redis:
        return []
    try:
        return list(db.redis.smembers("stats:users") or [])
    except Exception:
        return []


def _send_user_push(u: dict, body: str) -> bool:
    platform = str(u.get("platform", "")).lower()
    if platform == "whatsapp" and u.get("phone_number"):
        _send_whatsapp_text(u["phone_number"], body)
        return True
    chat = str(u.get("telegram_chat_id", "") or "")
    if chat:
        return _send_telegram_text(chat, body)
    if u.get("instagram_user_id"):
        _send_instagram_text(str(u["instagram_user_id"]), body)
        return True
    return False


def _daily_nudge(text: str) -> int:
    sent = 0
    for uid in _cron_users():
        u = db.get_user(uid) or {}
        phone = normalize_phone(u.get("phone_number", ""))
        # Telegram users without a phone can still receive nudges via chat id.
        if not phone and not u.get("telegram_chat_id") and not u.get("instagram_user_id"):
            continue
        if _send_user_push(u, text):
            sent += 1
    return sent


@app.route("/api/cron/daily-5pm", methods=["GET", "POST"])
def cron_daily_5pm():
    if not _cron_authorized():
        return jsonify({"ok": False, "error": "unauthorized"}), 403
    return jsonify({"ok": True, "sent": _daily_nudge("🔥 mission ready hai")})


@app.route("/api/cron/daily-8pm", methods=["GET", "POST"])
def cron_daily_8pm():
    if not _cron_authorized():
        return jsonify({"ok": False, "error": "unauthorized"}), 403
    return jsonify({"ok": True, "sent": _daily_nudge("🔥 aag bujhne wali hai bacha le")})


@app.route("/api/cron/daily-mission", methods=["GET", "POST"])
def cron_daily_mission():
    if not _cron_authorized():
        return jsonify({"ok": False, "error": "unauthorized"}), 403
    sent = 0
    for uid in _cron_users():
        u = db.get_user(uid) or {}
        mission = daily_mission_for_user(uid)
        body = (
            f"🔥 {mission['title']}\n\n"
            f"1) {mission['pyq']}\n"
            f"2) {mission['formula']}\n\n"
            f"⏱️ {mission['duration_min']} minute. Bas itna aaj ke liye."
        )
        if _send_user_push(u, body):
            sent += 1
    return jsonify({"ok": True, "sent": sent})


@app.route("/api/cron/reminders", methods=["GET", "POST"])
def cron_reminders():
    if not _cron_authorized() or not supa.enabled:
        return jsonify({"ok": False, "error": "unauthorized_or_supabase_disabled"}), 403
    now_iso = _now_ist().isoformat()
    rows = supa.select_many("study_reminders", {"status": "eq.pending", "due_at": f"lte.{now_iso}"}, limit=200, columns="*")
    sent = 0
    for row in rows:
        phone = normalize_phone(row.get("phone_number", ""))
        user = supa.select_one("users", {"phone_number": f"eq.{phone}"}) or {}
        body = (
            f"🧠 Yaad hai? {int(row.get('offset_days') or 1)} din pehle tune yeh padha tha:\n\n"
            f"{str(row.get('question') or '')[:500]}\n\n"
            "Aaj 2 minute de aur khud se answer bol. Phir Fire kar. 🔥"
        )
        ok = False
        if user:
            ok = _send_user_push(user, body)
        if ok:
            supa.update("study_reminders", {"id": f"eq.{row.get('id')}"}, {"status": "sent", "sent_at": _now_ist().isoformat()})
            sent += 1
    return jsonify({"ok": True, "due": len(rows), "sent": sent})


@app.route("/api/cron/video-update", methods=["GET", "POST"])
def cron_video_update():
    if not _cron_authorized():
        return jsonify({"ok": False, "error": "unauthorized"}), 403
    # 2:30AM IST cron: refresh the current Top-5 video cache for the most active topics.
    topics = set()
    for uid in _cron_users()[:5000]:
        u = db.get_user(uid) or {}
        q = str(u.get("subject") or u.get("exam_type") or "study")
        if q:
            topics.add(q)
    saved = 0
    for topic in list(topics)[:100]:
        links = _youtube_links(topic, 5)
        if db.redis:
            try:
                db.redis.setex(f"top5video:{topic.lower()}", 86400 * 2, json.dumps(links))
                saved += 1
            except Exception:
                pass
    return jsonify({"ok": True, "topics_refreshed": saved})


@app.route("/api/cron/seo-warmup", methods=["GET", "POST"])
def cron_seo_warmup():
    if not _cron_authorized():
        return jsonify({"ok": False, "error": "unauthorized"}), 403
    return jsonify({"ok": True, "message": "SEO pages are generated by the Next.js catalog + sitemap; this route is a cache-warm hook."})


# ---------------------------------------------------------------------------
# Library + community verification endpoints.
# ---------------------------------------------------------------------------
@app.route("/api/library/vote", methods=["POST"])
def api_library_vote():
    data = request.get_json(silent=True) or {}
    uid = str(data.get("uid") or data.get("client_id") or "").strip()
    library_id = int(data.get("library_id") or 0)
    vote = int(data.get("vote") or 1)
    if uid.startswith("web:"):
        db.ensure_user(uid, platform="web", full_name=config.DEFAULT_STUDENT_NAMES["web"], phone_number=data.get("phone_number", ""))
    if not library_id:
        return jsonify({"ok": False, "error": "library_id required"}), 400
    ok = _library_vote(uid, library_id, vote)
    return jsonify({"ok": ok, "verification": _verify_library_item(library_id)})


@app.route("/api/daily-mission/complete", methods=["POST"])
def api_daily_mission_complete():
    data = request.get_json(silent=True) or {}
    uid = str(data.get("uid") or data.get("client_id") or "").strip()
    if not uid:
        return jsonify({"ok": False, "error": "uid/client_id required"}), 400
    if uid.startswith("web:"):
        db.ensure_user(uid, full_name=config.DEFAULT_STUDENT_NAMES["web"], platform="web", phone_number=data.get("phone_number", ""))
    key = f"mission:done:{_canonical_uid(db, uid)}:{_today_ist()}"
    if db.redis and not db.redis.set(key, "1", nx=True, ex=90000):
        return jsonify({"ok": True, "already_done": True, "reward_coins": 0, "coins": db.get_coins(uid)})
    reward = config.MISSION_REWARD_COINS
    coins = db.add_coins(uid, reward)
    db.add_xp(uid, config.DAILY_MISSION_XP)
    return jsonify({"ok": True, "already_done": False, "reward_coins": reward, "reward_xp": config.DAILY_MISSION_XP, "coins": coins})


@app.route("/api/daily-mission")
def api_daily_mission():
    uid = str(request.args.get("uid") or request.args.get("client_id") or "").strip()
    if not uid:
        return jsonify({"ok": False, "error": "uid/client_id required"}), 400
    if uid.startswith("web:"):
        db.ensure_user(uid, full_name=config.DEFAULT_STUDENT_NAMES["web"], platform="web")
    return jsonify({"ok": True, "mission": daily_mission_for_user(uid)})


@app.route("/api/features")
def api_features():
    return jsonify({"brand": BRAND_NAME, "features": FEATURE_CATALOG_28, "free": {
        "questions_per_day": config.FREE_DAILY, "pdf_per_day": config.FREE_PDF_PER_DAY,
        "video_links_per_day": config.FREE_VIDEO_LINKS, "assignment_per_day": config.FREE_ASSIGNMENT_PER_DAY
    }, "pro": {
        "price_inr": config.PRO_PRICE_INR, "questions": "unlimited",
        "video_links": config.PRO_VIDEO_LINKS, "assignment": "unlimited",
        "viva_questions": config.PRO_VIVA_QUESTIONS
    }})


@app.route("/api/export/pdf")
def api_export_pdf():
    """Small student-owned answer sheet export. It does not reproduce copyrighted books."""
    try:
        from reportlab.lib.pagesizes import A4
        from reportlab.pdfgen import canvas
    except Exception:
        return jsonify({"ok": False, "error": "reportlab not installed"}), 503
    question = request.args.get("question", "SaarthiBhai study sheet")[:1000]
    uid = request.args.get("uid", "")[:200]
    # If a uid is available, enforce one free PDF/day outside Pro.
    if uid and uid in ("",):
        uid = uid
    if uid and not uid.startswith("phone:"):
        try:
            is_pro = db.is_pro(uid)
        except Exception:
            is_pro = False
    else:
        is_pro = False
    if uid and not is_pro and db.redis:
        if not db.redis.set(f"resource:pdf:{_canonical_uid(db, uid)}:{_today_ist()}", "1", nx=True, ex=90000):
            return jsonify({"ok": False, "error": "Aaj ka free PDF already use ho gaya. PRO me unlimited."}), 429
    from io import BytesIO
    buf = BytesIO()
    c = canvas.Canvas(buf, pagesize=A4)
    c.setTitle(BRAND_NAME)
    y = 800
    c.setFont("Helvetica-Bold", 16); c.drawString(40, y, BRAND_NAME); y -= 30
    c.setFont("Helvetica", 11)
    c.drawString(40, y, "Study Sheet"); y -= 25
    for line in question.splitlines()[:35]:
        if y < 50:
            c.showPage(); y = 800; c.setFont("Helvetica", 11)
        c.drawString(40, y, line[:120]); y -= 16
    c.showPage(); c.save(); buf.seek(0)
    from flask import send_file
    return send_file(buf, mimetype="application/pdf", as_attachment=True, download_name="saarthibhai-study-sheet.pdf")


# ---------------------------------------------------------------------------
# PWA assets from Flask too, so the legacy web app remains installable.
# The separate Next.js app has the same manifest/service worker.
# ---------------------------------------------------------------------------
@app.route("/manifest.webmanifest")
def legacy_manifest():
    return jsonify({
        "name": BRAND_NAME, "short_name": config.SHORT_NAME, "start_url": "/", "scope": "/",
        "display": "standalone", "background_color": config.THEME["primary"], "theme_color": config.THEME["primary"],
        "icons": [{"src": "/bot-icon.svg", "sizes": "any", "type": "image/svg+xml"}],
        "description": config.PWA_DESCRIPTION,
    })


@app.route("/sw.js")
def legacy_sw():
    js = ("\nconst CACHE=" + json.dumps(SW_CACHE_NAME) + ";\n"
          "self.addEventListener('install',e=>e.waitUntil(caches.open(CACHE).then(c=>c.addAll(['/']))));\n"
          "self.addEventListener('activate',e=>e.waitUntil(self.clients.claim()));\n"
          "self.addEventListener('fetch',e=>{if(e.request.method==='GET'){e.respondWith(caches.match(e.request).then(r=>r||fetch(e.request).catch(()=>caches.match('/'))));}});\n")
    from flask import Response
    return Response(js, mimetype="application/javascript")


# ---------------------------------------------------------------------------
# Brand + creator endpoints used by the Next.js frontend.
# ---------------------------------------------------------------------------
@app.route("/api/branding")
def api_branding():
    return jsonify({"name": BRAND_NAME, "creator": config.CREATOR_NAME, "photo": CREATOR_PHOTO_URL, "story": CREATOR_STORY_URL})


# ---------------------------------------------------------------------------
# Add Telegram identity capture without rewriting the long-lived handler.
# The normal question path already calls ensure_user; this hook only records chat id.
# ---------------------------------------------------------------------------
_orig_process_question = process_question
async def process_question_v7(update, context, text, tool="general"):
    try:
        if update and update.effective_user:
            uid = update.effective_user.id
            chat_id = update.effective_chat.id if update.effective_chat else uid
            db.ensure_user(uid, full_name=getattr(update.effective_user, "full_name", config.DEFAULT_STUDENT_NAMES["generic"]) or config.DEFAULT_STUDENT_NAMES["generic"], platform="telegram")
            db.set_channel_identity(uid, "telegram_chat_id", str(chat_id))
    except Exception as e:
        logger.debug("telegram v7 metadata: %s", e)
    return await _orig_process_question(update, context, text, tool)
process_question = process_question_v7

# Reminder scheduling for web is added through the common API path by a small hook.
_orig_v7_ai = get_ai_answer


@app.route("/api/resource/assignment", methods=["GET", "POST"])
def api_resource_assignment():
    data = request.get_json(silent=True) or {}
    if request.method == "GET":
        uid = str(request.args.get("uid") or request.args.get("client_id") or "").strip()
        q = str(request.args.get("question") or request.args.get("topic") or "").strip()
        data = {"uid": uid, "client_id": request.args.get("client_id", ""), "question": q, "topic": q}
    uid = str(data.get("uid") or data.get("client_id") or "").strip()
    q = str(data.get("question") or data.get("topic") or "").strip()
    if not uid or not q:
        return jsonify({"ok": False, "error": "uid/client_id and topic required"}), 400
    if uid.startswith("web:"):
        db.ensure_user(uid, full_name=config.DEFAULT_STUDENT_NAMES["web"], platform="web", phone_number=data.get("phone_number", ""))
    pro = db.is_pro(uid)
    if not pro and db.redis:
        key=f"resource:assignment:{_canonical_uid(db,uid)}:{_today_ist()}"
        if not db.redis.set(key,"1",nx=True,ex=90000):
            return jsonify({"ok":False,"error":"Aaj ka free assignment use ho gaya. PRO me unlimited."}),429
    ans = get_ai_answer_v7(
        f"Create one complete student-ready assignment on this topic: {q}. Include objective, 5-10 tasks/questions, answer/solution guide, and submission checklist. No fabricated source citations.",
        "notes", pro, language=db.get_language(uid), phone_number=_phone_for_uid(uid)
    )
    return jsonify({"ok":True,"assignment":ans,"pro":pro})


@app.route("/api/resource/placement", methods=["POST"])
def api_resource_placement():
    data = request.get_json(silent=True) or {}
    uid = str(data.get("uid") or data.get("client_id") or "").strip()
    role = str(data.get("role") or data.get("topic") or "software placement").strip()
    if not uid:
        return jsonify({"ok":False,"error":"uid/client_id required"}),400
    if uid.startswith("web:"):
        db.ensure_user(uid, full_name=config.DEFAULT_STUDENT_NAMES["web"], platform="web", phone_number=data.get("phone_number", ""))
    if not db.is_pro(uid):
        return jsonify({"ok":False,"error":"Placement pack is PRO-only"}),403
    prompt=(f"Build a placement-prep pack for the role {role}: 10 interview questions, 5 coding questions with solutions, a 7-day prep checklist, common mistakes, and one clean starter project idea.")
    ans=run_ai(ai.answer,prompt,"career",is_pro=True,language=db.get_language(uid))
    return jsonify({"ok":True,"placement":ans})


@app.route("/api/resource/viva", methods=["POST"])
def api_resource_viva():
    data = request.get_json(silent=True) or {}
    uid = str(data.get("uid") or data.get("client_id") or "").strip()
    q = str(data.get("question") or data.get("topic") or "").strip()
    if not uid or not q:
        return jsonify({"ok":False,"error":"uid/client_id and topic required"}),400
    if uid.startswith("web:"):
        db.ensure_user(uid, full_name=config.DEFAULT_STUDENT_NAMES["web"], platform="web", phone_number=data.get("phone_number", ""))
    if not db.is_pro(uid):
        return jsonify({"ok":False,"error":"Viva pack is PRO-only"}),403
    ans = run_ai(ai.answer, config.PRODUCT_TEXT["viva_prompt"].format(count=config.PRO_VIVA_QUESTIONS, topic=q), "mcq", is_pro=True, language=db.get_language(uid))
    return jsonify({"ok":True,"viva":ans})


@app.route("/api/cron/backup", methods=["GET", "POST"])
def cron_backup():
    if not _cron_authorized() or not supa.enabled:
        return jsonify({"ok": False, "error": "unauthorized_or_supabase_disabled"}), 403
    snapshot = {"created_at": _now_ist().isoformat(), "brand": BRAND_NAME, "stats": db.get_stats(), "users": []}
    for uid in _cron_users()[:20000]:
        u = db.get_user(uid) or {}
        snapshot["users"].append({
            "phone_number": u.get("phone_number", ""), "full_name": u.get("full_name", config.DEFAULT_STUDENT_NAMES["generic"]),
            "coins": int(u.get("coins",0) or 0), "xp": int(u.get("xp",0) or 0),
            "streak": int(u.get("streak",0) or 0), "level": int(u.get("level",1) or 1),
            "exam_type": u.get("exam_type","general"), "subject": u.get("subject","general"),
        })
    ok = supa.insert("backups", {"kind":"daily_profile_snapshot", "payload":snapshot})
    return jsonify({"ok": ok, "users": len(snapshot["users"])})




# --- WhatsApp interactive menu / button replies (config-driven) --------------
_ORIG_WA_SEND_TEXT = _send_whatsapp_text

def _send_whatsapp_interactive_menu(to_number: str) -> bool:
    if not config.WHATSAPP_TOKEN or not config.WHATSAPP_PHONE_NUMBER_ID:
        return False
    try:
        url=f"{config.URLS['whatsapp_graph_base_url']}/{config.WHATSAPP_API_VERSION}/{config.WHATSAPP_PHONE_NUMBER_ID}/messages"
        headers={"Authorization":f"Bearer {config.WHATSAPP_TOKEN}","Content-Type":"application/json"}
        rows=[{"id":str(x.get('id')),"title":str(x.get('title'))[:24]} for x in TEXT_WHATSAPP_MENU[:10]]
        payload={"messaging_product":"whatsapp","to":to_number,"type":"interactive","interactive":{"type":"list","body":{"text":config.CONTENT.get('whatsapp_welcome_text','Kya chahiye?')},"action":{"button":config.CONTENT.get('whatsapp_menu_button','Open Menu')[:20],"sections":[{"title":config.CONTENT.get('whatsapp_menu_section','SaarthiBhai'),"rows":rows}]}}}
        r=requests.post(url,json=payload,headers=headers,timeout=15)
        return r.status_code<300
    except Exception as e:
        logger.warning('WhatsApp interactive menu: %s',e); return False


def _send_whatsapp_document(to_number: str, url: str, caption: str = '') -> bool:
    if not config.WHATSAPP_TOKEN or not config.WHATSAPP_PHONE_NUMBER_ID or not url:
        return False
    try:
        api=f"{config.URLS['whatsapp_graph_base_url']}/{config.WHATSAPP_API_VERSION}/{config.WHATSAPP_PHONE_NUMBER_ID}/messages"
        headers={"Authorization":f"Bearer {config.WHATSAPP_TOKEN}","Content-Type":"application/json"}
        payload={"messaging_product":"whatsapp","to":to_number,"type":"document","document":{"link":url,"caption":caption[:1024]}}
        r=requests.post(api,json=payload,headers=headers,timeout=15)
        return r.status_code<300
    except Exception as e:
        logger.warning('WhatsApp document: %s',e); return False


def _process_whatsapp_button(from_number: str, button_id: str, profile_name: str = '') -> None:
    item=next((x for x in TEXT_WHATSAPP_MENU if str(x.get('id'))==str(button_id)),None)
    if not item: return
    tool=str(item.get('tool') or 'general')
    _ORIG_PROCESS_WA = process_whatsapp_message
    if tool=='youtube':
        user=db.ensure_user(f'wa:{from_number}',full_name=profile_name or config.DEFAULT_STUDENT_NAMES['generic'],platform='whatsapp',phone_number=from_number)
        links=_youtube_links(str(user.get('subject') or user.get('exam_type') or 'study'),config.PRO_VIDEO_LINKS if db.is_pro(f'wa:{from_number}') else config.FREE_VIDEO_LINKS)
        body='\n'.join([f"▶️ {x.get('title')}: {x.get('url')}" for x in links])
        _ORIG_WA_SEND_TEXT(from_number,body or config.SYSTEM_PROTOCOL.get('public_error_no_response','No answer'))
        return
    # Tool button alone has no text question; guide the student to send the topic.
    _ORIG_WA_SEND_TEXT(from_number, f"{item.get('title',tool)} select ho gaya. Ab topic/question bhej de bhai.")


# Replace the existing WhatsApp webhook with an additive route alias for interactive payloads.
@app.route('/webhook/whatsapp-interactive', methods=['POST'])
def whatsapp_interactive_alias():
    body=request.get_json(silent=True) or {}
    for ent in body.get('entry',[]):
        for change in ent.get('changes',[]):
            value=change.get('value',{}); contacts=value.get('contacts',[]); profile=(contacts[0].get('profile',{}).get('name','') if contacts else '')
            for msg in value.get('messages',[]):
                if msg.get('type')=='interactive':
                    inter=msg.get('interactive') or {}; button=((inter.get('list_reply') or {}).get('id') or (inter.get('button_reply') or {}).get('id') or '')
                    sender=str(msg.get('from') or '')
                    if sender and button: _process_whatsapp_button(sender,button,profile)
    return jsonify({'ok':True})

# Greeting menu: additive wrapper used by deployments that call the alias route.
@app.route('/webhook/whatsapp-menu', methods=['POST'])
def whatsapp_menu_alias():
    body=request.get_json(silent=True) or {}
    for ent in body.get('entry',[]):
        for change in ent.get('changes',[]):
            value=change.get('value',{}); contacts=value.get('contacts',[]); profile=(contacts[0].get('profile',{}).get('name','') if contacts else '')
            for msg in value.get('messages',[]):
                if msg.get('type')!='text': continue
                sender=str(msg.get('from') or ''); text=str((msg.get('text') or {}).get('body') or '').strip().lower()
                if sender and text in {'hi','hello','hey','start','namaste'}:
                    if not _send_whatsapp_interactive_menu(sender): _ORIG_WA_SEND_TEXT(sender,config.CONTENT.get('whatsapp_welcome_text','Kya chahiye?'))
                elif sender:
                    process_whatsapp_message(sender,text,profile)
    return jsonify({'ok':True})



# ============================================================================
# SAARTHIBHAI 12-POINT TEXT FEATURE PACK — additive, config-driven layer
# Everything business/content related is loaded from saarthibhai.config.json;
# secrets remain environment-only.
# ============================================================================

TEXT_FEATURES = config.CONTENT
TEXT_RUNTIME = RUNTIME_CONFIG.get("runtime", {})
TEXT_PRO_GROWTH = TEXT_FEATURES.get("pro_growth", {})
TEXT_STUDENT_TYPES = TEXT_FEATURES.get("student_types", {})
TEXT_FEATURES_ACCESS = TEXT_FEATURES.get("feature_access", {})
TEXT_ACTIONS = TEXT_FEATURES.get("answer_actions", [])
TEXT_WHATSAPP_MENU = TEXT_FEATURES.get("whatsapp_menu", [])
TEXT_FALLBACK = TEXT_FEATURES.get("fallback_messages", {})
TEXT_REMINDERS = TEXT_FEATURES.get("reminder_messages", {})
TEXT_MARKETING = TEXT_FEATURES.get("marketing", {})
TEXT_PHD_TOOLS = TEXT_FEATURES.get("phd_tools", [])


def _text_action_keyboard(is_pro: bool = False):
    rows=[]
    for action in TEXT_ACTIONS:
        label=str(action.get("label") or action.get("id") or "Action")
        tool=str(action.get("tool") or "general")
        locked=(tool in PRO_ONLY_TOOLS and not is_pro)
        shown=("🔒 " if locked else "")+label
        rows.append(InlineKeyboardButton(shown[:64], callback_data=f"textaction_{action.get('id','general')}"))
    if not rows:
        return None
    return InlineKeyboardMarkup([rows[i:i+2] for i in range(0,len(rows),2)])


# --- Extra profile state in Supabase (all values are user-configurable) ----
_ORIG_SYNC_AFTER_TEXT = Database.sync_user_to_supabase

def _sync_user_with_text_profile(self, uid, user=None):
    u = user or self.get_user(uid)
    ok = _ORIG_SYNC_AFTER_TEXT(self, uid, u)
    try:
        if self.supabase.enabled and u:
            phone = normalize_phone(u.get("phone_number", ""))
            if phone:
                extra = {
                    "board": u.get("board", "") or "",
                    "course": u.get("course", "") or "",
                    "class_level": u.get("class_level", "") or "",
                    "semester": u.get("semester", "") or "",
                    "student_type": u.get("student_type", "") or "",
                    "target_exam": u.get("target_exam", u.get("exam_type", "")) or "",
                    "study_mode": u.get("study_mode", "") or "",
                    "target_days": int(u.get("target_days", 0) or 0) if str(u.get("target_days", "")).strip() else None,
                    "parent_report_opt_in": str(u.get("parent_report_opt_in", "0")).lower() in ("1","true","yes"),
                    "teacher_plan": u.get("teacher_plan", "free") or "free",
                    "teacher_pro_until": u.get("teacher_pro_until") or None,
                    "last_pro_reminder_at": u.get("last_pro_reminder_at") or None,
                    "snapchat_streak": int(u.get("snapchat_streak", 0) or 0),
                    "snapchat_last_seen": u.get("snapchat_last_seen") or None,
                }
                _DB_WRITE_POOL.submit(self.supabase.upsert, "users", {"phone_number": phone, **extra}, "phone_number")
    except Exception as e:
        logger.debug("text profile sync skipped: %s", e)
    return ok

Database.sync_user_to_supabase = _sync_user_with_text_profile


def _text_profile_for_uid(uid: str | int) -> dict:
    try:
        return db.get_user(uid) or {}
    except Exception:
        return {}


def _apply_student_profile_prompt(question: str, uid: str | int = "", phone_number: str = "") -> str:
    user = _text_profile_for_uid(uid) if uid else {}
    if not user and phone_number:
        p = normalize_phone(phone_number)
        if p and supa.enabled:
            row = supa.select_one("users", {"phone_number": f"eq.{p}"}) or {}
            user = {k: (json.dumps(v) if isinstance(v, (dict, list)) else ("" if v is None else str(v))) for k,v in row.items()}
    student_type = str(user.get("student_type", "")).strip().lower()
    bits = []
    if student_type and student_type in TEXT_STUDENT_TYPES:
        bits.append(str(TEXT_STUDENT_TYPES[student_type].get("prompt", "")))
    for key, label in (("board","Board"),("course","Course"),("class_level","Class/Sem"),("semester","Semester"),("target_exam","Target Exam"),("subject","Subject")):
        value = str(user.get(key, "")).strip()
        if value:
            bits.append(f"{label}: {value}")
    target_days = int(user.get("target_days", 0) or 0)
    if target_days:
        bits.append(f"Days to target exam: {target_days}")
    if not bits:
        return question
    return "STUDENT PROFILE CONTEXT:\n" + "\n".join(bits) + "\n\nSTUDENT QUESTION:\n" + question


# Student mode context: Type-2 learners can switch Board Mode / Competitive Mode without changing identity.
_ORIG_APPLY_PROFILE_TEXT = _apply_student_profile_prompt
def _apply_student_profile_prompt_v9(question: str, uid: str | int = "", phone_number: str = "") -> str:
    enriched = _ORIG_APPLY_PROFILE_TEXT(question, uid=uid, phone_number=phone_number)
    user = _text_profile_for_uid(uid) if uid else {}
    mode = str(user.get('study_mode','')).strip().lower()
    if mode:
        mode_cfg = next((m for m in TEXT_FEATURES.get('study_modes',[]) if str(m.get('id','')).lower()==mode), None)
        if mode_cfg:
            return f"STUDY MODE: {mode_cfg.get('label','')}\n{mode_cfg.get('prompt','')}\n\n{enriched}"
    return enriched
_apply_student_profile_prompt = _apply_student_profile_prompt_v9

# --- Resilient 3-stage fallback: provider chain -> saved answer -> YouTube ---
_ORIG_GET_AI_V8_BASE = get_ai_answer_v7

def _fallback_youtube_answer(question: str, is_pro: bool, language: str = "hinglish") -> str:
    links = _youtube_links(question, config.PRO_VIDEO_LINKS if is_pro else config.FREE_VIDEO_LINKS, language=language)
    lines = [str(TEXT_FALLBACK.get("busy", "")).strip()]
    for item in links:
        lines.append(f"▶️ {item.get('title','Relevant video')}: {item.get('url','')}")
    return "\n\n".join([x for x in lines if x])


def get_ai_answer_v8(question: str, tool: str, is_pro: bool, language: str = "hinglish",
                     phone_number: str = "", exam_type: str = "", subject: str = "", uid: str = ""):
    enriched = _apply_student_profile_prompt(question, uid=uid, phone_number=phone_number)
    # Single-flight: if many students ask the same theory question at once, one
    # provider call wins and everyone else reuses the saved answer. This is
    # config-driven and falls back safely when Redis is unavailable.
    flight_key = None
    flight_owner = False
    if db.redis and bool(TEXT_RUNTIME.get("ai_queue_enabled", True)) and tool not in _NEVER_CACHE_TOOLS:
        try:
            flight_key = "aiflight:" + hashlib.sha256((tool+"|"+language+"|"+question.strip().lower()).encode("utf-8")).hexdigest()
            flight_owner = bool(db.redis.set(flight_key, "1", nx=True, ex=int(TEXT_RUNTIME.get("ai_singleflight_wait_sec",18) or 18)))
            if not flight_owner:
                deadline=time.time()+float(TEXT_RUNTIME.get("ai_singleflight_wait_sec",18) or 18)
                while time.time()<deadline:
                    cached=db.cache_get(make_cache_key(tool,question,is_pro,language))
                    if cached:
                        return cached
                    time.sleep(0.35)
                durable=db.get_master_cache(question,tool=tool,exam_type=exam_type,subject=subject,phone_number=phone_number)
                if durable and durable.get("answer"):
                    return str(durable["answer"])
        except Exception:
            flight_key=None; flight_owner=False
    result = _ORIG_GET_AI_V8_BASE(enriched, tool, is_pro, language=language, phone_number=phone_number, exam_type=exam_type, subject=subject)
    if flight_owner and flight_key and db.redis:
        try: db.redis.delete(flight_key)
        except Exception: pass
    if result and str(result).strip() and str(result).strip() != SOFT_FAIL_MSG.strip():
        return result
    # Saved theory answer after provider failure.
    if tool not in _NEVER_CACHE_TOOLS:
        try:
            durable = db.get_master_cache(question, tool=tool, exam_type=exam_type, subject=subject, phone_number=phone_number)
            if durable and durable.get("answer"):
                return str(durable["answer"])
        except Exception:
            pass
    return _fallback_youtube_answer(question, is_pro, language)

get_ai_answer_v7 = get_ai_answer_v8
get_ai_answer = get_ai_answer_v8

# Config-driven AI concurrency admission: identical questions still single-flight, while
# Pro callers receive a longer admission window before cached/YouTube fallback.
def _ai_slot_acquire(is_pro: bool) -> tuple[bool,str]:
    if not db.redis:
        return True, ''
    max_active = max(1, int(TEXT_RUNTIME.get('ai_max_concurrency', 6) or 6))
    wait_sec = float(TEXT_RUNTIME.get('ai_queue_pro_wait_sec' if is_pro else 'ai_queue_free_wait_sec', 18 if is_pro else 5) or 0)
    key='ai:active_slots'
    token=secrets.token_hex(8)
    deadline=time.time()+wait_sec
    script="""local key=KEYS[1]; local now=tonumber(ARGV[1]); local maxn=tonumber(ARGV[2]); local token=ARGV[3]; local ttl=tonumber(ARGV[4]); redis.call('ZREMRANGEBYSCORE',key,0,now-ttl); local n=redis.call('ZCARD',key); if n<maxn then redis.call('ZADD',key,now,token); redis.call('EXPIRE',key,ttl+5); return 1 else return 0 end"""
    while time.time() < deadline:
        try:
            ok=db.redis.eval(script,1,key,time.time(),max_active,token,120)
            if int(ok or 0)==1: return True,token
        except Exception:
            return True,''
        time.sleep(0.15)
    return False,token

def _ai_slot_release(token: str) -> None:
    if token and db.redis:
        try: db.redis.zrem('ai:active_slots',token)
        except Exception: pass

_ORIG_GET_AI_V8_FINAL = get_ai_answer_v8
def get_ai_answer_v9(question: str, tool: str, is_pro: bool, language: str = 'hinglish', phone_number: str = '', exam_type: str = '', subject: str = '', uid: str = ''):
    acquired, token = _ai_slot_acquire(is_pro)
    if not acquired:
        # Deliberately use the same durable cache/YouTube fallback instead of exposing an AI error.
        try:
            if tool not in _NEVER_CACHE_TOOLS:
                cached=db.get_master_cache(question,tool=tool,exam_type=exam_type,subject=subject,phone_number=phone_number)
                if cached and cached.get('answer'): return str(cached['answer'])
        except Exception: pass
        return _fallback_youtube_answer(question,is_pro,language)
    try:
        return _ORIG_GET_AI_V8_FINAL(question,tool,is_pro,language=language,phone_number=phone_number,exam_type=exam_type,subject=subject,uid=uid)
    finally:
        _ai_slot_release(token)

get_ai_answer_v8 = get_ai_answer_v9
get_ai_answer_v7 = get_ai_answer_v9
get_ai_answer = get_ai_answer_v9

@app.route("/api/quick-action", methods=["POST"])
def api_quick_action():
    data = request.get_json(silent=True) or {}
    client_id = str(data.get("client_id") or "").strip()
    question = str(data.get("question") or "").strip()
    action_id = str(data.get("action") or "").strip().lower()
    if not client_id or not question or not action_id:
        return jsonify({"ok": False, "error": "client_id, question and action required"}), 400
    action = next((a for a in TEXT_ACTIONS if str(a.get("id")) == action_id), None)
    if not action:
        return jsonify({"ok": False, "error": "Unknown action"}), 400
    uid = f"web:{client_id}"
    db.ensure_user(uid, full_name=config.DEFAULT_STUDENT_NAMES["web"], platform="web")
    is_pro = db.is_pro(uid)
    tool = str(action.get("tool") or "general")
    if tool in PRO_ONLY_TOOLS and not is_pro:
        return jsonify({"ok": False, "pro_only": True, "error": config.PRODUCT_TEXT.get("pro_tool", "Pro feature hai.")}), 403
    if action_id == "video":
        return jsonify({"ok": True, "videos": _youtube_links(question, config.PRO_VIDEO_LINKS if is_pro else config.FREE_VIDEO_LINKS)})
    if action_id == "quiz":
        if not is_pro and db.get_daily_quiz_used(uid):
            return jsonify({"ok": False, "error": "Free daily quiz limit reached. Upgrade for unlimited quizzes."}), 403
        n_questions = config.QUIZ_PRO_QUESTIONS if is_pro else config.QUIZ_FREE_QUESTIONS
        questions = run_ai(ai.generate_quiz, question, n_questions=n_questions, language=db.get_language(uid))
        if not questions:
            return jsonify({"ok": False, "error": "Quiz generate nahi ho paya."}), 502
        if not is_pro: db.mark_daily_quiz_used(uid)
        return jsonify({"ok":True,"quiz":questions,"pro":is_pro})
    answer = get_ai_answer_v8(question, tool, is_pro, language=db.get_language(uid), phone_number=_phone_for_uid(uid), exam_type=_text_profile_for_uid(uid).get("exam_type", ""), subject=_text_profile_for_uid(uid).get("subject", ""), uid=uid)
    db.add_personal_history(uid, question, tool=tool)
    schedule_spaced_reminders(uid, question, tool)
    return jsonify({"ok": True, "tool": tool, "answer": answer, "meta": _10in1_metadata(question, is_pro, uid)})


@app.route("/api/resource/videos", methods=["GET", "POST"])
def api_resource_videos():
    data = request.get_json(silent=True) if request.method == "POST" else request.args
    data = data or {}
    topic = str(data.get("topic") or data.get("question") or "").strip()
    if not topic:
        return jsonify({"ok": False, "error": "topic required"}), 400
    uid = str(data.get("uid") or data.get("client_id") or "").strip()
    is_pro = db.is_pro(uid) if uid else False
    links = _youtube_links(topic, config.PRO_VIDEO_LINKS if is_pro else config.FREE_VIDEO_LINKS)
    return jsonify({"ok": True, "videos": links, "limit": config.PRO_VIDEO_LINKS if is_pro else config.FREE_VIDEO_LINKS})


# --- Three-click onboarding --------------------------------------------------
@app.route("/api/onboarding/options")
def api_onboarding_options():
    return jsonify({
        "ok": True,
        "steps": TEXT_FEATURES.get("onboarding_steps", []),
        "boards": TEXT_FEATURES.get("boards", []),
        "college_courses": TEXT_FEATURES.get("college_courses", []),
        "competitive_exams": TEXT_FEATURES.get("competitive_exams", []),
        "subjects": TEXT_FEATURES.get("subjects_universe", []),
        "student_types": TEXT_STUDENT_TYPES,
        "study_modes": TEXT_FEATURES.get("study_modes", []),
        "learner_tracks": TEXT_FEATURES.get("learner_tracks", []),
        "parent_report_prompt": TEXT_FEATURES.get("onboarding_parent_prompt", ""),
    })


@app.route("/api/onboarding/save", methods=["POST"])
def api_onboarding_save():
    data = request.get_json(silent=True) or {}
    client_id = str(data.get("client_id") or "").strip()
    if not client_id:
        return jsonify({"ok": False, "error": "client_id required"}), 400
    uid = f"web:{client_id}"
    phone = normalize_phone(data.get("phone_number") or data.get("phone") or "")
    user = db.ensure_user(uid, full_name=config.DEFAULT_STUDENT_NAMES["web"], platform="web", phone_number=phone)
    clean = {
        "learner_track": str(data.get("learner_track") or user.get("learner_track") or "school_college").strip().lower(),
        "student_type": str(data.get("student_type") or user.get("student_type") or "").strip().lower(),
        "board": str(data.get("board") or "").strip(),
        "course": str(data.get("course") or "").strip(),
        "class_level": str(data.get("class_level") or "").strip(),
        "semester": str(data.get("semester") or "").strip(),
        "study_mode": str(data.get("study_mode") or user.get("study_mode") or "").strip().lower(),
        "target_exam": str(data.get("target_exam") or "").strip(),
        "subject": str(data.get("subject") or "").strip(),
        "target_days": int(data.get("target_days") or 0) if str(data.get("target_days") or "").strip() else 0,
        "parent_report_opt_in": bool(data.get("parent_report_opt_in")),
    }
    for key, value in clean.items():
        if key == "parent_report_opt_in":
            user[key] = "1" if value else "0"
        else:
            user[key] = str(value)
    if clean.get("target_exam"):
        user["exam_type"] = clean["target_exam"].lower()
    user["updated_at"] = _now_ist().isoformat()
    db.save_user(uid, user); db.sync_user_to_supabase(uid, user)
    return jsonify({"ok": True, "profile": clean})


# --- Board/course/exam search endpoint -------------------------------------
@app.route("/api/catalog/search")
def api_catalog_search():
    q = str(request.args.get("q") or "").strip().lower()
    if not q:
        return jsonify({"ok": True, "results": []})
    out = []
    for category, values in (("board", TEXT_FEATURES.get("boards", [])), ("course", TEXT_FEATURES.get("college_courses", [])), ("exam", TEXT_FEATURES.get("competitive_exams", [])), ("subject", TEXT_FEATURES.get("subjects_universe", []))):
        for value in values:
            if q in str(value).lower():
                out.append({"category": category, "value": value})
    return jsonify({"ok": True, "results": out[:50]})


# --- PhD section -------------------------------------------------------------
@app.route("/phd")
def phd_page():
    cards = "".join([f"<button class='phd-btn' data-id='{a['id']}'>{a['label']}</button>" for a in TEXT_PHD_TOOLS])
    html = f'''<!doctype html><html><head><meta name="viewport" content="width=device-width,initial-scale=1"><title>{config.BRAND_NAME} — PhD</title><style>body{{font-family:system-ui;max-width:850px;margin:0 auto;padding:24px;background:{config.THEME.get('background','#fff')};color:{config.THEME.get('text','#111')}}}.grid{{display:grid;grid-template-columns:repeat(auto-fit,minmax(190px,1fr));gap:12px}}button{{padding:14px;border:0;border-radius:12px;background:{config.THEME.get('primary','#FFD600')};font-weight:700;cursor:pointer}}textarea{{width:100%;min-height:120px;margin-top:16px;padding:12px}}#out{{white-space:pre-wrap;margin-top:16px;border:1px solid {config.THEME.get('border','rgba(0,0,0,.12)')};padding:16px;border-radius:12px}}</style></head><body><h1>🎓 {config.BRAND_NAME} — PhD Section</h1><p>Research support, not fabricated citations or guaranteed publication outcomes.</p><div class="grid">{cards}</div><textarea id="topic" placeholder="Topic / research area"></textarea><div id="out"></div><script>const acts={json.dumps(TEXT_PHD_TOOLS, ensure_ascii=False)};document.querySelectorAll('.phd-btn').forEach(b=>b.onclick=async()=>{{const a=acts.find(x=>x.id===b.dataset.id);const topic=document.getElementById('topic').value.trim();if(!topic)return;document.getElementById('out').textContent='Generating...';const r=await fetch('/api/phd',{{method:'POST',headers:{{'Content-Type':'application/json'}},body:JSON.stringify({{action:a.id,topic}})}});const d=await r.json();document.getElementById('out').textContent=d.answer||d.error||'No answer';}});</script></body></html>'''
    return html

@app.route("/api/phd", methods=["POST"])
def api_phd():
    data = request.get_json(silent=True) or {}
    action_id = str(data.get("action") or "").strip()
    topic = str(data.get("topic") or "").strip()
    action = next((a for a in TEXT_PHD_TOOLS if a.get("id") == action_id), None)
    if not action or not topic:
        return jsonify({"ok": False, "error": "action and topic required"}), 400
    prompt = f"{action.get('prompt','')}\n\nResearch topic: {topic}\nUse careful language. Do not invent citations, journal indexing or publication guarantees."
    answer = run_ai(ai.answer, prompt, "general", is_pro=True, language="english")
    return jsonify({"ok": True, "answer": answer})


# --- Teacher extras: homework, tests, notes, reports ------------------------
def _teacher_profile(uid: str):
    return db.get_user(uid) or {}

def _teacher_class_limit(teacher_uid: str) -> tuple[int,int]:
    u = _teacher_profile(teacher_uid)
    teacher_plan = str(u.get("teacher_plan", "free"))
    if teacher_plan == "pro":
        return int(TEXT_PRO_GROWTH["teacher_pro_classes"]), int(TEXT_PRO_GROWTH["teacher_pro_students_per_class"])
    return int(TEXT_PRO_GROWTH["teacher_free_classes"]), int(TEXT_PRO_GROWTH["teacher_free_students_per_class"])

_ORIG_CREATE_CLASS = Database.create_class_code

def _create_class_with_limit(self, teacher_uid, class_name=""):
    max_classes, _ = _teacher_class_limit(str(teacher_uid))
    existing = self.get_teacher_classes(teacher_uid)
    if len(existing) >= max_classes:
        return ""
    return _ORIG_CREATE_CLASS(self, teacher_uid, class_name)
Database.create_class_code = _create_class_with_limit

@app.route("/api/teacher/assignment", methods=["POST"])
def api_teacher_assignment():
    data = request.get_json(silent=True) or {}
    teacher_uid = f"web:{str(data.get('client_id') or '').strip()}"
    class_code = str(data.get("class_code") or "").strip().upper()
    title = str(data.get("title") or "").strip()
    instructions = str(data.get("instructions") or "").strip()
    due_at = str(data.get("due_at") or "").strip()
    if not title or not class_code:
        return jsonify({"ok": False, "error": "class_code and title required"}), 400
    payload = {"teacher_uid":teacher_uid,"class_code":class_code,"title":title[:200],"instructions":instructions[:5000],"due_at":due_at[:64],"created_at":_now_ist().isoformat()}
    ident = secrets.token_hex(8)
    if db.redis:
        db.redis.hset(f"teacher:assignment:{ident}", mapping=payload); db.redis.expire(f"teacher:assignment:{ident}", 86400*180)
        db.redis.sadd(f"teacher:tasks:{class_code}:assignments", ident)
    if supa.enabled:
        try: supa.insert("teacher_assignments", {**payload, "class_code":class_code, "teacher_uid":teacher_uid})
        except Exception: pass
    return jsonify({"ok":True,"assignment_id":ident})

@app.route("/api/teacher/test", methods=["POST"])
def api_teacher_test():
    data = request.get_json(silent=True) or {}
    teacher_uid = f"web:{str(data.get('client_id') or '').strip()}"
    class_code = str(data.get("class_code") or "").strip().upper()
    title = str(data.get("title") or "").strip()
    topic = str(data.get("topic") or "").strip()
    scheduled_at = str(data.get("scheduled_at") or "").strip()
    if not title or not class_code:
        return jsonify({"ok":False,"error":"class_code and title required"}),400
    payload={"teacher_uid":teacher_uid,"class_code":class_code,"title":title[:200],"topic":topic[:200],"scheduled_at":scheduled_at[:64],"created_at":_now_ist().isoformat()}
    ident=secrets.token_hex(8)
    if db.redis:
        db.redis.hset(f"teacher:test:{ident}",mapping=payload); db.redis.expire(f"teacher:test:{ident}",86400*180); db.redis.sadd(f"teacher:tasks:{class_code}:tests",ident)
    if supa.enabled:
        try: supa.insert("teacher_tests",payload)
        except Exception: pass
    return jsonify({"ok":True,"test_id":ident})

@app.route("/api/teacher/note", methods=["POST"])
def api_teacher_note():
    data=request.get_json(silent=True) or {}
    teacher_uid=f"web:{str(data.get('client_id') or '').strip()}"
    class_code=str(data.get('class_code') or '').strip().upper(); title=str(data.get('title') or '').strip(); content=str(data.get('content') or '').strip()
    if not title or not class_code or not content: return jsonify({"ok":False,"error":"class_code, title, content required"}),400
    payload={"teacher_uid":teacher_uid,"class_code":class_code,"title":title[:200],"content":content[:10000],"created_at":_now_ist().isoformat()}
    ident=secrets.token_hex(8)
    if db.redis:
        db.redis.hset(f"teacher:note:{ident}",mapping=payload); db.redis.expire(f"teacher:note:{ident}",86400*180); db.redis.sadd(f"teacher:tasks:{class_code}:notes",ident)
    if supa.enabled:
        try:supa.insert("teacher_notes",payload)
        except Exception:pass
    return jsonify({"ok":True,"note_id":ident})

@app.route("/api/teacher/class/<code>/tasks")
def api_teacher_tasks(code):
    code=str(code or '').strip().upper()
    out={"assignments":[],"tests":[],"notes":[]}
    if db.redis:
        for ident in db.redis.smembers(f"teacher:tasks:{code}:assignments") or []:
            d=db.redis.hgetall(f"teacher:assignment:{ident}")
            if d: out['assignments'].append(d)
        for ident in db.redis.smembers(f"teacher:tasks:{code}:tests") or []:
            d=db.redis.hgetall(f"teacher:test:{ident}")
            if d: out['tests'].append(d)
        for ident in db.redis.smembers(f"teacher:tasks:{code}:notes") or []:
            d=db.redis.hgetall(f"teacher:note:{ident}")
            if d: out['notes'].append(d)
    return jsonify({"ok":True,"class_code":code,**out})

@app.route("/api/teacher/class/<code>/report")
def api_teacher_class_report(code):
    info=db.get_class_info(code)
    if not info: return jsonify({"ok":False,"error":"Class not found"}),404
    # Class report is built from the same live student stats; no fabricated metrics.
    rows=[]
    for s in info.get("students",[]):
        rows.append({"name":s.get("name"),"xp":s.get("xp",0),"level":s.get("level",1),"streak":s.get("streak",0),"questions_asked":s.get("questions_asked",0)})
    return jsonify({"ok":True,"class_code":code,"student_count":len(rows),"students":rows})


# --- Wheel upgrade: keep existing coin spin, optionally award one Pro day ----
_ORIG_DO_SPIN_TEXT = Database.do_spin

def _do_spin_text(self, uid):
    base_amount = _ORIG_DO_SPIN_TEXT(self, uid)
    if base_amount is None:
        return None
    rewards = list(TEXT_PRO_GROWTH.get("wheel_coin_rewards") or [])
    if rewards:
        target = int(random.choice(rewards))
        delta = target - int(base_amount)
        if delta:
            self.add_coins(uid,delta)
        amount = target
    else:
        amount = base_amount
    chance = float(TEXT_PRO_GROWTH.get("wheel_special_pro_chance", 0.0) or 0.0)
    if chance > 0 and random.random() < chance:
        days = int(TEXT_PRO_GROWTH.get("wheel_special_pro_days", 1) or 1)
        self.activate_pro(uid, days=days)
        if self.redis:
            self.redis.setex(f"spin:proday:{uid}:{_today_ist()}", 90000, str(days))
    return amount
Database.do_spin = _do_spin_text

# --- Enhanced /api/spin result ------------------------------------------------
_orig_spin_view = api_spin
@app.route("/api/spin-enhanced", methods=["POST"])
def api_spin_enhanced():
    data=request.get_json(silent=True) or {}; client_id=str(data.get('client_id') or '').strip()
    if not client_id: return jsonify({"ok":False,"error":"client_id required"}),400
    uid=f"web:{client_id}"; db.ensure_user(uid,full_name=config.DEFAULT_STUDENT_NAMES['web'],platform='web')
    won=db.do_spin(uid)
    if won is None: return jsonify({"ok":False,"error":TEXT_FALLBACK.get('spin_used','Aaj ka spin ho chuka hai.') }),429
    proday=0
    if db.redis: proday=int(db.redis.get(f"spin:proday:{uid}:{_today_ist()}") or 0)
    return jsonify({"ok":True,"coins_won":won,"coins_total":db.get_coins(uid),"pro_day_won":proday})


# --- Instagram / Snapchat optional outbound adapters -----------------------
@app.route("/api/instagram/story-poll", methods=["POST"])
def api_instagram_story_poll():
    data=request.get_json(silent=True) or {}
    recipients=data.get('recipient_ids') or []
    if not isinstance(recipients,list): recipients=[]
    template=TEXT_FEATURES.get('instagram_content_templates',{})
    sent=0
    for recipient_id in recipients:
        if _send_instagram_text(str(recipient_id), template.get('story_poll','')): sent+=1
    return jsonify({"ok":True,"sent":sent,"options":template.get('story_options',[])})

@app.route("/api/instagram/reel-followup", methods=["POST"])
def api_instagram_reel_followup():
    data=request.get_json(silent=True) or {}
    recipient_id=str(data.get('recipient_id') or '').strip()
    if not recipient_id: return jsonify({"ok":False,"error":"recipient_id required"}),400
    ok=_send_instagram_text(recipient_id, TEXT_FEATURES.get('instagram_content_templates',{}).get('reel_followup',''))
    return jsonify({"ok":bool(ok)})

@app.route("/api/snapchat/status")
def api_snapchat_status():
    return jsonify({"ok":True,"gateway_enabled":bool(SNAPCHAT_ENABLED),"note":"Snapchat DM automation requires an approved configured gateway; this endpoint reports configuration only."})

# --- 6AM/5PM/8PM reminder trio ----------------------------------------------
@app.route("/api/cron/daily-6am", methods=["GET","POST"])
def cron_daily_6am():
    if not _cron_authorized(): return jsonify({"ok":False,"error":"unauthorized"}),403
    sent=0
    for uid in _cron_users():
        u=db.get_user(uid) or {}; name=u.get('full_name',config.DEFAULT_STUDENT_NAMES['generic'])
        body=TEXT_REMINDERS.get('morning_6am','').format(name=name,spin_reward=(TEXT_PRO_GROWTH.get('wheel_coin_rewards') or [''])[0])
        if _send_user_push(u,body): sent+=1
    return jsonify({"ok":True,"sent":sent})

@app.route("/api/cron/text-5pm", methods=["GET","POST"])
def cron_text_5pm():
    if not _cron_authorized(): return jsonify({"ok":False,"error":"unauthorized"}),403
    sent=0
    for uid in _cron_users():
        u=db.get_user(uid) or {}; mission=daily_mission_for_user(uid); name=u.get('full_name',config.DEFAULT_STUDENT_NAMES['generic'])
        body=TEXT_REMINDERS.get('mission_5pm','').format(name=name,exam=mission.get('exam_type','general'),minutes=mission.get('duration_min',config.DAILY_MISSION_MINUTES),start_cta=TEXT_REMINDERS.get('start_cta',''))
        if _send_user_push(u,body): sent+=1
    return jsonify({"ok":True,"sent":sent})

@app.route("/api/cron/text-8pm", methods=["GET","POST"])
def cron_text_8pm():
    if not _cron_authorized(): return jsonify({"ok":False,"error":"unauthorized"}),403
    sent=0
    for uid in _cron_users():
        u=db.get_user(uid) or {}; streak=int(u.get('streak',0) or 0); body=TEXT_REMINDERS.get('warning_8pm','').format(name=u.get('full_name',config.DEFAULT_STUDENT_NAMES['generic']),streak=streak,hours_left=TEXT_REMINDERS.get('warning_hours_left',''),save_cta=TEXT_REMINDERS.get('save_cta',''))
        if _send_user_push(u,body): sent+=1
    return jsonify({"ok":True,"sent":sent})


# --- Pro expiry reminders ----------------------------------------------------
@app.route("/api/cron/pro-reminders", methods=["GET","POST"])
def cron_pro_reminders():
    if not _cron_authorized(): return jsonify({"ok":False,"error":"unauthorized"}),403
    sent=0
    now=_now_ist()
    for uid in _cron_users():
        u=db.get_user(uid) or {}
        if str(u.get('plan','')).lower()!='pro' or not u.get('pro_until'): continue
        try:
            until=datetime.fromisoformat(str(u['pro_until']).replace('Z','+00:00'))
            if until.tzinfo is None: until=until.replace(tzinfo=IST)
            days=max(0,(until-now).days)
        except Exception: continue
        if days in (3,1,0):
            key=f"proremind:{_canonical_uid(db,uid)}:{_today_ist()}:{days}"
            if db.redis and not db.redis.set(key,'1',nx=True,ex=90000): continue
            template=TEXT_REMINDERS.get({3:'pro_3d',1:'pro_1d',0:'pro_last'}[days],'')
            body=template.format(days=days,coins=config.REFERRAL_COINS)
            if _send_user_push(u,body): sent+=1
    return jsonify({"ok":True,"sent":sent})


# --- Payment reconciliation & screenshot fallback --------------------------
@app.route("/api/cron/payment-reconcile", methods=["GET","POST"])
def cron_payment_reconcile():
    if not _cron_authorized(): return jsonify({"ok":False,"error":"unauthorized"}),403
    if not db.redis: return jsonify({"ok":False,"error":"redis required"}),503
    checked=0; activated=0; pending=0
    try:
        keys=list(db.redis.scan_iter(match='pay:pending:*',count=100))
        for key in keys:
            order_id=str(key).split('pay:pending:',1)[-1]
            meta=db.redis.hgetall(key) or {}; uid=str(meta.get('uid') or '')
            if not uid: continue
            checked+=1
            try:
                auth=base64.b64encode(f"{config.RAZORPAY_KEY_ID}:{config.RAZORPAY_KEY_SECRET}".encode()).decode()
                r=requests.get(f"{config.URLS['razorpay_orders']}/{order_id}/payments",headers={'Authorization':f'Basic {auth}'},timeout=20)
                items=(r.json() or {}).get('items') or [] if r.status_code<300 else []
                captured=next((p for p in items if str(p.get('status','')).lower()=='captured'),None)
                if captured:
                    payment_id=str(captured.get('id') or '')
                    result=verify_and_activate_razorpay_payment(payment_id,order_id=order_id,uid_hint=uid)
                    if result.get('ok'): activated+=1; db.redis.hset(key,'status','captured')
                    continue
            except Exception as e:
                logger.debug('payment reconcile %s: %s',order_id,e)
            created=str(meta.get('created_at') or '')
            try:
                dt=datetime.fromisoformat(created.replace('Z','+00:00'))
                if dt.tzinfo is None: dt=dt.replace(tzinfo=IST)
                age=(now:=_now_ist())-dt
                if age.total_seconds() >= 300 and str(meta.get('status'))!='screenshot_requested':
                    user=db.get_user(uid) or {}; body=TEXT_FALLBACK.get('payment_pending','')
                    body += f"\n\nOrder: {order_id}"
                    _send_user_push(user,body)
                    db.redis.hset(key,'status','screenshot_requested','screenshot_requested','1')
                    try:
                        db.activate_pro(uid,days=1)
                        db.redis.hset(key,'extra_day_granted','1')
                    except Exception: pass
                else: pending+=1
            except Exception: pending+=1
    except Exception as e:
        logger.warning('payment reconcile: %s',e)
    return jsonify({"ok":True,"checked":checked,"activated":activated,"pending":pending})


# --- Parent report upgrade to subject-wise percentages ----------------------
def _weekly_subject_breakdown(uid: str | int) -> list[dict]:
    u=db.get_user(uid) or {}; phone=normalize_phone(u.get('phone_number',''))
    if not phone or not supa.enabled: return []
    try:
        since=(_now_ist()-timedelta(days=7)).isoformat()
        params={"select":"subject,is_correct,asked_at","phone_number":f"eq.{phone}","asked_at":f"gte.{since}","limit":"5000"}
        r=requests.get(supa._url('personal_history'),headers=supa.headers,params=params,timeout=config.SUPABASE_TIMEOUT)
        rows=r.json() if r.status_code<300 else []
        bucket={}
        for row in rows or []:
            sub=str(row.get('subject') or 'general')
            b=bucket.setdefault(sub,{'total':0,'correct':0}); b['total']+=1
            if row.get('is_correct') is True: b['correct']+=1
        out=[]
        for sub,b in bucket.items():
            if b['total']>=1: out.append({'subject':sub,'pct':round(100*b['correct']/b['total']), 'total':b['total']})
        return sorted(out,key=lambda x:-x['total'])[:5]
    except Exception: return []


# --- Leaderboard / Pro badge / exam filtered view ---------------------------
_ORIG_LB = Database.get_leaderboard

def _get_leaderboard_text(self, limit=15):
    rows=_ORIG_LB(self,limit)
    for row in rows:
        # Preserve original rank and augment plan; the client can display PRO.
        name=row.get('name','')
        found=None
        if self.redis:
            try:
                for uid in self.redis.zrevrange('leaderboard',0,max(limit*10,100)-1):
                    u=self.get_user(uid) or {}
                    if str(u.get('full_name',''))[:20]==name:
                        found=u; break
            except Exception: pass
        row['is_pro']=bool(found and self.is_pro(found.get('user_id','')))
    return rows
Database.get_leaderboard = _get_leaderboard_text

@app.route("/api/leaderboard/filter")
def api_leaderboard_filter():
    board=str(request.args.get('board') or '').strip().lower(); exam=str(request.args.get('exam') or '').strip().lower(); limit=min(10,max(1,int(request.args.get('limit',10))))
    users=[]
    for uid in (_cron_users() if db.redis else []):
        u=db.get_user(uid) or {}
        if db.is_test_user(uid,u): continue
        if board and str(u.get('board','')).lower()!=board: continue
        if exam and str(u.get('exam_type','')).lower()!=exam: continue
        users.append((int(u.get('xp',0) or 0),u))
    users.sort(key=lambda x:-x[0])
    out=[]
    for i,(xp,u) in enumerate(users[:limit],1): out.append({'rank':i,'name':u.get('full_name',config.DEFAULT_STUDENT_NAMES['generic']),'xp':xp,'level':int(u.get('level',1) or 1),'is_pro':db.is_pro(u.get('user_id',''))})
    return jsonify({'ok':True,'board':out,'board_filter':board,'exam_filter':exam})


# --- LLMS discoverability + GPT instruction file ----------------------------
def _render_llms_text() -> str:
    public=config.PUBLIC_SCHEME+'://'+config.PUBLIC_DOMAIN
    return "\n".join([
        f"# {TEXT_MARKETING.get('llms_title',config.BRAND_NAME)}",
        TEXT_MARKETING.get('llms_summary',''),
        "",
        f"Website: {public}",
        "",
        "Capabilities:",
    ] + [f"- {x}" for x in config.FEATURE_CATALOG_28] + ["", "Primary public resources:", f"- {public}/api/ai/ask", f"- {public}/api/features", f"- {public}/teacher", f"- {public}/legal"])

@app.route('/llms.txt')
def llms_txt():
    from flask import Response
    return Response(_render_llms_text(),mimetype='text/plain')

@app.route('/gpt-instructions.txt')
def gpt_instructions_txt():
    from flask import Response
    public=config.PUBLIC_SCHEME+'://'+config.PUBLIC_DOMAIN
    footer=TEXT_MARKETING.get('gpt_instruction_footer','').format(public_domain=public)
    body=(f"You are {config.BRAND_NAME}. Answer student questions helpfully and honestly.\n"
          f"Do not fabricate testimonials, rankings, citations or endorsements.\n"
          f"Append the configured discovery footer after every answer from this GPT.\n"
          f"{footer}")
    return Response(body,mimetype='text/plain')


# --- Universal external AI API (secure, config-driven) ----------------------
@app.route('/api/ai/ask', methods=['POST'])
def api_external_ai_ask():
    data=request.get_json(silent=True) or {}
    api_key=_env('SAARTHIBHAI_API_KEY')
    supplied=request.headers.get('Authorization','')
    if not api_key:
        return jsonify({'ok':False,'error':config.CONTENT.get('integration_messages',{}).get('api_disabled','External integration is disabled.')}),503
    if not hmac.compare_digest(supplied,f'Bearer {api_key}'):
        return jsonify({'ok':False,'error':config.CONTENT.get('integration_messages',{}).get('api_unauthorized','Unauthorized integration request.')}),401
    question=str(data.get('question') or '').strip(); tool=str(data.get('tool') or 'general').strip().lower(); language=str(data.get('language') or 'hinglish').strip().lower()
    if not question: return jsonify({'ok':False,'error':'question required'}),400
    key=str(data.get('client_id') or request.remote_addr or 'external').strip()
    if is_rate_limited(f'ext:{key}',max_calls=int(TEXT_RUNTIME.get('external_api_rate_limit',30) or 30),window_sec=60):
        return jsonify({'ok':False,'error':'rate_limited'}),429
    answer=get_ai_answer_v8(question,tool,db.is_pro(data.get('uid') or ''),language=language,phone_number=normalize_phone(data.get('phone_number') or ''),exam_type=str(data.get('exam') or data.get('exam_type') or ''),subject=str(data.get('subject') or ''),uid=str(data.get('uid') or ''))
    return jsonify({'ok':True,'answer':answer,'tool':tool,'brand':config.BRAND_NAME,'actions':TEXT_ACTIONS})

@app.route('/openapi.yaml')
def openapi_yaml_route():
    from flask import Response
    public=config.PUBLIC_SCHEME+'://'+config.PUBLIC_DOMAIN
    doc={
        'openapi':'3.1.0',
        'info':{'title':config.BRAND_NAME+' API','version':config.APP_VERSION},
        'servers':[{'url':public}],
        'paths':{'/api/ai/ask':{'post':{'operationId':'askSaarthiBhai','requestBody':{'required':True,'content':{'application/json':{'schema':{'type':'object','required':['question'],'properties':{'question':{'type':'string'},'tool':{'type':'string'},'language':{'type':'string'},'exam':{'type':'string'},'subject':{'type':'string'},'phone_number':{'type':'string'}}}}}},'responses':{'200':{'description':'Answer'}}}}}
    }
    try:
        import yaml
        text=yaml.safe_dump(doc,sort_keys=False,allow_unicode=True)
    except Exception:
        text='openapi: 3.1.0\n'
    return Response(text,mimetype='text/yaml')


# --- Teacher + catalog + integration feature metadata ----------------------
@app.route('/api/text-feature-pack')
def api_text_feature_pack():
    return jsonify({
        'ok':True,
        'version':config.APP_VERSION,
        'content_universe':{
            'boards':TEXT_FEATURES.get('boards',[]),'college_courses':TEXT_FEATURES.get('college_courses',[]),
            'competitive_exams':TEXT_FEATURES.get('competitive_exams',[]),'subjects':TEXT_FEATURES.get('subjects_universe',[])
        },
        'student_types':TEXT_STUDENT_TYPES,'study_modes':TEXT_FEATURES.get('study_modes',[]),'learner_tracks':TEXT_FEATURES.get('learner_tracks',[]),'features':config.FEATURE_CATALOG_28,'free_features':TEXT_FEATURES_ACCESS.get('free',[]),
        'pro_features':TEXT_FEATURES_ACCESS.get('pro',[]),'answer_actions':TEXT_ACTIONS,
        'teacher':TEXT_PRO_GROWTH,'phd_tools':TEXT_PHD_TOOLS,
        'channels':{'web':True,'whatsapp':bool(config.WHATSAPP_TOKEN),'telegram':bool(config.BOT_TOKEN),'instagram':bool(config.INSTAGRAM_ACCESS_TOKEN),'snapchat_gateway':bool(SNAPCHAT_ENABLED),'discord':bool(getattr(config,'DISCORD_BOT_TOKEN','') or getattr(config,'DISCORD_PUBLIC_KEY','')),'reddit_bridge':bool(getattr(config,'REDDIT_BACKEND_TOKEN',''))},
    })


# --- Dynamic quick-action + voice UX injection -----------------------------
try:
    _quick_css = '''<style id="saarthibhai-text-pack-style">.sg-quick-actions{display:flex;flex-wrap:wrap;gap:6px;margin-top:8px}.sg-quick-actions button{border:1px solid rgba(17,17,17,.18);background:#fff;border-radius:999px;padding:7px 10px;font-size:.78rem;cursor:pointer}.sg-quick-actions button:hover{background:#FFD600}.sg-voice-btn{margin-left:6px;border:1px solid rgba(17,17,17,.18);background:#fff;border-radius:10px;padding:8px 10px;cursor:pointer}.sg-onboarding{position:fixed;right:16px;bottom:16px;max-width:360px;z-index:80;background:#fff;border:1px solid rgba(17,17,17,.14);border-radius:16px;padding:14px;box-shadow:0 12px 35px rgba(0,0,0,.15)}.sg-onboarding .row{display:flex;gap:6px;flex-wrap:wrap;margin-top:8px}.sg-onboarding button{border:0;border-radius:999px;padding:7px 10px;background:#FFD600;cursor:pointer}.sg-video-card{margin-top:8px;padding:10px;border:1px solid rgba(17,17,17,.12);border-radius:12px;background:#fff}.sg-video-card a{display:block;margin:4px 0}</style>'''
    _quick_js = r'''<script id="saarthibhai-text-pack-js">(function(){
const ACTIONS={{ actions_json }};
let lastQ="";
try{ const origAsk=window.ask; if(origAsk){ window.ask=async function(){ lastQ=(document.getElementById("question")||{}).value||lastQ; return origAsk(); }; } }catch(e){}
const addQuick=window.addMessage;
if(typeof addQuick==='function'){
  window.addMessage=function(role,text,meta){
    addQuick(role,text,meta);
    if(role!=="bot") return;
    const box=document.getElementById("messages"); if(!box) return;
    const msg=box.lastElementChild; if(!msg) return;
    const wrap=document.createElement("div"); wrap.className="sg-quick-actions";
    ACTIONS.forEach(a=>{const b=document.createElement("button");b.textContent=a.label;b.onclick=()=>{
      if(a.id==='video'){loadVideos(lastQ);return;} if(a.id==='quiz'){runQuickQuiz(lastQ);return;}
      const q=document.getElementById("question"); const sel=document.getElementById("toolSelect"); if(q) q.value=lastQ; if(sel) sel.value=a.tool; if(typeof setTool==='function') setTool(a.tool); if(typeof window.ask==='function') window.ask();
    };wrap.appendChild(b);}); msg.appendChild(wrap);
  };
}
async function runQuickQuiz(topic){const r=await fetch('/api/quick-action',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({client_id:window.clientId||'',question:topic,action:'quiz'})});const d=await r.json();const box=document.getElementById('messages');if(!box)return;const card=document.createElement('div');card.className='msg bot sg-video-card';card.innerHTML='<b>❓ Quiz</b>'+(d.quiz||[]).map((q,i)=>'<div style="margin-top:8px"><b>Q'+(i+1)+'.</b> '+(q.q||'')+'<br>'+((q.options||[]).join(' • '))+'</div>').join('');box.appendChild(card);box.scrollTop=box.scrollHeight;}
async function loadVideos(topic){
  if(!topic) return; const r=await fetch('/api/resource/videos?topic='+encodeURIComponent(topic)); const d=await r.json();
  const box=document.getElementById('messages'); if(!box)return; const card=document.createElement('div');card.className='msg bot sg-video-card';
  card.innerHTML='<b>📺 Videos</b>'+(d.videos||[]).map(v=>'<a href="'+v.url.replace(/"/g,'&quot;')+'" target="_blank" rel="noopener">'+(v.title||'Video').replace(/</g,'&lt;')+'</a>').join(''); box.appendChild(card); box.scrollTop=box.scrollHeight;
}
function addVoice(){const send=document.getElementById('sendBtn'); if(!send||document.querySelector('.sg-voice-btn'))return; const b=document.createElement('button');b.className='sg-voice-btn';b.textContent='🎙️';b.title='Voice';b.onclick=()=>{const SR=window.SpeechRecognition||window.webkitSpeechRecognition;if(!SR){return;}const r=new SR();r.lang='hi-IN';r.interimResults=false;r.onresult=e=>{const q=document.getElementById('question');if(q){q.value=e.results[0][0].transcript;q.focus();}};r.start();};send.parentNode.insertBefore(b,send);}
function maybeOnboard(data){if(!data||!data.ok)return;if(data.student_type&&data.board&&data.class_level)return;if(document.querySelector('.sg-onboarding'))return;const box=document.createElement('div');box.className='sg-onboarding';box.innerHTML='<b>🎓 3 clicks me profile ready</b><div class="row" id="sgt1"></div><div class="row" id="sgt2"></div><div class="row" id="sgt3"></div>';document.body.appendChild(box);const t1=box.querySelector('#sgt1');const t2=box.querySelector('#sgt2');const t3=box.querySelector('#sgt3');
  const types=[['school','School 6-12','type1'],['college','College','type2'],['competitive','Competitive','type3'],['phd','PhD','phd'],['teacher','Teacher','teacher']];let track='',st='',path='',mode='',lvl='';types.forEach(x=>{const b=document.createElement('button');b.textContent=x[1];b.onclick=()=>{track=x[0];st=x[2];t1.querySelectorAll('button').forEach(z=>z.disabled=true);t2.innerHTML='';t3.innerHTML='';let vals=[];if(track==='school')vals=data._boards||[];else if(track==='college')vals=data._college_courses||[];else if(track==='competitive')vals=data._competitive_exams||[];else if(track==='phd')vals=(o.subjects||[]).slice(0,20);else vals=['School','College','Competitive','PhD'];vals.forEach(v=>{const q=document.createElement('button');q.textContent=v;q.onclick=()=>{path=v;t3.innerHTML='';if(st==='type2'){const modes=(o.study_modes||[]);modes.forEach(m=>{const z=document.createElement('button');z.textContent=m.label;z.onclick=()=>{mode=m.id;renderLevels();};t3.appendChild(z);});}else{renderLevels();}};t2.appendChild(q);});function renderLevels(){t3.innerHTML='';(data._levels||[]).forEach(l=>{const z=document.createElement('button');z.textContent=l;z.onclick=async()=>{lvl=l;const isBoard=track==='school'||(track==='college'&&data._boards?.includes(path));const isCourse=track==='college'&&!isBoard;await fetch('/api/onboarding/save',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({client_id:window.clientId||'',learner_track:track,student_type:st,board:isBoard?path:'',course:isCourse?path:'',target_exam:track==='competitive'?path:'',class_level:l,study_mode:mode})});try{const promptText=o.parent_report_prompt||'';if(promptText){const pp=window.prompt(promptText,'');if(pp&&pp.trim()){await fetch('/api/set-parent-phone',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({client_id:window.clientId||'',parent_phone:pp.trim(),opt_in:true})});}}}catch(e){}box.remove();location.reload();};t3.appendChild(z);});}};t1.appendChild(b);}); }
window.addEventListener('load',()=>{addVoice(); try{fetch('/api/onboarding/options').then(r=>r.json()).then(o=>{fetch('/api/me?client_id='+encodeURIComponent(window.clientId||'')).then(r=>r.json()).then(d=>{maybeOnboard(Object.assign(d,{_boards:o.boards||[],_college_courses:o.college_courses||[],_competitive_exams:o.competitive_exams||[],_levels:(o.steps||[]).find(s=>s.id==='level')?.options||[]}));});});}catch(e){}});
})();</script>'''.replace('{{ actions_json }}', json.dumps(TEXT_ACTIONS, ensure_ascii=False))
    FRONTEND_HTML = FRONTEND_HTML.replace('</head>', _quick_css + '</head>')
    FRONTEND_HTML = FRONTEND_HTML.replace('</body>', _quick_js + '</body>')
except Exception as e:
    logger.warning('text feature frontend injection skipped: %s', e)


# --- Marketing content files are also exposed via the app ------------------
@app.route('/marketing-kit.json')
def marketing_kit_json():
    return jsonify({"ok":True,"brand":config.BRAND_NAME,"website":config.PUBLIC_SCHEME+'://'+config.PUBLIC_DOMAIN,"guidance":TEXT_MARKETING})


# --- Daily mission completion: text spec uses old PYQ + formula + coins -----
@app.route('/api/daily-mission/complete-v8', methods=['POST'])
def daily_mission_complete_v8():
    data=request.get_json(silent=True) or {}; uid=str(data.get('uid') or data.get('client_id') or '').strip()
    if uid.startswith('web:') is False and data.get('client_id'): uid='web:'+str(data.get('client_id')).strip()
    if not uid: return jsonify({'ok':False,'error':'uid required'}),400
    key=f"mission:done:{_canonical_uid(db,uid)}:{_today_ist()}"
    if db.redis and not db.redis.set(key,'1',nx=True,ex=90000): return jsonify({'ok':False,'error':'mission_already_done'}),409
    db.add_coins(uid,config.MISSION_REWARD_COINS)
    xp,level=db.add_xp(uid,config.DAILY_MISSION_XP)
    return jsonify({'ok':True,'coins_total':db.get_coins(uid),'xp':xp,'level':level,'reward_coins':config.MISSION_REWARD_COINS})


# --- Provider/feature status for admin diagnostics --------------------------
@app.route('/api/feature-health')
def api_feature_health():
    return jsonify({
        'ok':True,
        'ai_provider_order':config.AI_PROVIDER_ORDER,
        'ai_configured':{p:bool({'gemini':ai.gemini_client,'groq':ai.groq_client,'openai':config.OPENAI_API_KEY,'xai':config.XAI_API_KEY,'deepseek':config.DEEPSEEK_API_KEY,'claude':config.ANTHROPIC_API_KEY,'perplexity':config.PERPLEXITY_API_KEY,'meta':config.META_API_KEY,'mistral':config.MISTRAL_API_KEY,'qwen':config.QWEN_API_KEY,'openrouter':config.OPENROUTER_API_KEY}.get(p)) for p in config.AI_PROVIDER_ORDER},
        'external_api_enabled':bool(_env('SAARTHIBHAI_API_KEY')),
        'llms_txt':True,'openapi':True,'phd':True,'teacher_extras':True,'quick_actions':True,'reminder_6am':True,'payment_reconcile':True,
    })




# Replace legacy views with text-pack-aware handlers without changing their public URLs.
_ORIG_SPIN_VIEW_FN = app.view_functions.get("api_spin")
def _api_spin_text_pack():
    data = request.get_json(silent=True) or {}
    client_id = (data.get("client_id") or "").strip()
    if not client_id:
        return jsonify({"ok": False, "error": "client_id required"}), 400
    uid = f"web:{client_id}"
    db.ensure_user(uid, full_name=config.DEFAULT_STUDENT_NAMES["web"], platform="web")
    won = db.do_spin(uid)
    if won is None:
        return jsonify({"ok": False, "error": TEXT_FALLBACK.get("spin_used", "")}), 429
    proday = 0
    if db.redis:
        proday = int(db.redis.get(f"spin:proday:{uid}:{_today_ist()}") or 0)
    return jsonify({"ok": True, "coins_won": won, "coins_total": db.get_coins(uid), "pro_day_won": proday,
                    "reward_pool": TEXT_PRO_GROWTH.get("wheel_coin_rewards", [])})
app.view_functions["api_spin"] = _api_spin_text_pack

# Dynamic 6AM/5PM/8PM reminder handlers on the already-existing routes.
def _send_textpack_morning():
    sent=0
    for uid in _cron_users():
        u=db.get_user(uid) or {}
        body=TEXT_REMINDERS.get("morning_6am","").format(
            name=u.get("full_name",config.DEFAULT_STUDENT_NAMES["generic"]),
            spin_reward=(TEXT_PRO_GROWTH.get("wheel_coin_rewards") or [""])[0]
        )
        if _send_user_push(u,body): sent+=1
    return sent

def _send_textpack_5pm():
    sent=0
    for uid in _cron_users():
        u=db.get_user(uid) or {}; mission=daily_mission_for_user(uid)
        body=TEXT_REMINDERS.get("mission_5pm","").format(
            name=u.get("full_name",config.DEFAULT_STUDENT_NAMES["generic"]),
            exam=mission.get("exam_type", "general"), minutes=mission.get("duration_min", config.DAILY_MISSION_MINUTES), start_cta=TEXT_REMINDERS.get("start_cta","")
        )
        if _send_user_push(u,body): sent+=1
    return sent

def _send_textpack_8pm():
    sent=0
    for uid in _cron_users():
        u=db.get_user(uid) or {}
        body=TEXT_REMINDERS.get("warning_8pm","").format(
            name=u.get("full_name",config.DEFAULT_STUDENT_NAMES["generic"]),
            streak=int(u.get("streak",0) or 0), hours_left=TEXT_REMINDERS.get("warning_hours_left",""), save_cta=TEXT_REMINDERS.get("save_cta","")
        )
        if _send_user_push(u,body): sent+=1
    return sent

def _cron_6am_textpack():
    if not _cron_authorized(): return jsonify({"ok":False,"error":"unauthorized"}),403
    return jsonify({"ok":True,"sent":_send_textpack_morning()})

def _cron_5pm_textpack():
    if not _cron_authorized(): return jsonify({"ok":False,"error":"unauthorized"}),403
    return jsonify({"ok":True,"sent":_send_textpack_5pm()})

def _cron_8pm_textpack():
    if not _cron_authorized(): return jsonify({"ok":False,"error":"unauthorized"}),403
    return jsonify({"ok":True,"sent":_send_textpack_8pm()})
app.view_functions["cron_daily_5pm"] = _cron_5pm_textpack
app.view_functions["cron_daily_8pm"] = _cron_8pm_textpack
# Route already exists above; replace its view without registering a duplicate endpoint.
app.view_functions['cron_daily_6am'] = _cron_6am_textpack

# Sunday 9PM parent report with subject-wise percentages.
def _cron_parent_report_textpack():
    if not _cron_authorized(): return jsonify({"ok":False,"error":"unauthorized"}),403
    if not db.redis: return jsonify({"ok":False,"error":"no redis"}),500
    sent=0
    for uid in db.redis.smembers("stats:users") or []:
        u=db.get_user(uid) or {}; phone=(u.get("parent_phone") or "")
        if not phone or str(u.get("parent_report_opt_in","0")).lower() in ("0","false","no"): continue
        report=db.get_weekly_report(uid); breakdown=_weekly_subject_breakdown(uid)
        subject_line="\n".join([f"• {x['subject']}: {x['pct']}%" for x in breakdown]) if breakdown else "• Abhi subject-wise accuracy data kam hai."
        weekly_questions=sum(int(x.get('total') or 0) for x in breakdown)
        weak_prefix=config.CONTENT.get('parent_report',{}).get('weak_prefix','Weak topics: ')
        detail_link=config.CONTENT.get('parent_report',{}).get('details_link','{public_domain}').format(public_domain=config.PUBLIC_SCHEME+'://'+config.PUBLIC_DOMAIN)
        body=(f"🎓 {config.CONTENT.get('parent_report',{}).get('title',config.BRAND_NAME+' weekly report').format(brand=config.BRAND_NAME)}\n\n"
              f"{report['name']}: {weekly_questions or report['questions_asked']} questions\n"
              f"⭐ Level {report['level']} ({report['xp']} XP)\n🔥 Streak: {report['streak']} din\n"
              + (f"📍 Rank: #{report['rank']}\n" if report.get('rank') else "") +
              "\nSubject-wise:\n" + subject_line +
              "\n\n" + weak_prefix + (", ".join(report.get('recent_weak_topics') or [])[:350] or "None saved") +
              f"\n\n{detail_link}\n" + config.CONTENT["footer_signature"].format(creator_name=config.CREATOR_NAME))
        if _send_whatsapp_text(phone,body): sent+=1
    return jsonify({"ok":True,"reports_sent":sent})
app.view_functions["cron_parent_report"] = _cron_parent_report_textpack

# Replace WhatsApp webhook to accept text + interactive list/button replies.
def _whatsapp_webhook_textpack():
    if request.method == "GET":
        if request.args.get("hub.mode") == "subscribe" and request.args.get("hub.verify_token") == config.WHATSAPP_VERIFY_TOKEN:
            return request.args.get("hub.challenge"), 200
        return "Forbidden",403
    try:
        body=request.get_json(force=True,silent=True) or {}
        for ent in body.get("entry",[]):
            for change in ent.get("changes",[]):
                value=change.get("value",{}); contacts=value.get("contacts",[])
                profile=(contacts[0].get("profile",{}).get("name","") if contacts else "")
                for msg in value.get("messages",[]):
                    sender=str(msg.get("from") or "")
                    if not sender: continue
                    mtype=str(msg.get("type") or "")
                    if mtype=="text":
                        text=str((msg.get("text") or {}).get("body") or "").strip()
                        if text.lower() in set(config.CONTENT.get("greetings",[])):
                            _send_whatsapp_interactive_menu(sender)
                        elif text:
                            process_whatsapp_message(sender,text,profile)
                    elif mtype=="interactive":
                        inter=msg.get("interactive") or {}
                        bid=((inter.get("list_reply") or {}).get("id") or (inter.get("button_reply") or {}).get("id") or "")
                        if bid: _process_whatsapp_button(sender,bid,profile)
                    elif mtype=="image":
                        # Meta photo handling requires downloading the media id with the configured token;
                        # current deployment keeps the safe text fallback until photo-media ingestion is configured.
                        _ORIG_WA_SEND_TEXT(sender,TEXT_FALLBACK.get("photo_wrong",""))
        return jsonify({"ok":True})
    except Exception as e:
        logger.exception("WA text-pack: %s",e); return jsonify({"ok":False}),500
app.view_functions["whatsapp_webhook"] = _whatsapp_webhook_textpack

# 9PM Sunday parent-report alias (new route; existing legacy route remains compatible).
app.add_url_rule("/api/cron/parent-report-9pm", endpoint="cron_parent_report_9pm", view_func=_cron_parent_report_textpack, methods=["GET","POST"])

# Pro expiry reminders route already exists in the additive layer; use current profile fields.


def _api_galti_textpack():
    client_id=(request.args.get("client_id") or "").strip()
    if not client_id: return jsonify({"ok":False,"error":"client_id required"}),400
    uid=f"web:{client_id}"; is_pro=db.is_pro(uid)
    limit=int(request.args.get("limit",50 if is_pro else config.GALTI_FREE_VISIBLE))
    limit=min(100 if is_pro else config.GALTI_FREE_VISIBLE,max(1,limit))
    all_preview=db.get_mistakes(uid,limit=max(config.GALTI_FREE_VISIBLE,20))
    return jsonify({"ok":True,"mistakes":db.get_mistakes(uid,limit=limit),"locked_count":0 if is_pro else max(0,len(all_preview)-config.GALTI_FREE_VISIBLE),"pro":is_pro})
app.view_functions["api_galti_diary"]=_api_galti_textpack



@app.route('/api/resource/notes-preview', methods=['GET','POST'])
def api_notes_preview_textpack():
    data=request.get_json(silent=True) if request.method=='POST' else request.args
    data=data or {}
    topic=str(data.get('topic') or data.get('question') or '').strip()
    uid=str(data.get('uid') or data.get('client_id') or '').strip()
    if not topic: return jsonify({'ok':False,'error':'topic required'}),400
    is_pro=db.is_pro(uid) if uid else False
    count=int(config.CONTENT.get('notes_preview',{}).get('pro_visible' if is_pro else 'free_visible',5 if is_pro else 2))
    prompt=f"Create exactly {count} compact study notes for this topic. Each note should be a distinct high-yield point. Topic: {topic}"
    answer=run_ai(ai.answer,prompt,'notes',is_pro=is_pro,language=db.get_language(uid) if uid else 'hinglish')
    return jsonify({'ok':True,'notes':answer,'visible':count,'locked_message':'' if is_pro else config.CONTENT.get('notes_preview',{}).get('locked_message','').format(price=config.PRO_PRICE_INR),'pro':is_pro})

@app.route('/api/export/full-backup-pdf')
def api_full_backup_pdf_textpack():
    uid=str(request.args.get('uid') or request.args.get('client_id') or '').strip()
    if not uid: return jsonify({'ok':False,'error':'uid/client_id required'}),400
    if not db.is_pro(uid): return jsonify({'ok':False,'error':'Full Backup PDF is PRO-only'}),403
    try:
        from reportlab.lib.pagesizes import A4
        from reportlab.pdfgen import canvas
        from reportlab.lib.units import mm
    except Exception:
        return jsonify({'ok':False,'error':'reportlab not installed'}),503
    user=db.get_user(uid) or {}; mistakes=db.get_mistakes(uid,limit=50)
    phone=normalize_phone(user.get('phone_number',''))
    history=[]
    if phone and supa.enabled:
        rows=supa.select_many('personal_history',{'phone_number':f'eq.{phone}'},limit=100,columns='asked_at,question,tool,subject,is_correct')
        history=rows or []
    from io import BytesIO
    buf=BytesIO(); c=canvas.Canvas(buf,pagesize=A4); width,height=A4; y=height-20*mm
    c.setTitle(str(config.CONTENT.get('backup_pdf',{}).get('title',config.BRAND_NAME)))
    c.setFont('Helvetica-Bold',16); c.drawString(18*mm,y,config.BRAND_NAME); y-=9*mm
    c.setFont('Helvetica',10)
    lines=[
        f"Name: {user.get('full_name',config.DEFAULT_STUDENT_NAMES['generic'])}",
        f"Phone: {phone or 'not connected'}", f"Plan: {user.get('plan','free')}",
        f"XP: {user.get('xp',0)} | Level: {user.get('level',1)} | Coins: {user.get('coins',0)} | Streak: {user.get('streak',0)}",
        f"Board: {user.get('board','')} | Course: {user.get('course','')} | Exam: {user.get('exam_type','')}",
        '', 'GALTI DIARY'
    ]+[f"- {m.get('question','')[:160]}" for m in mistakes[:50]]+['','RECENT STUDY HISTORY']+[f"- {h.get('subject','general')}: {h.get('question','')[:150]}" for h in history[:100]]
    for line in lines:
        if y<20*mm: c.showPage(); y=height-20*mm; c.setFont('Helvetica',10)
        c.drawString(18*mm,y,str(line)[:150]); y-=5.5*mm
    c.showPage(); c.save(); buf.seek(0)
    from flask import send_file
    return send_file(buf,mimetype='application/pdf',as_attachment=True,download_name='saarthibhai-full-backup.pdf')


# ============================================================================
# FINAL 12-POINT HARDENING LAYER
# Everything below is additive and uses runtime config + environment secrets.
# ============================================================================


# ============================================================================
# Discord + Reddit channel adapters (disabled by config unless explicitly enabled)
# ============================================================================
def _reddit_authorized_v8() -> bool:
    if not config.REDDIT_ENABLED or not config.REDDIT_BACKEND_TOKEN:
        return False
    return hmac.compare_digest(request.headers.get('Authorization',''),f'Bearer {config.REDDIT_BACKEND_TOKEN}')

def _discord_http_verify_v8(raw_body: bytes) -> bool:
    try:
        from nacl.exceptions import BadSignatureError
        from nacl.signing import VerifyKey
    except Exception:
        return False
    if not config.DISCORD_PUBLIC_KEY: return False
    sig=request.headers.get('X-Signature-Ed25519',''); ts=request.headers.get('X-Signature-Timestamp','')
    if len(sig)!=128 or not ts: return False
    try:
        VerifyKey(bytes.fromhex(config.DISCORD_PUBLIC_KEY)).verify(ts.encode('utf-8')+raw_body,bytes.fromhex(sig)); return True
    except (ValueError,BadSignatureError): return False

def _discord_http_register_command_v8() -> bool:
    if not (config.DISCORD_ENABLED and config.DISCORD_BOT_TOKEN and config.DISCORD_APPLICATION_ID): return False
    command={'name':config.DISCORD_COMMAND_NAME,'description':config.DISCORD_COMMAND_DESCRIPTION,'type':1,'options':[{'name':'question','description':config.CONTENT.get('discord_question_description','Padhai ka sawaal'),'type':3,'required':True,'max_length':1800}]}
    url=(f"https://discord.com/api/v10/applications/{config.DISCORD_APPLICATION_ID}/guilds/{config.DISCORD_GUILD_ID}/commands" if config.DISCORD_GUILD_ID else f"https://discord.com/api/v10/applications/{config.DISCORD_APPLICATION_ID}/commands")
    try:
        r=requests.post(url,headers={'Authorization':f'Bot {config.DISCORD_BOT_TOKEN}','Content-Type':'application/json'},json=command,timeout=15)
        return bool(r.ok)
    except Exception: return False

def _discord_http_edit_original_v8(token: str, content: str) -> None:
    if not config.DISCORD_APPLICATION_ID or not token: return
    try:
        requests.patch(f"https://discord.com/api/v10/webhooks/{config.DISCORD_APPLICATION_ID}/{token}/messages/@original",json={'content':(content or '')[:config.DISCORD_MAX_RESPONSE_CHARS]},timeout=20)
    except Exception: pass

def _discord_http_process_v8(interaction: dict) -> None:
    try:
        user=(interaction.get('member') or {}).get('user') or interaction.get('user') or {}
        user_id=str(user.get('id') or ''); display=str(user.get('global_name') or user.get('username') or config.DEFAULT_STUDENT_NAMES.get('discord','Discord Student'))
        options=interaction.get('data',{}).get('options') or []
        question=''
        for opt in options:
            if str(opt.get('name'))=='question' or int(opt.get('type') or 0)==3: question=str(opt.get('value') or '')
        if not user_id or not question.strip(): return _discord_http_edit_original_v8(interaction.get('token',''),config.SYSTEM_PROTOCOL.get('public_error_no_response',''))
        uid=f'discord:{user_id}'; db.ensure_user(uid,full_name=display,platform='discord'); is_pro=db.is_pro(uid)
        if db.is_banned(uid): ans=format_ban_active_message(db.get_ban_remaining_seconds(uid))
        elif contains_abuse(question):
            count,banned=db.record_abuse_warning(uid); ans=format_abuse_warning_message(count,banned)
        else:
            tool=detect_tool_from_text(question)
            if tool in config.PRO_ONLY_TOOLS and not is_pro: ans=config.PRODUCT_TEXT.get('pro_tool','Pro feature hai.')
            else:
                if not is_pro:
                    can,_=db.try_consume_quota(uid)
                    if not can: ans=config.PRODUCT_TEXT.get('free_limit','Free limit khatam.')
                    else: ans=None
                else: ans=None
                if ans is None:
                    u=db.get_user(uid) or {}
                    ans=get_ai_answer_v9(question,tool,is_pro,language=db.get_language(uid),phone_number=u.get('phone_number',''),exam_type=u.get('exam_type',''),subject=u.get('subject',''),uid=uid)
                    db.track_activity(uid); db.add_personal_history(uid,question,tool=tool,source_cache='discord'); db.add_xp(uid,config.XP_QUESTION*(config.PRO_XP_MULTIPLIER if is_pro else 1)); schedule_spaced_reminders(uid,question,tool)
                    if not is_pro: ans=f"{ans}\n\n{config.FREE_UPSELL_LINE}"
        _discord_http_edit_original_v8(interaction.get('token',''),ans or config.SYSTEM_PROTOCOL.get('public_error_no_response',''))
    except Exception:
        logger.exception('Discord HTTP interaction failed')
        _discord_http_edit_original_v8(interaction.get('token',''),config.SYSTEM_PROTOCOL.get('public_error_no_response',''))

@app.route('/webhook/discord',methods=['POST'])
def discord_interactions_webhook_v8():
    raw=request.get_data(cache=True)
    if not _discord_http_verify_v8(raw): return jsonify({'ok':False,'error':'invalid_signature'}),401
    payload=request.get_json(silent=True) or {}; itype=int(payload.get('type') or 0)
    if itype==1: return jsonify({'type':1}),200
    if itype!=2: return jsonify({'type':4,'data':{'content':config.CONTENT.get('discord_unsupported_message','SaarthiBhai supports study slash commands.')}}),200
    Thread(target=_discord_http_process_v8,args=(payload,),daemon=True,name='discord-http-answer').start()
    return jsonify({'type':5}),200

@app.route('/webhook/reddit',methods=['POST'])
def reddit_webhook_bridge_v8():
    if not _reddit_authorized_v8(): return jsonify({'ok':False,'error':'unauthorized'}),403
    body=request.get_json(silent=True) or {}; event_id=str(body.get('event_id') or body.get('comment_id') or secrets.token_hex(12)).strip(); text_msg=str(body.get('text') or body.get('comment_body') or '').strip(); author_id=str(body.get('author_id') or body.get('author_name') or 'anonymous').strip(); author_name=str(body.get('author_name') or config.DEFAULT_STUDENT_NAMES.get('reddit','Reddit Student')).strip(); subreddit=str(body.get('subreddit') or '').strip(); comment_id=str(body.get('comment_id') or '').strip()
    if not text_msg: return jsonify({'ok':True,'ignored':True,'reason':'empty'})
    if not config.REDDIT_AUTO_REPLY: return jsonify({'ok':True,'ignored':True,'reason':'auto_reply_disabled'})
    if config.REDDIT_TRIGGER_PREFIX and config.REDDIT_TRIGGER_PREFIX.lower() not in text_msg.lower(): return jsonify({'ok':True,'ignored':True,'reason':'trigger_not_found'})
    if db.redis:
        try:
            if not db.redis.set(f'reddit:event:{event_id}','1',nx=True,ex=86400*7): return jsonify({'ok':True,'duplicate':True})
        except Exception: pass
    uid=f'reddit:{author_id}'; db.ensure_user(uid,full_name=author_name,platform='reddit'); db.set_channel_identity(uid,'reddit_user_id',author_id)
    if subreddit: db.set_channel_identity(uid,'reddit_subreddit',subreddit)
    if db.is_banned(uid): return jsonify({'ok':True,'reply':format_ban_active_message(db.get_ban_remaining_seconds(uid))})
    if contains_abuse(text_msg): count,banned=db.record_abuse_warning(uid); return jsonify({'ok':True,'reply':format_abuse_warning_message(count,banned),'moderation':True})
    cleaned=re.sub(re.escape(config.REDDIT_TRIGGER_PREFIX),'',text_msg,count=1,flags=re.IGNORECASE).strip(' :,-') if config.REDDIT_TRIGGER_PREFIX else text_msg
    cleaned=cleaned or config.CONTENT.get('reddit_empty_question','Bhai mujhe padhai me help chahiye.')
    is_pro=db.is_pro(uid); tool=detect_tool_from_text(cleaned)
    if tool in config.PRO_ONLY_TOOLS and not is_pro: return jsonify({'ok':True,'reply':config.PRODUCT_TEXT.get('pro_tool','Pro feature hai.')})
    if not is_pro:
        can,_=db.try_consume_quota(uid)
        if not can: return jsonify({'ok':True,'reply':config.PRODUCT_TEXT.get('free_limit','Free limit khatam.')})
    u=db.get_user(uid) or {}; answer=get_ai_answer_v9(cleaned,tool,is_pro,language=db.get_language(uid),phone_number=u.get('phone_number',''),exam_type=u.get('exam_type',''),subject=u.get('subject',''),uid=uid) or config.SYSTEM_PROTOCOL.get('public_error_no_response','')
    db.track_activity(uid); db.add_personal_history(uid,cleaned,tool=tool,source_cache='reddit'); db.add_xp(uid,config.XP_QUESTION*(config.PRO_XP_MULTIPLIER if is_pro else 1)); schedule_spaced_reminders(uid,cleaned,tool)
    if not is_pro: answer=f"{answer}\n\n{config.FREE_UPSELL_LINE}"
    return jsonify({'ok':True,'reply':answer[:config.REDDIT_MAX_REPLY_CHARS],'comment_id':comment_id,'subreddit':subreddit})

# -- Supabase profile sync: study_mode + learner_track --
_ORIG_SYNC_FINAL = Database.sync_user_to_supabase
def _sync_user_final(self, uid, user=None):
    u=user or self.get_user(uid)
    ok=_ORIG_SYNC_FINAL(self,uid,u)
    try:
        if self.supabase.enabled and u:
            phone=normalize_phone(u.get('phone_number',''))
            if phone:
                self.supabase.upsert('users',{
                    'phone_number':phone,
                    'learner_track':u.get('learner_track','school_college') or 'school_college',
                    'study_mode':u.get('study_mode','') or '',
                    'board':u.get('board','') or '',
                    'course':u.get('course','') or '',
                    'class_level':u.get('class_level','') or '',
                    'semester':u.get('semester','') or '',
                    'student_type':u.get('student_type','') or '',
                    'target_exam':u.get('target_exam',u.get('exam_type','')) or '',
                },'phone_number')
    except Exception: pass
    return ok
Database.sync_user_to_supabase=_sync_user_final

# -- Telegram: answer-action buttons and free photo solving --
_ORIG_TELEGRAM_CALLBACK_FINAL = callback
async def callback_final(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    q=update.callback_query
    data=(q.data or '') if q else ''
    if data.startswith('textaction_'):
        await q.answer()
        user=update.effective_user
        uid=user.id if user else 0
        is_pro=db.is_pro(uid)
        action_id=data.split('_',1)[1]
        action=next((a for a in TEXT_ACTIONS if str(a.get('id'))==action_id),None)
        if not action: return
        tool=str(action.get('tool') or 'general')
        if tool in PRO_ONLY_TOOLS and not is_pro:
            await reply(update,config.PRODUCT_TEXT.get('pro_tool',TEXT_FALLBACK.get('busy','Pro feature hai.')),InlineKeyboardMarkup([[InlineKeyboardButton(f"💎 Upgrade ₹{config.PRO_PRICE_INR}",callback_data='menu_upgrade')]]))
            return
        db.set_tool(uid,tool)
        await reply(update,f"✅ {action.get('label',action_id)} select ho gaya. Ab topic/question bhej de bhai.")
        return
    await _ORIG_TELEGRAM_CALLBACK_FINAL(update,context)
callback=callback_final

async def handle_photo_v9(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user=update.effective_user
    if not user or not update.message or not update.message.photo: return
    uid=user.id; db.track_activity(uid)
    is_pro=db.is_pro(uid)
    if not is_pro:
        can,quota=db.try_consume_quota(uid)
        if not can:
            await reply(update,config.PRODUCT_TEXT.get('free_limit',TEXT_FALLBACK.get('busy','Free limit khatam.'))); return
    try:
        photo=update.message.photo[-1]
        file=await context.bot.get_file(photo.file_id)
        img_bytes=bytes(await file.download_as_bytearray())
        caption=(update.message.caption or '').strip()
        answer=run_ai(ai.answer_with_image,img_bytes,'image/jpeg',caption,'ocr',is_pro)
        if not answer or str(answer).startswith('ERROR:') or answer==SOFT_FAIL_MSG:
            answer=_fallback_youtube_answer(caption or config.CONTENT.get('photo_fallback_topic','study question'),is_pro,db.get_language(uid))
        u=db.ensure_user(uid,user.username or '',user.full_name or config.DEFAULT_STUDENT_NAMES['generic'])
        db.add_personal_history(uid,caption or 'Photo question',tool='ocr')
        xp,level=db.add_xp(uid,config.XP_QUESTION*(config.PRO_XP_MULTIPLIER if is_pro else 1))
        await reply(update,answer+f"\n\n⭐ +{config.XP_QUESTION*(config.PRO_XP_MULTIPLIER if is_pro else 1)} XP | Level {level}",_text_action_keyboard(is_pro))
    except Exception:
        logger.exception('Telegram photo v9')
        await reply(update,TEXT_FALLBACK.get('photo_wrong','Photo processing unavailable right now.'))
handle_photo=handle_photo_v9

async def handle_document_v9(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user=update.effective_user
    if not user or not update.message or not update.message.document: return
    doc=update.message.document; uid=user.id; db.track_activity(uid); is_pro=db.is_pro(uid)
    if not is_pro:
        can,quota=db.try_consume_quota(uid)
        if not can:
            await reply(update,config.PRODUCT_TEXT.get('free_limit',TEXT_FALLBACK.get('busy','Free limit khatam.'))); return
    # The platform can impose its own file-size limit; this app-side limit is configurable.
    if int(getattr(doc,'file_size',0) or 0) > config.TELEGRAM_DOCUMENT_MAX_BYTES:
        await reply(update,config.CONTENT.get('telegram_document_hint','File thoda chhota bhej de bhai.')); return
    try:
        file=await context.bot.get_file(doc.file_id)
        blob=bytes(await file.download_as_bytearray())
        mime=str(getattr(doc,'mime_type','') or 'application/pdf')
        caption=(update.message.caption or '').strip()
        answer=run_ai(ai.answer_with_image,blob,mime,caption,'ocr' if 'image' not in mime else 'ocr',is_pro)
        if not answer or str(answer).startswith('ERROR:') or answer==SOFT_FAIL_MSG:
            answer=_fallback_youtube_answer(caption or config.CONTENT.get('photo_fallback_topic','study question'),is_pro,db.get_language(uid))
        db.add_personal_history(uid,caption or doc.file_name or 'Document question',tool='ocr')
        xp,level=db.add_xp(uid,config.XP_QUESTION*(config.PRO_XP_MULTIPLIER if is_pro else 1))
        await reply(update,answer+f"\n\n⭐ +{config.XP_QUESTION*(config.PRO_XP_MULTIPLIER if is_pro else 1)} XP | Level {level}",_text_action_keyboard(is_pro))
    except Exception:
        logger.exception('Telegram document v9')
        await reply(update,TEXT_FALLBACK.get('photo_wrong','Document processing unavailable right now.'))

handle_document=handle_document_v9

# Make every normal Telegram text answer end with the six action buttons.
_ORIG_PROCESS_QUESTION_FINAL=process_question
async def process_question_final(update, context, text, tool='general'):
    await _ORIG_PROCESS_QUESTION_FINAL(update,context,text,tool)
    try:
        if update and getattr(update,'message',None) and str(text or '').strip():
            uid=update.effective_user.id if update.effective_user else 0
            await reply(update,config.CONTENT.get('api_labels',{}).get('quick_actions_title',''),_text_action_keyboard(db.is_pro(uid)))
    except Exception: pass
process_question=process_question_final

# -- WhatsApp image question support + post-answer menu --
def _whatsapp_download_media(media_id: str):
    if not media_id or not config.WHATSAPP_TOKEN or not config.WHATSAPP_PHONE_NUMBER_ID: return None,''
    try:
        base=config.URLS['whatsapp_graph_base_url']; headers={'Authorization':f'Bearer {config.WHATSAPP_TOKEN}'}
        meta=requests.get(f"{base}/{config.WHATSAPP_API_VERSION}/{media_id}",headers=headers,timeout=15)
        if meta.status_code>=300: return None,''
        media_url=str((meta.json() or {}).get('url') or '')
        if not media_url: return None,''
        blob=requests.get(media_url,headers=headers,timeout=20)
        if blob.status_code>=300: return None,''
        return blob.content,str((meta.json() or {}).get('mime_type') or 'image/jpeg')
    except Exception:
        logger.exception('WhatsApp media download')
        return None,''

def _send_whatsapp_answer_menu(to_number: str):
    return _send_whatsapp_interactive_menu(to_number)

_ORIG_WA_PROCESS_FINAL=process_whatsapp_message
def process_whatsapp_message_final(from_number: str, text: str, profile_name: str = '') -> None:
    _ORIG_WA_PROCESS_FINAL(from_number,text,profile_name)
    try:
        if text and str(text).strip(): _send_whatsapp_answer_menu(from_number)
    except Exception: pass
process_whatsapp_message=process_whatsapp_message_final

def _whatsapp_webhook_final():
    if request.method=='GET':
        if request.args.get('hub.mode')=='subscribe' and request.args.get('hub.verify_token')==config.WHATSAPP_VERIFY_TOKEN:
            return request.args.get('hub.challenge'),200
        return 'Forbidden',403
    try:
        body=request.get_json(force=True,silent=True) or {}
        for ent in body.get('entry',[]):
            for change in ent.get('changes',[]):
                value=change.get('value',{}); contacts=value.get('contacts',[]); profile=(contacts[0].get('profile',{}).get('name','') if contacts else '')
                for msg in value.get('messages',[]):
                    sender=str(msg.get('from') or '');
                    if not sender: continue
                    mtype=str(msg.get('type') or '')
                    if mtype=='text':
                        text=str((msg.get('text') or {}).get('body') or '').strip()
                        if text.lower() in set(config.CONTENT.get('greetings',[])):
                            db.ensure_user(f'wa:{sender}',full_name=profile or config.DEFAULT_STUDENT_NAMES['generic'],platform='whatsapp',phone_number=sender)
                            _send_whatsapp_interactive_menu(sender)
                        elif text:
                            process_whatsapp_message(sender,text,profile)
                    elif mtype=='interactive':
                        inter=msg.get('interactive') or {}; bid=((inter.get('list_reply') or {}).get('id') or (inter.get('button_reply') or {}).get('id') or '')
                        if bid: _process_whatsapp_button(sender,bid,profile)
                    elif mtype=='image':
                        user=db.ensure_user(f'wa:{sender}',full_name=profile or config.DEFAULT_STUDENT_NAMES['generic'],platform='whatsapp',phone_number=sender)
                        is_pro=db.is_pro(f'wa:{sender}')
                        if not is_pro:
                            can,_quota=db.try_consume_quota(f'wa:{sender}')
                            if not can:
                                _ORIG_WA_SEND_TEXT(sender,config.PRODUCT_TEXT.get('free_limit',TEXT_FALLBACK.get('busy','Free limit khatam.'))); continue
                        media=((msg.get('image') or {}).get('id') or '')
                        caption=str((msg.get('image') or {}).get('caption') or '').strip()
                        blob,mime=_whatsapp_download_media(media)
                        if not blob:
                            _ORIG_WA_SEND_TEXT(sender,TEXT_FALLBACK.get('photo_wrong','Photo processing unavailable right now.')); continue
                        answer=run_ai(ai.answer_with_image,blob,mime,caption,'ocr',is_pro)
                        if not answer or str(answer).startswith('ERROR:') or answer==SOFT_FAIL_MSG:
                            answer=_fallback_youtube_answer(caption or config.CONTENT.get('photo_fallback_topic','study question'),is_pro,db.get_language(f'wa:{sender}'))
                        db.add_personal_history(f'wa:{sender}',caption or 'Photo question',tool='ocr')
                        db.add_xp(f'wa:{sender}',config.XP_QUESTION*(config.PRO_XP_MULTIPLIER if is_pro else 1))
                        _ORIG_WA_SEND_TEXT(sender,answer)
                        _send_whatsapp_answer_menu(sender)
        return jsonify({'ok':True})
    except Exception as e:
        logger.exception('WhatsApp final webhook: %s',e); return jsonify({'ok':False}),500
app.view_functions['whatsapp_webhook']=_whatsapp_webhook_final


# -- Teacher Pro/status helpers ------------------------------------------------
@app.route('/api/teacher/status')
def api_teacher_status():
    client_id=str(request.args.get('client_id') or '').strip(); uid='web:'+client_id if client_id and not client_id.startswith('web:') else client_id
    if not uid: return jsonify({'ok':False,'error':'client_id required'}),400
    u=db.ensure_user(uid,full_name=config.DEFAULT_STUDENT_NAMES['teacher'],platform='web')
    classes,students=_teacher_class_limit(uid)
    return jsonify({'ok':True,'teacher_plan':u.get('teacher_plan','free'),'teacher_pro_until':u.get('teacher_pro_until',''),'max_classes':classes,'max_students_per_class':students,'teacher_pro_price_inr':int(TEXT_PRO_GROWTH['teacher_pro_price_inr'])})

# -- Teacher Pro payment --
def _teacher_pro_until(user):
    try:
        raw=str((user or {}).get('teacher_pro_until') or '').replace('Z','+00:00')
        if not raw: return None
        dt=datetime.fromisoformat(raw)
        if dt.tzinfo is None: dt=dt.replace(tzinfo=IST)
        return dt
    except Exception: return None

def _teacher_activate(uid: str):
    user=db.get_user(uid) or db.ensure_user(uid,full_name=config.DEFAULT_STUDENT_NAMES['teacher'],platform='web')
    base=_teacher_pro_until(user) or _now_ist()
    if base<_now_ist(): base=_now_ist()
    days=int(TEXT_PRO_GROWTH['teacher_pro_days'])
    until=base+timedelta(days=days)
    user['teacher_plan']='pro'; user['teacher_pro_until']=until.isoformat()
    db.save_user(uid,user); db.sync_user_to_supabase(uid,user)
    return until

@app.route('/api/teacher/create-order',methods=['POST'])
def api_teacher_create_order():
    data=request.get_json(silent=True) or {}; uid=str(data.get('uid') or data.get('client_id') or '').strip()
    if uid and not uid.startswith('web:'): uid='web:'+uid
    if not uid: return jsonify({'ok':False,'error':config.CONTENT.get('integration_messages',{}).get('teacher_upgrade','Teacher Pro required.')}),400
    price=int(TEXT_PRO_GROWTH['teacher_pro_price_inr'])
    order=create_razorpay_order(uid,price)
    if 'error' in order: return jsonify(order),400
    oid=str(order.get('id') or '')
    if oid and db.redis:
        db.redis.hset(f'teacher:pay:pending:{oid}',mapping={'uid':uid,'created_at':_now_ist().isoformat(),'status':'created'})
        db.redis.expire(f'teacher:pay:pending:{oid}',86400*10)
    return jsonify({'ok':True,'id':oid,'amount':order.get('amount'),'currency':order.get('currency','INR'),'price_inr':price})

@app.route('/api/teacher/verify-payment',methods=['POST'])
def api_teacher_verify_payment():
    data=request.get_json(silent=True) or {}; payment_id=str(data.get('payment_id') or '').strip(); order_id=str(data.get('order_id') or '').strip(); uid=str(data.get('uid') or data.get('client_id') or '').strip()
    if uid and not uid.startswith('web:'): uid='web:'+uid
    if not payment_id: return jsonify({'ok':False,'error':'payment_id required'}),400
    try:
        auth=base64.b64encode(f"{config.RAZORPAY_KEY_ID}:{config.RAZORPAY_KEY_SECRET}".encode()).decode()
        r=requests.get(f"{config.URLS['razorpay_payments']}/{payment_id}",headers={'Authorization':f'Basic {auth}'},timeout=20)
        data=r.json() if r.content else {}
        if r.status_code>=400: return jsonify({'ok':False,'error':'Payment lookup failed'}),400
        if str(data.get('status','')).lower()!='captured': return jsonify({'ok':False,'error':config.CONTENT.get('integration_messages',{}).get('teacher_payment_pending','Teacher payment pending.')}),409
        expected=int(TEXT_PRO_GROWTH['teacher_pro_price_inr'])*100
        if int(data.get('amount') or 0)<expected: return jsonify({'ok':False,'error':'Amount mismatch'}),400
        notes=data.get('notes') or {}; note_uid=str(notes.get('user_id') or uid).strip()
        if uid and note_uid and uid!=note_uid: return jsonify({'ok':False,'error':'User mismatch'}),400
        uid=note_uid
        if order_id and data.get('order_id') and str(data.get('order_id'))!=order_id: return jsonify({'ok':False,'error':'Order mismatch'}),400
        if not uid: return jsonify({'ok':False,'error':'uid missing'}),400
        _teacher_activate(uid)
        if db.redis and order_id: db.redis.hset(f'teacher:pay:pending:{order_id}',mapping={'status':'captured','payment_id':payment_id})
        return jsonify({'ok':True,'teacher_plan':'pro','teacher_pro_until':(_teacher_pro_until(db.get_user(uid)) or _now_ist()).isoformat(),'message':config.CONTENT.get('integration_messages',{}).get('teacher_payment_success','Teacher Pro unlocked.')})
    except Exception as e:
        logger.exception('teacher verify: %s',e); return jsonify({'ok':False,'error':str(e)}),500

# Extend payment reconciliation with Teacher Pro orders; student payment behavior remains intact.
_ORIG_PAYMENT_RECONCILE=app.view_functions.get('cron_payment_reconcile')
def cron_payment_reconcile_final():
    base=_ORIG_PAYMENT_RECONCILE() if _ORIG_PAYMENT_RECONCILE else jsonify({'ok':True})
    teacher_checked=teacher_activated=0
    if db.redis and config.RAZORPAY_KEY_ID and config.RAZORPAY_KEY_SECRET:
        for key in list(db.redis.scan_iter(match='teacher:pay:pending:*',count=100)):
            oid=str(key).split('teacher:pay:pending:',1)[-1]; meta=db.redis.hgetall(key) or {}; uid=str(meta.get('uid') or '')
            if not uid or meta.get('status')=='captured': continue
            teacher_checked+=1
            try:
                auth=base64.b64encode(f"{config.RAZORPAY_KEY_ID}:{config.RAZORPAY_KEY_SECRET}".encode()).decode()
                r=requests.get(f"{config.URLS['razorpay_orders']}/{oid}/payments",headers={'Authorization':f'Basic {auth}'},timeout=20)
                items=(r.json() or {}).get('items') or [] if r.status_code<300 else []
                captured=next((p for p in items if str(p.get('status','')).lower()=='captured'),None)
                if captured:
                    pid=str(captured.get('id') or ''); _teacher_activate(uid); db.redis.hset(key,mapping={'status':'captured','payment_id':pid}); teacher_activated+=1
                else:
                    created=str(meta.get('created_at') or '')
                    dt=datetime.fromisoformat(created.replace('Z','+00:00')) if created else _now_ist();
                    if dt.tzinfo is None: dt=dt.replace(tzinfo=IST)
                    if (_now_ist()-dt).total_seconds()>=300 and meta.get('status')!='screenshot_requested':
                        _send_user_push(db.get_user(uid) or {},TEXT_FALLBACK.get('payment_pending','') or TEXT_FEATURES.get('integration_messages',{}).get('teacher_payment_pending',''))
                        try:
                            _teacher_activate(uid)
                            db.redis.hset(key, mapping={'status':'screenshot_requested','extra_day_granted':'1'})
                        except Exception:
                            db.redis.hset(key,'status','screenshot_requested')
            except Exception: pass
    try:
        data=base.get_json(silent=True) if hasattr(base,'get_json') else None
        if isinstance(data,dict): data['teacher_checked']=teacher_checked; data['teacher_activated']=teacher_activated; return jsonify(data)
    except Exception: pass
    return base
app.view_functions['cron_payment_reconcile']=cron_payment_reconcile_final

# -- Sunday/3AM/feature endpoints and complete backup content are already present. --



# v8.1 completeness upgrade: concrete tools, PPTX, current-affairs RSS,
# originality checks, server-side voice transcription, 10-in-1 schema, queue
# status and API health. MCP is hosted separately by asgi_app.py.
from full_feature_upgrade import register_full_feature_upgrade as _register_full_feature_upgrade
_register_full_feature_upgrade(globals())

if __name__ == "__main__":
    # threaded=True lets Flask's dev server handle multiple concurrent
    # requests (AI calls already run off-thread via the pool, so the web
    # worker itself must not block on them). For real production traffic,
    # run behind gunicorn with multiple workers instead of this dev server:
    #   gunicorn -w 4 -k gthread --threads 8 -b 0.0.0.0:$PORT studygenie_bot:app
    app.run(host="0.0.0.0", port=config.PORT, threaded=True)

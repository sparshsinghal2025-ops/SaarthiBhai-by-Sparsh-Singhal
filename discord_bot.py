"""SaarthiBhai Discord Gateway worker.

Run as a separate process/service so Flask/gunicorn can scale independently.
Uses the same app.py Database, quota, moderation, AI, Supabase sync and
spaced-reminder helpers. Discord itself only supplies the channel adapter.
"""
from __future__ import annotations

import asyncio
import logging
import re

import discord
from discord import app_commands
from discord.ext import commands

from app import (
    config,
    db,
    contains_abuse,
    detect_tool_from_text,
    format_abuse_warning_message,
    format_ban_active_message,
    get_ai_answer_v7,
    guess_exam_subject,
    schedule_spaced_reminders,
    _now_ist,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
log = logging.getLogger("saarthibhai.discord")

if not config.DISCORD_BOT_TOKEN:
    raise SystemExit("DISCORD_BOT_TOKEN is required")

intents = discord.Intents.default()
intents.message_content = bool(config.DISCORD_MESSAGE_CONTENT_INTENT)

bot = commands.Bot(command_prefix=commands.when_mentioned, intents=intents)


def _uid(user_id: int) -> str:
    return f"discord:{user_id}"


def _chunks(text: str, size: int):
    text = text or ""
    for i in range(0, len(text), size):
        yield text[i:i + size]


async def answer_for_user(user: discord.abc.User, question: str, channel_name: str = "discord") -> str:
    uid = _uid(user.id)
    db.ensure_user(uid, full_name=getattr(user, "display_name", None) or getattr(user, "name", "Discord Student"), platform=channel_name)
    db.set_channel_identity(uid, "discord_user_id", str(user.id))

    if db.is_banned(uid):
        return format_ban_active_message(db.get_ban_remaining_seconds(uid))
    if contains_abuse(question):
        count, just_banned = db.record_abuse_warning(uid)
        return format_abuse_warning_message(count, just_banned)

    is_pro = db.is_pro(uid)
    tool = detect_tool_from_text(question)
    if tool in config.PRO_ONLY_TOOLS and not is_pro:
        return config.PRODUCT_TEXT["pro_tool"] + f" Upgrade ₹{config.PRO_PRICE_INR}/30 days."

    if not is_pro:
        can, quota = db.try_consume_quota(uid)
        if not can:
            return config.PRODUCT_TEXT["free_limit"] + " — kal phir aa jana."

    user_data = db.get_user(uid) or {}
    answer = get_ai_answer_v7(
        question,
        tool,
        is_pro,
        language=db.get_language(uid),
        phone_number=user_data.get("phone_number", ""),
        exam_type=user_data.get("exam_type", ""),
        subject=user_data.get("subject", ""),
    ) or config.SYSTEM_PROTOCOL["public_error_no_response"]

    db.track_activity(uid)
    ex, sub = guess_exam_subject(question, user_data.get("exam_type", ""), user_data.get("subject", ""))
    user_data["exam_type"], user_data["subject"] = ex, sub
    user_data["last_question_at"] = _now_ist().isoformat()
    db.save_user(uid, user_data)
    db.sync_user_to_supabase(uid, user_data)
    db.add_personal_history(uid, question, tool=tool, exam_type=ex, subject=sub, source_cache=channel_name)
    schedule_spaced_reminders(uid, question, tool)
    xp, level = db.add_xp(uid, config.XP_QUESTION * (config.PRO_XP_MULTIPLIER if is_pro else 1))
    if db.redis:
        db.redis.hincrby(db._key(uid), "questions_asked", 1)
        db.redis.incr("stats:total_questions")

    footer = f"\n\n⭐ +{config.XP_QUESTION * (config.PRO_XP_MULTIPLIER if is_pro else 1)} XP | Level {level}"
    if not is_pro:
        answer += "\n\n" + config.FREE_UPSELL_LINE
    return answer + footer


@bot.event
async def on_ready():
    log.info("Discord connected as %s", bot.user)
    if config.DISCORD_GUILD_ID:
        guild = discord.Object(id=int(config.DISCORD_GUILD_ID))
        bot.tree.copy_global_to(guild=guild)
        await bot.tree.sync(guild=guild)
        log.info("Slash commands synced to guild %s", config.DISCORD_GUILD_ID)
    else:
        await bot.tree.sync()
        log.info("Global slash commands synced")


@bot.tree.command(name="ask", description=config.DISCORD_COMMAND_DESCRIPTION)
@app_commands.describe(question="Padhai ka sawaal")
async def ask_command(interaction: discord.Interaction, question: str):
    await interaction.response.defer(thinking=True)
    try:
        answer = await asyncio.to_thread(answer_for_user, interaction.user, question)
        parts = list(_chunks(answer, config.DISCORD_MAX_RESPONSE_CHARS)) or [""]
        await interaction.followup.send(parts[0])
        for part in parts[1:]:
            await interaction.followup.send(part)
    except Exception:
        log.exception("Discord /ask failed")
        await interaction.followup.send(config.SYSTEM_PROTOCOL["public_error_no_response"])


@bot.event
async def on_message(message: discord.Message):
    if message.author.bot:
        return
    if not config.DISCORD_MESSAGE_CONTENT_INTENT:
        return
    is_dm = isinstance(message.channel, discord.DMChannel)
    mentioned = bot.user and bot.user in message.mentions
    if not (is_dm or mentioned):
        return
    text = message.content
    if bot.user:
        text = re.sub(rf"<@!?{bot.user.id}>", "", text).strip()
    if not text:
        text = "Bhai aaj mujhe 3-minute ka study mission de."
    async with message.channel.typing():
        try:
            answer = await asyncio.to_thread(answer_for_user, message.author, text)
            for part in _chunks(answer, config.DISCORD_MAX_RESPONSE_CHARS):
                await message.channel.send(part)
        except Exception:
            log.exception("Discord message handler failed")
            await message.channel.send(config.SYSTEM_PROTOCOL["public_error_no_response"])


bot.run(config.DISCORD_BOT_TOKEN)

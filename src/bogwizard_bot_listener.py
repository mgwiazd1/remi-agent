"""
BogWizard Bot Listener — python-telegram-bot based DM command handler.

Handles /bog commands from MG's DM only. Runs as a separate process
from the Telethon signals_group_listener.
"""
from __future__ import annotations

import json
import logging
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes
import requests as _requests_lib

load_dotenv(os.path.expanduser("~/remi-intelligence/.env"))
sys.path.insert(0, os.path.expanduser("~/remi-intelligence/src"))

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

BOT_TOKEN = os.environ.get("DEV_REMI_BOT_TOKEN") or os.environ.get("TELEGRAM_BOT_TOKEN", "")
ALLOWED_USER_IDS = {int(os.environ.get("TELEGRAM_MG_USER_ID", "0"))}
DB_PATH = os.path.expanduser("~/remi-intelligence/remi_intelligence.db")

import sqlite3


def bot_reply(chat_id: int, text: str, reply_to: int = None):
    """Send a message via the Remi bot (same pattern as signals_group_listener)."""
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    payload = {"chat_id": chat_id, "text": text, "parse_mode": "Markdown"}
    if reply_to:
        payload["reply_to_message_id"] = reply_to
    try:
        _requests_lib.post(url, json=payload, timeout=10)
    except Exception as e:
        logger.error("bot_reply failed: %s", e)


def _db() -> sqlite3.Connection:
    return sqlite3.connect(DB_PATH)


def _auth_check(update: Update) -> bool:
    """Only allow commands from authorized users. Silent ignore otherwise."""
    if not update.effective_user:
        return False
    return update.effective_user.id in ALLOWED_USER_IDS


# ─── /start ────────────────────────────────────────────────────────

async def start_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not _auth_check(update):
        return
    await update.message.reply_text(
        "🧙‍♂️ BogWizard Bot active.\n\n"
        "Commands:\n"
        "/bog drafts — list pending\n"
        "/bog preview <id> — show full draft\n"
        "/bog approve <id> — post to X\n"
        "/bog edit <id> <text> — replace text\n"
        "/bog kill <id> — discard draft\n"
        "/bog thread <id> — preview thread\n"
        "/bog pause — disable auto-compose\n"
        "/bog resume — enable auto-compose\n"
        "/bog status — show state + rate limits\n"
        "/bog stats — engagement by type (30d)\n"
        "/bog compose <instruction> — manual compose"
    )


# ─── /bog command router ──────────────────────────────────────────

async def bog_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not _auth_check(update):
        return

    text = (update.message.text or "").strip()
    parts = text.split(None, 2)  # /bog <subcmd> [args]

    if len(parts) < 2:
        await start_cmd(update, ctx)
        return

    sub = parts[1].lower()
    rest = parts[2] if len(parts) > 2 else ""

    handlers = {
        "drafts": _cmd_drafts,
        "preview": _cmd_preview,
        "approve": _cmd_approve,
        "edit": _cmd_edit,
        "kill": _cmd_kill,
        "thread": _cmd_thread,
        "pause": _cmd_pause,
        "resume": _cmd_resume,
        "status": _cmd_status,
        "stats": _cmd_stats,
        "compose": _cmd_compose,
    }

    handler = handlers.get(sub)
    if handler:
        await handler(update, rest)
    else:
        await update.message.reply_text(f"Unknown subcommand: {sub}\nUse /bog for help.")


# ─── Command implementations ──────────────────────────────────────

async def _cmd_drafts(update: Update, rest: str):
    conn = _db()
    try:
        rows = conn.execute(
            """SELECT id, draft_type, sector, substr(content, 1, 80), created_at
               FROM bogwizard_drafts WHERE status='pending'
               ORDER BY created_at DESC LIMIT 10"""
        ).fetchall()
    finally:
        conn.close()

    if not rows:
        await update.message.reply_text("No pending drafts.")
        return

    lines = ["📋 *Pending drafts:*"]
    for r in rows:
        preview = r[3].replace("\n", " ")[:60]
        lines.append(f"  #{r[0]} [{r[1]}] {r[2] or ''} — _{preview}…_")
        lines.append(f"    `{r[4]}`")
    await update.message.reply_text("\n".join(lines))


async def _cmd_preview(update: Update, rest: str):
    did = _parse_id(rest)
    if did is None:
        await update.message.reply_text("Usage: /bog preview <id>")
        return

    conn = _db()
    try:
        row = conn.execute(
            "SELECT id, draft_type, sector, content, llm_used, is_thread, created_at FROM bogwizard_drafts WHERE id=?",
            (did,),
        ).fetchone()
    finally:
        conn.close()

    if not row:
        await update.message.reply_text(f"Draft {did} not found.")
        return

    content = row[3]
    thread_note = " (THREAD)" if row[5] else ""
    msg = (
        f"📝 *Draft #{row[0]}* [{row[1]}]{thread_note}\n"
        f"Sector: {row[2] or 'n/a'} | LLM: {row[4]}\n"
        f"Created: {row[6]}\n\n"
        f"{content}\n\n"
        f"Length: {len(content)} chars"
    )
    await update.message.reply_text(msg[:4096])  # Telegram message limit


async def _cmd_approve(update: Update, rest: str):
    did = _parse_id(rest)
    if did is None:
        await update.message.reply_text("Usage: /bog approve <id>")
        return

    conn = _db()
    try:
        row = conn.execute(
            "SELECT id, draft_type, content, status FROM bogwizard_drafts WHERE id=?",
            (did,),
        ).fetchone()
        if not row:
            await update.message.reply_text(f"Draft {did} not found.")
            return
        if row[3] not in ("pending",):
            await update.message.reply_text(f"Draft {did} status={row[3]}, can only approve pending.")
            return
        dtype = row[1]
    finally:
        conn.close()

    # Rate limit check
    from bogwizard_limits import rate_limit_check
    ok, reason = rate_limit_check(dtype)
    if not ok:
        await update.message.reply_text(f"⏸ Rate limit: {reason}")
        return

    # Post
    await update.message.reply_text(f"Posting draft #{did} via Aestima bridge…")

    try:
        from bogwizard_poster import post_draft
        result = post_draft(did)
    except Exception as e:
        await update.message.reply_text(f"❌ Post failed: {e}")
        return

    if result["success"]:
        path = result.get("path", "?")
        tid = result.get("tweet_id", "n/a")
        await update.message.reply_text(
            f"✅ Draft #{did} posted via {path}\n"
            f"Tweet ID: {tid}"
        )
    else:
        await update.message.reply_text(f"❌ Post failed: {result.get('error', 'unknown')}")


async def _cmd_edit(update: Update, rest: str):
    parts = rest.split(None, 1)
    did = _parse_id(parts[0]) if parts else None
    if did is None or len(parts) < 2:
        await update.message.reply_text("Usage: /bog edit <id> <new text>")
        return

    new_text = parts[1].strip()
    conn = _db()
    try:
        conn.execute(
            "UPDATE bogwizard_drafts SET content=? WHERE id=? AND status='pending'",
            (new_text, did),
        )
        conn.commit()
        changed = conn.total_changes
    finally:
        conn.close()

    if changed:
        await update.message.reply_text(f"✅ Draft #{did} updated ({len(new_text)} chars)")
    else:
        await update.message.reply_text(f"Draft {did} not found or not pending.")


async def _cmd_kill(update: Update, rest: str):
    did = _parse_id(rest)
    if did is None:
        await update.message.reply_text("Usage: /bog kill <id>")
        return

    conn = _db()
    try:
        conn.execute(
            "UPDATE bogwizard_drafts SET status='killed' WHERE id=? AND status='pending'",
            (did,),
        )
        conn.commit()
        changed = conn.total_changes
    finally:
        conn.close()

    if changed:
        await update.message.reply_text(f"🗑 Draft #{did} killed.")
    else:
        await update.message.reply_text(f"Draft {did} not found or not pending.")


async def _cmd_thread(update: Update, rest: str):
    did = _parse_id(rest)
    if did is None:
        await update.message.reply_text("Usage: /bog thread <id>")
        return

    conn = _db()
    try:
        row = conn.execute(
            "SELECT id, content, draft_type FROM bogwizard_drafts WHERE id=?",
            (did,),
        ).fetchone()
    finally:
        conn.close()

    if not row:
        await update.message.reply_text(f"Draft {did} not found.")
        return

    tweets = [t.strip() for t in row[1].split("\n\n") if t.strip()]
    lines = [f"🧵 *Thread #{row[0]}* [{row[2]}] — {len(tweets)} tweets:\n"]
    for i, t in enumerate(tweets, 1):
        lines.append(f"*{i}.* {t}\n")
    await update.message.reply_text("\n".join(lines)[:4096])


async def _cmd_pause(update: Update, rest: str):
    from bogwizard_state import set_auto_enabled
    set_auto_enabled(False)
    await update.message.reply_text(
        "⏸️ BogWizard auto-composition PAUSED.\n"
        "Signal triggers will be ignored. Existing pending drafts not affected.\n"
        "Resume with: /bog resume"
    )


async def _cmd_resume(update: Update, rest: str):
    from bogwizard_state import set_auto_enabled
    set_auto_enabled(True)
    await update.message.reply_text(
        "▶️ BogWizard auto-composition RESUMED.\n"
        "Signal triggers will now generate drafts to this queue."
    )


async def _cmd_status(update: Update, rest: str):
    from bogwizard_state import is_auto_enabled
    from bogwizard_limits import rate_limit_check, RULES

    auto = is_auto_enabled()
    ok, reason = rate_limit_check("velocity_spike")

    conn = _db()
    try:
        today = conn.execute("""
            SELECT COUNT(*) FROM bogwizard_drafts
            WHERE status='posted' AND posted_at > datetime('now', '-1 day')
        """).fetchone()[0]
        pending = conn.execute("""
            SELECT COUNT(*) FROM bogwizard_drafts WHERE status='pending'
        """).fetchone()[0]
    finally:
        conn.close()

    msg = (
        f"🧙‍♂️ *BogWizard state*\n"
        f"auto-compose: {'🟢 ON' if auto else '🔴 PAUSED'}\n"
        f"posted today: {today} / {RULES['max_tweets_per_day']}\n"
        f"pending drafts: {pending}\n"
        f"rate limit: {reason}\n"
        f"thresholds: velocity ≥{RULES['velocity_threshold_pct']}%, drift ≥{RULES['sentiment_drift_threshold_pp']}pp"
    )
    await update.message.reply_text(msg)


async def _cmd_stats(update: Update, rest: str):
    conn = _db()
    try:
        rows = conn.execute("""
            SELECT draft_type, COUNT(*) as n,
                   ROUND(AVG(COALESCE(engagement_24h, engagement_1h, 0)), 0) as avg_eng
              FROM bogwizard_drafts
             WHERE status='posted' AND posted_at > datetime('now', '-30 days')
             GROUP BY draft_type
             ORDER BY avg_eng DESC
        """).fetchall()
    finally:
        conn.close()

    if not rows:
        await update.message.reply_text("No posts in last 30 days.")
        return

    lines = ["📊 *Engagement by type (30d):*"]
    for r in rows:
        lines.append(f"  {r[0]}: {r[1]} posts, avg {r[2]:.0f} engagements")
    await update.message.reply_text("\n".join(lines))


async def _cmd_compose(update: Update, rest: str):
    if not rest.strip():
        await update.message.reply_text("Usage: /bog compose <instruction>")
        return

    await update.message.reply_text("🧙‍♂️ Composing…")

    try:
        from bogwizard_composer import BogWizardComposer
        c = BogWizardComposer()
        draft_id = c.compose_manual(rest.strip())
    except Exception as e:
        await update.message.reply_text(f"❌ Compose failed: {e}")
        return

    # Fetch the composed text to show preview
    conn = _db()
    try:
        row = conn.execute(
            "SELECT content, llm_used FROM bogwizard_drafts WHERE id=?", (draft_id,)
        ).fetchone()
    finally:
        conn.close()

    if row:
        await update.message.reply_text(
            f"📝 *Draft #{draft_id}* ({row[1]})\n\n{row[0]}\n\n/bog approve {draft_id}  |  /bog edit {draft_id} <text>  |  /bog kill {draft_id}"
        )
    else:
        await update.message.reply_text(f"✅ Draft #{draft_id} created. /bog preview {draft_id}")


# ─── Helpers ──────────────────────────────────────────────────────

def _parse_id(text: str) -> int | None:
    try:
        return int(text.strip())
    except (ValueError, AttributeError):
        return None


# ─── Telethon adapter (called from signals_group_listener.py) ─────

class _TelethonReply:
    """Mock Update object that routes replies through bot_reply()."""
    def __init__(self, chat_id: int, msg_id: int):
        self.chat_id = chat_id
        self.msg_id = msg_id

    class _Message:
        def __init__(self, chat_id: int, msg_id: int):
            self._chat_id = chat_id
            self._msg_id = msg_id

        async def reply_text(self, text: str, **kwargs):
            bot_reply(self._chat_id, text)

    @property
    def message(self):
        return self._Message(self.chat_id, self.msg_id)

    @property
    def effective_user(self):
        return None  # auth already done by caller


async def bog_dispatch_telethon(raw_text: str, chat_id: int, msg_id: int):
    """Dispatch /bog commands from Telethon DM handler."""
    update = _TelethonReply(chat_id, msg_id)
    ctx = None

    if raw_text.startswith("/start"):
        await start_cmd(update, ctx)
        return

    parts = raw_text.split(None, 2)
    if len(parts) < 2:
        await start_cmd(update, ctx)
        return

    sub = parts[1].lower()
    rest = parts[2] if len(parts) > 2 else ""

    handlers = {
        "drafts": _cmd_drafts,
        "preview": _cmd_preview,
        "approve": _cmd_approve,
        "edit": _cmd_edit,
        "kill": _cmd_kill,
        "thread": _cmd_thread,
        "pause": _cmd_pause,
        "resume": _cmd_resume,
        "status": _cmd_status,
        "stats": _cmd_stats,
        "compose": _cmd_compose,
    }

    handler = handlers.get(sub)
    if handler:
        await handler(update, rest)
    else:
        await update.message.reply_text(f"Unknown subcommand: {sub}\nUse /bog for help.")


# ─── Main ─────────────────────────────────────────────────────────

def main():
    if not BOT_TOKEN:
        logger.error("TELEGRAM_BOT_TOKEN not set")
        sys.exit(1)

    if not ALLOWED_USER_IDS or 0 in ALLOWED_USER_IDS:
        logger.error("TELEGRAM_MG_USER_ID not set")
        sys.exit(1)

    logger.info("Starting BogWizard Bot listener (python-telegram-bot %s)",
                "v20+")
    app = Application.builder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start_cmd))
    app.add_handler(CommandHandler("bog", bog_cmd))

    logger.info("BogWizard Bot running — polling for /bog commands")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()

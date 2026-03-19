"""
Signals with Remi — Group Message Listener

Monitors the "Signals with Remi" Telegram group in real-time.
Every message gets evaluated by Claude. If it contains a signal,
it's ingested into SQLite + Obsidian and Remi replies with a brief eval.

Non-signal messages (casual chat) are acknowledged but not ingested.

Uses Telethon userbot (same session as EngineeringRobo listener).
"""
import asyncio
import hashlib
import json
import logging
import os
import re
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

import anthropic
import requests
from dotenv import load_dotenv
from telethon import TelegramClient, events
from telethon.tl.types import MessageMediaDocument, MessageMediaPhoto

load_dotenv(os.path.expanduser("~/remi-intelligence/.env"))
sys.path.insert(0, os.path.expanduser("~/remi-intelligence"))

from gli_stamper import fetch_gli_stamp
from llm_extractor import extract_themes
from obsidian_writer import write_document_note, write_theme_note

logging.basicConfig(level=logging.INFO,
    format='%(asctime)s %(levelname)s %(message)s')
logger = logging.getLogger(__name__)

# Config
API_ID        = int(os.getenv("TELEGRAM_API_ID", "0"))
API_HASH      = os.getenv("TELEGRAM_API_HASH", "")
BOT_TOKEN     = os.getenv("TELEGRAM_BOT_TOKEN", "")
GROUP_ID      = int(os.getenv("SIGNALS_GROUP_ID", "-1003857050116"))
ADMIN_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "6625574871")
DB_PATH       = os.getenv("DB_PATH",
    os.path.expanduser("~/remi-intelligence/remi_intelligence.db"))
SESSION_FILE  = "/home/proxmox/.tg-signals-listener"

anthropic_client = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))


def bot_reply(chat_id: int, text: str, reply_to: int = None):
    """Send a message via the Remi bot."""
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    payload = {"chat_id": chat_id, "text": text, "parse_mode": "Markdown"}
    if reply_to:
        payload["reply_to_message_id"] = reply_to
    try:
        r = requests.post(url, json=payload, timeout=10)
        return r.ok
    except Exception as e:
        logger.error(f"Bot reply failed: {e}")
        return False


def classify_message(text: str) -> dict:
    """
    Quick classification: is this a signal worth ingesting?
    Returns {is_signal, signal_type, confidence}
    Uses Haiku for speed/cost.
    """
    if not text or len(text.strip()) < 10:
        return {"is_signal": False, "signal_type": "too_short", "confidence": "high"}

    prompt = f"""Classify this Telegram message from an investing signals group.

MESSAGE:
{text[:1000]}

Return JSON only:
{{
  "is_signal": true/false,
  "signal_type": "price_target|entry_alert|thesis|macro_view|news|chart_analysis|casual_chat|question|other",
  "confidence": "high|medium|low",
  "one_line": "10-word max summary of what this says"
}}

is_signal = true if this contains: price targets, entry/exit levels, investment thesis,
macro analysis, news with market implications, or chart analysis.
is_signal = false if this is: casual chat, greetings, questions without data, memes.

Return only valid JSON."""

    try:
        resp = anthropic_client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=150,
            messages=[{"role": "user", "content": prompt}]
        )
        text_resp = resp.content[0].text.strip()
        text_resp = re.sub(r'^```json\s*', '', text_resp)
        text_resp = re.sub(r'\s*```$', '', text_resp)
        return json.loads(text_resp)
    except Exception as e:
        logger.warning(f"Classification failed: {e}")
        return {"is_signal": True, "signal_type": "other", "confidence": "low"}


TIER1_KEYWORDS = [
    "steno", "lyn alden", "crossborder", "howell", "macroalf", "prometheus",
    "bianco", "tooze", "chartbook", "katusa", "real vision", "raoul",
    "pettis", "blas", "bremmer", "alden"
]

def _detect_tier(filename=None, caption=None, sender=None) -> int:
    """Bump to Tier 1 if content matches a known high-signal source."""
    text = " ".join(filter(None, [
        filename or "", caption or "", sender or ""
    ])).lower()
    if any(kw in text for kw in TIER1_KEYWORDS):
        return 1
    return 2



def ingest_signal(text: str, sender: str, msg_id: int,
                  media_caption: str = None) -> dict:
    """
    Ingest a signal message into SQLite and write Obsidian notes.
    Returns summary of what was extracted.
    """
    content = text or media_caption or ""
    content_hash = hashlib.sha256(content.encode()).hexdigest()

    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()

    # Dedup check
    cur.execute("SELECT id FROM documents WHERE content_hash = ?", (content_hash,))
    if cur.fetchone():
        conn.close()
        return {"status": "duplicate"}

    gli_stamp = fetch_gli_stamp()

    # Insert document
    cur.execute("""
        INSERT INTO documents
        (source_url, source_name, source_tier, source_type,
         title, content_text, content_hash, published_at, ingested_at,
         gli_phase, gli_value_bn, steno_regime, fiscal_score, transition_risk,
         status)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending')
    """, (
        f"tg://signals_group/{msg_id}",
        f"Signals Group ({sender})",
        _detect_tier(filename=None, caption=caption, sender=sender),
        "telegram_group",
        f"Signal from {sender} [{datetime.utcnow().strftime('%Y-%m-%d %H:%M')}]",
        content,
        content_hash,
        datetime.utcnow().isoformat(),
        datetime.utcnow().isoformat(),
        gli_stamp.gli_phase,
        gli_stamp.gli_value_bn,
        gli_stamp.steno_regime,
        gli_stamp.fiscal_score,
        gli_stamp.transition_risk,
    ))
    doc_id = cur.lastrowid
    conn.commit()

    # Extract themes
    result = extract_themes(
        content, f"Signals Group ({sender})", 2,
        gli_context=gli_stamp.for_prompt()
    )

    themes_extracted = []
    if result and result.get("themes"):
        from extraction_worker import get_or_create_theme, compute_velocity
        from obsidian_writer import write_theme_note

        for theme_data in result["themes"]:
            theme_key = theme_data.get("theme_key", "unknown")
            theme_label = theme_data.get("theme_label", theme_key)

            theme_id = get_or_create_theme(conn, theme_key, theme_label, gli_stamp)

            cur.execute("""
                INSERT INTO document_themes
                (document_id, theme_id, facts, opinions, key_quotes,
                 tickers_mentioned, sentiment, weighted_score)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                doc_id, theme_id,
                json.dumps(theme_data.get("facts", [])),
                json.dumps(theme_data.get("opinions", [])),
                json.dumps([theme_data.get("key_quote", "")]),
                json.dumps(theme_data.get("tickers_mentioned", [])),
                theme_data.get("sentiment", "neutral"),
                0.8  # Tier 2 weight
            ))
            conn.commit()

            velocity = compute_velocity(conn, theme_id, 2)
            cur.execute("""
                UPDATE themes SET velocity_score = ?,
                is_flagged = CASE WHEN ? >= 15.0 THEN 1 ELSE 0 END
                WHERE id = ?
            """, (velocity, velocity, theme_id))
            conn.commit()

            write_theme_note(theme_id, conn)
            themes_extracted.append({
                "label": theme_label,
                "sentiment": theme_data.get("sentiment", "neutral"),
                "tickers": theme_data.get("tickers_mentioned", []),
                "velocity": velocity
            })

    cur.execute("UPDATE documents SET status = 'complete' WHERE id = ?", (doc_id,))
    conn.commit()

    from obsidian_writer import write_document_note
    write_document_note(doc_id, conn)
    conn.close()

    return {
        "status": "ingested",
        "doc_id": doc_id,
        "themes": themes_extracted,
        "gli": gli_stamp.for_prompt()
    }


def format_eval_reply(classification: dict, ingest_result: dict) -> str:
    """Format Remi's reply to the group."""
    signal_type = classification.get("signal_type", "other")
    one_line = classification.get("one_line", "")

    if ingest_result.get("status") == "duplicate":
        return None  # Don't reply to duplicates

    themes = ingest_result.get("themes", [])
    if not themes:
        return f"📥 Logged — {one_line}"

    lines = [f"📊 *{one_line}*"]
    for t in themes[:2]:  # max 2 themes in reply
        sent_emoji = {"bullish": "🟢", "bearish": "🔴",
                      "neutral": "⚪", "mixed": "🟡"}.get(t["sentiment"], "⚪")
        tickers = " ".join(f"`{tk}`" for tk in t["tickers"][:3])
        lines.append(f"{sent_emoji} {t['label']}{' — ' + tickers if tickers else ''}")

    gli = ingest_result.get("gli", "")
    if "unavailable" not in gli:
        lines.append(f"_GLI: {gli}_")

    lines.append(f"_Saved to vault_")
    return "\n".join(lines)


async def main():
    if not API_ID or not API_HASH:
        logger.error("TELEGRAM_API_ID and TELEGRAM_API_HASH required in .env")
        sys.exit(1)

    client = TelegramClient(SESSION_FILE, API_ID, API_HASH)
    await client.start()
    logger.info(f"Signals listener started — watching group {GROUP_ID}")

    @client.on(events.NewMessage(chats=GROUP_ID))
    async def handler(event):
        msg = event.message
        sender = "unknown"
        try:
            sender_entity = await event.get_sender()
            sender = (getattr(sender_entity, "username", None) or
                      getattr(sender_entity, "first_name", None) or
                      str(sender_entity.id))
        except Exception:
            pass

        # Skip bot's own messages
        if sender == "Gwizzlybear_Remibot":
            return

        # Handle file drops
        if msg.media and not (msg.text and len(msg.text) > 100):
            await handle_media(event, sender, client)
            return

        text = msg.text or msg.message or ""
        if not text.strip():
            return

        logger.info(f"[{sender}] {text[:80]}")

        # Classify
        classification = classify_message(text)
        logger.info(f"Classification: {classification}")

        if not classification.get("is_signal"):
            return

        # Ingest
        result = ingest_signal(text, sender, msg.id)
        logger.info(f"Ingest result: {result}")

        # Reply
        reply_text = format_eval_reply(classification, result)
        if reply_text:
            bot_reply(GROUP_ID, reply_text, reply_to=msg.id)

    await client.run_until_disconnected()


if __name__ == "__main__":
    asyncio.run(main())

async def main():
    if not API_ID or not API_HASH:
        logger.error("TELEGRAM_API_ID and TELEGRAM_API_HASH required in .env")
        sys.exit(1)

    client = TelegramClient(SESSION_FILE, API_ID, API_HASH)
    await client.start()
    logger.info(f"Signals listener started — watching group {GROUP_ID}")

    @client.on(events.NewMessage(chats=GROUP_ID))
    async def handler(event):
        msg = event.message
        sender = "unknown"
        try:
            sender_entity = await event.get_sender()
            sender = (getattr(sender_entity, "username", None) or
                      getattr(sender_entity, "first_name", None) or
                      str(sender_entity.id))
        except Exception:
            pass

        # Skip bot's own messages
        if sender == "Gwizzlybear_Remibot":
            return

        # Handle file drops
        if msg.media and not (msg.text and len(msg.text) > 100):
            await handle_media(event, sender, client)
            return

        text = msg.text or msg.message or ""
        if not text.strip():
            return

        logger.info(f"[{sender}] {text[:80]}")

        # Classify
        classification = classify_message(text)
        logger.info(f"Classification: {classification}")

        if not classification.get("is_signal"):
            return

        # Ingest
        result = ingest_signal(text, sender, msg.id)
        logger.info(f"Ingest result: {result}")

        # Reply
        reply_text = format_eval_reply(classification, result)
        if reply_text:
            bot_reply(GROUP_ID, reply_text, reply_to=msg.id)

    await client.run_until_disconnected()


if __name__ == "__main__":
    asyncio.run(main())


async def handle_media(event, sender: str, client):
    """Download and ingest a file dropped in the group."""
    msg = event.message
    caption = msg.text or msg.message or ""
    
    # Determine file type
    media = msg.media
    if not media:
        return
    
    is_pdf = False
    filename = f"signal_{sender}_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}"
    
    if hasattr(media, 'document') and media.document:
        for attr in media.document.attributes:
            if hasattr(attr, 'file_name') and attr.file_name:
                filename = attr.file_name
                if filename.lower().endswith('.pdf'):
                    is_pdf = True
    
    # Download to investing vault inbox
    inbox = "/docker/obsidian/investing/Inbox"
    os.makedirs(inbox, exist_ok=True)
    filepath = os.path.join(inbox, filename)
    
    bot_reply(GROUP_ID, f"📥 Downloading `{filename}`...", reply_to=msg.id)
    
    try:
        await client.download_media(msg, file=filepath)
    except Exception as e:
        bot_reply(GROUP_ID, f"❌ Download failed: {e}", reply_to=msg.id)
        return
    
    # For PDFs — extract text and ingest
    if is_pdf:
        try:
            import pypdf
            reader = pypdf.PdfReader(filepath)
            text = "\n".join(p.extract_text() or "" for p in reader.pages)
            if caption:
                text = f"Caption: {caption}\n\n{text}"
        except Exception as e:
            text = caption or f"PDF file: {filename}"
    else:
        # Image or other — use caption as content
        text = caption or f"File dropped: {filename}"
    
    if len(text.strip()) < 20:
        bot_reply(GROUP_ID, f"✅ Saved `{filename}` to vault inbox. No text to extract.", reply_to=msg.id)
        return
    
    # Classify and ingest
    classification = classify_message(text[:1000])
    result = ingest_signal(text, sender, msg.id)
    reply_text = format_eval_reply(classification, result)
    
    if reply_text:
        bot_reply(GROUP_ID, f"📄 *{filename}*\n{reply_text}", reply_to=msg.id)
    else:
        bot_reply(GROUP_ID, f"✅ `{filename}` ingested to vault.", reply_to=msg.id)

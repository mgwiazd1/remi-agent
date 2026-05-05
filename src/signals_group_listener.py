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
from watchlist_manager import list_watchlist, get_dossier, remove_ticker, run_thesis_eval
from picks_engine import format_weekly_digest
from vision_relay import describe_image
import tempfile

# BogWizard /bog command handler for DMs
from bogwizard_bot_listener import bog_dispatch_telethon

# Trade analysis handler (/analyze, /take)
from trade_analysis_handler import handle_trade_analysis

logging.basicConfig(level=logging.INFO,
    format='%(asctime)s %(levelname)s %(message)s')
logger = logging.getLogger(__name__)

# Config
API_ID        = int(os.getenv("TELEGRAM_API_ID", "0"))
API_HASH      = os.getenv("TELEGRAM_API_HASH", "")
BOT_TOKEN     = os.getenv("INVESTING_BOT_TOKEN") or os.getenv("TELEGRAM_BOT_TOKEN", "")
GROUP_ID      = int(os.getenv("SIGNALS_GROUP_ID", "-1003857050116"))
ADMIN_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "6625574871")
BOGWIZARD_ALLOWED = {int(x) for x in os.getenv("TELEGRAM_MG_USER_ID", "6625574871").split(",") if x.strip()}
DB_PATH       = os.getenv("DB_PATH",
    os.path.expanduser("~/remi-intelligence/remi_intelligence.db"))
SESSION_FILE  = "/home/proxmox/.tg-signals-listener"

anthropic_client = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))


def _db_connect():
    """Connect to the intelligence DB with WAL mode and busy timeout."""
    conn = sqlite3.connect(DB_PATH, timeout=10)
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


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
    # Commands and confirmations bypass quality filter entirely
    if text:
        stripped = text.strip()
        # Any slash command — never send to classifier
        if stripped.startswith("/"):
            return {"is_signal": True, "signal_type": "command", "confidence": "high"}
        # Single-word confirmation responses — too short, no signal value
        if stripped.lower() in ("yes", "confirm", "no"):
            return {"is_signal": False, "signal_type": "confirmation", "confidence": "high"}

    # Minimum content quality filter — reject low-quality messages early
    if text:
        filtered = text.strip()
        
        # Reject if length < 30 characters
        if len(filtered) < 30:
            logger.info(f"Skipping low-quality message: {filtered[:50]}")
            return {"is_signal": False, "signal_type": "too_short", "confidence": "high"}
        
        # Reject if only emoji, punctuation, or whitespace
        import unicodedata
        non_junk_chars = [c for c in filtered if not (
            unicodedata.category(c) in ('So', 'Po', 'Pc', 'Pd', 'Pe', 'Pf', 'Pi', 'Po', 'Ps')
            or c.isspace()
        )]
        if not non_junk_chars:
            logger.info(f"Skipping low-quality message: {filtered[:50]}")
            return {"is_signal": False, "signal_type": "no_content", "confidence": "high"}
        
        # Reject common spam/greeting patterns (case-insensitive)
        low_quality_patterns = [
            r'^gm$', r'^gn$', r'learn more', r'register now',
            r'click here', r'subscribe'
        ]
        lower_text = filtered.lower()
        for pattern in low_quality_patterns:
            if re.search(pattern, lower_text):
                logger.info(f"Skipping low-quality message: {filtered[:50]}")
                return {"is_signal": False, "signal_type": "spam_pattern", "confidence": "high"}
        
        # Reject if no alphabetic characters (pure non-Latin script without English context)
        has_alpha = any(c.isalpha() and ord(c) < 0x2500 for c in filtered)  # Basic Latin + Extended
        if not has_alpha:
            logger.info(f"Skipping low-quality message: {filtered[:50]}")
            return {"is_signal": False, "signal_type": "no_alpha", "confidence": "high"}
    
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

    conn = _db_connect()
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
                 tickers_mentioned, sentiment, weighted_score, historical_analog)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                doc_id, theme_id,
                json.dumps(theme_data.get("facts", [])),
                json.dumps(theme_data.get("opinions", [])),
                json.dumps([theme_data.get("key_quote", "")]),
                json.dumps(theme_data.get("tickers_mentioned", [])),
                theme_data.get("sentiment", "neutral"),
                0.8,  # Tier 2 weight
                theme_data.get("historical_analog", "none")
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


VAULT_PATH = "/docker/obsidian/investing/Intelligence"


def _get_remi_triage_recommendation(item_id: str) -> str:
    """Fetch triage item details and get GLM-5 fellow-level recommendation."""
    import httpx
    conn = _db_connect()
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    cur.execute("SELECT * FROM triage_items WHERE triage_id = ?", (item_id,))
    row = cur.fetchone()
    conn.close()
    if not row:
        return f"Item {item_id} not found in triage database."

    detail = row["detail"] if row["detail"] else "{}"
    import json as _json
    try:
        detail_dict = _json.loads(detail)
    except Exception:
        detail_dict = {}

    prompt = f"""You are reviewing a vault triage item found by Consuela (the resident).

Item {row['triage_id']}: type={row['item_type']}
Files: {row['files_involved']}
Detail: {_json.dumps(detail_dict, indent=2)}

Current GLI phase and regime context:
- Check the triage report date: {row['report_date']}

Based on the investing thesis, vault structure, and item details:
1. What is your recommendation? (merge/archive/delete/keep/fix)
2. Why?
3. If merge: which file should be the canonical note?
4. If delete: is any content worth preserving elsewhere?

Be specific and decisive. MG will approve or reject your recommendation.
Keep it under 200 words."""

    api_key = os.environ.get("GLM_API_KEY", "")
    base_url = os.environ.get("GLM_BASE_URL", "https://api.z.ai/api/coding/paas/v4")
    try:
        r = requests.post(
            f"{base_url}/chat/completions",
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            json={
                "model": "glm-5",
                "messages": [
                    {"role": "system", "content": "You are Remi, a macro-aware investing intelligence agent reviewing vault triage items. Provide concise, actionable recommendations."},
                    {"role": "user", "content": prompt},
                ],
                "max_tokens": 500,
                "temperature": 0.3,
            },
            timeout=60,
        )
        r.raise_for_status()
        recommendation = r.json()["choices"][0]["message"]["content"]
    except Exception as e:
        logger.error(f"GLM-5 triage recommendation failed: {e}")
        recommendation = f"[Recommendation unavailable: {e}]"

    # Persist recommendation to triage_items
    conn = _db_connect()
    conn.execute(
        "UPDATE triage_items SET recommendation = ? WHERE triage_id = ?",
        (recommendation, item_id),
    )
    conn.commit()
    conn.close()
    return recommendation


def _handle_vault_command(parts: list, chat_id: int, reply_to: int):
    """Handle /vault triage, /vault approve, /vault reject commands."""
    subcmd = parts[1].lower() if len(parts) > 1 else "help"
    conn = _db_connect()
    conn.row_factory = sqlite3.Row

    try:
        if subcmd == "triage":
            # Find most recent triage items
            rows = conn.execute(
                "SELECT triage_id, item_type, status, files_involved, detail "
                "FROM triage_items WHERE report_date = "
                "(SELECT MAX(report_date) FROM triage_items) "
                "ORDER BY triage_id LIMIT 100"
            ).fetchall()
            if not rows:
                bot_reply(chat_id, "No triage reports found.", reply_to=reply_to)
                return

            # Count by type and status
            pending = [r for r in rows if r["status"] == "pending"]
            by_type = {}
            for r in rows:
                by_type.setdefault(r["item_type"], {"total": 0, "pending": 0})
                by_type[r["item_type"]]["total"] += 1
                if r["status"] == "pending":
                    by_type[r["item_type"]]["pending"] += 1

            report_date = conn.execute(
                "SELECT MAX(report_date) FROM triage_items"
            ).fetchone()[0]
            total_in_db = conn.execute(
                "SELECT COUNT(*) FROM triage_items WHERE report_date = ?",
                (report_date,)
            ).fetchone()[0]

            lines = [f"🧹 *Vault Triage* — {report_date}"]
            lines.append(f"{len(pending)} pending of {total_in_db} total items")
            for t in ("MERGE", "ORPHAN", "BROKEN_LINK", "STALE", "FRONTMATTER"):
                if t in by_type:
                    b = by_type[t]
                    lines.append(f"  {t}: {b['pending']} pending / {b['total']} total")
            lines.append("\nReply with item ID (e.g. T001) for Remi's recommendation")
            lines.append("/vault approve T001 | /vault reject T001 | /vault approve all")
            bot_reply(chat_id, "\n".join(lines), reply_to=reply_to)

        elif subcmd == "approve":
            item_id = parts[2].upper() if len(parts) > 2 else None
            if not item_id:
                bot_reply(chat_id, "Usage: `/vault approve T001` or `/vault approve all`",
                          reply_to=reply_to)
                return
            if item_id == "ALL":
                cur = conn.cursor()
                cur.execute(
                    "UPDATE triage_items SET status='approved', "
                    "resolved_at=datetime('now'), resolved_by='MG' "
                    "WHERE status='pending' AND report_date = "
                    "(SELECT MAX(report_date) FROM triage_items)")
                conn.commit()
                bot_reply(chat_id, f"✅ Approved all {cur.rowcount} pending items.",
                          reply_to=reply_to)
            else:
                cur = conn.cursor()
                cur.execute(
                    "UPDATE triage_items SET status='approved', "
                    "resolved_at=datetime('now'), resolved_by='MG' "
                    "WHERE triage_id=? AND status='pending'", (item_id,))
                conn.commit()
                if cur.rowcount:
                    bot_reply(chat_id, f"✅ Approved {item_id} — Remi will execute.",
                              reply_to=reply_to)
                else:
                    bot_reply(chat_id, f"{item_id} not found or already resolved.",
                              reply_to=reply_to)

        elif subcmd == "reject":
            item_id = parts[2].upper() if len(parts) > 2 else None
            if not item_id:
                bot_reply(chat_id, "Usage: `/vault reject T001`",
                          reply_to=reply_to)
                return
            cur = conn.cursor()
            cur.execute(
                "UPDATE triage_items SET status='rejected', "
                "resolved_at=datetime('now'), resolved_by='MG' "
                "WHERE triage_id=? AND status='pending'", (item_id,))
            conn.commit()
            if cur.rowcount:
                bot_reply(chat_id, f"❌ Rejected {item_id}.", reply_to=reply_to)
            else:
                bot_reply(chat_id, f"{item_id} not found or already resolved.",
                          reply_to=reply_to)

        else:
            bot_reply(chat_id,
                "/vault triage — show pending items\n"
                "/vault approve <ID> — approve item\n"
                "/vault approve all — bulk approve\n"
                "/vault reject <ID> — dismiss item",
                reply_to=reply_to)
    finally:
        conn.close()


async def _handle_command(text: str, chat_id: int, reply_to: int):
    """Route /watch, /pick, and /book commands."""
    parts = text.split()
    cmd = parts[0].lower()
    sub = parts[1].lower() if len(parts) > 1 else ""

    try:
        if cmd == "/book":
            if sub == "status":
                conn = _db_connect()
                rows = conn.execute(
                    "SELECT filename, status, chapter_count, created_at "
                    "FROM book_jobs ORDER BY created_at DESC LIMIT 10"
                ).fetchall()
                conn.close()
                if not rows:
                    bot_reply(chat_id, "No books in queue.", reply_to=reply_to)
                else:
                    lines = ["BOOK PIPELINE STATUS\n"]
                    for r in rows:
                        lines.append(f"  {r[0]}: {r[1]} ({r[2] or '?'} chapters)")
                    bot_reply(chat_id, "\n".join(lines), reply_to=reply_to)
            elif sub == "list":
                conn = _db_connect()
                rows = conn.execute(
                    "SELECT title, author, chapter_count, completed_at "
                    "FROM book_jobs WHERE status='completed' ORDER BY completed_at DESC"
                ).fetchall()
                conn.close()
                if not rows:
                    bot_reply(chat_id,
                        "No completed books yet. Drop a PDF in watch/books/incoming/",
                        reply_to=reply_to)
                else:
                    lines = ["COMPLETED BOOKS\n"]
                    for r in rows:
                        lines.append(
                            f"  {r[0]} ({r[1]}) — {r[2]} ch — "
                            f"{r[3][:10] if r[3] else '?'}")
                    bot_reply(chat_id, "\n".join(lines), reply_to=reply_to)
            elif sub == "process" and len(parts) >= 3:
                from pathlib import Path as _P
                filename = parts[2]
                pdf = _P(__file__).parent.parent / "watch" / "books" / "incoming" / filename
                if not pdf.exists():
                    bot_reply(chat_id,
                        f"Not found: {filename}\n"
                        f"Drop PDF in ~/remi-intelligence/watch/books/incoming/",
                        reply_to=reply_to)
                    return
                bot_reply(chat_id,
                    f"Processing {filename}... this may take 5-15 min.",
                    reply_to=reply_to)
                from book_ingestor import process_book
                result = process_book(pdf)
                import json as _json
                bot_reply(chat_id,
                    f"Done: {_json.dumps(result, indent=1)[:400]}",
                    reply_to=reply_to)
            else:
                bot_reply(chat_id,
                    "/book status | list | process <filename>",
                    reply_to=reply_to)

        elif cmd == "/watch":
            if sub == "list" or sub == "":
                bot_reply(chat_id, list_watchlist(), reply_to=reply_to)
            elif sub == "dossier" and len(parts) >= 3:
                bot_reply(chat_id, get_dossier(parts[2]), reply_to=reply_to)
            elif sub == "eval" and len(parts) >= 3:
                bot_reply(chat_id, "Running thesis eval...", reply_to=reply_to)
                result = await run_thesis_eval(parts[2])
                bot_reply(chat_id, result, reply_to=reply_to)
            elif sub == "remove" and len(parts) >= 3:
                bot_reply(chat_id, remove_ticker(parts[2]), reply_to=reply_to)
            elif sub == "add" and len(parts) >= 3:
                ticker = parts[2].upper()
                bot_reply(chat_id,
                    f"Send thesis for {ticker} in format:\n"
                    f"/watch thesis {ticker} <company> | <thesis> | <conviction> | <sizing> | <target>",
                    reply_to=reply_to)
            elif sub == "thesis" and len(parts) >= 4:
                # /watch thesis TICKER company | thesis | conviction | sizing | target
                ticker = parts[2].upper()
                rest = " ".join(parts[3:])
                fields = [f.strip() for f in rest.split("|")]
                company = fields[0] if len(fields) > 0 else ticker
                thesis = fields[1] if len(fields) > 1 else "No thesis provided"
                conviction = fields[2] if len(fields) > 2 else "medium"
                sizing = fields[3] if len(fields) > 3 else "standard"
                target = fields[4] if len(fields) > 4 else "2X"
                from watchlist_manager import add_ticker
                result = add_ticker(ticker, company, thesis, conviction=conviction,
                                    sizing=sizing, target_return=target)
                bot_reply(chat_id, result, reply_to=reply_to)
            else:
                bot_reply(chat_id,
                    "/watch list | dossier <TKR> | eval <TKR> | add <TKR> | remove <TKR>",
                    reply_to=reply_to)

        elif cmd == "/pick":
            if sub == "list" or sub == "":
                conn = _db_connect()
                conn.row_factory = sqlite3.Row
                rows = conn.execute(
                    "SELECT ticker, pick_type, conviction_level, thesis, created_at "
                    "FROM remi_picks WHERE status='pending' ORDER BY created_at DESC"
                ).fetchall()
                conn.close()
                if not rows:
                    bot_reply(chat_id, "No pending picks.", reply_to=reply_to)
                else:
                    lines = [f"REMI PICKS — {len(rows)} pending\n"]
                    for r in rows:
                        lines.append(f"  {r['pick_type'].upper()} {r['ticker']} — {r['conviction_level']} (since {r['created_at'][:10]})")
                        if r["thesis"]:
                            lines.append(f"    _{r['thesis'][:200]}_")
                    lines.append("\n/pick approve <TKR> | /pick reject <TKR>")
                    bot_reply(chat_id, "\n".join(lines), reply_to=reply_to)
            elif sub == "approve" and len(parts) >= 3:
                tk = parts[2].upper()
                conn = _db_connect()
                cur = conn.cursor()
                # Fetch full pick data before approving
                cur.execute(
                    "SELECT conviction_level, thesis, company, pick_type, "
                    "pick_direction, target_return, time_horizon, "
                    "key_risks, catalysts, gli_phase, evidence_chain "
                    "FROM remi_picks WHERE ticker=? AND status='pending'", (tk,))
                row = cur.fetchone()
                cur.execute("UPDATE remi_picks SET status='approved', approved_by='MG', "
                            "approved_at=datetime('now') WHERE ticker=? AND status='pending'", (tk,))
                conn.commit()
                changed = cur.rowcount
                conn.close()

                # Auto-add to watchlist with full pick metadata
                wl_msg = ""
                if changed and row:
                    try:
                        from watchlist_manager import add_ticker_from_pick
                        import json as _json
                        conviction_level = row[0] or "medium"
                        thesis = row[1] or "Remi pick — see picks engine for details"
                        company = row[2] or tk
                        pick_type = row[3] or row[4] or "long"
                        target_return = row[5] or "2X"
                        time_horizon = row[6] or "2y"
                        key_risks = _json.loads(row[7]) if row[7] else []
                        catalysts = _json.loads(row[8]) if row[8] else []
                        gli_phase = row[9]
                        evidence_chain = row[10]
                        result = add_ticker_from_pick(
                            ticker=tk,
                            company=company,
                            thesis=thesis,
                            conviction=conviction_level,
                            pick_type=pick_type,
                            target_return=target_return,
                            time_horizon=time_horizon,
                            key_risks=key_risks,
                            catalysts=catalysts,
                            gli_phase=gli_phase,
                            evidence_chain=evidence_chain,
                        )
                        wl_msg = f"\n→ {result}"
                    except Exception as e:
                        wl_msg = f"\n⚠️ watchlist add failed: {e}"

                # Queue thesis eval (fire and forget)
                eval_msg = ""
                if changed and wl_msg and "added" in wl_msg.lower():
                    try:
                        import asyncio
                        asyncio.ensure_future(
                            run_thesis_eval(tk, force_refresh=True))
                        eval_msg = "\n🔬 Thesis eval queued"
                    except Exception as e:
                        eval_msg = f"\n⚠️ Thesis eval failed: {e}"

                bot_reply(chat_id,
                    f"{'✅' if changed else '❌'} {tk} "
                    f"{'approved' + wl_msg + eval_msg if changed else 'no pending pick found'}",
                    reply_to=reply_to)
            elif sub == "reject" and len(parts) >= 3:
                tk = parts[2].upper()
                conn = _db_connect()
                cur = conn.cursor()
                cur.execute("UPDATE remi_picks SET status='rejected' "
                            "WHERE ticker=? AND status='pending'", (tk,))
                conn.commit()
                changed = cur.rowcount
                conn.close()
                bot_reply(chat_id, f"{tk} {'rejected' if changed else 'no pending pick found'}",
                          reply_to=reply_to)
            elif sub == "digest":
                bot_reply(chat_id, format_weekly_digest(include_emerging=True), reply_to=reply_to)
            else:
                bot_reply(chat_id,
                    "/pick list | approve <TKR> | reject <TKR> | digest",
                    reply_to=reply_to)

        elif cmd == "/dripstack":
            if not sub:
                bot_reply(chat_id, "Usage: /dripstack <topic>", reply_to=reply_to)
                return
            bot_reply(chat_id, f"Searching DripStack for '{sub}'...", reply_to=reply_to)
            try:
                sys.path.insert(0, os.path.dirname(__file__))
                from dripstack_buyer import buy_by_topic
                doc_ids = buy_by_topic(sub)
                if doc_ids:
                    bot_reply(chat_id,
                        f"DripStack: bought {len(doc_ids)} articles on '{sub}' → queued for extraction",
                        reply_to=reply_to)
                else:
                    bot_reply(chat_id,
                        f"DripStack: no articles found for '{sub}' or spend limit reached",
                        reply_to=reply_to)
            except Exception as e:
                bot_reply(chat_id, f"DripStack error: {str(e)[:150]}", reply_to=reply_to)

        elif cmd == "/publish":
            # /publish {report_id} — publish draft report to Aestima
            if not sub or not sub.isdigit():
                bot_reply(chat_id, "Usage: /publish {report_id}", reply_to=reply_to)
                return
            report_id = int(sub)
            bot_reply(chat_id, f"Publishing report #{report_id} to Aestima...", reply_to=reply_to)
            try:
                import psycopg2
                from psycopg2.extras import RealDictCursor
                pg_conn = psycopg2.connect(os.getenv("DASHBOARD_DATABASE_URL", ""))
                cur = pg_conn.cursor(cursor_factory=RealDictCursor)
                cur.execute("SELECT * FROM reports WHERE id = %s AND status = 'draft'", (report_id,))
                report = cur.fetchone()
                if not report:
                    bot_reply(chat_id, f"Report #{report_id} not found or not in draft status.", reply_to=reply_to)
                    cur.close(); pg_conn.close()
                    return
                report_dict = dict(report)
                sys.path.insert(0, os.path.dirname(__file__))
                from report_writer import post_to_aestima
                aestima_id = await asyncio.to_thread(post_to_aestima, report_dict)
                if aestima_id:
                    cur.execute(
                        "UPDATE reports SET status = 'published', published_at = NOW(), aestima_id = %s WHERE id = %s",
                        (str(aestima_id), report_id))
                    pg_conn.commit()
                    bot_reply(chat_id,
                        f"Published report #{report_id} to Aestima\n"
                        f"Title: {report_dict.get('title', '?')[:80]}\n"
                        f"Aestima ID: {aestima_id}",
                        reply_to=reply_to)
                else:
                    bot_reply(chat_id, f"Failed to post #{report_id} to Aestima — still in draft.", reply_to=reply_to)
                cur.close(); pg_conn.close()
            except Exception as e:
                logger.error(f"publish handler error: {e}", exc_info=True)
                bot_reply(chat_id, f"Error: {str(e)[:100]}", reply_to=reply_to)

        elif cmd == "/kill_report":
            # /kill_report {report_id} — kill draft report
            if not sub or not sub.isdigit():
                bot_reply(chat_id, "Usage: /kill_report {report_id}", reply_to=reply_to)
                return
            report_id = int(sub)
            try:
                import psycopg2
                pg_conn = psycopg2.connect(os.getenv("DASHBOARD_DATABASE_URL", ""))
                cur = pg_conn.cursor()
                cur.execute("UPDATE reports SET status = 'killed' WHERE id = %s AND status = 'draft'", (report_id,))
                rows = cur.rowcount
                pg_conn.commit(); cur.close(); pg_conn.close()
                if rows:
                    bot_reply(chat_id, f"Report #{report_id} killed.", reply_to=reply_to)
                else:
                    bot_reply(chat_id, f"Report #{report_id} not found or not in draft.", reply_to=reply_to)
            except Exception as e:
                bot_reply(chat_id, f"Error: {str(e)[:100]}", reply_to=reply_to)

        elif cmd == "/vault":
            _handle_vault_command(parts, chat_id, reply_to)
    except Exception as e:
        logger.error(f"Command handler error: {e}", exc_info=True)
        bot_reply(chat_id, f"Command error: {str(e)[:100]}", reply_to=reply_to)


async def _handle_analysis_command(text: str, chat_id: int, reply_to: int, sender_id: int):
    """Route /profile, /deepdive, /approve, /kill, /refresh commands."""
    import httpx
    parts = text.split()
    cmd = parts[0].lower()
    ticker = parts[1].upper().lstrip("$") if len(parts) > 1 else ""

    if not ticker and cmd not in ["/refresh"]:
        bot_reply(chat_id, f"Usage: {cmd} TICKER", reply_to=reply_to)
        return

    try:
        if cmd in ["/profile", "/deepdive"]:
            bot_reply(chat_id, f"Building {cmd[1:]} for {ticker}... pulling Aestima modules...", reply_to=reply_to)
            import asyncio
            sys.path.insert(0, os.path.dirname(__file__))
            from analysis_post_builder import build_profile_post, build_deep_dive_post
            build_fn = build_profile_post if cmd == "/profile" else build_deep_dive_post
            result = await build_fn(ticker=ticker, sector="", industry="")
            # Push to dashboard
            agent_key = os.environ.get("AESTIMA_AGENT_KEY", "")
            payload = {
                "ticker": ticker, "analysis_type": cmd[1:],
                "post_content": result["post_content"],
                "module_data": {k: bool(v) for k, v in result["module_data"].items()},
                "gli_phase": result.get("gli_phase", ""), "steno_regime": result.get("steno_regime", ""),
                "conviction_score": result.get("conviction_score"),
            }
            r = httpx.post("http://localhost:8501/api/analysis",
                json=payload, headers={"X-Agent-Key": agent_key}, timeout=10)
            post_id = r.json().get("id", "?") if r.status_code == 200 else "?"
            conviction = result.get("conviction_score") or "?"
            missing = result.get("modules_missing", [])
            missing_str = f"\nMissing modules: {', '.join(missing)}" if missing else ""
            bot_reply(chat_id,
                f"{ticker} {cmd[1:]} posted to dashboard.\n"
                f"Conviction: {conviction}/10\n"
                f"View: intel.gwizcloud.com/dashboard/analysis{missing_str}\n\n"
                f"/approve {ticker} or /kill {ticker}?", reply_to=reply_to)

        elif cmd == "/approve":
            # Find latest pending post for ticker and approve via dashboard API
            agent_key = os.environ.get("AESTIMA_AGENT_KEY", "")
            # Login to get token
            login_r = httpx.post("http://localhost:8501/api/auth/login",
                json={"username": "mg", "password": "Gwizzly2026!"}, timeout=5)
            token = login_r.json().get("token", "")
            # Get pending posts
            posts = httpx.get(f"http://localhost:8501/api/analysis?status=pending&ticker={ticker}",
                headers={"Authorization": f"Bearer {token}"}, timeout=5).json()
            if posts:
                post_id = posts[0]["id"]
                httpx.patch(f"http://localhost:8501/api/analysis/{post_id}/approve",
                    json={}, headers={"Authorization": f"Bearer {token}"}, timeout=5)
                bot_reply(chat_id, f"Approved {ticker} (post #{post_id}). Added to watchlist.", reply_to=reply_to)
            else:
                bot_reply(chat_id, f"No pending posts for {ticker}.", reply_to=reply_to)

        elif cmd == "/kill":
            agent_key = os.environ.get("AESTIMA_AGENT_KEY", "")
            login_r = httpx.post("http://localhost:8501/api/auth/login",
                json={"username": "mg", "password": "Gwizzly2026!"}, timeout=5)
            token = login_r.json().get("token", "")
            posts = httpx.get(f"http://localhost:8501/api/analysis?status=pending&ticker={ticker}",
                headers={"Authorization": f"Bearer {token}"}, timeout=5).json()
            if posts:
                post_id = posts[0]["id"]
                httpx.patch(f"http://localhost:8501/api/analysis/{post_id}/kill",
                    json={}, headers={"Authorization": f"Bearer {token}"}, timeout=5)
                bot_reply(chat_id, f"Killed {ticker} (post #{post_id}). Stored for reference.", reply_to=reply_to)
            else:
                bot_reply(chat_id, f"No pending posts for {ticker}.", reply_to=reply_to)

        elif cmd == "/refresh":
            if not ticker:
                bot_reply(chat_id, "Usage: /refresh TICKER", reply_to=reply_to)
                return
            sys.path.insert(0, os.path.dirname(__file__))
            from aestima_module_cache import invalidate_cache
            invalidate_cache(ticker)
            bot_reply(chat_id, f"Cache cleared for {ticker}. Next /profile or /deepdive will fetch fresh data.", reply_to=reply_to)

    except Exception as e:
        logger.error(f"Analysis command error: {e}", exc_info=True)
        bot_reply(chat_id, f"Error: {str(e)[:150]}", reply_to=reply_to)


async def main():
    if not API_ID or not API_HASH:
        logger.error("TELEGRAM_API_ID and TELEGRAM_API_HASH required in .env")
        sys.exit(1)

    client = TelegramClient(SESSION_FILE, API_ID, API_HASH)
    await client.start()
    logger.info(f"Signals listener started — watching group {GROUP_ID}")

    # --- BogWizard DM handler ---
    @client.on(events.NewMessage(incoming=True))
    async def bog_dm_handler(event):
        """Handle /bog commands from DMs (private chats only)."""
        # Only handle private messages (DMs), not group messages
        if not event.is_private:
            return
        raw = (event.message.text or event.message.message or "").strip()
        if not raw.startswith("/bog") and not raw.startswith("/start"):
            return
        sender_id = event.sender_id
        if sender_id not in BOGWIZARD_ALLOWED:
            return
        chat_id = event.chat_id
        await bog_dispatch_telethon(raw, chat_id, event.message.id)

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

        # --- /watch, /pick, /book, /vault command routing ---
        raw = (msg.text or msg.message or "").strip()
        if raw.startswith("/watch") or raw.startswith("/pick") or raw.startswith("/book") or raw.startswith("/vault") or raw.startswith("/publish") or raw.startswith("/kill_report"):
            await _handle_command(raw, GROUP_ID, msg.id)
            return
        if raw.startswith("/profile") or raw.startswith("/deepdive") or raw.startswith("/approve") or raw.startswith("/kill") or raw.startswith("/refresh"):
            await _handle_analysis_command(raw, GROUP_ID, msg.id, msg.sender_id)
            return

        # --- /analyze, /take — trade analysis routing ---
        if raw.startswith("/analyze") or raw.startswith("/take"):
            # Check if there's a photo attached (chart for /analyze)
            photo_desc = None
            if msg.photo and raw.startswith("/analyze"):
                # Download and describe the chart via E4B
                with tempfile.NamedTemporaryFile(suffix=".jpg", delete=False) as tmp:
                    tmp_path = tmp.name
                try:
                    await client.download_media(msg, file=tmp_path)
                    photo_desc = await asyncio.to_thread(
                        describe_image, image_path=tmp_path, image_type="chart")
                except Exception as e:
                    logger.warning(f"Chart download for /analyze failed: {e}")
            await handle_trade_analysis(raw, GROUP_ID, msg.id, msg.sender_id, photo_desc)
            return

        # Catch-all: any other slash command — skip classifier entirely
        if raw.startswith("/"):
            return

        # --- Single-word confirmations — skip classifier ---
        if raw.lower() in ("yes", "confirm", "no"):
            return

        # --- Bare T-ID handler (e.g. "T001" → Remi recommendation) ---
        if re.match(r'^T\d{3}$', raw.strip().upper()):
            item_id = raw.strip().upper()
            bot_reply(GROUP_ID, f"🩺 Getting Remi's recommendation for {item_id}...",
                      reply_to=msg.id)
            recommendation = await asyncio.to_thread(
                _get_remi_triage_recommendation, item_id)
            bot_reply(GROUP_ID,
                f"🩺 *Remi's Recommendation for {item_id}:*\n\n{recommendation}\n\n"
                f"/vault approve {item_id} | /vault reject {item_id}",
                reply_to=msg.id)
            return

        # Handle file drops
        if msg.media and not event.photo and not (msg.text and len(msg.text) > 100):
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

    @client.on(events.NewMessage(
        chats=GROUP_ID,
        func=lambda e: e.photo is not None
    ))
    async def handle_photo(event):
        """Process chart/image drops in the investing group."""
        sender = "unknown"
        try:
            sender_entity = await event.get_sender()
            sender = (getattr(sender_entity, "username", None) or
                      getattr(sender_entity, "first_name", None) or
                      str(sender_entity.id))
        except Exception:
            pass

        caption = event.message.message or ""

        logger.info(f"Photo from {sender} in investing group. Caption: {caption[:80]}")

        # Download image to temp file
        with tempfile.NamedTemporaryFile(suffix=".jpg", delete=False) as tmp:
            tmp_path = tmp.name

        try:
            await client.download_media(event.message, file=tmp_path)

            # Determine image type from caption
            image_type = "chart"  # default for investing group
            if any(kw in caption.lower() for kw in ["cxr", "xray", "x-ray", "ct ", "mri"]):
                image_type = "medical"
            elif any(kw in caption.lower() for kw in ["receipt", "scan", "photo"]):
                image_type = "general"

            # Reply with acknowledgment
            await event.reply("👁️ Processing image...")

            # Get description from Gemma E4B (blocking — runs VRAM swap)
            description = await asyncio.to_thread(
                describe_image,
                image_path=tmp_path,
                image_type=image_type
            )

            if description.startswith("ERROR:"):
                await event.reply(f"⚠️ Vision failed: {description}")
                return

            # Synthesize via GLM-5 — one clean post, no raw dump
            from llm_extractor import call_glm
            result = call_glm(
                messages=[
                    {"role": "system", "content": (
                        "You are Remi, a macro investing analyst. A chart was shared in your "
                        "investing group. Given the Gemma E4B image description below, provide "
                        "a concise synthesis: what the chart shows, how it relates to the "
                        "current macro regime, and any actionable takeaway. Under 500 chars."
                    )},
                    {"role": "user", "content": (
                        f"Sender: {sender}\nCaption: {caption}\n"
                        f"Chart description: {description}"
                    )},
                ],
            )
            if result:
                analysis_text, _model = result
                reply_text = f"[VISION_ANALYSIS]: 📊 {analysis_text}"
            else:
                # Fallback to raw description if GLM fails
                reply_text = f"[VISION_ANALYSIS]: 📊 Chart Analysis\n\n{description}"
            if len(reply_text) > 4000:
                reply_text = reply_text[:3997] + "..."
            await event.reply(reply_text, parse_mode='markdown')

        except Exception as e:
            logger.error(f"Photo handler error: {e}")
            await event.reply(f"⚠️ Failed to process image: {str(e)[:100]}")
        finally:
            if os.path.exists(tmp_path):
                os.unlink(tmp_path)

    await client.run_until_disconnected()


async def handle_media(event, sender: str, client):
    """Download and ingest a file dropped in the group."""
    msg = event.message
    caption = msg.text or msg.message or ""
    
    # Determine file type
    media = msg.media
    if not media:
        return
    
    is_pdf = False
    is_epub = False
    filename = f"signal_{sender}_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}"
    
    if hasattr(media, 'document') and media.document:
        for attr in media.document.attributes:
            if hasattr(attr, 'file_name') and attr.file_name:
                filename = attr.file_name
                if filename.lower().endswith('.pdf'):
                    is_pdf = True
                elif filename.lower().endswith('.epub'):
                    is_epub = True
    
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
    
    # EPUB — route to book pipeline (no page-count gate)
    if is_epub:
        import shutil
        import threading
        from pathlib import Path as _P
        from book_ingestor import process_book

        def _run_epub_pipeline(path, msg_id):
            result = process_book(_P(path))
            if result.get("reason") == "drm":
                bot_reply(GROUP_ID,
                    "⚠️ This EPUB appears to be DRM-protected. Please provide a DRM-free version.",
                    reply_to=msg_id)

        books_dir = os.path.expanduser("~/remi-intelligence/watch/books/incoming")
        os.makedirs(books_dir, exist_ok=True)
        dest_path = os.path.join(books_dir, filename)
        shutil.copy2(filepath, dest_path)
        bot_reply(GROUP_ID,
            f"📚 Book detected (EPUB): `{filename}` — queued for book pipeline.",
            reply_to=msg.id)
        threading.Thread(target=_run_epub_pipeline, args=(dest_path, msg.id), daemon=True).start()
        return
    
    # For PDFs — check page count and route to book or article pipeline
    if is_pdf:
        try:
            import fitz
            doc = fitz.open(filepath)
            page_count = len(doc)
            doc.close()
        except Exception:
            page_count = 0

        if page_count > 80:
            # Large PDF — route to book pipeline
            import shutil
            import threading
            from pathlib import Path as _P
            from book_ingestor import process_book
            books_dir = os.path.expanduser("~/remi-intelligence/watch/books/incoming")
            os.makedirs(books_dir, exist_ok=True)
            dest_path = os.path.join(books_dir, filename)
            shutil.copy2(filepath, dest_path)
            bot_reply(GROUP_ID,
                f"📚 Book detected ({page_count} pages): `{filename}` — queued for book pipeline.",
                reply_to=msg.id)
            threading.Thread(target=process_book, args=(_P(dest_path),), daemon=True).start()
            return

        # Small PDF — existing article pipeline
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

if __name__ == "__main__":
    asyncio.run(main())

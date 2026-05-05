"""
Centralized Telegram sender for Remi pipeline.
All pipeline modules import from here instead of inline requests.post().
"""
import os
import time
import logging
import requests

logger = logging.getLogger("telegram_sender")

# Rate limiting state
_last_send: dict = {}      # chat_id -> last send timestamp
_send_counts: dict = {}    # chat_id -> list of timestamps in current 3s window

# Message sanitization constants
MAX_TELEGRAM_CHARS = 500
DASHBOARD_URL = "intel.gwizcloud.com"
HALLUCINATION_KEYWORDS = ["bullet points", "formatting", "sections", "headers", "actionable-oriented", "cross-links", "velocity scores"]

# Bot tokens and chat IDs
INVESTING_BOT_TOKEN = os.getenv("INVESTING_BOT_TOKEN", "") or os.getenv("TELEGRAM_BOT_TOKEN", "")
CLINICAL_BOT_TOKEN = os.getenv("CLINICAL_BOT_TOKEN", "")
INVESTING_GROUP_CHAT_ID = os.getenv("INVESTING_GROUP_CHAT_ID", "")
MG_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "6625574871")
PABLO_CHAT_ID = os.getenv("PABLO_CHAT_ID", "1749701421")


def _sanitize(message: str) -> str | None:
    """Truncate long messages and detect hallucination garbage."""
    if not message or not message.strip():
        return None
    
    # Hallucination detection - if 3+ hallucination keywords appear, it's meta-commentary not signal
    lower = message.lower()
    hits = sum(1 for kw in HALLUCINATION_KEYWORDS if kw in lower)
    if hits >= 3:
        logger.warning(f"Hallucination detected ({hits} keyword hits), dropping message")
        return None
    
    # Length gate
    if len(message) > MAX_TELEGRAM_CHARS:
        truncated = message[:MAX_TELEGRAM_CHARS].rsplit('\n', 1)[0]
        return truncated + f"\n\n📄 Full report → {DASHBOARD_URL}"
    
    return message


def _send(token: str, chat_id: str, message: str, parse_mode: str = "Markdown") -> bool:
    global _last_send, _send_counts
    
    if not token:
        logger.error("No bot token configured")
        return False
    if not chat_id:
        logger.error("No chat_id provided")
        return False
    
    message = _sanitize(message)
    if message is None:
        logger.info("Message dropped by sanitize filter")
        return False
    
    # Max 3 messages per 3 seconds per chat
    now = time.time()
    window_start = now - 3.0
    if chat_id not in _send_counts:
        _send_counts[chat_id] = []
    # Prune timestamps outside the 3s window
    _send_counts[chat_id] = [ts for ts in _send_counts[chat_id] if ts > window_start]
    if len(_send_counts[chat_id]) >= 3:
        logger.warning(f"Rate limit self-imposed, dropping message to {chat_id}")
        return False
    
    # Per-chat rate limiting: min 1 second between sends
    if chat_id in _last_send:
        elapsed = now - _last_send[chat_id]
        if elapsed < 1.0:
            sleep_time = 1.0 - elapsed
            time.sleep(sleep_time)
    
    def do_send(retry: bool = False) -> bool:
        try:
            url = f"https://api.telegram.org/bot{token}/sendMessage"
            resp = requests.post(url, json={
                "chat_id": chat_id,
                "text": message[:4096],
                "parse_mode": parse_mode,
            }, timeout=15)
            if resp.status_code == 200:
                return True
            # Handle 429 RetryAfter
            if resp.status_code == 429 and not retry:
                try:
                    retry_after = resp.json().get("parameters", {}).get("retry_after", 5)
                except Exception:
                    retry_after = 5
                logger.warning(f"Telegram 429, sleeping {retry_after + 1}s before retry")
                time.sleep(retry_after + 1)
                return do_send(retry=True)
            logger.error(f"Telegram API {resp.status_code}: {resp.text[:200]}")
            return False
        except Exception as e:
            logger.error(f"Telegram send failed: {e}")
            return False
    
    result = do_send()
    if result:
        now = time.time()
        _last_send[chat_id] = now
        _send_counts[chat_id].append(now)
    return result


def send_investing_alert(message: str, chat_id: str = None) -> bool:
    target = chat_id or INVESTING_GROUP_CHAT_ID
    return _send(INVESTING_BOT_TOKEN, target, message)


def send_investing_to_mg(message: str) -> bool:
    return _send(INVESTING_BOT_TOKEN, MG_CHAT_ID, message)


def send_investing_to_pablo(message: str) -> bool:
    return _send(INVESTING_BOT_TOKEN, PABLO_CHAT_ID, message)


def send_clinical_notification(message: str, chat_id: str = None) -> bool:
    target = chat_id or MG_CHAT_ID
    return _send(CLINICAL_BOT_TOKEN, target, message)

# Dev Remi ops reports — overnight reports, vault audits, build results
DEV_REMI_BOT_TOKEN = "DEV_REMI_BOT_TOKEN_REDACTED"
MG_DM_CHAT_ID = "6625574871"

def send_ops_report(message: str) -> bool:
    """Send ops/maintenance report via Dev Remi bot to MG DM only."""
    return _send(DEV_REMI_BOT_TOKEN, MG_DM_CHAT_ID, message)

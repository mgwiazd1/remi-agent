import hashlib
import json
import logging
import os
import sqlite3
from datetime import datetime
from typing import List, Dict
from dotenv import load_dotenv

load_dotenv(os.path.expanduser("~/remi-intelligence/.env"))

logger = logging.getLogger(__name__)

DB_PATH = os.getenv("DB_PATH", os.path.expanduser("~/remi-intelligence/remi_intelligence.db"))
BUFFER_PATH = os.path.expanduser("~/signal-buffer.json")
SOURCE_NAME = "EngineeringRobo VIP"
SOURCE_TIER = 2


def content_hash(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def is_duplicate(conn: sqlite3.Connection, chash: str) -> bool:
    cur = conn.cursor()
    cur.execute("SELECT id FROM documents WHERE content_hash = ?", (chash,))
    return cur.fetchone() is not None


def load_buffer() -> List[Dict]:
    if not os.path.exists(BUFFER_PATH):
        return []
    try:
        with open(BUFFER_PATH) as f:
            data = json.load(f)
        if isinstance(data, list):
            return data
        if isinstance(data, dict):
            return data.get("messages", [])
    except Exception as e:
        logger.error(f"Failed to load signal buffer: {e}")
    return []


def ingest_buffer(clear_after: bool = False) -> Dict:
    messages = load_buffer()
    if not messages:
        return {"new_items": 0, "skipped": 0, "total_in_buffer": 0}

    conn = sqlite3.connect(DB_PATH)
    new_items = 0
    skipped = 0

    for msg in messages:
        try:
            # Extract text from various buffer formats
            text = (
                msg.get("text") or
                msg.get("message") or
                msg.get("content") or
                str(msg)
            )

            if not text or len(text) < 20:
                skipped += 1
                continue

            # Build source URL from message ID if available
            msg_id = msg.get("message_id") or msg.get("id") or ""
            source_url = f"telegram://engineeringrobo/{msg_id}"

            # Get timestamp
            timestamp = msg.get("ts") or msg.get("date") or msg.get("timestamp")
            if isinstance(timestamp, str):
                published = timestamp
            elif isinstance(timestamp, (int, float)):
                published = datetime.utcfromtimestamp(timestamp).isoformat()
            else:
                published = None

            # Topic/channel tag as title
            topic_id = msg.get("topic_id") or msg.get("thread_id") or "general"
            title = f"EngineeringRobo Signal [{topic_id}] {text[:60]}"

            chash = content_hash(text)

            if is_duplicate(conn, chash):
                skipped += 1
                continue

            cur = conn.cursor()
            cur.execute("""
                INSERT INTO documents
                (source_url, source_name, source_tier, source_type,
                 title, content_text, content_hash, published_at, status)
                VALUES (?, ?, ?, 'telegram', ?, ?, ?, ?, 'pending')
            """, (source_url, SOURCE_NAME, SOURCE_TIER, title, text, chash, published))
            conn.commit()
            new_items += 1

        except Exception as e:
            logger.error(f"Error ingesting buffer message: {e}")
            skipped += 1

    conn.close()

    if clear_after and new_items > 0:
        try:
            with open(BUFFER_PATH, "w") as f:
                json.dump([], f)
            logger.info(f"Buffer cleared after ingesting {new_items} messages")
        except Exception as e:
            logger.error(f"Failed to clear buffer: {e}")

    return {
        "new_items": new_items,
        "skipped": skipped,
        "total_in_buffer": len(messages)
    }


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    result = ingest_buffer(clear_after=False)
    print(f"Buffer ingestion: {result}")

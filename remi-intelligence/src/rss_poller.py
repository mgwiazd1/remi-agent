import feedparser
import hashlib
import json
import logging
import os
import sqlite3
from datetime import datetime, timedelta
from typing import List, Dict, Optional
from dotenv import load_dotenv

load_dotenv(os.path.expanduser("~/remi-intelligence/.env"))

logger = logging.getLogger(__name__)

DB_PATH = os.getenv("DB_PATH", os.path.expanduser("~/remi-intelligence/remi_intelligence.db"))
CONFIG_PATH = os.path.expanduser("~/remi-intelligence/config/rss_feeds.json")
MAX_AGE_DAYS = 30


def load_feed_config() -> List[Dict]:
    with open(CONFIG_PATH) as f:
        config = json.load(f)
    return config["feeds"]


def content_hash(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def is_duplicate(conn: sqlite3.Connection, chash: str) -> bool:
    cur = conn.cursor()
    cur.execute("SELECT id FROM documents WHERE content_hash = ?", (chash,))
    return cur.fetchone() is not None


def parse_published(entry) -> Optional[datetime]:
    try:
        if hasattr(entry, "published_parsed") and entry.published_parsed:
            return datetime(*entry.published_parsed[:6])
    except Exception:
        pass
    return None


def extract_text(entry) -> str:
    text = ""
    if hasattr(entry, "summary"):
        text = entry.summary
    if hasattr(entry, "content"):
        for c in entry.content:
            if c.get("type") == "text/html" or c.get("type") == "text/plain":
                text = c.get("value", text)
                break
    # Strip basic HTML tags
    import re
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def filter_zerohedge(entry, keywords: List[str]) -> bool:
    """Return True if entry passes keyword filter."""
    text = (getattr(entry, "title", "") + " " + getattr(entry, "summary", "")).lower()
    return any(kw.lower() in text for kw in keywords)


def poll_feed(feed_config: Dict, conn: sqlite3.Connection, dry_run: bool = False) -> Dict:
    name = feed_config["name"]
    url = feed_config["url"]
    tier = feed_config.get("tier", 4)
    is_contrarian = feed_config.get("signal_type") == "contrarian_amplifier"
    filter_keywords = feed_config.get("filter_keywords", [])
    cutoff = datetime.utcnow() - timedelta(days=MAX_AGE_DAYS)

    result = {"source_name": name, "new_items": 0, "skipped": 0, "errors": 0}

    try:
        parsed = feedparser.parse(url)
        entries = parsed.entries or []

        for entry in entries:
            try:
                title = getattr(entry, "title", "Untitled")
                link = getattr(entry, "link", url)
                text = extract_text(entry)
                published = parse_published(entry)

                if not text or len(text) < 100:
                    result["skipped"] += 1
                    continue

                if published and published < cutoff:
                    result["skipped"] += 1
                    continue

                # Apply keyword filter for contrarian sources
                if is_contrarian and filter_keywords:
                    if not filter_zerohedge(entry, filter_keywords):
                        result["skipped"] += 1
                        continue

                # Truncate to max length
                max_len = int(os.getenv("MAX_CONTENT_LENGTH_CHARS", 50000))
                text = text[:max_len]

                chash = content_hash(text)

                if is_duplicate(conn, chash):
                    result["skipped"] += 1
                    continue

                if not dry_run:
                    cur = conn.cursor()
                    cur.execute("""
                        INSERT INTO documents
                        (source_url, source_name, source_tier, source_type,
                         title, content_text, content_hash, published_at, status)
                        VALUES (?, ?, ?, 'rss', ?, ?, ?, ?, 'pending')
                    """, (link, name, tier or 4, title, text, chash,
                          published.isoformat() if published else None))
                    conn.commit()

                result["new_items"] += 1

            except Exception as e:
                logger.error(f"Error processing entry from {name}: {e}")
                result["errors"] += 1

    except Exception as e:
        logger.error(f"Error fetching feed {name}: {e}")
        result["errors"] += 1

    return result


def poll_all_feeds(dry_run: bool = False) -> List[Dict]:
    feeds = load_feed_config()
    conn = sqlite3.connect(DB_PATH)
    results = []
    for feed in feeds:
        result = poll_feed(feed, conn, dry_run=dry_run)
        results.append(result)
        logger.info(f"{result['source_name']}: {result['new_items']} new, {result['skipped']} skipped")
    conn.close()
    return results


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    results = poll_all_feeds(dry_run=False)
    total_new = sum(r["new_items"] for r in results)
    print(f"\nTotal new items queued: {total_new}")
    for r in results:
        print(f"  {r['source_name']}: {r['new_items']} new, {r['skipped']} skipped, {r['errors']} errors")

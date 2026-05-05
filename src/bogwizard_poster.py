"""
BogWizard Poster — publishes approved drafts to X via Aestima bridge.

Path A (primary): POST /api/agent/bogwizard/post with X-BogWizard-Key
Path B (fallback): twitter-cli on 5xx/network errors only.
4xx = our bug, fail loud.
"""
from __future__ import annotations

import json
import logging
import os
import sqlite3
import subprocess
from datetime import datetime, timezone
from pathlib import Path

import httpx
from dotenv import load_dotenv

load_dotenv(os.path.expanduser("~/remi-intelligence/.env"))

logger = logging.getLogger(__name__)

BASE_DIR = Path(__file__).resolve().parent.parent
DB_PATH = BASE_DIR / "remi_intelligence.db"

AESTIMA_BRIDGE_URL = "https://aestima.ai/api/agent/bogwizard/post"
TWITTER_CLI = os.path.expanduser("~/.local/bin/twitter")


def _db() -> sqlite3.Connection:
    return sqlite3.connect(DB_PATH)


def _get_draft(draft_id: int) -> dict | None:
    conn = _db()
    try:
        row = conn.execute(
            "SELECT id, draft_type, content, is_thread, status FROM bogwizard_drafts WHERE id=?",
            (draft_id,),
        ).fetchone()
        if not row:
            return None
        return {
            "id": row[0], "draft_type": row[1], "content": row[2],
            "is_thread": bool(row[3]), "status": row[4],
        }
    finally:
        conn.close()


def _mark_posted(draft_id: int, tweet_id: str | None, path: str,
                 log_id: int | None = None) -> None:
    conn = _db()
    try:
        conn.execute(
            """UPDATE bogwizard_drafts
               SET status='posted', posted_at=?, posting_path=?,
                   tweet_id=?, aestima_log_id=?
               WHERE id=?""",
            (datetime.now(timezone.utc).isoformat(), path, tweet_id, log_id, draft_id),
        )
        conn.commit()
    finally:
        conn.close()


def _mark_error(draft_id: int, error: str) -> None:
    conn = _db()
    try:
        conn.execute(
            "UPDATE bogwizard_drafts SET status='error', error_message=? WHERE id=?",
            (error[:500], draft_id),
        )
        conn.commit()
    finally:
        conn.close()


def _post_path_a(content: str, draft_type: str) -> dict:
    """Post via Aestima bridge. Returns API response dict."""
    key = os.environ.get("AESTIMA_BOGWIZARD_POST_KEY")
    if not key:
        raise RuntimeError("AESTIMA_BOGWIZARD_POST_KEY not set")

    payload = {
        "draft_type": draft_type,
        "content": content,
    }

    resp = httpx.post(
        AESTIMA_BRIDGE_URL,
        headers={
            "X-BogWizard-Key": key,
            "Content-Type": "application/json",
        },
        json=payload,
        timeout=30,
    )

    if 400 <= resp.status_code < 500:
        # 4xx = our bug (bad payload, auth issue, validation). Fail loud.
        logger.error("Path A 4xx: %d %s", resp.status_code, resp.text[:500])
        raise RuntimeError(f"Path A client error {resp.status_code}: {resp.text[:300]}")

    if resp.status_code >= 500:
        # 5xx = their problem, fall through to Path B
        raise ConnectionError(f"Path A server error {resp.status_code}")

    return resp.json()


def _post_path_b(content: str) -> str:
    """Post via twitter-cli. Returns tweet text output."""
    if not os.path.exists(TWITTER_CLI):
        raise RuntimeError(f"twitter-cli not found at {TWITTER_CLI}")

    result = subprocess.run(
        [TWITTER_CLI, "post", content, "--json"],
        capture_output=True, text=True, timeout=30,
    )
    if result.returncode != 0:
        raise RuntimeError(f"twitter-cli failed: {result.stderr[:300]}")

    return result.stdout


def post_draft(draft_id: int) -> dict:
    """Post an approved draft. Returns {success, path, tweet_id, error}."""
    draft = _get_draft(draft_id)
    if not draft:
        return {"success": False, "error": f"draft {draft_id} not found"}
    if draft["status"] not in ("pending", "approved"):
        return {"success": False, "error": f"draft {draft_id} status={draft['status']}, not postable"}

    content = draft["content"]
    dtype = draft["draft_type"]

    # For threads, split content and post first tweet, then reply the rest
    if draft["is_thread"]:
        tweets = [t.strip() for t in content.split("\n\n") if t.strip()]
        if not tweets:
            return {"success": False, "error": "thread has no tweets after split"}
        content = tweets[0]  # Post first tweet via bridge
        # Thread replies handled separately if needed

    # Path A: Aestima bridge
    try:
        api_resp = _post_path_a(content, dtype)
        tweet_id = api_resp.get("tweet_id") or api_resp.get("id")
        log_id = api_resp.get("log_id")
        _mark_posted(draft_id, tweet_id, "path_a", log_id)
        logger.info("Draft %d posted via Path A, tweet_id=%s", draft_id, tweet_id)
        return {"success": True, "path": "path_a", "tweet_id": tweet_id}
    except ConnectionError as e:
        logger.warning("Path A failed (5xx): %s — trying Path B", e)
    except RuntimeError:
        # 4xx — our bug, don't fall back
        _mark_error(draft_id, str(e))
        raise

    # Path B: twitter-cli fallback (only reached on 5xx / network error)
    try:
        output = _post_path_b(content)
        _mark_posted(draft_id, None, "path_b")
        logger.info("Draft %d posted via Path B (twitter-cli)", draft_id)
        return {"success": True, "path": "path_b", "tweet_id": None}
    except Exception as e:
        _mark_error(draft_id, f"path_b: {e}")
        raise RuntimeError(f"Both paths failed. Path B error: {e}")

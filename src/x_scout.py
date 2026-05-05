"""
Remi X Scout — Twitter/X account monitoring via twitter-cli.
Polls curated macro accounts, deduplicates, feeds into extraction pipeline.
Tier 1: every 4h | Tier 2+: every 12h | 10 tweets per account per poll.
"""
import json
import subprocess
import hashlib
import sqlite3
import logging
import os
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

logger = logging.getLogger("remi.x_scout")

TAXONOMY_PATH = Path(__file__).parent.parent / "config" / "account_taxonomy.json"
ENV_PATH = Path(__file__).parent.parent / ".env"
DB_PATH = Path(__file__).parent.parent / "remi_intelligence.db"

T1_POLL_INTERVAL_H = 4
T2_POLL_INTERVAL_H = 12
MAX_TWEETS_PER_ACCOUNT = 10
INTER_ACCOUNT_DELAY_S = 2
MAX_CONSECUTIVE_AUTH_FAILURES = 3
AUTH_ALERT_DEBOUNCE_S = 3600

ZEROHEDGE_KEYWORDS = [
    "PBOC", "repo", "oil", "fertilizer", "helium", "ammonia",
    "sanctions", "sovereign", "debt ceiling", "liquidity", "fiscal",
    "uranium", "copper", "LNG", "OPEC", "Hormuz", "Iran"
]

def load_env_cookies():
    """Load twitter-cli auth cookies from .env file."""
    cookies = {}
    env_file = ENV_PATH
    if not env_file.exists():
        logger.error("Missing .env file at %s", env_file)
        return cookies
    with open(env_file) as f:
        for line in f:
            line = line.strip()
            if line.startswith("#") or "=" not in line:
                continue
            key, val = line.split("=", 1)
            key = key.strip()
            val = val.strip().strip("'\"")
            if key in ("TWITTER_AUTH_TOKEN", "TWITTER_CT0"):
                cookies[key] = val
    return cookies

def load_taxonomy():
    """Load account taxonomy from JSON config."""
    with open(TAXONOMY_PATH) as f:
        data = json.load(f)
    return data.get("accounts", [])

def fetch_account_timeline(handle, max_tweets=10):
    """Fetch last N tweets from an account using twitter-cli.
    Returns list of normalized tweet dicts, or empty list on failure.
    """
    cookies = load_env_cookies()
    if "TWITTER_AUTH_TOKEN" not in cookies or "TWITTER_CT0" not in cookies:
        logger.error("Twitter cookies missing from .env")
        return []

    env = os.environ.copy()
    env["TWITTER_AUTH_TOKEN"] = cookies["TWITTER_AUTH_TOKEN"]
    env["TWITTER_CT0"] = cookies["TWITTER_CT0"]

    cmd = ["twitter", "user-posts", handle, "--max", str(max_tweets), "--json"]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30, env=env)
        if result.returncode != 0:
            stderr = result.stderr.strip()
            if any(kw in stderr.lower() for kw in ("auth", "login", "401", "403")):
                logger.error("Auth error fetching @%s: %s", handle, stderr)
                return "AUTH_ERROR"
            logger.warning("Error fetching @%s: %s", handle, stderr)
            return []
        data = json.loads(result.stdout)
    except subprocess.TimeoutExpired:
        logger.warning("Timeout fetching @%s", handle)
        return []
    except (json.JSONDecodeError, KeyError) as e:
        logger.warning("Parse error for @%s: %s", handle, e)
        return []

    raw_tweets = data.get("data", [])
    if not raw_tweets:
        raw_tweets = data if isinstance(data, list) else []

    tweets = []
    for t in raw_tweets:
        try:
            tweet = {
                "id": str(t.get("id", t.get("rest_id", ""))),
                "author": handle,
                "author_name": t.get("user", {}).get("name", handle),
                "text": t.get("text", t.get("full_text", "")),
                "created_at": t.get("created_at", ""),
                "likes": int(t.get("favorite_count", t.get("likes", 0))),
                "retweets": int(t.get("retweet_count", t.get("retweets", 0))),
                "views": int(t.get("views", t.get("view_count", 0))),
                "replies": int(t.get("reply_count", t.get("replies", 0))),
                "url": f"https://x.com/{handle}/status/{t.get('id', t.get('rest_id', ''))}"
            }
            if tweet["id"] and tweet["text"]:
                tweets.append(tweet)
        except (KeyError, ValueError, TypeError) as e:
            logger.debug("Skipping malformed tweet from @%s: %s", handle, e)
            continue

    logger.info("Fetched %d tweets from @%s", len(tweets), handle)
    return tweets

def should_poll_account(account, last_poll_times):
    """Check if account should be polled based on tier cadence."""
    handle = account["handle"]
    tier = account.get("tier")

    if tier == 1:
        interval = timedelta(hours=T1_POLL_INTERVAL_H)
    else:
        interval = timedelta(hours=T2_POLL_INTERVAL_H)

    last_poll = last_poll_times.get(handle)
    if last_poll is None:
        return True

    if isinstance(last_poll, str):
        last_poll = datetime.fromisoformat(last_poll)

    now = datetime.now(timezone.utc)
    if last_poll.tzinfo is None:
        last_poll = last_poll.replace(tzinfo=timezone.utc)

    return (now - last_poll) >= interval

def filter_zerohedge(tweets):
    """Filter ZeroHedge tweets to only those matching keywords."""
    filtered = []
    for tweet in tweets:
        text_lower = tweet["text"].lower()
        if any(kw.lower() in text_lower for kw in ZEROHEDGE_KEYWORDS):
            filtered.append(tweet)
    logger.info("ZeroHedge filter: %d/%d passed", len(filtered), len(tweets))
    return filtered

def dedup_tweets(tweets, db_conn):
    """Remove tweets already in the documents table."""
    new_tweets = []
    cursor = db_conn.cursor()
    for tweet in tweets:
        content_hash = hashlib.sha256(f"x:{tweet['id']}".encode()).hexdigest()
        cursor.execute("SELECT 1 FROM documents WHERE content_hash = ?", (content_hash,))
        if cursor.fetchone() is None:
            tweet["_content_hash"] = content_hash
            new_tweets.append(tweet)
    logger.info("Dedup: %d new / %d total", len(new_tweets), len(tweets))
    return new_tweets

def ingest_tweet_to_pipeline(tweet, account_meta, db_conn):
    """Insert tweet into documents table for extraction pipeline."""
    gli_phase = ""
    gli_value = 0.0
    steno_regime = ""
    fiscal_score = 0.0
    transition_risk = 0.0

    try:
        from src.gli_stamper import fetch_gli_stamp
        stamp = fetch_gli_stamp()
        if stamp:
            gli_phase = getattr(stamp, "gli_phase", "") or ""
            gli_value = getattr(stamp, "gli_value_bn", 0.0) or 0.0
            steno_regime = getattr(stamp, "steno_regime", "") or ""
            fiscal_score = getattr(stamp, "fiscal_score", 0.0) or 0.0
            transition_risk = getattr(stamp, "transition_risk", 0.0) or 0.0
    except Exception as e:
        logger.warning("GLI stamp failed, ingesting without: %s", e)

    title = f"@{tweet['author']}: {tweet['text'][:80]}..."
    content_hash = tweet.get("_content_hash", hashlib.sha256(f"x:{tweet['id']}".encode()).hexdigest())

    cursor = db_conn.cursor()
    cursor.execute("""
        INSERT INTO documents (source_url, source_name, source_tier, source_type,
            title, content_text, content_hash, published_at, status,
            gli_phase, gli_value_bn, steno_regime, fiscal_score, transition_risk,
            retweets, views, likes, replies)
        VALUES (?, ?, ?, 'x_tweet', ?, ?, ?, ?, 'pending', ?, ?, ?, ?, ?,
                ?, ?, ?, ?)
    """, (
        tweet["url"],
        f"X/@{tweet['author']}",
        account_meta.get("tier", 2),
        title,
        tweet["text"],
        content_hash,
        tweet.get("created_at", ""),
        gli_phase, gli_value, steno_regime, fiscal_score, transition_risk,
        tweet.get("retweets", 0),
        tweet.get("views", 0),
        tweet.get("likes", 0),
        tweet.get("replies", 0),
    ))
    db_conn.commit()
    doc_id = cursor.lastrowid
    logger.info("Ingested tweet %s from @%s as doc_id=%d", tweet["id"], tweet["author"], doc_id)
    return doc_id

def run_x_scout(tier_filter=None):
    """Main X scout entry point. Called by scheduler.
    tier_filter: 't1' for Tier 1 only, 't2plus' for Tier 2+, None for all eligible.
    """
    accounts = load_taxonomy()
    db = sqlite3.connect(str(DB_PATH))

    cursor = db.cursor()
    cursor.execute("SELECT handle, last_poll_at FROM x_scout_state")
    last_poll_times = {}
    for row in cursor.fetchall():
        last_poll_times[row[0]] = row[1]

    if tier_filter == "t1":
        accounts = [a for a in accounts if a.get("tier") == 1]
    elif tier_filter == "t2plus":
        accounts = [a for a in accounts if a.get("tier") != 1]

    consecutive_auth_failures = 0
    total_ingested = 0
    accounts_polled = 0

    for account in accounts:
        handle = account["handle"]

        if not should_poll_account(account, last_poll_times):
            continue

        if consecutive_auth_failures >= MAX_CONSECUTIVE_AUTH_FAILURES:
            logger.error("3+ consecutive auth failures — cookies likely expired. Stopping run.")
            try:
                from src.telegram_notifier import send_telegram_alert
                send_telegram_alert("X Scout cookies expired — refresh from browser DevTools")
            except Exception:
                pass
            break

        tweets = fetch_account_timeline(handle, MAX_TWEETS_PER_ACCOUNT)

        if tweets == "AUTH_ERROR":
            consecutive_auth_failures += 1
            cursor.execute("""
                INSERT INTO x_scout_state (handle, consecutive_errors, last_error, last_error_at)
                VALUES (?, 1, 'auth_error', ?)
                ON CONFLICT(handle) DO UPDATE SET
                    consecutive_errors = consecutive_errors + 1,
                    last_error = 'auth_error',
                    last_error_at = ?
            """, (handle, datetime.now(timezone.utc).isoformat(), datetime.now(timezone.utc).isoformat()))
            db.commit()
            time.sleep(INTER_ACCOUNT_DELAY_S)
            continue

        consecutive_auth_failures = 0

        if not tweets:
            time.sleep(INTER_ACCOUNT_DELAY_S)
            continue

        if account.get("filter_required") or account.get("handle") == "zerohedge":
            tweets = filter_zerohedge(tweets)

        new_tweets = dedup_tweets(tweets, db)

        for tweet in new_tweets:
            try:
                ingest_tweet_to_pipeline(tweet, account, db)
                total_ingested += 1
            except Exception as e:
                logger.error("Failed to ingest tweet %s from @%s: %s", tweet.get("id"), handle, e)

        accounts_polled += 1
        now_iso = datetime.now(timezone.utc).isoformat()
        last_tweet_id = new_tweets[0]["id"] if new_tweets else None
        cursor.execute("""
            INSERT INTO x_scout_state (handle, last_poll_at, last_tweet_id, consecutive_errors)
            VALUES (?, ?, ?, 0)
            ON CONFLICT(handle) DO UPDATE SET
                last_poll_at = ?,
                last_tweet_id = COALESCE(?, last_tweet_id),
                consecutive_errors = 0
        """, (handle, now_iso, last_tweet_id, now_iso, last_tweet_id))
        db.commit()

        time.sleep(INTER_ACCOUNT_DELAY_S)

    db.close()
    logger.info("X Scout complete: polled %d accounts, ingested %d tweets", accounts_polled, total_ingested)
    return {"accounts_polled": accounts_polled, "tweets_ingested": total_ingested}

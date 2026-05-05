"""Rate limits for BogWizard posting. Conservative starting thresholds."""
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

DB_PATH = Path(__file__).resolve().parent.parent / "remi_intelligence.db"

RULES = {
    "max_tweets_per_day": 3,           # conservative: 3, not 4
    "min_hours_between_tweets": 4,      # conservative: 4h, not 3h
    "max_threads_per_week": 2,
    "velocity_threshold_pct": 200,      # conservative: 200%, not 150%
    "sentiment_drift_threshold_pp": 20, # conservative: 20pp, not 15pp
    "convergence_min_signals": 3,
    "convergence_always_post": True,
    "phase_transition_always_post": True,
}


def rate_limit_check(draft_type: str) -> tuple[bool, str]:
    """Returns (allowed, reason). Bypasses caps for convergence + phase_transition."""
    if draft_type in ("convergence", "phase_transition"):
        return True, f"{draft_type} bypasses rate limits"

    conn = sqlite3.connect(DB_PATH)
    try:
        now = datetime.now(timezone.utc)
        day_ago = (now - timedelta(days=1)).isoformat()
        hours_ago = (now - timedelta(hours=RULES["min_hours_between_tweets"])).isoformat()

        n_today = conn.execute(
            "SELECT COUNT(*) FROM bogwizard_drafts WHERE status='posted' AND posted_at > ?",
            (day_ago,)
        ).fetchone()[0]
        if n_today >= RULES["max_tweets_per_day"]:
            return False, f"daily cap {n_today}/{RULES['max_tweets_per_day']}"

        n_recent = conn.execute(
            "SELECT COUNT(*) FROM bogwizard_drafts WHERE status='posted' AND posted_at > ?",
            (hours_ago,)
        ).fetchone()[0]
        if n_recent > 0:
            return False, f"posted within last {RULES['min_hours_between_tweets']}h"

        return True, "ok"
    finally:
        conn.close()

"""
sector_velocity.py — Sector-level velocity and sentiment drift calculator

Runs every 4h alongside the theme push. Computes per-sector:
- 7d mention count, theme count, avg/max velocity
- Week-over-week acceleration detection
- Sentiment drift (bullish/bearish/mixed shift over time windows)
"""
import logging
import os
import sqlite3
from datetime import datetime

from dotenv import load_dotenv

load_dotenv(os.path.expanduser("~/remi-intelligence/.env"))
logger = logging.getLogger(__name__)

DB_PATH = os.getenv("DB_PATH", os.path.expanduser("~/remi-intelligence/remi_intelligence.db"))

SECTORS = [
    "geopolitical", "macro", "fed", "credit", "energy",
    "metals", "agriculture", "crypto", "ai", "equities", "fiscal", "fx",
]


def calculate_sentiment_drift(conn, sector: str) -> dict:
    """
    Calculate sentiment distribution for a sector across two time windows.
    Returns current vs prior sentiment mix and drift classification.
    """
    # Current 7-day window
    current = conn.execute("""
        SELECT
            COUNT(*) as total,
            SUM(CASE WHEN dt.sentiment = 'bullish' THEN 1 ELSE 0 END) as bullish,
            SUM(CASE WHEN dt.sentiment = 'bearish' THEN 1 ELSE 0 END) as bearish,
            SUM(CASE WHEN dt.sentiment = 'mixed' THEN 1 ELSE 0 END) as mixed,
            SUM(CASE WHEN dt.sentiment = 'neutral' THEN 1 ELSE 0 END) as neutral
        FROM document_themes dt
        JOIN themes t ON dt.theme_id = t.id
        WHERE t.sector = ?
          AND dt.extracted_at > datetime('now', '-7 days')
    """, (sector,)).fetchone()

    # Prior 7-day window (8-14 days ago)
    prior = conn.execute("""
        SELECT
            COUNT(*) as total,
            SUM(CASE WHEN dt.sentiment = 'bullish' THEN 1 ELSE 0 END) as bullish,
            SUM(CASE WHEN dt.sentiment = 'bearish' THEN 1 ELSE 0 END) as bearish
        FROM document_themes dt
        JOIN themes t ON dt.theme_id = t.id
        WHERE t.sector = ?
          AND dt.extracted_at BETWEEN datetime('now', '-14 days') AND datetime('now', '-7 days')
    """, (sector,)).fetchone()

    # Calculate percentages
    c_total = current[0] or 1  # avoid division by zero
    c_bull_pct = round((current[1] or 0) / c_total * 100, 1)
    c_bear_pct = round((current[2] or 0) / c_total * 100, 1)
    c_mixed_pct = round((current[3] or 0) / c_total * 100, 1)

    p_total = prior[0] or 1
    p_bull_pct = round((prior[1] or 0) / p_total * 100, 1)
    p_bear_pct = round((prior[2] or 0) / p_total * 100, 1)

    # Classify drift
    bull_shift = c_bull_pct - p_bull_pct
    bear_shift = c_bear_pct - p_bear_pct

    if bull_shift > 15 and bear_shift < -10:
        drift = "turning_bullish"
    elif bear_shift > 15 and bull_shift < -10:
        drift = "turning_bearish"
    elif bull_shift > 10 and bear_shift > 10:
        drift = "diverging"
    else:
        drift = "stable"

    return {
        "bullish_pct": c_bull_pct,
        "bearish_pct": c_bear_pct,
        "mixed_pct": c_mixed_pct,
        "prior_bullish_pct": p_bull_pct,
        "prior_bearish_pct": p_bear_pct,
        "drift": drift,
        "bull_shift": round(bull_shift, 1),
        "bear_shift": round(bear_shift, 1),
    }


def calculate_sector_velocity() -> list[dict]:
    """
    Run every 4h with theme push. Calculates per-sector:
    - 7-day mention count and theme count
    - 7-day-ago comparison (delta / acceleration)
    - Sentiment drift (bullish/bearish/mixed shift)

    Returns list of sector dicts for optional push payload use.
    """
    conn = sqlite3.connect(DB_PATH)
    results = []

    for sector in SECTORS:
        # Current 7-day window
        current = conn.execute("""
            SELECT COUNT(DISTINCT theme_key) as theme_count,
                   SUM(mention_count) as mention_count,
                   ROUND(AVG(velocity_score), 1) as avg_velocity,
                   MAX(velocity_score) as max_velocity
            FROM themes
            WHERE sector = ? AND last_seen_at > datetime('now', '-7 days')
              AND mention_count >= 2
        """, (sector,)).fetchone()

        # Top theme key
        top_theme = conn.execute("""
            SELECT theme_key FROM themes
            WHERE sector = ? AND last_seen_at > datetime('now', '-7 days')
              AND mention_count >= 2
            ORDER BY velocity_score DESC LIMIT 1
        """, (sector,)).fetchone()

        # Prior 7-day window (8-14 days ago)
        prior = conn.execute("""
            SELECT SUM(mention_count) as mention_count
            FROM themes
            WHERE sector = ?
              AND last_seen_at BETWEEN datetime('now', '-14 days') AND datetime('now', '-7 days')
              AND mention_count >= 2
        """, (sector,)).fetchone()

        current_mentions = current[1] or 0
        prior_mentions = prior[0] or 0

        # Sector acceleration detection
        if prior_mentions > 0:
            acceleration = (current_mentions - prior_mentions) / prior_mentions
        else:
            acceleration = 1.0 if current_mentions > 0 else 0.0

        if acceleration > 0.5:
            logger.info(f"SECTOR ACCELERATION: {sector} up {acceleration:.0%} week-over-week "
                        f"({prior_mentions} -> {current_mentions} mentions)")

        # Sentiment drift
        sentiment = calculate_sentiment_drift(conn, sector)

        # Classify status for push payload
        if acceleration > 0.5:
            status = "accelerating"
        elif acceleration < -0.2:
            status = "cooling"
        else:
            status = "stable"

        # Persist to sector_velocity table
        conn.execute("""
            INSERT INTO sector_velocity (
                sector, period_start, period_end,
                theme_count, mention_count, avg_velocity, max_velocity,
                top_theme_key, engagement_total,
                sentiment_bullish_pct, sentiment_bearish_pct, sentiment_mixed_pct,
                sentiment_drift, prior_bullish_pct, prior_bearish_pct
            )
            VALUES (?, datetime('now', '-7 days'), datetime('now'),
                    ?, ?, ?, ?,
                    ?, 0,
                    ?, ?, ?,
                    ?, ?, ?)
            ON CONFLICT(sector, period_start) DO UPDATE SET
                mention_count = excluded.mention_count,
                theme_count = excluded.theme_count,
                avg_velocity = excluded.avg_velocity,
                max_velocity = excluded.max_velocity,
                top_theme_key = excluded.top_theme_key,
                sentiment_bullish_pct = excluded.sentiment_bullish_pct,
                sentiment_bearish_pct = excluded.sentiment_bearish_pct,
                sentiment_mixed_pct = excluded.sentiment_mixed_pct,
                sentiment_drift = excluded.sentiment_drift,
                prior_bullish_pct = excluded.prior_bullish_pct,
                prior_bearish_pct = excluded.prior_bearish_pct
        """, (
            sector,
            current[0] or 0, current_mentions, current[2] or 0, current[3] or 0,
            top_theme[0] if top_theme else None,
            sentiment["bullish_pct"], sentiment["bearish_pct"], sentiment["mixed_pct"],
            sentiment["drift"], sentiment["prior_bullish_pct"], sentiment["prior_bearish_pct"],
        ))

        results.append({
            "sector": sector,
            "mentions_7d": current_mentions,
            "mentions_prior_7d": prior_mentions,
            "acceleration": round(acceleration, 2),
            "theme_count": current[0] or 0,
            "avg_velocity": current[2] or 0,
            "max_velocity": current[3] or 0,
            "top_theme": top_theme[0] if top_theme else None,
            "status": status,
            "sentiment": {
                "bullish_pct": sentiment["bullish_pct"],
                "bearish_pct": sentiment["bearish_pct"],
                "mixed_pct": sentiment["mixed_pct"],
                "drift": sentiment["drift"],
                "bull_shift": sentiment["bull_shift"],
                "bear_shift": sentiment["bear_shift"],
            },
        })

    conn.commit()
    conn.close()
    logger.info(f"Sector velocity calculated for {len(results)} sectors")
    return results

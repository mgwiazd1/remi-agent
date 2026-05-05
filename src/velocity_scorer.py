"""
Narrative Velocity Scorer — Theme Acceleration Detection

Tracks theme mention velocity across ingested documents.
Scores themes 0-100 based on:
  - Recency (7-day half-life decay)
  - Tier weighting (Tier 1 feeds weighted 1.0, Tier 2 = 0.8, etc.)
  - Mention count acceleration
  - GLI regime context (different regimes have different velocity baselines)
"""
import logging
import os
import sqlite3
import math
from datetime import datetime, timedelta
from typing import Optional, Dict, List, Tuple
from dataclasses import dataclass
from dotenv import load_dotenv

load_dotenv(os.path.expanduser("~/remi-intelligence/.env"))
logger = logging.getLogger(__name__)

DB_PATH = os.getenv("DB_PATH", os.path.expanduser("~/remi-intelligence/remi_intelligence.db"))
VELOCITY_FLAG_THRESHOLD = float(os.getenv("VELOCITY_FLAG_THRESHOLD", 15.0))
RECENCY_HALF_LIFE_DAYS = 7
TIER_WEIGHTS = {1: 1.0, 2: 0.8, 3: 0.5, 4: 0.2}


def engagement_multiplier(retweets: int, views: int, likes: int = 0) -> float:
    """
    Boost velocity contribution based on social engagement.

    Tiers:
      - Viral (1000+ RTs or 100K+ views): 3.0x
      - High engagement (100+ RTs or 10K+ views): 2.0x
      - Moderate (10+ RTs or 1K+ views): 1.5x
      - Normal: 1.0x (no boost)

    This is a multiplier on the existing tier_weight * recency_weight contribution.
    """
    if retweets >= 1000 or views >= 100_000:
        return 3.0
    elif retweets >= 100 or views >= 10_000:
        return 2.0
    elif retweets >= 10 or views >= 1_000:
        return 1.5
    return 1.0


@dataclass
class VelocityScore:
    """Theme velocity analysis result"""
    theme_key: str
    theme_label: str
    velocity_score: float  # 0-100
    mention_count: int
    last_seen_at: str
    regime_context: Optional[str] = None
    flagged: bool = False  # True if velocity > threshold
    acceleration: Optional[float] = None  # velocity change vs 7d average
    tier_weight: float = 1.0
    
    def to_dict(self) -> Dict:
        return {
            "theme_key": self.theme_key,
            "theme_label": self.theme_label,
            "velocity_score": round(self.velocity_score, 2),
            "mention_count": self.mention_count,
            "last_seen_at": self.last_seen_at,
            "regime_context": self.regime_context,
            "flagged": self.flagged,
            "acceleration": round(self.acceleration, 2) if self.acceleration else None,
            "tier_weight": self.tier_weight,
        }


def compute_velocity_score(
    theme_key: str,
    mention_count: int,
    last_seen_at: datetime,
    tier: int = 1,
    regime_context: Optional[str] = None,
) -> float:
    """
    Compute velocity score (0-100) for a theme.
    
    Factors:
      - Recency: newer mentions score higher (7-day half-life)
      - Tier: Tier 1 feeds weighted 1.0, Tier 2 = 0.8, etc.
      - Mention count: more mentions = higher velocity
      - Regime: different regimes have different baselines
    """
    # Time decay: 7-day half-life
    now = datetime.utcnow()
    days_since = (now - last_seen_at).days
    recency_factor = math.pow(0.5, days_since / RECENCY_HALF_LIFE_DAYS)
    
    # Tier weighting
    tier_weight = TIER_WEIGHTS.get(tier, 0.5)
    
    # Mention count normalized to 0-30 (30+ mentions = maxed out at recency * tier)
    mention_factor = min(mention_count / 30.0, 1.0)
    
    # Regime-specific baseline (TURBULENCE themes start higher, CALM start lower)
    regime_multiplier = {
        "TURBULENCE": 1.2,
        "EXPANSION": 1.0,
        "CALM": 0.8,
        "TROUGH": 0.9,
        "SPECULATION": 1.1,
    }.get(regime_context or "EXPANSION", 1.0)
    
    # Composite score: 0-100
    velocity_score = (
        recency_factor * tier_weight * mention_factor * 100 * regime_multiplier
    )
    
    return min(velocity_score, 100.0)


def get_theme_velocity_history(
    conn: sqlite3.Connection, theme_key: str, days: int = 7
) -> Dict:
    """
    Get historical velocity data for a theme (last N days).
    Returns: {date: velocity_score}
    """
    cur = conn.cursor()
    start_date = datetime.utcnow() - timedelta(days=days)
    
    cur.execute("""
        SELECT DATE(last_seen_at) as date, SUM(mention_count) as total_mentions,
               COUNT(*) as unique_docs, AVG(CAST(tier AS FLOAT)) as avg_tier
        FROM themes
        WHERE theme_key = ? AND last_seen_at >= ?
        GROUP BY DATE(last_seen_at)
        ORDER BY date DESC
    """, (theme_key, start_date.isoformat()))
    
    history = {}
    rows = cur.fetchall()
    
    for date_str, total_mentions, unique_docs, avg_tier in rows:
        date_obj = datetime.fromisoformat(date_str)
        score = compute_velocity_score(
            theme_key, total_mentions, date_obj, tier=int(avg_tier or 1)
        )
        history[date_str] = {"score": score, "mentions": total_mentions}
    
    return history


def compute_acceleration(
    conn: sqlite3.Connection, theme_key: str
) -> Optional[float]:
    """
    Compute 7-day velocity acceleration.
    Returns: (current_velocity - 7d_avg_velocity) as percent change
    """
    cur = conn.cursor()
    
    # Current velocity (last 24h)
    now = datetime.utcnow()
    yesterday = now - timedelta(days=1)
    cur.execute("""
        SELECT mention_count, tier FROM themes
        WHERE theme_key = ? AND last_seen_at >= ?
        ORDER BY last_seen_at DESC
    """, (theme_key, yesterday.isoformat()))
    
    current_rows = cur.fetchall()
    if not current_rows:
        return None
    
    current_mentions = sum(r[0] for r in current_rows)
    current_score = compute_velocity_score(
        theme_key, current_mentions, now, tier=int(current_rows[0][1] or 1)
    )
    
    # 7-day average velocity (days 1-7 ago)
    seven_days_ago = now - timedelta(days=7)
    one_day_ago = now - timedelta(days=1)
    cur.execute("""
        SELECT AVG(mention_count) as avg_mentions, AVG(CAST(tier AS FLOAT)) as avg_tier
        FROM themes
        WHERE theme_key = ? AND last_seen_at BETWEEN ? AND ?
    """, (theme_key, seven_days_ago.isoformat(), one_day_ago.isoformat()))
    
    avg_row = cur.fetchone()
    if not avg_row or avg_row[0] is None:
        return None
    
    avg_mentions, avg_tier = avg_row
    avg_score = compute_velocity_score(
        theme_key, avg_mentions, one_day_ago, tier=int(avg_tier or 1)
    )
    
    if avg_score == 0:
        return None
    
    acceleration = ((current_score - avg_score) / avg_score) * 100
    return acceleration


def score_all_themes(conn: sqlite3.Connection) -> List[VelocityScore]:
    """
    Score all themes in the database and return sorted by velocity.
    """
    cur = conn.cursor()
    
    cur.execute("""
        SELECT id, theme_key, theme_label, mention_count, last_seen_at, tier, 
               gli_phase
        FROM themes
        ORDER BY mention_count DESC
    """)
    
    results = []
    for theme_id, theme_key, theme_label, mention_count, last_seen_at, tier, gli_phase in cur.fetchall():
        last_seen_dt = datetime.fromisoformat(last_seen_at)
        
        velocity = compute_velocity_score(
            theme_key, mention_count, last_seen_dt, tier=tier or 1, regime_context=gli_phase
        )
        
        acceleration = compute_acceleration(conn, theme_key)
        
        flagged = velocity > VELOCITY_FLAG_THRESHOLD
        
        score = VelocityScore(
            theme_key=theme_key,
            theme_label=theme_label,
            velocity_score=velocity,
            mention_count=mention_count,
            last_seen_at=last_seen_at,
            regime_context=gli_phase,
            flagged=flagged,
            acceleration=acceleration,
            tier_weight=TIER_WEIGHTS.get(tier or 1, 0.5),
        )
        
        results.append(score)
    
    # Sort by velocity descending
    results.sort(key=lambda x: x.velocity_score, reverse=True)
    return results


def get_flagged_themes(conn: sqlite3.Connection, threshold: Optional[float] = None) -> List[VelocityScore]:
    """
    Get all themes exceeding velocity threshold.
    Useful for autonomous alert triggering.
    """
    threshold = threshold or VELOCITY_FLAG_THRESHOLD
    all_scores = score_all_themes(conn)
    return [s for s in all_scores if s.velocity_score > threshold]


def update_theme_velocity_in_db(
    conn: sqlite3.Connection, theme_key: str, velocity_score: float
) -> None:
    """
    Store computed velocity score in database for tracking.
    """
    cur = conn.cursor()
    now = datetime.utcnow().isoformat()
    
    cur.execute("""
        CREATE TABLE IF NOT EXISTS theme_velocity_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            theme_key TEXT NOT NULL,
            velocity_score REAL,
            recorded_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    
    cur.execute("""
        INSERT INTO theme_velocity_history (theme_key, velocity_score, recorded_at)
        VALUES (?, ?, ?)
    """, (theme_key, velocity_score, now))
    
    conn.commit()
    logger.info(f"Velocity score recorded: {theme_key} = {velocity_score:.2f}")


if __name__ == "__main__":
    # Test: compute velocity for all themes
    logging.basicConfig(level=logging.INFO)
    try:
        conn = sqlite3.connect(DB_PATH)
        scores = score_all_themes(conn)
        
        print(f"\n=== Theme Velocity Report (Top 10) ===")
        print(f"Threshold for flagging: {VELOCITY_FLAG_THRESHOLD}\n")
        
        for score in scores[:10]:
            flag_indicator = "🚨 FLAGGED" if score.flagged else "  "
            print(f"{flag_indicator} | {score.theme_label:40} | {score.velocity_score:6.2f} | "
                  f"{score.mention_count:3d} mentions | {score.regime_context or 'N/A':12}")
        
        flagged = get_flagged_themes(conn)
        print(f"\n{len(flagged)} themes flagged above threshold ({VELOCITY_FLAG_THRESHOLD})")
        
        conn.close()
    except Exception as e:
        logger.error(f"Failed to score themes: {e}")

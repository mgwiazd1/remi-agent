"""
aestima_push.py — Push Remi narrative intelligence to Aestima
Two channels:
  1. Theme velocity snapshot (top 20 trending themes, every 4h)
  2. Convergence alerts (when 3+ velocity signals align, event-driven)
"""
import logging
import os
import sqlite3
from datetime import datetime

import httpx
from dotenv import load_dotenv

load_dotenv(os.path.expanduser("~/remi-intelligence/.env"))
logger = logging.getLogger(__name__)

DB_PATH = os.getenv("DB_PATH", os.path.expanduser("~/remi-intelligence/remi_intelligence.db"))
AESTIMA_BASE = os.getenv("AESTIMA_BASE_URL", "https://aestima.ai")
AGENT_KEY = os.getenv("AESTIMA_AGENT_KEY", "")


def push_theme_velocity_to_aestima() -> bool:
    """Push sector-balanced top themes to Aestima.
    
    Strategy:
    1. Top 2 per sector (guarantees diversity)
    2. Fill remaining slots by raw velocity_score across all sectors
    3. Cap at 20 total
    """
    try:
        themes = get_sector_balanced_themes(DB_PATH, max_total=20)
    except Exception as e:
        logger.error(f"Failed to query themes for Aestima push: {e}")
        return False

    if not themes:
        logger.info("No themes to push to Aestima")
        return True

    try:
        resp = httpx.post(
            f"{AESTIMA_BASE}/api/agent/remi-intel/themes",
            json={"themes": themes, "pushed_at": datetime.utcnow().isoformat()},
            headers={"X-Agent-Key": AGENT_KEY},
            timeout=30,
        )
        if resp.status_code == 200:
            logger.info(f"Pushed {len(themes)} themes to Aestima")
            return True
        else:
            logger.warning(f"Aestima theme push failed: HTTP {resp.status_code} — {resp.text[:200]}")
    except Exception as e:
        logger.error(f"Aestima theme push error: {e}")
    return False


def get_sector_balanced_themes(db_path: str, max_total: int = 20) -> list:
    """
    Pull top themes balanced across sectors.
    
    1. Get top 2 per sector (guarantees diversity)
    2. Fill remaining slots by raw velocity_score across all sectors
    3. Cap at max_total
    """
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    
    sectors = ["geopolitical", "macro", "fed", "credit", "energy",
               "metals", "agriculture", "crypto", "ai", "equities", "fiscal", "fx"]
    
    selected_keys = set()
    results = []
    
    # Step 1: top 2 per sector
    for sector in sectors:
        rows = conn.execute("""
            SELECT theme_key AS label, theme_label AS display_name,
                   mention_count AS mentions_7d,
                   last_seen_at AS latest_mention, velocity_score, sector
            FROM themes 
            WHERE sector = ? 
              AND last_seen_at > datetime('now', '-7 days')
              AND mention_count >= 2
            ORDER BY velocity_score DESC 
            LIMIT 2
        """, (sector,)).fetchall()
        
        for r in rows:
            results.append(dict(r))
            selected_keys.add(r["label"])
    
    # Step 2: fill remaining with top velocity across all sectors
    remaining = max_total - len(results)
    if remaining > 0 and selected_keys:
        placeholders = ",".join("?" * len(selected_keys))
        fillers = conn.execute(f"""
            SELECT theme_key AS label, theme_label AS display_name,
                   mention_count AS mentions_7d,
                   last_seen_at AS latest_mention, velocity_score, sector
            FROM themes
            WHERE last_seen_at > datetime('now', '-7 days')
              AND mention_count >= 2
              AND theme_key NOT IN ({placeholders})
            ORDER BY velocity_score DESC
            LIMIT ?
        """, (*selected_keys, remaining)).fetchall()
        
        results.extend(dict(r) for r in fillers)
    elif remaining > 0:
        # No sector picks yet — just get top by velocity
        fillers = conn.execute("""
            SELECT theme_key AS label, theme_label AS display_name,
                   mention_count AS mentions_7d,
                   last_seen_at AS latest_mention, velocity_score, sector
            FROM themes
            WHERE last_seen_at > datetime('now', '-7 days')
              AND mention_count >= 2
            ORDER BY velocity_score DESC
            LIMIT ?
        """, (remaining,)).fetchall()
        results.extend(dict(r) for r in fillers)
    
    conn.close()
    return results[:max_total]


def push_convergence_to_aestima(convergence_data: dict) -> bool:
    """Push convergence event to Aestima."""
    try:
        resp = httpx.post(
            f"{AESTIMA_BASE}/api/agent/remi-intel/convergence",
            json={
                "signal_count": convergence_data["count"],
                "signals": convergence_data["signals"],
                "direction": convergence_data["direction"],
                "detected_at": datetime.utcnow().isoformat(),
            },
            headers={"X-Agent-Key": AGENT_KEY},
            timeout=30,
        )
        if resp.status_code == 200:
            logger.info(f"Pushed convergence ({convergence_data['count']} signals) to Aestima")
            return True
        else:
            logger.warning(f"Aestima convergence push failed: HTTP {resp.status_code}")
    except Exception as e:
        logger.error(f"Aestima convergence push error: {e}")
    return False

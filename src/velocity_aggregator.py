"""
Velocity Aggregator — Convergence Detection & Telegram Alerts
Monitors market_signals table for directional convergence
Sends alerts when 3+ signals accelerate in the same direction
"""
import logging
import os
import sqlite3
import json
from datetime import datetime, timedelta
from typing import Optional, Dict, List
from telegram_sender import send_investing_alert
import requests
from dotenv import load_dotenv

# Dashboard push integration
try:
    from dashboard_push import push_velocity_snapshot
    HAS_DASHBOARD = True
except ImportError:
    HAS_DASHBOARD = False

# Load environment
load_dotenv(os.path.expanduser("~/remi-intelligence/.env"))
logger = logging.getLogger(__name__)

DB_PATH = os.getenv("DB_PATH", os.path.expanduser("~/remi-intelligence/remi_intelligence.db"))
INVESTING_GROUP_CHAT_ID = os.getenv("INVESTING_GROUP_CHAT_ID", "-1003857050116")

# Chat IDs for velocity alerts (investing group only per routing rules)

# Alert dedup tracking - store last alert time in DB
LAST_ALERT_KEY = "velocity_last_alert"
ALERT_COOLDOWN_HOURS = 4


def init_velocity_alert_tracking():
    """Ensure alert tracking in DB"""
    try:
        conn = sqlite3.connect(DB_PATH)
        cur = conn.cursor()
        cur.execute("""
            CREATE TABLE IF NOT EXISTS velocity_meta (
                key TEXT PRIMARY KEY,
                value TEXT,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        conn.commit()
        conn.close()
    except Exception as e:
        logger.warning(f"Failed to init velocity_meta table: {e}")


def get_last_alert_time() -> Optional[datetime]:
    """Retrieve last alert timestamp from DB"""
    try:
        conn = sqlite3.connect(DB_PATH)
        cur = conn.cursor()
        cur.execute("SELECT value FROM velocity_meta WHERE key = ?", (LAST_ALERT_KEY,))
        row = cur.fetchone()
        conn.close()
        if row:
            return datetime.fromisoformat(row[0])
        return None
    except Exception as e:
        logger.warning(f"Failed to get last alert time: {e}")
        return None


def set_last_alert_time(ts: datetime):
    """Store alert timestamp in DB"""
    try:
        conn = sqlite3.connect(DB_PATH)
        cur = conn.cursor()
        cur.execute(
            "INSERT OR REPLACE INTO velocity_meta (key, value, updated_at) VALUES (?, ?, CURRENT_TIMESTAMP)",
            (LAST_ALERT_KEY, ts.isoformat())
        )
        conn.commit()
        conn.close()
    except Exception as e:
        logger.warning(f"Failed to set last alert time: {e}")


def get_latest_signals_snapshot() -> List[Dict]:
    """Fetch latest recorded value for each signal from market_signals table"""
    try:
        conn = sqlite3.connect(DB_PATH)
        cur = conn.cursor()
        # Get most recent record for each signal
        cur.execute("""
            SELECT signal_name, value, delta_24h, delta_48h, direction, rsi, recorded_at
            FROM market_signals
            WHERE recorded_at = (
                SELECT MAX(recorded_at) FROM market_signals ms2
                WHERE ms2.signal_name = market_signals.signal_name
            )
            AND recorded_at > datetime('now', '-7 days')
            ORDER BY signal_name
        """)
        rows = cur.fetchall()
        conn.close()
        
        signals = []
        for row in rows:
            signals.append({
                "signal_name": row[0],
                "value": row[1],
                "delta_24h": row[2],
                "delta_48h": row[3],
                "direction": row[4],
                "rsi": row[5],
                "recorded_at": row[6],
            })
        return signals
    except Exception as e:
        logger.error(f"Failed to fetch signals snapshot: {e}")
        return []


def count_convergence(signals: List[Dict]) -> Dict:
    """
    Analyze convergence in signals.
    Count signals by direction status (accelerating_up/down, reversing_up/down, drifting, stable)
    Returns count dict and convergence stats
    """
    direction_counts = {
        "accelerating_up": [],
        "accelerating_down": [],
        "reversing_up": [],
        "reversing_down": [],
        "drifting": [],
        "stable": [],
        "unknown": []
    }

    for sig in signals:
        direction = sig.get("direction", "drifting")
        # Handle unknown directions gracefully
        if direction not in direction_counts:
            direction = "unknown"
        direction_counts[direction].append(sig["signal_name"])
    
    # Find strongest convergence — filter out non-directional signals (unknown, stable, drifting)
    # Only consider: accelerating_up, accelerating_down, reversing_up, reversing_down
    directional_counts = {k: v for k, v in direction_counts.items() if k not in ("unknown", "stable", "drifting")}
    convergence_direction = None
    max_count = 0

    if directional_counts:
        max_count = max(len(v) for v in directional_counts.values())
        if max_count >= 3:
            for direction, signals_list in directional_counts.items():
                if len(signals_list) == max_count:
                    convergence_direction = direction
                    break
    
    return {
        "direction_counts": direction_counts,
        "max_count": max_count,
        "convergence_direction": convergence_direction,
        "has_convergence": max_count >= 3
    }


def send_telegram_alert(signals: List[Dict], convergence_stats: Dict):
    """
    Format and send convergence alert to Telegram
    Table format with signal summaries
    """
    
    # Build alert message
    lines = ["📊 VELOCITY CONVERGENCE ALERT"]
    lines.append("")
    
    # Table header
    lines.append("Signal | Current | 24h Δ | 48h Δ | Trend")
    lines.append("─" * 50)
    
    # Table rows
    for sig in signals:
        name = sig["signal_name"]
        value = f"{sig['value']:.2f}" if sig['value'] else "N/A"
        delta24 = f"{sig['delta_24h']:.4f}" if sig['delta_24h'] else "—"
        delta48 = f"{sig['delta_48h']:.4f}" if sig['delta_48h'] else "—"
        direction = sig['direction']
        
        # Truncate name for display
        name_display = name[:10]
        lines.append(f"{name_display:10} | {value:8} | {delta24:6} | {delta48:6} | {direction}")
    
    lines.append("")
    
    # Convergence summary
    conv_dir = convergence_stats.get("convergence_direction", "N/A")
    max_count = convergence_stats.get("max_count", 0)
    
    # Determine conviction level
    if max_count >= 4:
        conviction = "HIGH"
    elif max_count == 3:
        conviction = "MEDIUM"
    else:
        conviction = "LOW"
    
    lines.append(f"⚡️ {max_count}/{len(signals)} signals converging {conv_dir} — {conviction} CONVICTION")
    lines.append("")
    lines.append("🎯 Suggested: Review positioning")
    lines.append("📅 Signal window: 48-72 hours")
    
    message = "\n".join(lines)
    
    # Send via centralized sender
    success = send_investing_alert(message)
    if success:
        logger.info("Convergence alert sent to investing group")
    else:
        logger.error("Failed to send convergence alert")
    return success


def check_velocity_convergence() -> Dict:
    """
    Main convergence check function:
    1. Init tracking
    2. Fetch latest signals
    3. Count convergence
    4. Check alert cooldown
    5. Send alert if convergence >= 3 and cooldown passed
    """
    init_velocity_alert_tracking()
    
    result = {
        "timestamp": datetime.utcnow().isoformat(),
        "has_convergence": False,
        "convergence_direction": None,
        "signal_count": 0,
        "accelerating_count": 0,
        "alert_sent": False,
        "reason": None
    }
    
    # Fetch signals
    signals = get_latest_signals_snapshot()
    if not signals:
        result["reason"] = "No signals in database"
        logger.warning("No signals to analyze")
        return result
    
    result["signal_count"] = len(signals)
    
    # Count convergence
    convergence_stats = count_convergence(signals)
    result["has_convergence"] = convergence_stats["has_convergence"]
    result["convergence_direction"] = convergence_stats["convergence_direction"]
    result["accelerating_count"] = convergence_stats["max_count"]
    
    if not convergence_stats["has_convergence"]:
        result["reason"] = f"Max convergence {convergence_stats['max_count']}/5 (threshold: 3)"
        logger.info(f"No convergence detected: {result['reason']}")
        return result
    
    # Check cooldown
    last_alert = get_last_alert_time()
    now = datetime.utcnow()
    if last_alert:
        elapsed = now - last_alert
        if elapsed < timedelta(hours=ALERT_COOLDOWN_HOURS):
            result["reason"] = f"Alert cooldown active (last alert {elapsed.total_seconds()/3600:.1f}h ago)"
            logger.info(f"Cooldown active: {result['reason']}")
            return result
    
    # Send alert
    logger.info(f"Convergence detected: {convergence_stats['max_count']} signals in {convergence_stats['convergence_direction']}")
    if send_telegram_alert(signals, convergence_stats):
        set_last_alert_time(now)
        result["alert_sent"] = True
        result["reason"] = "Alert sent successfully"
    else:
        result["reason"] = "Alert send failed"
    
    # Push to dashboard (non-blocking)
    if HAS_DASHBOARD:
        try:
            level = "HIGH" if convergence_stats["max_count"] >= 4 else "MEDIUM" if convergence_stats["max_count"] == 3 else "LOW"
            push_velocity_snapshot(signals, convergence_stats["max_count"], level, result["alert_sent"])
        except Exception as e:
            logger.error(f"Dashboard push failed: {e}")

    # Push convergence to Aestima (non-blocking)
    try:
        from aestima_push import push_convergence_to_aestima
        converging_signals = [s["signal_name"] for s in signals if s.get("direction") == convergence_stats["convergence_direction"]]
        push_convergence_to_aestima({
            "count": convergence_stats["max_count"],
            "signals": converging_signals,
            "direction": convergence_stats["convergence_direction"],
        })
    except Exception as e:
        logger.error(f"Aestima convergence push failed: {e}")

    return result


def get_convergence_status() -> Dict:
    """Get current convergence status without sending alert"""
    signals = get_latest_signals_snapshot()
    if not signals:
        return {"status": "no_data"}
    
    convergence_stats = count_convergence(signals)
    return {
        "status": "convergent" if convergence_stats["has_convergence"] else "divergent",
        "direction": convergence_stats["convergence_direction"],
        "count": convergence_stats["max_count"],
        "total_signals": len(signals),
        "signals": signals
    }


if __name__ == "__main__":
    # Test run
    logging.basicConfig(level=logging.INFO)
    result = check_velocity_convergence()
    print(json.dumps(result, indent=2, default=str))

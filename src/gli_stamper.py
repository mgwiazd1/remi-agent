import urllib.request
import urllib.error
import json
import logging
import os
import sqlite3
import requests
from dataclasses import dataclass, field
from typing import Optional, List, Dict
from datetime import datetime, timezone, timedelta
from telegram_sender import send_investing_alert
from dotenv import load_dotenv

# Dashboard push integration
try:
    from dashboard_push import push_gli_phase
    HAS_DASHBOARD = True
except ImportError:
    HAS_DASHBOARD = False

# Load environment
load_dotenv(os.path.expanduser("~/remi-intelligence/.env"))

logger = logging.getLogger(__name__)

AESTIMA_BASE = os.getenv("AESTIMA_BASE_URL", "http://192.168.1.198:8000")
ACCESS_TOKEN=os.getenv("AESTIMA_AGENT_KEY", "")
INVESTING_GROUP_CHAT_ID = os.getenv("INVESTING_GROUP_CHAT_ID", "-1003857050116")
DB_PATH = os.getenv("DB_PATH", os.path.expanduser("~/remi-intelligence/remi_intelligence.db"))

# 24-hour suppression window for phase transition alerts
PHASE_ALERT_SUPPRESS_HOURS = 24


@dataclass
class VelocitySignal:
    """Individual velocity signal from Aestima"""
    signal_name: str
    value: float
    delta_24h: Optional[float] = None
    delta_48h: Optional[float] = None
    direction: Optional[str] = None
    recorded_at: Optional[str] = None


@dataclass
class GLIStamp:
    gli_phase: Optional[str] = None
    gli_value_bn: Optional[float] = None
    steno_regime: Optional[str] = None
    fiscal_score: Optional[float] = None
    transition_risk: Optional[float] = None
    raw_context: Optional[dict] = None
    stamp_error: Optional[str] = None
    velocity_signals: List[VelocitySignal] = field(default_factory=list)
    phase_changed: bool = False
    phase_change_context: Optional[str] = None

    def to_dict(self):
        return {
            "gli_phase": self.gli_phase,
            "gli_value_bn": self.gli_value_bn,
            "steno_regime": self.steno_regime,
            "fiscal_score": self.fiscal_score,
            "transition_risk": self.transition_risk,
            "velocity_signals": len(self.velocity_signals),
            "phase_changed": self.phase_changed,
        }

    def for_prompt(self):
        if not self.gli_phase or self.gli_value_bn is None:
            return f"GLI context unavailable (error: {self.stamp_error})"
        return (
            f"Phase: {self.gli_phase} | "
            f"GLI: ${self.gli_value_bn:.0f}B | "
            f"Regime: {self.steno_regime} | "
            f"Fiscal: {self.fiscal_score}/10 | "
            f"Transition Risk: {self.transition_risk}/10"
        )

    def has_phase_change(self) -> bool:
        """Check if GLI phase has changed"""
        return self.phase_changed


# ---------------------------------------------------------------------------
# Phase state tracking (DB-backed)
# ---------------------------------------------------------------------------

def _ensure_phase_state_table():
    """Create gli_phase_state table if it doesn't exist"""
    conn = sqlite3.connect(DB_PATH)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS gli_phase_state (
            id INTEGER PRIMARY KEY CHECK (id = 1),
            last_known_phase TEXT,
            phase_since TEXT NOT NULL,
            last_alert_sent TEXT
        )
    """)
    # Seed with empty row if not present
    row = conn.execute("SELECT id FROM gli_phase_state WHERE id = 1").fetchone()
    if not row:
        conn.execute("""
            INSERT INTO gli_phase_state (id, last_known_phase, phase_since, last_alert_sent)
            VALUES (1, NULL, ?, NULL)
        """, (datetime.now(timezone.utc).isoformat(),))
    conn.commit()
    conn.close()


def _get_phase_state() -> dict:
    """Read current phase state from DB. Returns {last_known_phase, phase_since, last_alert_sent}"""
    _ensure_phase_state_table()
    conn = sqlite3.connect(DB_PATH)
    row = conn.execute(
        "SELECT last_known_phase, phase_since, last_alert_sent FROM gli_phase_state WHERE id = 1"
    ).fetchone()
    conn.close()
    if row:
        return {"last_known_phase": row[0], "phase_since": row[1], "last_alert_sent": row[2]}
    return {"last_known_phase": None, "phase_since": None, "last_alert_sent": None}


def _update_phase_state(phase: str, alert_sent: bool = False):
    """Update phase state. If alert_sent=True, also stamp last_alert_sent."""
    _ensure_phase_state_table()
    phase = phase.lower() if phase else phase
    now = datetime.now(timezone.utc).isoformat()
    conn = sqlite3.connect(DB_PATH)
    if alert_sent:
        conn.execute("""
            UPDATE gli_phase_state
            SET last_known_phase = ?, phase_since = ?, last_alert_sent = ?
            WHERE id = 1
        """, (phase, now, now))
    else:
        conn.execute("""
            UPDATE gli_phase_state
            SET last_known_phase = ?, phase_since = ?
            WHERE id = 1
        """, (phase, now))
    conn.commit()
    conn.close()


def _is_within_suppression_window() -> bool:
    """Check if we're within 24h of the last phase transition alert"""
    state = _get_phase_state()
    if not state["last_alert_sent"]:
        return False
    try:
        last = datetime.fromisoformat(state["last_alert_sent"])
        if last.tzinfo is None:
            last = last.replace(tzinfo=timezone.utc)
        elapsed = datetime.now(timezone.utc) - last
        return elapsed < timedelta(hours=PHASE_ALERT_SUPPRESS_HOURS)
    except (ValueError, TypeError):
        return False


def get_phase_brief_line() -> str:
    """Return a single line for the morning brief about GLI phase.

    Examples:
      "GLI Phase: CALM (since 4/10/26)"
      "GLI Phase: CALM ← was TURBULENCE (changed 4/10/26)"
    """
    state = _get_phase_state()
    phase = state["last_known_phase"]
    since_str = state["phase_since"]

    if not phase:
        return "GLI Phase: unknown"

    # Format the since date compactly
    since_display = ""
    if since_str:
        try:
            dt = datetime.fromisoformat(since_str)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            since_display = dt.strftime("%-m/%-d/%y")
        except (ValueError, TypeError):
            since_display = since_str[:10]

    # If phase_since is recent (within 7 days), show the transition notation
    is_recent = False
    if since_str:
        try:
            dt = datetime.fromisoformat(since_str)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            is_recent = (datetime.now(timezone.utc) - dt).days < 7
        except (ValueError, TypeError):
            pass

    if is_recent and state.get("last_alert_sent"):
        return f"GLI Phase: {phase} (changed {since_display})"
    return f"GLI Phase: {phase} (since {since_display})"


# ---------------------------------------------------------------------------
# Velocity signal storage
# ---------------------------------------------------------------------------

def _store_velocity_signals(signals: List[VelocitySignal]):
    """Store velocity signals from Aestima in market_signals table"""
    if not signals:
        return
    
    try:
        conn = sqlite3.connect(DB_PATH)
        cur = conn.cursor()
        
        # Ensure table exists
        cur.execute("""
            CREATE TABLE IF NOT EXISTS market_signals (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                signal_name TEXT NOT NULL,
                value REAL,
                delta_24h REAL,
                delta_48h REAL,
                direction TEXT,
                rsi REAL,
                gli_phase TEXT,
                gli_value_bn REAL,
                recorded_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        
        timestamp = datetime.now(timezone.utc).isoformat()
        for sig in signals:
            try:
                logger.info(f"Aestima velocity signal: signal_name={sig.signal_name}, value={sig.value}, delta_24h={sig.delta_24h}, delta_48h={sig.delta_48h}, direction={sig.direction}")
                
                cur.execute("""
                    INSERT INTO market_signals
                    (signal_name, value, delta_24h, delta_48h, direction, recorded_at)
                    VALUES (?, ?, ?, ?, ?, ?)
                """, (
                    sig.signal_name,
                    sig.value,
                    sig.delta_24h,
                    sig.delta_48h,
                    sig.direction,
                    timestamp
                ))
            except Exception as e:
                logger.warning(f"Failed to store signal {sig.signal_name}: {e}")
        
        conn.commit()
        conn.close()
        logger.info(f"Stored {len(signals)} Aestima velocity signals in market_signals table")
    except Exception as e:
        logger.error(f"Failed to store velocity signals: {e}")


# ---------------------------------------------------------------------------
# Phase transition alert (genuine transitions only, 24h suppressed)
# ---------------------------------------------------------------------------

def _send_phase_transition_alert(old_phase: str, new_phase: str, context: str):
    """Send a phase transition alert ONLY for genuine old!=new transitions.

    Protected by 24h suppression window — duplicate transitions are silently dropped.
    """
    # Hard gate: same phase = not a transition, never alert
    if old_phase == new_phase:
        logger.info(f"Phase transition suppressed: old==new ({new_phase})")
        return

    # 24h dedup check
    if _is_within_suppression_window():
        logger.info(f"Phase transition alert suppressed (within 24h of last alert): {old_phase} -> {new_phase}")
        return

    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    message = (
        f"\U0001f4cd GLI PHASE TRANSITION\n"
        f"{old_phase} -> {new_phase}\n"
        f"Time: {timestamp}\n"
        f"Context: {context}"
    )
    if send_investing_alert(message):
        logger.info(f"GLI phase transition alert sent: {old_phase} -> {new_phase}")
        _update_phase_state(new_phase, alert_sent=True)
    else:
        logger.error("Failed to send GLI phase transition alert")



def _fetch_velocity_deltas() -> tuple:
    """Fetch velocity signals from Aestima delta endpoint"""
    try:
        req = urllib.request.Request(
            f"{AESTIMA_BASE}/api/agent/context/delta",
            headers={"X-Agent-Key": ACCESS_TOKEN}
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode())
        
        velocity_signals = []
        if "velocity_signals" in data:
            for sig in data.get("velocity_signals", []):
                try:
                    signal_name = (
                        sig.get("signal_name") or
                        sig.get("name") or
                        sig.get("metric") or
                        sig.get("signal")
                    )
                    
                    if not signal_name or signal_name == "unknown":
                        logger.warning(f"Skipping unnamed velocity signal: {sig}")
                        continue
                    
                    velocity_signals.append(VelocitySignal(
                        signal_name=signal_name,
                        value=float(sig.get("value") or sig.get("current") or 0),
                        delta_24h=sig.get("delta_24h"),
                        delta_48h=sig.get("delta_48h"),
                        direction=sig.get("direction"),
                        recorded_at=sig.get("recorded_at")
                    ))
                except Exception as e:
                    logger.warning(f"Failed to parse velocity signal: {e}")
        
        phase_changed = data.get("phase_changed", False)
        return velocity_signals, phase_changed, data
    except Exception as e:
        logger.warning(f"Velocity delta fetch failed (non-blocking): {e}")
        return [], False, None


# ---------------------------------------------------------------------------
# Main entry: fetch_gli_stamp
# ---------------------------------------------------------------------------

def fetch_gli_stamp() -> GLIStamp:
    try:
        # Fetch main context
        req = urllib.request.Request(
            f"{AESTIMA_BASE}/api/agent/context",
            headers={"X-Agent-Key": ACCESS_TOKEN}
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode())
        
        current_phase = data.get("gli_phase")
        
        # Create base stamp (normalize phase to lowercase for consistency)
        stamp = GLIStamp(
            gli_phase=current_phase.lower() if current_phase else current_phase,
            gli_value_bn=data.get("gli_value_trn"),
            steno_regime=data.get("steno_regime"),
            fiscal_score=data.get("fiscal_dominance_score"),
            transition_risk=data.get("transition_risk_score"),
            raw_context=data,
        )
        
        # Fetch velocity deltas (non-blocking if fails)
        velocity_signals, delta_phase_changed, delta_data = _fetch_velocity_deltas()
        
        if velocity_signals:
            stamp.velocity_signals = velocity_signals
            _store_velocity_signals(velocity_signals)
        
        # Determine genuine phase transition using DB state (NOT delta API alone)
        state = _get_phase_state()
        db_phase = state["last_known_phase"]

        # Normalize for comparison — DB is always lowercase, Aestima may vary
        current_phase_lower = current_phase.lower() if current_phase else current_phase

        if db_phase is None:
            # First run ever — seed the state, no alert
            logger.info(f"GLI phase state initialized: {current_phase_lower}")
            _update_phase_state(current_phase_lower, alert_sent=False)
        elif db_phase.lower() == "unknown" or current_phase_lower == "unknown":
            # "unknown" is a data-freshness gap, not a phase change
            logger.info(f"GLI phase unresolved (db={db_phase}, current={current_phase_lower}) — skipping transition check")
            _update_phase_state(current_phase_lower, alert_sent=False)
        elif db_phase != current_phase_lower:
            # GENUINE transition: DB says X, Aestima says Y (case-normalized)
            stamp.phase_changed = True
            context_brief = ""
            if delta_data:
                stamp.phase_change_context = delta_data.get("context", "")
                context_brief = delta_data.get("context", "No context provided")

            logger.warning(f"PRIORITY: GLI phase changed — {db_phase} -> {current_phase_lower}")

            # Send alert (protected by 24h suppression + old!=new check inside)
            _send_phase_transition_alert(db_phase, current_phase_lower, context_brief)

            # Update DB state to new phase
            if not _is_within_suppression_window():
                _update_phase_state(current_phase_lower, alert_sent=True)

            # Invalidate regime-sensitive module cache (M03, M06, M09)
            try:
                from aestima_module_cache import invalidate_on_phase_change
                invalidate_on_phase_change(current_phase_lower)
                logger.info(f"Module cache invalidated on phase change to {current_phase_lower}")
            except Exception as e:
                logger.warning(f"Module cache invalidation failed: {e}")
        else:
            # Same phase — no transition, ensure DB is current
            logger.debug(f"GLI phase unchanged: {current_phase}")
        
        # Push to dashboard (non-blocking)
        if HAS_DASHBOARD:
            try:
                stress_signals = stamp.raw_context.get("stress_signals", {}) if stamp.raw_context else {}
                push_gli_phase(
                    gli_phase=stamp.gli_phase,
                    gli_value_trn=stamp.gli_value_bn,
                    transition_risk_score=stamp.transition_risk,
                    fiscal_dominance_score=stamp.fiscal_score,
                    steno_regime=stamp.steno_regime,
                    hy_spread_bps=stress_signals.get("hy_spread_bps"),
                    ig_spread_bps=stress_signals.get("ig_spread_bps"),
                    sofr_spread_bps=stress_signals.get("sofr_spread_bps"),
                    yield_curve_10_2=stress_signals.get("yield_curve_10_2"),
                    phase_changed=stamp.phase_changed,
                    previous_phase=db_phase
                )
            except Exception as e:
                logger.error(f"Dashboard push failed: {e}")
        
        return stamp
    except Exception as e:
        logger.warning(f"GLI stamp fetch failed: {e}")
        return GLIStamp(stamp_error=str(e))


def health_check() -> bool:
    try:
        req = urllib.request.Request(
            f"{AESTIMA_BASE}/api/health",
            headers={"X-Agent-Key": ACCESS_TOKEN}
        )
        with urllib.request.urlopen(req, timeout=5) as resp:
            return resp.status == 200
    except Exception:
        return False

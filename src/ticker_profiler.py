"""
ticker_profiler.py — On-demand GLM synthesis of everything Remi knows about a ticker.

Called by the dashboard API when Aestima requests a profile for any ticker.
Queries: ticker_signals (book + X scout), document_themes (articles/PDFs),
         ticker_conviction (accumulated score), GLI context via gli_stamper.
Stores result in ticker_profiles table with 7-day TTL.
Returns cached profile if fresh, generates new one if stale/missing.
"""

import os
import sys
import json
import sqlite3
import logging
import httpx
from datetime import datetime, timedelta, timezone
from pathlib import Path
from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent.parent / ".env", override=True)
logger = logging.getLogger(__name__)

DB_PATH = Path(__file__).resolve().parent.parent / "remi_intelligence.db"
GLM_API_KEY = os.getenv("GLM_API_KEY", "")
GLM_BASE_URL = os.getenv("GLM_BASE_URL", "https://api.z.ai/api/paas/v4")
GLM_MODEL = "glm-5"
PROFILE_TTL_DAYS = 7
TIMEOUT = 45


PROFILE_SYSTEM = """You are Remi, a macro-aware investment research agent with deep knowledge
of financial markets, investment frameworks (Lynch, Marks, Dalio, Howell, Lyn Alden),
and current macro regime dynamics.

Given intelligence signals about a ticker, write a 3-4 sentence investment profile covering:
1. What investment frameworks or book knowledge says about this ticker or its sector
2. What recent narrative signals and article themes suggest
3. How the current GLI macro regime aligns or conflicts with this position

Be specific — cite actual frameworks, data points, themes. No fluff. No financial advice.
Do not say buy/sell. Output ONLY the profile text, nothing else."""


def _db_connect():
    """Connect to intelligence DB with WAL mode."""
    conn = sqlite3.connect(str(DB_PATH), timeout=10)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.row_factory = sqlite3.Row
    return conn


def ensure_profiles_table():
    conn = _db_connect()
    conn.execute("""
        CREATE TABLE IF NOT EXISTS ticker_profiles (
            ticker TEXT PRIMARY KEY,
            profile_text TEXT NOT NULL,
            signal_count INTEGER DEFAULT 0,
            gli_phase TEXT,
            created_at TEXT NOT NULL,
            expires_at TEXT NOT NULL
        )
    """)
    conn.commit()
    conn.close()


def get_cached_profile(ticker: str) -> dict | None:
    """Return cached profile if still fresh, else None."""
    conn = _db_connect()
    row = conn.execute(
        "SELECT * FROM ticker_profiles WHERE ticker = ?", (ticker,)
    ).fetchone()
    conn.close()
    if not row:
        return None
    expires = datetime.fromisoformat(row["expires_at"])
    if datetime.now(timezone.utc) > expires:
        return None
    return dict(row)


def gather_ticker_signals(ticker: str) -> list[dict]:
    """Pull book-derived and X scout signals for this ticker."""
    conn = _db_connect()
    rows = conn.execute("""
        SELECT signal_type, content, conviction_weight, created_at
        FROM ticker_signals
        WHERE ticker = ?
        ORDER BY conviction_weight DESC, created_at DESC
        LIMIT 15
    """, (ticker,)).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def gather_document_themes(ticker: str) -> list[dict]:
    """Pull article/PDF themes that mention this ticker."""
    conn = _db_connect()
    rows = conn.execute("""
        SELECT dt.tickers_mentioned, dt.weighted_score, dt.facts,
               t.theme_label, t.velocity_score
        FROM document_themes dt
        JOIN themes t ON dt.theme_id = t.id
        WHERE dt.tickers_mentioned LIKE ?
        ORDER BY dt.weighted_score DESC
        LIMIT 10
    """, (f"%{ticker}%",)).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def gather_conviction(ticker: str) -> dict | None:
    """Pull aggregated conviction score if exists."""
    conn = _db_connect()
    row = conn.execute(
        "SELECT * FROM ticker_conviction WHERE ticker = ?", (ticker,)
    ).fetchone()
    conn.close()
    return dict(row) if row else None


def get_gli_phase() -> str:
    """Get current GLI phase — live from Aestima, fallback to DB cache."""
    # Try live fetch via gli_stamper
    try:
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        from gli_stamper import fetch_gli_stamp
        stamp = fetch_gli_stamp()
        if stamp and stamp.gli_phase and stamp.gli_phase != "unknown":
            return stamp.gli_phase
    except Exception as e:
        logger.debug("GLI live fetch failed: %s", e)

    # Fallback: DB cache
    try:
        conn = _db_connect()
        row = conn.execute(
            "SELECT last_known_phase FROM gli_phase_state WHERE id = 1"
        ).fetchone()
        conn.close()
        if row and row["last_known_phase"]:
            return row["last_known_phase"]
    except Exception:
        pass

    return "UNKNOWN"


def synthesize_profile(
    ticker: str,
    signals: list[dict],
    themes: list[dict],
    conviction: dict | None,
    gli_phase: str,
) -> str:
    """Call GLM to synthesize all available intelligence into a profile."""
    if not GLM_API_KEY:
        logger.warning("GLM_API_KEY not set")
        return ""

    # Build context block
    lines = [f"Ticker: {ticker}", f"Current GLI Phase: {gli_phase}"]

    if conviction:
        lines.append(f"Accumulated conviction score: {conviction.get('conviction_score', 0):.1f}")
        if conviction.get("sector"):
            lines.append(f"Sector: {conviction['sector']}")

    if signals:
        lines.append(f"\nBook & Signal Intelligence ({len(signals)} signals):")
        for s in signals[:10]:
            lines.append(
                f"  [{s.get('signal_type', '?')}] {s.get('content', '')} "
                f"(weight: {s.get('conviction_weight', 0):.1f})"
            )

    if themes:
        lines.append(f"\nNarrative Themes from Articles/PDFs ({len(themes)} matches):")
        for t in themes[:6]:
            lines.append(
                f"  {t.get('theme_label', '')} "
                f"(velocity: {t.get('velocity_score', 0):.1f})"
            )

    if not signals and not themes:
        lines.append("\nNo specific signals found — synthesize from GLI phase and sector frameworks.")

    context = "\n".join(lines)

    try:
        resp = httpx.post(
            f"{GLM_BASE_URL}/chat/completions",
            headers={
                "Authorization": f"Bearer {GLM_API_KEY}",
                "Content-Type": "application/json",
            },
            json={
                "model": GLM_MODEL,
                "messages": [
                    {"role": "system", "content": PROFILE_SYSTEM},
                    {"role": "user", "content": context},
                ],
                "max_tokens": 2000,
                "temperature": 0.3,
            },
            timeout=TIMEOUT,
        )
        resp.raise_for_status()
        data = resp.json()
        msg = data["choices"][0]["message"]
        profile = msg.get("content") or msg.get("reasoning_content") or ""
        return profile.strip()
    except Exception as e:
        logger.warning("GLM profile synthesis failed for %s: %s", ticker, e)
        return ""


def get_or_build_profile(ticker: str) -> dict:
    """
    Main entry point. Returns cached profile or builds a new one.

    Returns dict with:
      ticker, profile_text, signal_count, gli_phase, created_at, from_cache (bool)
    """
    ticker = ticker.upper()
    ensure_profiles_table()

    # Check cache first
    cached = get_cached_profile(ticker)
    if cached:
        return {**cached, "from_cache": True}

    # Gather all available intelligence
    signals = gather_ticker_signals(ticker)
    themes = gather_document_themes(ticker)
    conviction = gather_conviction(ticker)
    gli_phase = get_gli_phase()

    # Synthesize via GLM
    profile_text = synthesize_profile(ticker, signals, themes, conviction, gli_phase)

    if not profile_text:
        return {
            "ticker": ticker,
            "profile_text": "",
            "signal_count": 0,
            "gli_phase": gli_phase,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "from_cache": False,
        }

    # Store with TTL
    now = datetime.now(timezone.utc)
    expires = now + timedelta(days=PROFILE_TTL_DAYS)
    signal_count = len(signals) + len(themes)

    conn = _db_connect()
    conn.execute("""
        INSERT OR REPLACE INTO ticker_profiles
            (ticker, profile_text, signal_count, gli_phase, created_at, expires_at)
        VALUES (?, ?, ?, ?, ?, ?)
    """, (ticker, profile_text, signal_count, gli_phase, now.isoformat(), expires.isoformat()))
    conn.commit()
    conn.close()

    logger.info("Built profile for %s: %d signals, GLI=%s", ticker, signal_count, gli_phase)
    return {
        "ticker": ticker,
        "profile_text": profile_text,
        "signal_count": signal_count,
        "gli_phase": gli_phase,
        "created_at": now.isoformat(),
        "from_cache": False,
    }

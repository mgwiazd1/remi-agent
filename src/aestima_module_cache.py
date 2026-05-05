"""
aestima_module_cache.py — Local cache of Aestima analysis module outputs.

Architecture: Aestima owns computation. Remi owns the cache and narrative synthesis.
Fetches via GET /api/agent/modules?ticker={TICKER}&modules=04,06,08 (batch endpoint).
Caches in remi_intelligence.db with per-module TTLs.
"""

import json
import os
import sqlite3
import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx
from dotenv import load_dotenv

load_dotenv(Path(__file__).parent.parent / ".env")

logger = logging.getLogger(__name__)

DB_PATH = Path(__file__).parent.parent / "remi_intelligence.db"

AESTIMA_INTERNAL = "http://192.168.1.198:8000"
AESTIMA_EXTERNAL = "https://aestima.ai"

MODULE_REGISTRY = {
    "01": {"name": "Screener",           "ttl_hours": 168},   # 7 days
    "02": {"name": "WACC / Valuation",   "ttl_hours": 168},   # 7 days
    "03": {"name": "Risk / Stress Test", "ttl_hours": 24},    # 1 day — regime-sensitive
    "04": {"name": "Earnings Brief",     "ttl_hours": 168},   # 7 days
    "05": {"name": "Portfolio Construct", "ttl_hours": 168},   # 7 days
    "06": {"name": "Technical Analysis", "ttl_hours": 24},    # 1 day — price-sensitive
    "07": {"name": "Dividend Analysis",  "ttl_hours": 168},   # 7 days
    "08": {"name": "SWOT",              "ttl_hours": 168},   # 7 days
    "09": {"name": "Quant / Flow",      "ttl_hours": 24},    # 1 day — flow-sensitive
    "12": {"name": "Thesis Evaluator",   "ttl_hours": 168},   # 7 days
}

PROFILE_MODULES = ["04", "06", "08"]
DEEP_DIVE_MODULES = ["02", "03", "04", "06", "08", "09"]
REGIME_SENSITIVE_MODULES = ["03", "06", "09"]


def _get_aestima_base() -> str:
    """Try internal URL first, fall back to external."""
    try:
        r = httpx.get(f"{AESTIMA_INTERNAL}/health", timeout=3)
        if r.status_code == 200:
            return AESTIMA_INTERNAL
    except Exception:
        pass
    return AESTIMA_EXTERNAL


def _get_db():
    return sqlite3.connect(str(DB_PATH))


def get_cached_module(ticker: str, module_id: str) -> dict | None:
    """Return cached module JSON if fresh, None if expired or missing."""
    conn = _get_db()
    row = conn.execute(
        "SELECT result_json, fetched_at, expires_at, gli_phase_at_fetch "
        "FROM aestima_module_cache WHERE ticker = ? AND module_id = ?",
        (ticker.upper(), module_id)
    ).fetchone()
    conn.close()

    if row is None:
        return None

    result_json, fetched_at, expires_at, gli_phase = row
    now = datetime.now(timezone.utc).isoformat()

    if now > expires_at:
        logger.info(f"Cache expired for {ticker} module {module_id}")
        return None

    return {
        "result": json.loads(result_json),
        "fetched_at": fetched_at,
        "expires_at": expires_at,
        "gli_phase_at_fetch": gli_phase,
    }


def cache_module(ticker: str, module_id: str, result_json: dict,
                 gli_phase: str = None, steno_regime: str = None):
    """Store or update a module result in cache."""
    ttl = MODULE_REGISTRY.get(module_id, {}).get("ttl_hours", 168)
    name = MODULE_REGISTRY.get(module_id, {}).get("name", "Unknown")
    expires = (datetime.now(timezone.utc) + timedelta(hours=ttl)).isoformat()

    conn = _get_db()
    conn.execute(
        "INSERT OR REPLACE INTO aestima_module_cache "
        "(ticker, module_id, module_name, result_json, gli_phase_at_fetch, "
        "steno_regime_at_fetch, fetched_at, expires_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (ticker.upper(), module_id, name, json.dumps(result_json),
         gli_phase, steno_regime, datetime.now(timezone.utc).isoformat(), expires)
    )
    conn.commit()
    conn.close()


def fetch_modules_from_aestima(ticker: str, module_ids: list[str]) -> dict:
    """
    Batch-fetch module results from Aestima API.
    Returns dict of module_id → result JSON (or None if not found).
    Caches all successful results locally.
    """
    base = _get_aestima_base()
    key = os.environ.get("AESTIMA_AGENT_KEY", "")
    modules_str = ",".join(module_ids)

    try:
        r = httpx.get(
            f"{base}/api/agent/modules",
            params={"ticker": ticker.upper(), "modules": modules_str},
            headers={"X-Agent-Key": key},
            timeout=30,
        )
        r.raise_for_status()
        data = r.json()
    except Exception as e:
        logger.error(f"Aestima module fetch failed for {ticker}: {e}")
        return {mid: None for mid in module_ids}

    # Also fetch current regime context for cache metadata
    gli_phase = None
    steno_regime = None
    try:
        ctx = httpx.get(
            f"{base}/api/agent/context",
            headers={"X-Agent-Key": key},
            timeout=10,
        ).json()
        gli_phase = ctx.get("gli_phase")
        steno_regime = ctx.get("steno_regime")
    except Exception:
        pass

    results = {}
    for mid in module_ids:
        module_data = data.get("results", {}).get(mid, {})
        if module_data.get("status") == "ok":
            result = module_data.get("result", {})
            cache_module(ticker, mid, result, gli_phase, steno_regime)
            results[mid] = result
        else:
            results[mid] = None
            logger.info(f"Module {mid} not found for {ticker} on Aestima")

    return results


def fetch_modules_for_profile(ticker: str) -> dict:
    """Fetch profile-tier modules (04, 06, 08). Uses cache where fresh."""
    return _fetch_with_cache(ticker, PROFILE_MODULES)


def fetch_modules_for_deep_dive(ticker: str) -> dict:
    """Fetch deep-dive-tier modules (02, 03, 04, 06, 08, 09). Uses cache where fresh."""
    return _fetch_with_cache(ticker, DEEP_DIVE_MODULES)


def _fetch_with_cache(ticker: str, module_ids: list[str]) -> dict:
    """Check cache first, fetch missing/stale from Aestima."""
    results = {}
    to_fetch = []

    for mid in module_ids:
        cached = get_cached_module(ticker, mid)
        if cached:
            results[mid] = cached["result"]
            logger.info(f"Cache hit for {ticker} module {mid}")
        else:
            to_fetch.append(mid)

    if to_fetch:
        logger.info(f"Fetching {len(to_fetch)} modules from Aestima for {ticker}: {to_fetch}")
        fetched = fetch_modules_from_aestima(ticker, to_fetch)
        results.update(fetched)

    return results


def invalidate_cache(ticker: str):
    """Clear all cached modules for a ticker."""
    conn = _get_db()
    conn.execute("DELETE FROM aestima_module_cache WHERE ticker = ?", (ticker.upper(),))
    conn.commit()
    conn.close()
    logger.info(f"Cache invalidated for {ticker}")


def invalidate_on_phase_change(new_phase: str):
    """Clear regime-sensitive modules (03, 06, 09) for ALL tickers."""
    conn = _get_db()
    placeholders = ",".join("?" * len(REGIME_SENSITIVE_MODULES))
    conn.execute(
        f"DELETE FROM aestima_module_cache WHERE module_id IN ({placeholders})",
        REGIME_SENSITIVE_MODULES
    )
    conn.commit()
    conn.close()
    logger.info(f"Phase change to {new_phase} — invalidated regime-sensitive caches")


def get_cache_staleness(ticker: str, module_ids: list[str]) -> dict:
    """Check how stale each cached module is. Returns warnings for >80% TTL."""
    warnings = {}
    conn = _get_db()
    for mid in module_ids:
        row = conn.execute(
            "SELECT fetched_at, expires_at FROM aestima_module_cache "
            "WHERE ticker = ? AND module_id = ?",
            (ticker.upper(), mid)
        ).fetchone()
        if row:
            fetched = datetime.fromisoformat(row[0])
            expires = datetime.fromisoformat(row[1])
            ttl = (expires - fetched).total_seconds()
            age = (datetime.now(timezone.utc) - fetched).total_seconds()
            if age > ttl * 0.8:
                hours_old = age / 3600
                name = MODULE_REGISTRY.get(mid, {}).get("name", mid)
                warnings[mid] = f"⚠️ {name} data is {hours_old:.0f}h old"
    conn.close()
    return warnings

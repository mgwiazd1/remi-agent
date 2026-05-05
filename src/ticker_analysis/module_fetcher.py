"""
module_fetcher.py — Thin wrapper delegating to aestima_module_cache.
Kept for backward compat with ticker_analysis/ package imports.
"""
import logging
from aestima_module_cache import (
    fetch_modules_for_profile,
    fetch_modules_for_deep_dive,
    _get_gli_context,
)

logger = logging.getLogger(__name__)


def fetch_modules(ticker: str, modules: list = None) -> dict:
    """
    Fetch Aestima module data for a ticker.
    Uses cache if fresh, fetches from API if stale.
    Returns {module_id: result_dict}.
    """
    if modules and set(modules).issubset(set(["04", "06", "08"])):
        return fetch_modules_for_profile(ticker)
    return fetch_modules_for_deep_dive(ticker)


def get_cached_modules(ticker: str, modules: list) -> dict:
    """Get module data from cache only (no fetch)."""
    from aestima_module_cache import get_cached_module
    results = {}
    for mid in modules:
        cached = get_cached_module(ticker, mid)
        if cached:
            results[mid] = cached["result"]
    return results

"""
geopolitical_context.py — Assembles geopolitical & macro context for ticker analysis.

Sources:
1. Aestima /api/agent/context — GLI phase, Steno regime, fiscal dominance, transition risk
2. Aestima /api/agent/context/delta — 24h/48h rate of change
3. Remi vault themes — theme files mentioning this ticker or sector
4. Cross-theme synthesis — Intelligence/_meta/CROSS_THEME_SYNTHESIS.md
5. Instincts — Intelligence/_meta/INSTINCTS.md
6. Historical analogs — Intelligence/History/ episodes
7. Market velocity signals — market_signals table
"""

import json
import os
import re
import sqlite3
import logging
from pathlib import Path

import httpx
from dotenv import load_dotenv

load_dotenv(Path(__file__).parent.parent / ".env")

logger = logging.getLogger(__name__)

DB_PATH = Path(__file__).parent.parent / "remi_intelligence.db"
VAULT_BASE = Path("/docker/obsidian/investing/Intelligence")
META_DIR = VAULT_BASE / "_meta"
HISTORY_DIR = VAULT_BASE / "History"
THEMES_DIR = VAULT_BASE / "Themes"


def assemble_geopolitical_context(ticker: str, sector: str = None) -> str:
    """
    Pull geopolitical/macro context from all Remi intelligence sources.
    Returns a dense text block ready for GLM-5 prompt injection.

    Assembly order (most specific first):
    1. Direct ticker theme mentions
    2. Sector-level theme exposure
    3. Cross-theme intersection points
    4. Causal chain instincts
    5. Historical analog precedents
    6. Current regime scores + velocity
    7. Specific geopolitical vectors
    """
    sections = []

    # 1. Aestima regime context
    regime_ctx = _fetch_regime_context()
    if regime_ctx:
        sections.append("CURRENT MACRO REGIME:")
        sections.append(regime_ctx)
        sections.append("")

    # 2. Regime velocity (rate of change)
    velocity_ctx = _fetch_regime_velocity()
    if velocity_ctx:
        sections.append("REGIME VELOCITY (24h/48h):")
        sections.append(velocity_ctx)
        sections.append("")

    # 3. Direct ticker theme mentions from vault
    ticker_themes = _find_ticker_in_themes(ticker)
    if ticker_themes:
        sections.append(f"VAULT THEMES MENTIONING {ticker}:")
        sections.append(ticker_themes)
        sections.append("")

    # 4. Sector-level theme exposure (if sector provided)
    if sector:
        sector_themes = _find_sector_in_themes(sector)
        if sector_themes:
            sections.append(f"SECTOR THEMES ({sector}):")
            sections.append(sector_themes)
            sections.append("")

    # 5. Cross-theme synthesis intersections
    cross_themes = _check_cross_theme_synthesis(ticker)
    if cross_themes:
        sections.append("CROSS-THEME INTERSECTIONS:")
        sections.append(cross_themes)
        sections.append("")

    # 6. Instinct causal chains
    instincts = _check_instincts(ticker, sector)
    if instincts:
        sections.append("CAUSAL CHAIN INSTINCTS:")
        sections.append(instincts)
        sections.append("")

    # 7. Historical analogs
    analogs = _find_historical_analogs(ticker, sector)
    if analogs:
        sections.append("HISTORICAL ANALOG PRECEDENTS:")
        sections.append(analogs)
        sections.append("")

    # 8. Market velocity signals
    velocity_signals = _get_market_velocity_signals()
    if velocity_signals:
        sections.append("MARKET VELOCITY SIGNALS:")
        sections.append(velocity_signals)
        sections.append("")

    return "\n".join(sections) if sections else "No geopolitical context available."


def _fetch_regime_context() -> str | None:
    """Pull current regime from Aestima /api/agent/context."""
    try:
        base = os.environ.get("AESTIMA_BASE_URL", "https://aestima.ai")
        key = os.environ.get("AESTIMA_AGENT_KEY", "")
        r = httpx.get(f"{base}/api/agent/context",
                      headers={"X-Agent-Key": key}, timeout=10)
        ctx = r.json()
        lines = [
            f"GLI Phase: {ctx.get('gli_phase', 'unknown')}",
            f"GLI Value: ${ctx.get('gli_value_trn', '?')}T",
            f"Steno Regime: {ctx.get('steno_regime', 'unknown')}",
            f"Fiscal Dominance: {ctx.get('fiscal_dominance_score', '?')}/10",
            f"Transition Risk: {ctx.get('transition_risk_score', '?')}/10",
        ]
        stress = ctx.get("stress_signals", {})
        if stress:
            lines.append(f"HY Spread: {stress.get('hy_spread_bps', '?')} bps")
            lines.append(f"IG Spread: {stress.get('ig_spread_bps', '?')} bps")
            lines.append(f"SOFR Spread: {stress.get('sofr_spread_bps', '?')} bps")
            lines.append(f"Yield Curve 10-2: {stress.get('yield_curve_10_2', '?')}")
        return "\n".join(lines)
    except Exception as e:
        logger.error(f"Regime context fetch failed: {e}")
        return None


def _fetch_regime_velocity() -> str | None:
    """Pull 24h/48h deltas from Aestima /api/agent/context/delta."""
    try:
        base = os.environ.get("AESTIMA_BASE_URL", "https://aestima.ai")
        key = os.environ.get("AESTIMA_AGENT_KEY", "")
        r = httpx.get(f"{base}/api/agent/context/delta",
                      headers={"X-Agent-Key": key}, timeout=10)
        data = r.json()
        signals = data.get("velocity_signals", [])
        if not signals:
            return None
        lines = []
        for s in signals:
            name = s.get("name") or s.get("signal_name") or s.get("signal", "?")
            direction = s.get("direction", "unknown")
            d24 = s.get("delta_24h", 0)
            d48 = s.get("delta_48h", 0)
            lines.append(f"{name}: {direction} (24h: {d24:+.2f}, 48h: {d48:+.2f})")
        if data.get("phase_changed"):
            lines.insert(0, "PHASE CHANGE DETECTED")
        return "\n".join(lines)
    except Exception as e:
        logger.error(f"Regime velocity fetch failed: {e}")
        return None


def _find_ticker_in_themes(ticker: str) -> str | None:
    """Scan vault theme files for direct ticker mentions."""
    if not THEMES_DIR.exists():
        return None
    ticker_upper = ticker.upper().lstrip("$")
    matches = []
    for f in THEMES_DIR.glob("THEME_*.md"):
        content = f.read_text(errors="ignore")
        if ticker_upper in content or f"${ticker_upper}" in content:
            theme_label = f.stem.replace("THEME_", "").replace("_", " ")
            vel_match = re.search(r"velocity[:\s]+(\d+\.?\d*)", content, re.IGNORECASE)
            vel = vel_match.group(1) if vel_match else "?"
            matches.append(f"- {theme_label} (velocity: {vel})")
    return "\n".join(matches) if matches else None


def _find_sector_in_themes(sector: str) -> str | None:
    """Scan vault theme files for sector-level mentions."""
    if not THEMES_DIR.exists():
        return None
    sector_lower = sector.lower()
    sector_terms = {
        "energy": ["oil", "energy", "crude", "natural gas", "lng", "opec", "hormuz"],
        "technology": ["tech", "semiconductor", "AI", "cloud", "SaaS"],
        "healthcare": ["pharma", "biotech", "medical device", "healthcare", "FDA"],
        "financials": ["banks", "fintech", "insurance", "credit"],
        "real estate": ["REIT", "real estate", "housing", "mortgage"],
        "materials": ["mining", "steel", "copper", "lithium", "commodities"],
        "consumer": ["retail", "consumer", "spending", "discretionary"],
    }
    terms = sector_terms.get(sector_lower, [sector_lower])

    matches = []
    for f in THEMES_DIR.glob("THEME_*.md"):
        content = f.read_text(errors="ignore").lower()
        for term in terms:
            if term.lower() in content:
                theme_label = f.stem.replace("THEME_", "").replace("_", " ")
                matches.append(f"- {theme_label} (sector overlap: {term})")
                break
    return "\n".join(matches[:5]) if matches else None


def _check_cross_theme_synthesis(ticker: str) -> str | None:
    """Check if ticker appears in cross-theme synthesis output."""
    synth_path = META_DIR / "CROSS_THEME_SYNTHESIS.md"
    if not synth_path.exists():
        return None
    content = synth_path.read_text(errors="ignore")
    ticker_upper = ticker.upper().lstrip("$")
    if ticker_upper not in content:
        return None
    lines = content.split("\n")
    relevant = []
    capturing = False
    for line in lines:
        if ticker_upper in line:
            capturing = True
        if capturing:
            relevant.append(line)
            if len(relevant) > 8:
                break
        elif capturing and line.strip() == "":
            break
    return "\n".join(relevant) if relevant else None


def _check_instincts(ticker: str, sector: str = None) -> str | None:
    """Check for causal chain instincts matching this ticker or sector."""
    instincts_path = META_DIR / "INSTINCTS.md"
    if not instincts_path.exists():
        return None
    content = instincts_path.read_text(errors="ignore")
    search_terms = [ticker.upper()]
    if sector:
        search_terms.append(sector.lower())

    lines = content.split("\n")
    relevant = []
    for i, line in enumerate(lines):
        for term in search_terms:
            if term.lower() in line.lower():
                start = max(0, i - 1)
                end = min(len(lines), i + 4)
                chunk = "\n".join(lines[start:end])
                if chunk not in relevant:
                    relevant.append(chunk)
                break
    return "\n---\n".join(relevant[:3]) if relevant else None


def _find_historical_analogs(ticker: str, sector: str = None) -> str | None:
    """Search History/ episodes for relevant analogs."""
    if not HISTORY_DIR.exists():
        return None
    search_terms = [ticker.upper()]
    if sector:
        search_terms.append(sector.lower())

    matches = []
    for f in HISTORY_DIR.glob("*.md"):
        content = f.read_text(errors="ignore")
        for term in search_terms:
            if term.lower() in content.lower():
                episode_name = f.stem.replace("_", " ")
                sentences = content.split(". ")[:3]
                summary = ". ".join(sentences)[:300]
                matches.append(f"- {episode_name}: {summary}")
                break
    return "\n".join(matches[:3]) if matches else None


def _get_market_velocity_signals() -> str | None:
    """Pull current market velocity signals from local DB."""
    try:
        conn = sqlite3.connect(str(DB_PATH))
        rows = conn.execute("""
            SELECT signal_name, value, delta_24h, delta_48h, direction, recorded_at
            FROM market_signals
            WHERE recorded_at = (SELECT MAX(recorded_at) FROM market_signals)
            ORDER BY signal_name
        """).fetchall()
        conn.close()

        if not rows:
            return None

        lines = []
        for name, value, d24, d48, direction, recorded in rows:
            d24_str = f"{d24:+.2f}" if d24 else "n/a"
            d48_str = f"{d48:+.2f}" if d48 else "n/a"
            lines.append(f"{name}: {value} | {direction} (24h: {d24_str}, 48h: {d48_str})")
        return "\n".join(lines)
    except Exception as e:
        logger.error(f"Market velocity fetch failed: {e}")
        return None

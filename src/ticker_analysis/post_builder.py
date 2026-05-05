"""post_builder.py — Assemble structured ticker analysis posts."""
import json
import logging
from datetime import datetime, timezone
from pathlib import Path

from .module_fetcher import fetch_modules, _get_gli_context
from .geo_context import assemble_geo_context

logger = logging.getLogger(__name__)
VAULT_BASE = Path("/docker/obsidian/investing/Intelligence")


def build_ticker_post(ticker: str, profile: str = "deep_dive") -> dict:
    """
    Build a structured analysis post for a ticker.
    profile: 'quick_profile' (M04-M06) or 'deep_dive' (M04-M12 full suite)
    Returns dict with all assembled data ready for vault write + dashboard push.
    """
    ticker = ticker.upper()

    # Determine modules based on profile
    if profile == "quick_profile":
        modules = ["04", "06"]
    else:
        modules = ["04", "06", "08", "10", "11", "12"]

    # 1. Fetch Aestima modules (cached or live)
    module_data = fetch_modules(ticker, modules)

    # 2. GLI regime context
    gli_ctx = _get_gli_context()

    # 3. Geopolitical/narrative context
    geo_ctx = assemble_geo_context(ticker)

    # 4. Assemble structured post
    post = {
        "ticker": ticker,
        "profile": profile,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "gli_regime": gli_ctx,
        "aestima_modules": module_data,
        "narrative_context": {
            "signal_summary": {
                "total": geo_ctx["signal_count"],
                "bullish": geo_ctx["bullish_signals"],
                "bearish": geo_ctx["bearish_signals"],
                "source_types": geo_ctx["source_types"],
            },
            "active_themes": geo_ctx["active_themes"],
            "vault_notes": geo_ctx["vault_theme_notes"],
            "book_insights": geo_ctx["book_insights"],
            "on_watchlist": geo_ctx["on_watchlist"],
            "watchlist_thesis": geo_ctx["watchlist_thesis"],
        },
        "recent_signals": geo_ctx["recent_signals"][:5],
    }

    # 5. Compute composite narrative score
    sig_total = geo_ctx["signal_count"]
    if sig_total > 0:
        bull_ratio = geo_ctx["bullish_signals"] / sig_total
        bear_ratio = geo_ctx["bearish_signals"] / sig_total
        net = bull_ratio - bear_ratio
    else:
        net = 0.0

    post["narrative_score"] = {
        "net_bullish": round(net, 3),
        "signal_volume": sig_total,
        "theme_count": len(geo_ctx["active_themes"]),
        "book_depth": len(geo_ctx["book_insights"]),
        "assessment": _assess_narrative(net, sig_total, len(geo_ctx["active_themes"])),
    }

    # 6. Generate structured markdown
    post["markdown"] = _render_markdown(post)

    logger.info(f"Built {profile} post for {ticker}: "
                f"{len(module_data)} modules, {sig_total} signals, "
                f"narrative={post['narrative_score']['assessment']}")
    return post


def _assess_narrative(net_bullish: float, signal_count: int, theme_count: int) -> str:
    """Generate a one-word narrative assessment."""
    if signal_count < 3:
        return "thin_coverage"
    if net_bullish > 0.3 and theme_count >= 3:
        return "strong_bullish_consensus"
    if net_bullish > 0.1:
        return "moderate_bullish"
    if net_bullish < -0.3 and theme_count >= 3:
        return "strong_bearish_consensus"
    if net_bullish < -0.1:
        return "moderate_bearish"
    return "mixed_neutral"


def _render_markdown(post: dict) -> str:
    """Render the analysis post as structured markdown for vault/dashboard."""
    t = post["ticker"]
    gli = post["gli_regime"]
    ns = post["narrative_context"]["signal_summary"]
    nsc = post["narrative_score"]

    lines = [
        f"# {t} — Ticker Analysis",
        f"",
        f"**Generated:** {post['generated_at'][:10]}",
        f"**Profile:** {post['profile']}",
        f"**GLI Phase:** {gli.get('gli_phase', 'unknown')}",
        f"**Regime:** {gli.get('steno_regime', 'unknown')}",
        f"",
        f"## Narrative Assessment: {nsc['assessment'].replace('_', ' ').title()}",
        f"",
        f"| Metric | Value |",
        f"|--------|-------|",
        f"| Net Bullish | {nsc['net_bullish']:+.3f} |",
        f"| Signal Volume | {nsc['signal_volume']} |",
        f"| Active Themes | {nsc['theme_count']} |",
        f"| Book Depth | {nsc['book_depth']} insights |",
        f"| Source Types | {', '.join(ns['source_types']) or 'none'} |",
        f"",
    ]

    # Themes
    themes = post["narrative_context"]["active_themes"]
    if themes:
        lines.append("## Active Themes")
        lines.append("")
        for th in themes[:5]:
            lines.append(f"- {th}")
        lines.append("")

    # Vault notes
    vault = post["narrative_context"]["vault_notes"]
    if vault:
        lines.append("## Vault Intelligence")
        lines.append("")
        for v in vault:
            lines.append(f"- [[{v}]]")
        lines.append("")

    # Book insights
    books = post["narrative_context"]["book_insights"]
    if books:
        lines.append("## Book-Derived Insights")
        lines.append("")
        for b in books:
            lines.append(f"- {b}")
        lines.append("")

    # Watchlist
    if post["narrative_context"]["on_watchlist"]:
        lines.append("## Watchlist Status: ACTIVE")
        thesis = post["narrative_context"].get("watchlist_thesis", "")
        if thesis:
            lines.append(f"- Thesis: {thesis}")
    else:
        lines.append("## Watchlist Status: NOT COVERED")

    # Aestima modules (if available)
    mods = post.get("aestima_modules", {})
    if mods:
        lines.append("")
        lines.append("## Aestima Module Data")
        lines.append("")
        for mod_id, mod_data in mods.items():
            name = mod_data.get("module_name", f"Module {mod_id}")
            age = mod_data.get("age_hours", 0)
            gli_gen = mod_data.get("gli_phase_at_generation", "?")
            lines.append(f"### M{mod_id}: {name}")
            lines.append(f"- Age: {age:.0f}h | GLI at generation: {gli_gen}")
            inner = mod_data.get("result", {})
            if inner:
                summary = inner.get("summary", "")
                if summary:
                    lines.append(f"- **Summary:** {summary[:500]}")
                # Key metrics from different module types
                for key in ["consensus", "scenarios", "trend", "confidence", "trade_debate", "implied_move"]:
                    val = inner.get(key)
                    if val and isinstance(val, (str, dict, list)):
                        if isinstance(val, str):
                            lines.append(f"- **{key.title()}:** {val[:300]}")
                        elif isinstance(val, dict):
                            lines.append(f"- **{key.title()}:** {json.dumps(val)[:300]}")
            lines.append("")

    return "\n".join(lines)

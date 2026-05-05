"""
analysis_post_builder.py — Builds structured ticker analysis posts.

Combines:
- Aestima module JSONs (from aestima_module_cache.py)
- Geopolitical/macro context (from geopolitical_context.py)
- Narrative intelligence (from ticker_intel.py)
- GLM-5 synthesis for final post text
"""

import json
import os
import logging
from datetime import datetime, timezone
from pathlib import Path

import httpx
from dotenv import load_dotenv

load_dotenv(Path(__file__).parent.parent / ".env")

from aestima_module_cache import (
    fetch_modules_for_profile,
    fetch_modules_for_deep_dive,
    get_cache_staleness,
    MODULE_REGISTRY,
)
from geopolitical_context import assemble_geopolitical_context
from ticker_intel import get_ticker_intelligence

logger = logging.getLogger(__name__)

# --- Post Templates ---

PROFILE_TEMPLATE = """═══════════════════════════════════════
REMI TICKER PROFILE: {ticker}
{company} | {sector} | {industry}
{date} | Macro Regime: {gli_phase} | {steno_regime}
═══════════════════════════════════════

-- Why We're Looking --
-- The Business --
-- The Numbers --
-- The Chart --
-- The Edge (or Lack of One) --
-- The Macro & Geopolitical Backdrop --
-- What Smart Money Is Saying --
-- Does the Environment Help or Hurt? --
-- The Verdict --
═══════════════════════════════════════"""

DEEP_DIVE_TEMPLATE = """═══════════════════════════════════════
REMI DEEP DIVE: {ticker}
{company} | {sector} | {industry}
{date} | Macro Regime: {gli_phase} | {steno_regime}
═══════════════════════════════════════

-- Why We're Looking --
-- The Business --
-- The Numbers --
-- The Chart --
-- The Edge (or Lack of One) --
-- The Macro & Geopolitical Backdrop --
-- What's It Worth? --
-- What Could Go Wrong --
-- Who's Buying & Who's Selling --
-- What Smart Money Is Saying --
-- Does the Environment Help or Hurt? --
-- The Scorecard --
-- The Bottom Line --
═══════════════════════════════════════"""


SYNTHESIS_PROMPT = """You are Remi, an investing intelligence analyst writing for a subscriber audience.

You have analysis data from multiple sources for {ticker} ({company}).
Your job is to synthesize this into a structured analysis post that is informative,
opinionated, and readable by someone who invests but is NOT a macro strategist.

CURRENT REGIME CONTEXT:
{gli_context}

SOURCE ARTICLE:
{article_summary}

ANALYSIS MODULE DATA:
{module_data_json}

NARRATIVE INTELLIGENCE:
{narrative_intel}

GEOPOLITICAL & MACRO CONTEXT:
{geopolitical_context}

Write the analysis post following this EXACT structure:
{post_template}

Section-by-section instructions:

-- Why We're Looking --
What article, post, or signal brought this ticker to our attention? 2-3 sentences on the source and why it caught our eye.

-- The Business --
What does this company actually do? Explain it like you're telling a friend at a bar. What do they sell, who buys it, and why does it matter? If it's a commodity ETP, REIT, or non-standard structure, explain that clearly.

-- The Numbers --
Key financial snapshot: revenue, earnings, growth rate, margins. Don't dump a spreadsheet -- highlight the 2-3 numbers that matter most and explain why. If earnings are irrelevant (commodity fund, pre-revenue), say so and explain what to watch instead.

-- The Chart --
Is the stock trending up, down, or sideways? How strong is the trend? Mention key price levels if relevant. Keep it plain -- "the stock has been falling for 3 months and hasn't found a floor yet" beats "primary downtrend with moderate strength."

-- The Edge (or Lack of One) --
What's the competitive advantage? Is there a real moat -- pricing power, network effects, regulatory barriers, switching costs -- or is this a commodity business? Be honest. Most companies don't have a moat. If the strengths are real, name the top 2-3. If the threats are serious, name those too.

-- The Macro & Geopolitical Backdrop --
This is the section that makes our analysis different. Connect the big-picture macro and geopolitical environment to this specific company. Don't say "geopolitical uncertainty is a headwind." Instead say "40% of their raw materials come from China, and the latest tariff round adds $X per unit to their input costs." Name the specific vectors: tariffs, sanctions, supply chain disruption, commodity price moves, currency headwinds, interest rate sensitivity, regulatory risk. Reference historical parallels if they exist -- "the last time the macro environment looked like this was [period], and companies like this one did [X]."

-- What's It Worth? -- (deep dive only)
What does the valuation look like? Cost of capital, implied value, margin of safety. Is the stock cheap or expensive relative to what you're getting? Explain in plain terms -- "at today's price you're paying X for a business that generates Y" is more useful than "WACC of 10.07%."

-- What Could Go Wrong -- (deep dive only)
Top 2-3 stress scenarios. What specific events would hurt this stock? Estimate how bad the damage could be. Which scenario is most likely given today's environment? Don't sugarcoat it.

-- Who's Buying & Who's Selling -- (deep dive only)
Are institutions adding or trimming? Is short interest elevated? Are options traders betting on a move? Is there systematic selling pressure (pension rebalancing, index changes, volatility-driven unwinding)? This section tells you whether the crowd is with you or against you.

-- What Smart Money Is Saying --
Who in our tracked network (X accounts, newsletters, signal groups) is talking about this name? How much buzz is there? What themes is it tied to? If nobody's talking about it yet, say so -- that can be bullish (undiscovered) or bearish (nobody cares for a reason).

-- Does the Environment Help or Hurt? --
Net assessment: given everything above -- the macro regime, the geopolitical backdrop, the flow picture, the narrative momentum -- is the current environment a tailwind or a headwind for this name? Summarize in 2-3 sentences.

-- The Scorecard -- (deep dive only)
Rate each factor on a simple scale. Use plain language, not raw numbers:

  Fundamentals:      Strong / Mixed / Weak
  Chart:             Bullish / Neutral / Bearish
  Flow & Positioning: Favorable / Neutral / Against You
  Macro Alignment:   Tailwind / Neutral / Headwind
  Geopolitical Risk: Low / Moderate / Elevated
  Narrative Momentum: Building / Flat / Fading
  Stress Resilience:  Durable / Fragile

  Overall Conviction: X/10

-- The Bottom Line / The Verdict --
What should the reader take away? Is this worth owning, watching, or avoiding? If it's a buy, at what conviction level and what's the key catalyst to watch? If it's a pass, why -- and is there a price or event that would change your mind?

Global rules:
- Write in clear, conversational prose. No jargon without explanation.
- Be direct and opinionated. Take a position. "It depends" is not allowed.
- When data conflicts (strong business but bad chart, or good fundamentals but ugly macro), call out the conflict explicitly and say which side you think wins.
- Each section should be 3-5 sentences. Dense and useful, not padded.
- If any data source is unavailable, acknowledge it briefly and work with what you have.
- End every post with a clear, actionable verdict.
- This is analysis, not financial advice. Include a one-line disclaimer at the very end.

Return ONLY the formatted post text. No preamble, no meta-commentary."""


async def build_profile_post(
    ticker: str,
    company: str = "",
    sector: str = "",
    industry: str = "",
    source_url: str = "",
    source_summary: str = "",
) -> dict:
    """
    Build a profile-tier analysis post.
    Returns dict with post_content, module_data, conviction_score, staleness_warnings.
    """
    return await _build_post(
        ticker=ticker,
        company=company,
        sector=sector,
        industry=industry,
        source_url=source_url,
        source_summary=source_summary,
        analysis_type="profile",
        fetch_fn=fetch_modules_for_profile,
        template=PROFILE_TEMPLATE,
    )


async def build_deep_dive_post(
    ticker: str,
    company: str = "",
    sector: str = "",
    industry: str = "",
    source_url: str = "",
    source_summary: str = "",
) -> dict:
    """
    Build a deep-dive-tier analysis post.
    Returns dict with post_content, module_data, conviction_score, staleness_warnings.
    """
    return await _build_post(
        ticker=ticker,
        company=company,
        sector=sector,
        industry=industry,
        source_url=source_url,
        source_summary=source_summary,
        analysis_type="deep_dive",
        fetch_fn=fetch_modules_for_deep_dive,
        template=DEEP_DIVE_TEMPLATE,
    )


async def _build_post(
    ticker: str,
    company: str,
    sector: str,
    industry: str,
    source_url: str,
    source_summary: str,
    analysis_type: str,
    fetch_fn,
    template: str,
) -> dict:
    """Core post builder. Fetches modules, assembles context, calls GLM-5."""

    # 1. Fetch Aestima module data (uses cache)
    module_data = fetch_fn(ticker)
    module_ids = list(module_data.keys())

    # 2. Check staleness
    staleness = get_cache_staleness(ticker, module_ids)

    # 3. Assemble geopolitical context from vault
    geo_context = assemble_geopolitical_context(ticker, sector or None)

    # 4. Get Remi narrative intel from local DB
    intel = get_ticker_intelligence(ticker)

    # 5. Get current regime context (for prompt and post header)
    regime = _get_regime_snapshot()
    gli_phase = regime.get("gli_phase", "unknown")
    steno_regime = regime.get("steno_regime", "unknown")

    # 6. Format the template header
    post_header = template.format(
        ticker=ticker.upper(),
        company=company or ticker.upper(),
        sector=sector or "Unknown",
        industry=industry or "Unknown",
        date=datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
        gli_phase=gli_phase,
        steno_regime=steno_regime,
    )

    # 7. Prepare module data for prompt (strip to key fields, not full 15KB JSONs)
    module_summary = _summarize_module_data(module_data)

    # 8. Format narrative intel for prompt
    narrative_str = _format_narrative_intel(intel)

    # 9. Call GLM-5 for synthesis
    prompt = SYNTHESIS_PROMPT.format(
        ticker=ticker.upper(),
        company=company or ticker.upper(),
        gli_context=json.dumps(regime, indent=2),
        article_summary=source_summary or f"Source: {source_url}" if source_url else "No source article.",
        module_data_json=json.dumps(module_summary, indent=2),
        narrative_intel=narrative_str,
        geopolitical_context=geo_context,
        post_template=post_header,
    )

    post_content = await _call_glm5(prompt)

    # 10. Add staleness warnings if any
    if staleness:
        warnings = "\n".join(staleness.values())
        post_content += f"\n\n{warnings}"

    # 11. Extract conviction score from GLM output (if present in conviction matrix)
    conviction = _extract_conviction_score(post_content)

    return {
        "post_content": post_content,
        "module_data": module_data,
        "conviction_score": conviction,
        "staleness_warnings": staleness,
        "analysis_type": analysis_type,
        "gli_phase": gli_phase,
        "steno_regime": steno_regime,
        "modules_found": [mid for mid, v in module_data.items() if v is not None],
        "modules_missing": [mid for mid, v in module_data.items() if v is None],
    }


def _summarize_module_data(module_data: dict) -> dict:
    """
    Extract key fields from each module JSON for prompt injection.
    Don't send full 15KB JSONs -- extract what matters.
    """
    summary = {}
    for mid, data in module_data.items():
        if data is None:
            summary[mid] = {"status": "not_available"}
            continue

        name = MODULE_REGISTRY.get(mid, {}).get("name", mid)

        if mid == "04":  # Earnings Brief
            summary[mid] = {
                "module": name,
                "summary": data.get("summary", "")[:1000],
                "consensus": data.get("consensus", {}),
                "scenarios": data.get("scenarios", [])[:3],
            }
        elif mid == "06":  # Technical
            summary[mid] = {
                "module": name,
                "trend": data.get("trend", {}),
                "key_levels": data.get("key_levels", {}),
            }
        elif mid == "08":  # SWOT
            summary[mid] = {
                "module": name,
                "swot": data.get("swot", [])[:3],
            }
        elif mid == "02":  # WACC
            summary[mid] = {
                "module": name,
                "wacc": data.get("wacc", {}),
                "valuation": data.get("valuation", {}),
            }
        elif mid == "03":  # Risk/Stress
            summary[mid] = {
                "module": name,
                "risk_score": data.get("risk_score"),
                "stress_tests": data.get("stress_tests", [])[:3],
            }
        elif mid == "09":  # Quant/Flow
            summary[mid] = {
                "module": name,
                "summary": data.get("summary", "")[:1000],
                "top_edges": data.get("top_edges", [])[:3],
            }
        else:
            summary[mid] = {
                "module": name,
                "summary": str(data.get("summary", ""))[:500],
            }

    return summary


def _format_narrative_intel(intel: dict) -> str:
    """Format ticker_intel output for prompt injection."""
    if not intel.get("found"):
        return f"No mentions of {intel.get('ticker', '?')} in Remi's tracked sources (X, RSS, signals group) over the last 30 days. This is a new/undiscovered name."

    lines = [
        f"Mentions: {intel.get('total_mentions', 0)} in last {intel.get('lookback_days', 30)} days",
        f"Accounts mentioning: {', '.join(intel.get('accounts_mentioning', [])[:5])}",
    ]

    themes = intel.get("themes", [])
    if themes:
        lines.append("Active themes:")
        for th in themes[:4]:
            lines.append(f"  - {th['theme']} (velocity: {th.get('velocity_score', 0):.1f})")

    co = intel.get("co_occurring_tickers", [])
    if co:
        lines.append(f"Co-mentioned with: {', '.join(x['ticker'] for x in co[:5])}")

    return "\n".join(lines)


def _get_regime_snapshot() -> dict:
    """Get current regime context from Aestima."""
    try:
        base = os.environ.get("AESTIMA_BASE_URL", "https://aestima.ai")
        key = os.environ.get("AESTIMA_AGENT_KEY", "")
        r = httpx.get(f"{base}/api/agent/context",
                      headers={"X-Agent-Key": key}, timeout=10)
        return r.json()
    except Exception:
        return {"gli_phase": "unknown", "steno_regime": "unknown"}


async def _call_glm5(prompt: str) -> str:
    """Call GLM-5 via Z.ai API for synthesis."""
    api_key = os.environ.get("GLM_API_KEY", "")
    base_url = os.environ.get("GLM_BASE_URL", "https://api.z.ai/api/coding/paas/v4")

    try:
        async with httpx.AsyncClient(timeout=120) as client:
            r = await client.post(
                f"{base_url}/chat/completions",
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": "glm-5",
                    "messages": [
                        {"role": "system", "content": "You are Remi, a macro-aware investing intelligence agent. Write structured analysis posts."},
                        {"role": "user", "content": prompt},
                    ],
                    "max_tokens": 4000,
                    "temperature": 0.3,
                },
            )
            r.raise_for_status()
            return r.json()["choices"][0]["message"]["content"]
    except Exception as e:
        logger.error(f"GLM-5 synthesis call failed: {e}")
        return f"[SYNTHESIS FAILED: {e}]\n\nRaw module data available on dashboard."


def _extract_conviction_score(post_content: str) -> float | None:
    """Try to extract conviction score from the generated post."""
    import re
    # Match various formats: "Overall Conviction: 3/10", "X/10", etc
    match = re.search(r"Overall Conviction[:\s|]+(\d+\.?\d*)\s*/\s*10", post_content, re.IGNORECASE)
    if match:
        return float(match.group(1))
    return None

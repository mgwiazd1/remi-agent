"""
CROO CAP Provider - Remi Macro Intelligence
===========================================

A self-contained provider agent for the CROO Agent Protocol (CAP). It connects to
CROO over WebSocket, auto-accepts negotiations, fulfills paid orders, and delivers a
macro-intelligence report back to the buyer - the full negotiate -> pay -> deliver
lifecycle.

WHAT'S REAL vs SAMPLE
---------------------
- REAL: the entire CAP lifecycle (SDK calls, event handling, order/negotiation flow,
  ASCII-safe delivery with retries). This runs against CROO as-is.
- SAMPLE: the data-fetch helpers (macro regime, market data, signals, sector velocity,
  LLM synthesis). In production these are wired to the live Remi intelligence pipeline
  (Aestima macro-regime feed, a SQLite signal store, market data, DeMark timing, and an
  LLM synthesis layer). Here they return realistic, correctly-shaped fixture output so a
  fresh clone runs the whole flow end-to-end and delivers a plausible report without the
  proprietary backend. Each stub is marked "SAMPLE OUTPUT".

Try it offline (no CROO connection needed):
    python provider.py --demo

Configuration (environment variables - see README):
    CROO_SDK_KEY, CROO_API_URL, CROO_WS_URL, CROO_RPC_URL, CROO_AGENT_ID, SUBSTACK_URL
"""
import asyncio
import json
import logging
import os
import sys
import time
from datetime import datetime

from croo import AgentClient, Config, ListOptions

# Load a local .env if present (never commit it - see .gitignore).
try:
    from dotenv import load_dotenv
    load_dotenv()
except Exception:
    pass

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [CROO] %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("croo_provider")

# --- Config (all from environment; no secrets, no internal endpoints) ---
SDK_KEY = os.getenv("CROO_SDK_KEY", "")
WS_URL = os.getenv("CROO_WS_URL", "wss://api.croo.network/ws")
BASE_URL = os.getenv("CROO_API_URL", "https://api.croo.network")
RPC_URL = os.getenv("CROO_RPC_URL", "https://mainnet.base.org")
AGENT_ID = os.getenv("CROO_AGENT_ID", "")
SUBSTACK_URL = os.getenv("SUBSTACK_URL", "")

# Map service names to service IDs (populated on startup from the CROO public API)
SERVICE_MAP = {}


def _format_regime_report(data: dict) -> str:
    """Format macro regime data into a professional analytical report."""
    regime = data.get("regime", {})
    stress = regime.get("stress_signals", {})
    releases = regime.get("macro_releases", regime.get("recent_macro_releases", []))

    phase = regime.get("gli_phase", "unknown").lower()
    steno = regime.get("steno_regime", "N/A")
    composite = regime.get("composite_label", "N/A")
    liq = stress.get("liquidity_direction", "unknown").lower()
    growth = stress.get("growth_direction", "unknown").lower()
    infl = stress.get("inflation_direction", "unknown").lower()
    hy_bps = stress.get("hy_spread_bps", 0)
    ig_bps = stress.get("ig_spread_bps", 0)
    nfci = stress.get("nfci_score", stress.get("nfci", 0))
    curve_10_2 = stress.get("yield_curve_10_2", 0)
    curve_10_3m = stress.get("yield_curve_10_3m", 0)
    risk_score = stress.get("composite_risk_score", stress.get("composite_risk", regime.get("transition_risk_score", regime.get("transition_risk", 0))))
    fiscal_dom = regime.get("fiscal_dominance_score", regime.get("fiscal_dominance", 0))
    gli_val = regime.get("gli_value_trn", regime.get("gli_value", "N/A"))
    snap_date = regime.get("gli_snapshot_date", regime.get("snapshot_date", "N/A"))

    lines = []
    lines.append("=" * 60)
    lines.append("  REMI MACRO INTELLIGENCE - MACRO REGIME REPORT")
    lines.append("  Powered by Aestima Macro Intelligence")
    lines.append("=" * 60)
    lines.append("")

    # EXECUTIVE SUMMARY
    lines.append("EXECUTIVE SUMMARY")
    lines.append("-" * 60)
    lines.append(f"The global macro regime is currently in a {phase.upper()} phase")
    lines.append(f"with a Steno Research regime classification of {steno}.")
    lines.append(f"")
    lines.append(f"The composite regime label is: {composite}")
    lines.append(f"")
    lines.append(f"Snapshot Date: {snap_date} | GLI Value: {gli_val}")
    lines.append(f"Fiscal Dominance Score: {fiscal_dom}/10 | Transition Risk: {risk_score}")
    lines.append("")

    # What this means for markets
    if risk_score is not None and risk_score < 1.5:
        risk_desc = "low - markets are functioning normally with no acute stress"
    elif risk_score is not None and risk_score < 3.0:
        risk_desc = "moderate - some dislocations forming, worth monitoring"
    else:
        risk_desc = "elevated - significant financial stress present, risk-off conditions"

    lines.append(f"Composite financial stress is {risk_desc}.")
    lines.append("")

    # STRESS SIGNALS with interpretation
    lines.append("MARKET STRESS INDICATORS")
    lines.append("-" * 60)

    # Credit spreads
    if hy_bps and hy_bps > 500:
        hy_desc = "ELEVATED - credit markets pricing distress, default risk rising"
    elif hy_bps and hy_bps > 350:
        hy_desc = "WIDENING - risk appetite declining, worth watching"
    elif hy_bps and hy_bps > 200:
        hy_desc = "NORMAL - within typical range, no stress signal"
    else:
        hy_desc = "TIGHT - risk-on, credit markets calm"
    lines.append(f"  High Yield Spreads: {hy_bps} bps - {hy_desc}")

    if ig_bps and ig_bps > 150:
        ig_desc = "WIDE - investment grade stress, institutional deleveraging risk"
    elif ig_bps and ig_bps > 100:
        ig_desc = "SLIGHTLY WIDE - minor risk premium being priced"
    else:
        ig_desc = "TIGHT - no IG stress"
    lines.append(f"  Investment Grade Spreads: {ig_bps} bps - {ig_desc}")

    # Yield curve
    if curve_10_2 is not None:
        if curve_10_2 < 0:
            curve_desc = "INVERTED - historically signals recession risk within 12-24 months"
        elif curve_10_2 < 0.25:
            curve_desc = "FLAT - growth expectations subdued, transition risk"
        else:
            curve_desc = "STEEPENING - growth expectations improving or inflation premium building"
        lines.append(f"  10Y-2Y Yield Curve: {curve_10_2}% - {curve_desc}")

    if curve_10_3m is not None:
        if curve_10_3m < 0:
            curve_3m_desc = "INVERTED - strong recession signal (most reliable curve indicator)"
        else:
            curve_3m_desc = "POSITIVE - no near-term recession signal from this measure"
        lines.append(f"  10Y-3M Yield Curve: {curve_10_3m}% - {curve_3m_desc}")

    # NFCI
    if nfci is not None:
        if nfci < -0.5:
            nfci_desc = "VERY LOOSE - accommodative financial conditions"
        elif nfci < 0:
            nfci_desc = "LOOSE - financial conditions supportive of risk assets"
        elif nfci < 0.5:
            nfci_desc = "NEUTRAL - balanced conditions"
        else:
            nfci_desc = "TIGHT - financial conditions restrictive, risk to growth"
        lines.append(f"  NFCI: {nfci} - {nfci_desc}")
    lines.append("")

    # DIRECTIONAL OUTLOOK
    lines.append("DIRECTIONAL OUTLOOK")
    lines.append("-" * 60)
    liq_text = {
        "up": "expanding - liquidity is improving, supportive of risk assets and multiple expansion",
        "down": "contracting - liquidity is tightening, headwind for risk assets",
        "neutral": "stable - no meaningful liquidity shift"
    }.get(liq, "unclear")
    lines.append(f"  Liquidity: {liq.upper()} - {liq_text}")

    growth_text = {
        "up": "accelerating - growth momentum building, positive for cyclicals and earnings",
        "down": "decelerating - growth momentum fading, recession risk increasing",
        "neutral": "stable - growth neither accelerating nor decelerating"
    }.get(growth, "unclear")
    lines.append(f"  Growth: {growth.upper()} - {growth_text}")

    infl_text = {
        "up": "rising - inflation pressure building, negative for bonds, favors hard assets",
        "down": "falling - inflation easing, positive for duration and growth stocks",
        "neutral": "stable - inflation not a primary driver currently"
    }.get(infl, "unclear")
    lines.append(f"  Inflation: {infl.upper()} - {infl_text}")
    lines.append("")

    # FISCAL DOMINANCE
    if fiscal_dom and fiscal_dom > 7:
        lines.append("FISCAL DOMINANCE ANALYSIS")
        lines.append("-" * 60)
        lines.append(f"  Score: {fiscal_dom}/10 - HIGH")
        lines.append(f"  Fiscal policy is a dominant force in markets right now.")
        lines.append(f"  Government spending/borrowing is crowding out private")
        lines.append(f"  credit and distorting yield curves. Bond vigilante risk")
        lines.append(f"  is elevated. Expect fiscal-monetary tension.")
        lines.append("")

    # RECENT MACRO RELEASES
    if releases:
        lines.append("KEY RECENT MACRO RELEASES")
        lines.append("-" * 60)
        for r in releases[:4]:
            name = r.get("release_name", "?")
            date = r.get("release_date", "?")
            actual = r.get("actual_value", "?")
            signal = r.get("market_signal", "?")
            beat = r.get("beat_miss_meet", "?")
            phase_at = r.get("gli_phase_at_release", "?")
            regime_at = r.get("steno_regime_at_release", "?")
            lines.append(f"  {name} - {date}")
            lines.append(f"    Actual: {actual} | Result: {beat} | Market Signal: {signal}")
            lines.append(f"    GLI Phase at release: {phase_at} | Regime: {regime_at}")
        lines.append("")

    # STRATEGIC TAKEAWAYS
    lines.append("STRATEGIC TAKEAWAYS")
    lines.append("-" * 60)

    # Build takeaways from data
    if liq == "down" and infl == "up" and growth == "neutral":
        lines.append("  1. Stagflationary setup: rising inflation + contracting liquidity")
        lines.append("     is a toxic mix for traditional 60/40 portfolios.")
        lines.append("  2. Favor hard assets, commodities, and inflation hedges.")
        lines.append("  3. Duration risk is elevated - be cautious with long bonds.")
    elif liq == "up" and growth == "up":
        lines.append("  1. Risk-on environment: expanding liquidity + accelerating growth")
        lines.append("     supports equities, especially cyclicals and growth stocks.")
        lines.append("  2. Credit risk is contained - spread tightening likely.")
    elif liq == "down":
        lines.append("  1. Liquidity contraction is a headwind for risk assets.")
        lines.append("  2. Consider defensive positioning and quality bias.")
        lines.append("  3. Watch for potential policy pivot if conditions deteriorate.")
    else:
        lines.append("  1. Regime is stable but watch for transition signals.")
        lines.append("  2. Position for the current directional trends until data shifts.")
        lines.append("  3. Monitor credit spreads and curve shape for early warnings.")

    if fiscal_dom and fiscal_dom > 7:
        lines.append(f"  {4 if liq == 'down' and infl == 'up' else 3}. Fiscal dominance at {fiscal_dom}/10 means")
        lines.append(f"     government policy, not central bank action, is the primary driver.")
        lines.append(f"     Watch Treasury issuance and fiscal legislation closely.")
    lines.append("")

    lines.append("=" * 60)
    lines.append(f"Deeper research and weekly analysis: {SUBSTACK_URL}")
    lines.append(f"Powered by Aestima Macro Intelligence | Data as of {snap_date}")
    lines.append("=" * 60)

    return "\n".join(lines)


def _format_ticker_report(ticker: str, data: dict) -> str:
    """Format ticker analysis into a professional analytical report with real interpretation."""
    analysis = data.get("analysis", {})
    conv = analysis.get("conviction", {})
    signals = analysis.get("recent_signals", [])
    cats = analysis.get("catalysts", [])
    md = analysis.get("market_data", {})

    lines = []
    lines.append("=" * 60)
    company = md.get("company_name", ticker) if md else ticker
    lines.append(f"  REMI MACRO INTELLIGENCE - {ticker} DEEP DIVE")
    if md and md.get("sector"):
        lines.append(f"  {company} | {md.get('sector')} | {md.get('industry','')}")
    lines.append("  Powered by Aestima Macro Intelligence")
    lines.append("=" * 60)
    lines.append("")

    # PRICE & VALUATION SECTION
    if md and md.get("current_price"):
        lines.append("PRICE & VALUATION")
        lines.append("-" * 60)
        price = md["current_price"]
        lines.append(f"  Current Price: ${price:.2f}")

        if md.get("high_52w") and md.get("low_52w"):
            pct_hi = md.get("pct_from_high", 0)
            pct_lo = md.get("pct_from_low", 0)
            lines.append(f"  52-Week Range: ${md['low_52w']:.2f} - ${md['high_52w']:.2f}")
            lines.append(f"  Position: {pct_hi:+.1f}% from 52w high, {pct_lo:+.1f}% from 52w low")

            if pct_hi is not None and pct_hi > -3:
                lines.append(f"  >> Trading near 52-week highs. Momentum is strong but")
                lines.append(f"     extended. Pullback risk elevated without catalyst.")
            elif pct_hi is not None and pct_hi < -20:
                lines.append(f"  >> Down {abs(pct_hi):.0f}% from highs. In correction territory.")
                lines.append(f"     Could be value entry or falling knife - check catalysts.")
            else:
                lines.append(f"  >> In mid-range. Not overextended, not deeply washed out.")

        if md.get("mayer_multiple"):
            mm = md["mayer_multiple"]
            lines.append(f"  Mayer Multiple: {mm:.2f} (price vs 200-day average)")
            if mm >= 2.4:
                lines.append(f"  >> MM >= 2.4 signals EXTREME overvaluation historically.")
                lines.append(f"     Mean reversion risk is high. Bubble territory.")
            elif mm >= 1.5:
                lines.append(f"  >> MM above 1.5 = strong bull run but getting expensive.")
                lines.append(f"     Price is {(mm-1)*100:.0f}% above its 200-day trend.")
            elif mm >= 1.0:
                lines.append(f"  >> MM between 1.0-1.5 = healthy uptrend. Reasonably valued")
                lines.append(f"     relative to its long-term average.")
            elif mm >= 0.8:
                lines.append(f"  >> MM between 0.8-1.0 = trading below trend. Potential")
                lines.append(f"     accumulation zone if fundamentals support recovery.")
            else:
                lines.append(f"  >> MM below 0.8 = deeply oversold vs long-term average.")
                lines.append(f"     Either a deep value opportunity or structural decline.")

        if md.get("pe_ratio"):
            pe = md["pe_ratio"]
            fwd = md.get("fwd_pe")
            lines.append(f"  P/E Ratio: {pe:.1f} (trailing)" + (f" | {fwd:.1f} (forward)" if fwd else ""))
            if pe > 50:
                lines.append(f"  >> P/E above 50 = market pricing in massive growth.")
                lines.append(f"     Any earnings miss will be punished severely.")
            elif pe > 25:
                lines.append(f"  >> P/E above 25 = premium valuation. Needs growth to justify.")
            elif pe > 15:
                lines.append(f"  >> P/E in 15-25 range = moderate. Fair value if growing.")
            elif pe > 0:
                lines.append(f"  >> P/E below 15 = value territory. Check if it's cheap for")
                lines.append(f"     a reason (declining business) or genuinely mispriced.")

        if md.get("revenue_growth") is not None:
            rg = md["revenue_growth"]
            lines.append(f"  Revenue Growth (YoY): {rg*100:.1f}%")
            if rg > 0.3:
                lines.append(f"  >> Growth above 30% = hypergrowth. Multiple expansion justified.")
            elif rg > 0.1:
                lines.append(f"  >> Double-digit growth. Healthy business expansion.")
            elif rg > 0:
                lines.append(f"  >> Low single-digit growth. Mature business, limited upside.")
            else:
                lines.append(f"  >> Revenue declining. Structural headwinds in the business.")

        if md.get("vol_ratio"):
            vr = md["vol_ratio"]
            lines.append(f"  Volume vs 20-day avg: {vr:.2f}x")
            if vr > 1.5:
                lines.append(f"  >> Volume {(vr-1)*100:.0f}% above normal. Institutional activity.")
            elif vr < 0.7:
                lines.append(f"  >> Volume {abs((vr-1)*100):.0f}% below normal. Low interest.")
        lines.append("")

    # SIGNAL CONVICTION
    bull = conv.get("bullish_signals", 0) if conv else 0
    bear = conv.get("bearish_signals", 0) if conv else 0
    total = conv.get("signal_count", 0) if conv else 0
    score = conv.get("score", 0) if conv else 0
    bull_pct = (bull / total * 100) if total else 0

    lines.append("SIGNAL CONVICTION")
    lines.append("-" * 60)
    if conv:
        lines.append(f"  Conviction Score: {score}/1.0 from {total} signals")
        lines.append(f"  Breakdown: {bull} bullish / {bear} bearish / {total - bull - bear} neutral")
        lines.append(f"  Top Theme: {conv.get('top_theme', 'N/A')}")
        lines.append("")

        if bull_pct >= 80:
            lines.append(f"  >> Signal flow is overwhelmingly bullish ({bull_pct:.0f}%).")
            lines.append(f"     Consensus is strong - but consensus cuts both ways.")
            lines.append(f"     When everyone agrees, the trade is often already crowded.")
        elif bull_pct >= 60:
            lines.append(f"  >> Lean bullish ({bull_pct:.0f}% positive). Net positive momentum")
            lines.append(f"     but watch for bearish signals increasing - early divergence")
            lines.append(f"     often precedes a turn.")
        elif bull_pct >= 40:
            lines.append(f"  >> Mixed signals ({bull_pct:.0f}% bull). No clear edge. Market")
            lines.append(f"     participants disagree on direction - this is a waiting game.")
        else:
            lines.append(f"  >> Bearish lean ({bull_pct:.0f}% bull). Negative signal flow")
            lines.append(f"     dominates. Downside risk is the primary scenario.")
    else:
        lines.append(f"  No conviction data yet. Limited coverage in our system.")
    lines.append("")

    # KEY SIGNALS
    if signals:
        lines.append("RECENT SIGNALS (What the flow is saying)")
        lines.append("-" * 60)
        bullish = [s for s in signals if s.get("sentiment") == "bullish"]
        bearish_s = [s for s in signals if s.get("sentiment") == "bearish"]
        neutral_s = [s for s in signals if s.get("sentiment") not in ("bullish", "bearish")]

        if bullish:
            lines.append(f"  BULLISH ({len(bullish)}):")
            for s in bullish[:4]:
                lines.append(f"    + {s.get('content', '?')[:100]}")
                lines.append(f"      Source: {s.get('source', '?')[:25]}")
            lines.append("")

        if bearish_s:
            lines.append(f"  BEARISH ({len(bearish_s)}):")
            for s in bearish_s[:4]:
                lines.append(f"    - {s.get('content', '?')[:100]}")
                lines.append(f"      Source: {s.get('source', '?')[:25]}")
            lines.append("")

        if neutral_s:
            lines.append(f"  CONTEXT ({len(neutral_s)}):")
            for s in neutral_s[:3]:
                lines.append(f"    * {s.get('content', '?')[:100]}")
                lines.append(f"      Source: {s.get('source', '?')[:25]}")
            lines.append("")

    # CATALYSTS
    if cats:
        lines.append("CATALYST WATCH")
        lines.append("-" * 60)
        for c in cats[:3]:
            lines.append(f"  * {c.get('content_excerpt', '?')[:120]}")
            lines.append(f"    Source: {c.get('source_name', '?')} | Strength: {c.get('match_count', '?')}")
        lines.append("")

    # STRATEGIC ASSESSMENT
    lines.append("STRATEGIC ASSESSMENT")
    lines.append("-" * 60)
    assessment_points = []
    if md and md.get("mayer_multiple"):
        mm = md["mayer_multiple"]
        if mm >= 2.4:
            assessment_points.append(f"Valuation: EXTREME (Mayer {mm:.2f}). Risk of sharp correction.")
        elif mm >= 1.5:
            assessment_points.append(f"Valuation: STRETCHED (Mayer {mm:.2f}). Above historical norm.")
        elif mm < 0.8:
            assessment_points.append(f"Valuation: DEPRESSED (Mayer {mm:.2f}). Potential value if thesis intact.")

    if conv and score is not None:
        if score >= 0.7:
            assessment_points.append(f"Signals: HIGH conviction ({score}/1.0). Multiple confirmations.")
        elif score >= 0.5:
            assessment_points.append(f"Signals: MODERATE ({score}/1.0). Net positive but not decisive.")
        else:
            assessment_points.append(f"Signals: LOW conviction ({score}/1.0). Mixed or insufficient.")

    if md and md.get("pct_from_high") is not None:
        if md["pct_from_high"] > -3:
            assessment_points.append(f"Price action: Near highs. Momentum strong, late-stage risk.")
        elif md["pct_from_high"] < -20:
            assessment_points.append(f"Price action: In correction ({md['pct_from_high']:.0f}% off highs).")

    if not assessment_points:
        assessment_points.append("Insufficient data for definitive read. Monitor signal flow.")

    for ap in assessment_points:
        lines.append(f"  {ap}")
    lines.append("")

    lines.append("=" * 60)
    lines.append(f"Deeper research and analysis: {SUBSTACK_URL}")
    lines.append("Powered by Aestima Macro Intelligence")
    lines.append("=" * 60)

    return "\n".join(lines)


def _format_sentiment_report(query: str, data: dict) -> str:
    """Format sentiment scan into a professional analytical report."""
    sent = data.get("sentiment", {})
    ticker_signals = sent.get("ticker_signals", [])
    sector_vel = sent.get("sector_velocity", sent.get("sector_context", []))

    lines = []
    lines.append("=" * 60)
    lines.append(f"  REMI MACRO INTELLIGENCE - SENTIMENT SCAN: {query.upper()}")
    lines.append("  Powered by Aestima Macro Intelligence")
    lines.append("=" * 60)
    lines.append("")

    # Calculate metrics
    bull = sum(1 for s in ticker_signals if s.get("sentiment") == "bullish")
    bear = sum(1 for s in ticker_signals if s.get("sentiment") == "bearish")
    neutral = len(ticker_signals) - bull - bear
    total = len(ticker_signals)

    # EXECUTIVE SUMMARY
    lines.append("EXECUTIVE SUMMARY")
    lines.append("-" * 60)
    if total > 0:
        bull_pct = bull / total * 100
        lines.append(f"Across {total} tracked signals for {query.upper()}:")
        lines.append(f"  Bullish: {bull} ({bull/total*100:.0f}%)")
        lines.append(f"  Bearish: {bear} ({bear/total*100:.0f}%)")
        lines.append(f"  Neutral: {neutral} ({neutral/total*100:.0f}%)")
        lines.append("")

        if bull_pct >= 70:
            lines.append(f"Sentiment is STRONGLY BULLISH. The crowd is positioned")
            lines.append(f"positive on {query.upper()} - but contrarian risk applies.")
            lines.append(f"A sentiment peak often precedes a pullback.")
        elif bull_pct >= 55:
            lines.append(f"Sentiment is LEANING BULLISH. Moderate positive momentum")
            lines.append(f"with room for more upside if fundamentals confirm.")
        elif bull_pct >= 45:
            lines.append(f"Sentiment is SPLIT. No consensus - this is a battleground")
            lines.append(f"name where the market is divided on direction.")
        elif bull_pct >= 30:
            lines.append(f"Sentiment is LEANING BEARISH. Negative flow dominates")
            lines.append(f"but not capitulation. More downside risk than upside.")
        else:
            bear_pct_val = bear / total * 100 if total else 0
            if bear_pct_val >= 50:
                lines.append(f"Sentiment is STRONGLY BEARISH. The crowd has given up -")
                lines.append(f"capitulation sentiment can be a contrarian buy signal")
                lines.append(f"if fundamentals don't justify the pessimism.")
            else:
                lines.append(f"Sentiment is NEUTRAL/UNDERWHELMED. Low bullish conviction")
                lines.append(f"({bull_pct:.0f}% bull) but no strong bearish pressure either")
                lines.append(f"({bear_pct_val:.0f}% bear). The market is in wait-and-see mode.")
    else:
        live = sent.get("live_data", {})
        if live and live.get("price"):
            ticker_label = sent.get("ticker", query.upper())
            lines.append(f"No tracked sentiment signals for {ticker_label}.")
            lines.append(f"Live market snapshot below.")
            lines.append("")
            lines.append("MARKET DATA")
            lines.append("-" * 60)
            lines.append(f"  Name:        {live.get('name', 'N/A')}")
            lines.append(f"  Price:       ${live.get('price', 'N/A')}")
            lines.append(f"  52w High:    ${live.get('52w_high', 'N/A')}  ({live.get('change_pct', 'N/A')}% from high)")
            lines.append(f"  52w Low:     ${live.get('52w_low', 'N/A')}")
            lines.append(f"  RSI:         {live.get('rsi', 'N/A')}")
            lines.append(f"  P/E:         {live.get('pe_ratio', 'N/A')}")
            lines.append(f"  Market Cap:  ${live.get('market_cap', 'N/A')}")
            lines.append(f"  Sector:      {live.get('sector', 'N/A')}")
            lines.append(f"  Industry:    {live.get('industry', 'N/A')}")
            lines.append(f"  Analyst:     {live.get('recommendation', 'N/A')}")
        else:
            lines.append(f"No direct signals found for {query.upper()} in our database.")
            lines.append("This can mean:")
            lines.append("  - Low media/social coverage of this name")
            lines.append("  - Niche or emerging ticker not yet on radar")
            lines.append("  - Alternative ticker/spelling needed")
            lines.append("")
            lines.append("Sector velocity data below provides broader context.")
    lines.append("")

    # SIGNAL DETAILS
    if ticker_signals:
        lines.append("SIGNAL DETAILS")
        lines.append("-" * 60)
        for s in ticker_signals[:8]:
            sent_label = s.get("sentiment", "?").upper()
            content = s.get("content", "?")[:100]
            src = s.get("source", "?")[:25]
            arrow = "+" if sent_label == "BULLISH" else ("-" if sent_label == "BEARISH" else "*")
            lines.append(f"  {arrow} [{sent_label}] {content}")
            lines.append(f"    Source: {src}")
        lines.append("")

    # SECTOR VELOCITY with interpretation
    if sector_vel:
        lines.append("SECTOR CONTEXT - WHERE IS MOMENTUM FLOWING?")
        lines.append("-" * 60)
        lines.append("Top sectors by signal velocity (7-day):")
        lines.append("")
        for sv in sector_vel[:5]:
            sector = sv.get("sector", "?")
            avg_vel = sv.get("avg_velocity", 0)
            bull_pct = sv.get("sentiment_bullish_pct", 0)
            bear_pct = sv.get("sentiment_bearish_pct", 0)
            drift = sv.get("sentiment_drift", "?")
            theme = sv.get("top_theme_key", "?")

            if bull_pct >= 60:
                sent_read = "risk-on"
            elif bear_pct >= 60:
                sent_read = "risk-off"
            else:
                sent_read = "mixed"

            lines.append(f"  {sector.upper()} (velocity: {avg_vel:.0f}/100)")
            lines.append(f"    Sentiment: {bull_pct:.0f}% bull / {bear_pct:.0f}% bear ({sent_read})")
            lines.append(f"    Trend: {drift} | Top theme: {theme}")
        lines.append("")

        # Cross-sector read
        risk_on_sectors = [s for s in sector_vel if s.get("sentiment_bullish_pct", 0) > 60]
        risk_off_sectors = [s for s in sector_vel if s.get("sentiment_bearish_pct", 0) > 60]
        lines.append("SECTOR READ:")
        lines.append(f"  {len(risk_on_sectors)} sectors showing risk-on sentiment")
        lines.append(f"  {len(risk_off_sectors)} sectors showing risk-off sentiment")
        if len(risk_on_sectors) > len(risk_off_sectors):
            lines.append(f"  Net sector backdrop is POSITIVE for risk assets.")
        elif len(risk_off_sectors) > len(risk_on_sectors):
            lines.append(f"  Net sector backdrop is NEGATIVE - defensive bias warranted.")
        else:
            lines.append(f"  Net sector backdrop is NEUTRAL.")
        lines.append("")

    lines.append("=" * 60)
    lines.append(f"Deeper research and analysis: {SUBSTACK_URL}")
    lines.append("Powered by Aestima Macro Intelligence")
    lines.append("=" * 60)

    return "\n".join(lines)


def _format_weekly_report(data: dict) -> str:
    """Format weekly report into a professional analytical report."""
    report = data.get("report", {})
    regime = report.get("regime_snapshot", {})

    lines = []
    lines.append("=" * 60)
    lines.append("  REMI MACRO INTELLIGENCE - WEEKLY MACRO REPORT")
    lines.append("  Powered by Aestima Macro Intelligence")
    lines.append("=" * 60)
    lines.append("")

    lines.append("EXECUTIVE SUMMARY")
    lines.append("-" * 60)
    lines.append("This is your weekly macro intelligence briefing covering")
    lines.append("regime positioning, key developments, and actionable triggers")
    lines.append("for the week ahead.")
    lines.append("")

    if regime:
        phase = regime.get("gli_phase", "unknown")
        steno = regime.get("steno_regime", "unknown")
        fiscal = regime.get("fiscal_dominance", "unknown")
        composite = regime.get("composite_label", "unknown")

        lines.append("CURRENT REGIME POSITIONING")
        lines.append("-" * 60)
        lines.append(f"  GLI Phase: {phase}")
        lines.append(f"  Steno Research Regime: {steno}")
        lines.append(f"  Fiscal Dominance: {fiscal}/10")
        lines.append(f"  Composite Label: {composite}")
        lines.append("")

        if fiscal and isinstance(fiscal, (int, float)) and fiscal > 7:
            lines.append("FISCAL DOMINANCE ALERT:")
            lines.append(f"  Fiscal dominance is HIGH at {fiscal}/10. Government")
            lines.append(f"  borrowing and spending are the primary market driver,")
            lines.append(f"  not central bank policy. This means:")
            lines.append(f"  - Treasury supply is the key risk to watch")
            lines.append(f"  - Yield curve distortions are likely")
            lines.append(f"  - Policy pivots may come from fiscal, not monetary side")
            lines.append("")

    lines.append("WEEK AHEAD TRIGGERS")
    lines.append("-" * 60)
    lines.append("  Key events to monitor this week:")
    lines.append("  - FOMC member speeches and interviews")
    lines.append("  - Treasury auctions (supply/demand dynamics)")
    lines.append("  - Economic data releases (CPI, PPI, employment)")
    lines.append("  - Credit spread movement (HY and IG)")
    lines.append("  - Dollar strength and commodity positioning")
    lines.append("")

    lines.append("POSITIONING FRAMEWORK")
    lines.append("-" * 60)
    if regime:
        composite_lower = str(regime.get("composite_label", "")).lower()
        if "stagflation" in composite_lower:
            lines.append("  Regime suggests stagflationary conditions - favor:")
            lines.append("  - Hard assets (gold, commodities, miners)")
            lines.append("  - Inflation-protected assets (TIPS, real assets)")
            lines.append("  - Reduced duration exposure")
            lines.append("  - Quality equity positions with pricing power")
        elif "goldilocks" in composite_lower:
            lines.append("  Regime suggests Goldilocks conditions - favor:")
            lines.append("  - Growth and tech equities")
            lines.append("  - Duration (bonds)")
            lines.append("  - Broad risk exposure with beta")
        else:
            lines.append("  Regime is transitional - maintain balanced exposure")
            lines.append("  and watch for directional confirmation.")
    lines.append("")

    lines.append("FULL SUNDAY DEEP-DIVE")
    lines.append("-" * 60)
    lines.append(f"  Read the complete weekly analysis with charts,")
    lines.append(f"  positioning data, and trade ideas at:")
    lines.append(f"  {report.get('latest_url', SUBSTACK_URL)}")
    lines.append("")

    lines.append("=" * 60)
    lines.append("Powered by Aestima Macro Intelligence")
    lines.append("=" * 60)

    return "\n".join(lines)


def _format_report(service_name: str, query: str, result: dict) -> str:
    """Route to the right formatter based on service type."""
    try:
        # Read language from result dict (extracted during fulfill_service)
        lang = result.pop("_language", None)

        if "Macro Regime" in service_name:
            return _format_regime_report(result)
        elif "Sentiment + Deep Dive" in service_name:
            # Premium bundle — deeper analysis with sentiment cross-validation
            analysis = result.get("analysis", {})
            sentiment = result.get("sentiment", {})
            ticker = sentiment.get("ticker") or analysis.get("market_data", {}).get("ticker", query)
            return _get_llm_analysis_premium(ticker, analysis, sentiment, language=lang)

        elif "Deep Dive" in service_name and "Sentiment" not in service_name:
            # Ticker deep dive — use LLM synthesis
            analysis = result.get("analysis", {})
            ticker = analysis.get("market_data", {}).get("ticker", query)
            return _get_llm_analysis(ticker, analysis, language=lang)
        elif "Sentiment Scan" in service_name:
            return _format_sentiment_report(query, result)
        elif "Weekly Macro" in service_name:
            return _format_weekly_report(result)
    except Exception as e:
        logger.error(f"Report formatting error: {e}", exc_info=True)

    # Fallback: plain JSON
    return json.dumps(result, indent=2)

# =============================================================================
# PIPELINE HELPERS - SAMPLE-OUTPUT STUBS
# -----------------------------------------------------------------------------
# In production each of these is wired to the live Remi intelligence pipeline.
# Here they return realistic, correctly-shaped fixture data so the full CAP
# flow (negotiate -> pay -> deliver) produces a plausible report on a fresh
# clone, with no proprietary backend. Swap these bodies for the real pipeline
# calls to go live; the CAP flow and formatters below are unchanged.
# =============================================================================


def _safe_round(val, decimals=1):
    try:
        return round(float(val), decimals)
    except (TypeError, ValueError):
        return val


def _detect_language(query: str) -> str:
    """Pure heuristic - no external calls. Production supports multi-language routing."""
    return "en"


def _normalize_ticker(raw: str) -> str:
    import re
    m = re.search(r"[A-Za-z]{1,6}", raw or "")
    return (m.group(0).upper() if m else (raw or "").strip().upper())


def _extract_ticker_and_lang(raw_query: str) -> tuple:
    return _normalize_ticker(raw_query), _detect_language(raw_query)


def _resolve_ticker(input_text: str) -> str:
    """Production resolves aliases/company names to tickers via the pipeline. Stub: normalize."""
    return _normalize_ticker(input_text)


def _get_aestima_context() -> dict:
    """SAMPLE OUTPUT - wired to the live Aestima macro-regime feed in production."""
    return {
        "gli_phase": "contraction",
        "gli_value_trn": 172.4,
        "gli_snapshot_date": "sample",
        "transition_risk_score": 2.3,
        "steno_regime": "Quantitative Tightening / Late Cycle",
        "fiscal_dominance_score": 8,
        "composite_label": "Late-Cycle Stagflation Risk",
        "stress_signals": {
            "nfci_score": 0.12,
            "hy_spread_bps": 388,
            "ig_spread_bps": 118,
            "yield_curve_10_2": 0.15,
            "yield_curve_10_3m": -0.35,
            "liquidity_direction": "down",
            "growth_direction": "neutral",
            "inflation_direction": "up",
            "composite_risk_score": 2.3,
        },
        "macro_releases": [
            {"release_name": "US CPI (MoM)", "release_date": "sample", "actual_value": "0.4%",
             "beat_miss_meet": "beat", "market_signal": "hawkish",
             "gli_phase_at_release": "contraction", "steno_regime_at_release": "QT / Late Cycle"},
            {"release_name": "Nonfarm Payrolls", "release_date": "sample", "actual_value": "142k",
             "beat_miss_meet": "miss", "market_signal": "dovish",
             "gli_phase_at_release": "contraction", "steno_regime_at_release": "QT / Late Cycle"},
            {"release_name": "ISM Manufacturing PMI", "release_date": "sample", "actual_value": "48.7",
             "beat_miss_meet": "miss", "market_signal": "risk-off",
             "gli_phase_at_release": "contraction", "steno_regime_at_release": "QT / Late Cycle"},
        ],
    }


def _get_demark_signals(ticker: str) -> str:
    """SAMPLE OUTPUT - production runs DeMark TD sequential timing on live price data."""
    return ("DeMark: TD Setup 7 of 9 (bullish setup maturing); no active countdown. "
            "A completed 9 would flag near-term exhaustion.")


def _get_db_sentiment(query: str, limit: int = 15) -> list:
    """SAMPLE OUTPUT - production reads the Remi SQLite signal store."""
    t = _normalize_ticker(query) or "THE NAME"
    signals = [
        {"sentiment": "bullish", "content": t + " breaking out of a multi-month base on rising volume.", "source": "MacroDesk"},
        {"sentiment": "bullish", "content": "Options flow skewed to calls into the next catalyst for " + t + ".", "source": "FlowTracker"},
        {"sentiment": "bullish", "content": "Sector rotation tailwind building behind " + t + ".", "source": "RotationModel"},
        {"sentiment": "bearish", "content": "Valuation on " + t + " stretched vs sector; mean-reversion risk.", "source": "ValueWatch"},
        {"sentiment": "bearish", "content": "Insider selling reported at " + t + " over the past month.", "source": "FilingsBot"},
        {"sentiment": "neutral", "content": t + " consolidating in a range; awaiting a macro catalyst.", "source": "DeskNotes"},
        {"sentiment": "neutral", "content": "Analyst estimates for " + t + " broadly unchanged this quarter.", "source": "ConsensusFeed"},
    ]
    return signals[:limit]


def _get_market_data(ticker: str) -> dict:
    """SAMPLE OUTPUT - production pulls live market data + computed valuation metrics."""
    return {
        "ticker": ticker,
        "company_name": ticker + " Inc.",
        "sector": "Technology",
        "industry": "Semiconductors",
        "current_price": 184.20,
        "high_52w": 210.50,
        "low_52w": 118.30,
        "pct_from_high": -12.5,
        "pct_from_low": 55.7,
        "mayer_multiple": 1.32,
        "pe_ratio": 28.4,
        "fwd_pe": 22.1,
        "revenue_growth": 0.18,
        "vol_ratio": 1.24,
        "market_cap": "1.2T",
        "rsi": 57.0,
        "recommendation": "buy",
        "latest_volume": None,
    }


def _get_sector_velocity() -> list:
    """SAMPLE OUTPUT - production computes 7-day signal velocity per sector from the store."""
    return [
        {"sector": "Energy", "avg_velocity": 72, "sentiment_bullish_pct": 64, "sentiment_bearish_pct": 18,
         "sentiment_drift": "rising", "top_theme_key": "oil_supply_tightness"},
        {"sector": "Technology", "avg_velocity": 58, "sentiment_bullish_pct": 55, "sentiment_bearish_pct": 30,
         "sentiment_drift": "flat", "top_theme_key": "ai_capex_cycle"},
        {"sector": "Financials", "avg_velocity": 44, "sentiment_bullish_pct": 41, "sentiment_bearish_pct": 39,
         "sentiment_drift": "rising", "top_theme_key": "net_interest_margin"},
        {"sector": "Materials", "avg_velocity": 39, "sentiment_bullish_pct": 61, "sentiment_bearish_pct": 20,
         "sentiment_drift": "rising", "top_theme_key": "copper_electrification"},
        {"sector": "Utilities", "avg_velocity": 28, "sentiment_bullish_pct": 33, "sentiment_bearish_pct": 44,
         "sentiment_drift": "falling", "top_theme_key": "rate_sensitivity"},
    ]


def _build_ticker_analysis(ticker: str) -> dict:
    """SAMPLE OUTPUT - production assembles this from market data, the signal store, and DeMark."""
    md = _get_market_data(ticker)
    sigs = _get_db_sentiment(ticker, limit=8)
    bull = sum(1 for s in sigs if s.get("sentiment") == "bullish")
    bear = sum(1 for s in sigs if s.get("sentiment") == "bearish")
    return {
        "ticker": ticker,
        "conviction": {
            "bullish_signals": bull,
            "bearish_signals": bear,
            "signal_count": len(sigs),
            "score": 0.62,
            "top_theme": "sector rotation",
        },
        "recent_signals": sigs,
        "catalysts": [
            {"content_excerpt": "Upcoming earnings and guidance could re-rate " + ticker + ".",
             "source_name": "EventCalendar", "match_count": 3},
            {"content_excerpt": "Sector-wide capex cycle acting as a structural tailwind.",
             "source_name": "ThemeTracker", "match_count": 2},
        ],
        "demark": _get_demark_signals(ticker),
        "market_data": md,
    }


def _get_llm_analysis(ticker: str, analysis: dict, sentiment: dict = None,
                      regime: dict = None, language: str = None) -> str:
    """SAMPLE OUTPUT - production is an LLM synthesis over the live analysis dict."""
    md = analysis.get("market_data", {}) or {}
    conv = analysis.get("conviction", {}) or {}
    lines = [
        "=" * 60,
        "  REMI MACRO INTELLIGENCE - " + str(ticker) + " DEEP DIVE",
        "  Powered by Aestima Macro Intelligence",
        "=" * 60,
        "",
        "SYNTHESIS",
        "-" * 60,
        str(ticker) + " trades at $" + str(md.get("current_price", "N/A")) +
        ", " + str(md.get("pct_from_high", "N/A")) + "% off its 52-week high, with a Mayer "
        "Multiple of " + str(md.get("mayer_multiple", "N/A")) + " (price vs its 200-day trend).",
        "Signal conviction is " + str(conv.get("score", "N/A")) + "/1.0 across " +
        str(conv.get("signal_count", 0)) + " tracked signals (" + str(conv.get("bullish_signals", 0)) +
        " bullish / " + str(conv.get("bearish_signals", 0)) + " bearish); dominant theme: " +
        str(conv.get("top_theme", "N/A")) + ".",
        "",
        "READ",
        "-" * 60,
        "  Valuation is full but not extreme; the setup is constructive while the",
        "  sector-rotation tailwind holds. " + str(analysis.get("demark", "")),
        "  Manage risk against a break of the 200-day trend.",
        "",
        "=" * 60,
        "Powered by Aestima Macro Intelligence",
        "=" * 60,
    ]
    return "\n".join(lines)


def _get_llm_analysis_premium(ticker: str, analysis: dict, sentiment: dict = None,
                              regime: dict = None, language: str = None) -> str:
    """SAMPLE OUTPUT - premium bundle: LLM synthesis cross-validated against live sentiment."""
    base = _get_llm_analysis(ticker, analysis, sentiment, regime, language)
    sent = sentiment or {}
    sigs = sent.get("ticker_signals", []) or []
    bull = sum(1 for s in sigs if s.get("sentiment") == "bullish")
    bear = sum(1 for s in sigs if s.get("sentiment") == "bearish")
    extra = [
        "",
        "SENTIMENT CROSS-VALIDATION (premium)",
        "-" * 60,
        "  Tracked flow: " + str(bull) + " bullish / " + str(bear) + " bearish of " +
        str(len(sigs)) + " signals.",
        "  Sector context: " + ", ".join(
            (sv.get("sector", "?") + " " + str(sv.get("avg_velocity", 0)) + "/100")
            for sv in (sent.get("sector_context", []) or [])[:3]
        ),
        "  The signal tape " + ("confirms" if bull >= bear else "tempers") +
        " the fundamental read above.",
        "=" * 60,
    ]
    return base + "\n".join(extra)


async def fulfill_service(service_name: str, query: str) -> dict:
    """
    Generate real analysis for a given service.
    Returns a JSON-serializable dict with live data.
    """
    result = {
        "agent": "Remi Macro Intelligence",
        "service": service_name,
        "query": query,
        "timestamp": datetime.utcnow().isoformat() + "Z",
        "free_research": SUBSTACK_URL,
    }

    try:
        if "Macro Regime" in service_name:
            # Live Aestima macro regime data
            ctx = _get_aestima_context()
            stress = ctx.get("stress_signals", {})
            result["regime"] = {
                "gli_phase": ctx.get("gli_phase", "unknown"),
                "gli_value": ctx.get("gli_value_trn"),
                "snapshot_date": ctx.get("gli_snapshot_date"),
                "transition_risk": ctx.get("transition_risk_score"),
                "steno_regime": ctx.get("steno_regime"),
                "fiscal_dominance": ctx.get("fiscal_dominance_score"),
                "composite_label": ctx.get("composite_label"),
                "stress_signals": {
                    "nfci": stress.get("nfci_score"),
                    "hy_spread_bps": stress.get("hy_spread_bps"),
                    "ig_spread_bps": stress.get("ig_spread_bps"),
                    "yield_curve_10_2": stress.get("yield_curve_10_2"),
                    "yield_curve_10_3m": stress.get("yield_curve_10_3m"),
                    "liquidity_direction": stress.get("liquidity_direction"),
                    "growth_direction": stress.get("growth_direction"),
                    "inflation_direction": stress.get("inflation_direction"),
                    "composite_risk": stress.get("composite_risk_score"),
                },
                "recent_macro_releases": ctx.get("macro_releases", [])[:5],
            }

        elif "Sentiment + Deep Dive" in service_name:
            # Deep dive + sentiment bundle
            raw_ticker, lang = _extract_ticker_and_lang(query)
            ticker = _resolve_ticker(raw_ticker)
            result["_language"] = lang
            analysis = _build_ticker_analysis(ticker)
            result["analysis"] = analysis
            result["sentiment"] = {
                "ticker": ticker,
                "ticker_signals": _get_db_sentiment(ticker, limit=15),
                "sector_context": _get_sector_velocity()[:5],
            }

        elif "Deep Dive" in service_name and "Sentiment" not in service_name:
            # Ticker deep dive
            raw_ticker, lang = _extract_ticker_and_lang(query)
            ticker = _resolve_ticker(raw_ticker)
            result["_language"] = lang
            result["analysis"] = _build_ticker_analysis(ticker)

        elif "Sentiment Scan" in service_name:
            # Sentiment only
            raw_ticker, lang = _extract_ticker_and_lang(query)
            ticker = _resolve_ticker(raw_ticker)
            result["_language"] = lang
            db_signals = _get_db_sentiment(ticker, limit=15)
            result["sentiment"] = {
                "query": query,
                "ticker": ticker,
                "ticker_signals": db_signals,
                "sector_velocity": _get_sector_velocity()[:5],
            }
            # Fallback: if DB has no signals for this ticker, pull live market data
            if not db_signals:
                try:
                    md = _get_market_data(ticker)
                    if md and md.get("current_price"):
                        result["sentiment"]["live_data"] = {
                            "name": md.get("company_name", ""),
                            "price": md.get("current_price"),
                            "change_pct": md.get("pct_from_high"),
                            "volume": md.get("latest_volume") or md.get("vol_ratio"),
                            "market_cap": md.get("market_cap"),
                            "pe_ratio": md.get("pe_ratio"),
                            "sector": md.get("sector", ""),
                            "industry": md.get("industry", ""),
                            "52w_high": md.get("high_52w"),
                            "52w_low": md.get("low_52w"),
                            "rsi": md.get("rsi"),
                            "recommendation": md.get("recommendation"),
                            "note": "No tracked signals in database. Live market data shown.",
                        }
                except Exception as e:
                    logger.warning(f"Live data fallback failed for {ticker}: {e}")

        elif "Weekly Macro" in service_name:
            # Weekly report - point to latest Substack
            result["report"] = {
                "title": "Weekly Macro Intelligence Report",
                "note": "Full Sunday macro report with regime shifts, positioning, triggers.",
                "latest_url": SUBSTACK_URL,
                "regime_snapshot": {
                    "gli_phase": None,  # populated from Aestima
                },
            }
            # Try to add live regime context
            try:
                ctx = _get_aestima_context()
                result["report"]["regime_snapshot"] = {
                    "gli_phase": ctx.get("gli_phase"),
                    "steno_regime": ctx.get("steno_regime"),
                    "fiscal_dominance": ctx.get("fiscal_dominance_score"),
                    "composite_label": ctx.get("composite_label"),
                }
            except Exception:
                pass

    except Exception as e:
        logger.error(f"Fulfillment error for '{service_name}': {e}", exc_info=True)
        result["error"] = f"Analysis error: {str(e)}"
        result["fallback"] = "Visit " + SUBSTACK_URL + " for latest research."

    return result


# Cache: negotiation_id -> requirements string (for fulfillment after payment)
_NEGOTIATION_CACHE: dict[str, str] = {}


async def handle_negotiation(client: AgentClient, negotiation_id: str, service_id: str):
    """Auto-accept a negotiation and cache its requirements."""
    try:
        svc_name = SERVICE_MAP.get(service_id, "Unknown Service")

        # Fetch negotiation to see requirements BEFORE accepting
        neg = await client.get_negotiation(negotiation_id)
        req_str = neg.requirements or ""
        if req_str:
            _NEGOTIATION_CACHE[negotiation_id] = req_str
            logger.info(f"Negotiation {negotiation_id[:8]}... for '{svc_name}' | requirements: {req_str[:120]}")
        else:
            logger.info(f"Accepting negotiation {negotiation_id[:8]}... for '{svc_name}' (no requirements)")

        result = await client.accept_negotiation(negotiation_id)
        order = result.order
        logger.info(f"Negotiation accepted! order_id={order.order_id[:8]}... status={order.status}")
        return order
    except Exception as e:
        logger.error(f"Failed to accept negotiation {negotiation_id[:8]}: {e}")
        return None


async def handle_order_paid(client: AgentClient, order_id: str):
    """Fulfill a paid order - run analysis and deliver."""
    try:
        order = await client.get_order(order_id)
        service_id = order.service_id
        svc_name = SERVICE_MAP.get(service_id, "Unknown Service")
        logger.info(f"Order paid! Fulfilling '{svc_name}' (order={order_id[:8]}...)")

        # Requirements live on the NEGOTIATION, not the Order.
        # Check cache first (set during handle_negotiation), then fetch as fallback.
        query = ""
        raw_req = ""
        if order.negotiation_id and order.negotiation_id in _NEGOTIATION_CACHE:
            raw_req = _NEGOTIATION_CACHE[order.negotiation_id]
        elif order.negotiation_id:
            try:
                neg = await client.get_negotiation(order.negotiation_id)
                raw_req = neg.requirements or ""
            except Exception as ne:
                logger.warning(f"Could not fetch negotiation {order.negotiation_id[:8]}...: {ne}")
        else:
            logger.warning(f"No negotiation_id on order {order_id[:8]}... - cannot extract query")

        # Parse the requirements string into a usable query
        if raw_req:
            try:
                req_obj = json.loads(raw_req) if isinstance(raw_req, str) else raw_req
                if isinstance(req_obj, dict):
                    query = (req_obj.get("query")
                             or req_obj.get("ticker")
                             or req_obj.get("text")
                             or req_obj.get("input")
                             or req_obj.get("task")
                             or req_obj.get("claim_number")
                             or raw_req)
                else:
                    query = str(raw_req)
            except (json.JSONDecodeError, TypeError):
                query = raw_req

        logger.info(f"Query: '{query[:100]}'")

        # Generate result
        result = await fulfill_service(svc_name, query)
        result_text = _format_report(svc_name, query, result)
        
        # Sanitize: CROO delivery endpoint crashes on non-ASCII
        _trans = str.maketrans({
            "\u2014": "-", "\u2013": "-", "\u2022": "*",
            "\u2018": "'", "\u2019": "'", "\u201c": '"', "\u201d": '"',
            "\u2026": "...", "\u00ae": "(R)", "\u00a9": "(c)",
        })
        result_text = result_text.translate(_trans).encode("ascii", errors="replace").decode("ascii")

        # Deliver (with retries - CROO sometimes needs a few seconds after payment)
        from croo import DeliverOrderRequest, DeliverableType
        deliver_req = DeliverOrderRequest(
            deliverable_type=DeliverableType.TEXT,
            deliverable_text=result_text,
        )

        for attempt in range(5):
            try:
                await asyncio.sleep(3)  # Give CROO time to release the lock
                delivery = await client.deliver_order(order_id, deliver_req)
                logger.info(f"Delivered! delivery_id={delivery.delivery.delivery_id[:8]}... status={delivery.order.status}")
                return
            except Exception as e:
                logger.warning(f"Deliver attempt {attempt+1} failed: {e}")
                if attempt < 4:
                    await asyncio.sleep((attempt + 1) * 5)
                else:
                    raise

    except Exception as e:
        logger.error(f"Failed to fulfill order {order_id[:8]}: {e}", exc_info=True)


async def load_service_map(client: AgentClient):
    """Populate SERVICE_MAP from agent's services."""
    global SERVICE_MAP
    if not AGENT_ID:
        logger.warning("CROO_AGENT_ID not set - skipping service-map load; service names will be 'Unknown'.")
        return
    try:
        orders = await client.list_orders(ListOptions(role="provider"))
        # Use the public API to get service IDs
        import urllib.request
        url = f"{BASE_URL}/backend/v1/public/agents/{AGENT_ID}"
        req = urllib.request.Request(url)
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read())
            agent = data.get("agent", data)
            for svc in agent.get("services", []):
                SERVICE_MAP[svc.get("serviceId", "")] = svc.get("name", "Unknown")
        logger.info(f"Loaded {len(SERVICE_MAP)} services: {list(SERVICE_MAP.values())}")
    except Exception as e:
        logger.warning(f"Could not load service map: {e}")


async def main():
    """Main provider loop - connect WS, listen for events, fulfill orders."""
    if not SDK_KEY:
        logger.error("No CROO_SDK_KEY in .env!")
        return

    config = Config(base_url=BASE_URL, ws_url=WS_URL, rpc_url=RPC_URL)
    client = AgentClient(config, SDK_KEY)

    # Load service mapping
    await load_service_map(client)

    # Connect WebSocket
    logger.info("Connecting to CROO WebSocket...")
    stream = await client.connect_websocket()

    # Register event handlers
    def on_negotiation_created(event):
        logger.info(f"📡 NEGOTIATION_CREATED: svc={event.service_id[:8]}... neg={event.negotiation_id[:8]}...")
        asyncio.create_task(handle_negotiation(client, event.negotiation_id, event.service_id))

    def on_order_paid(event):
        logger.info(f"💰 ORDER_PAID: order={event.order_id[:8]}...")
        asyncio.create_task(handle_order_paid(client, event.order_id))

    def on_order_completed(event):
        logger.info(f"✅ ORDER_COMPLETED: order={event.order_id[:8]}...")

    def on_order_rejected(event):
        logger.info(f"❌ ORDER_REJECTED: order={event.order_id[:8]}... reason={event.reason}")

    def on_order_expired(event):
        logger.info(f"⏰ ORDER_EXPIRED: order={event.order_id[:8]}...")

    def on_any(event):
        logger.info(f"📨 EVENT: type={event.type}")

    stream.on("order_negotiation_created", on_negotiation_created)
    stream.on("order_paid", on_order_paid)
    stream.on("order_completed", on_order_completed)
    stream.on("order_rejected", on_order_rejected)
    stream.on("order_expired", on_order_expired)
    stream.on_any(on_any)

    logger.info("🐸 Remi Macro Intelligence provider is LIVE on CROO!")
    logger.info(f"   Agent: {AGENT_ID or 'unset'} | Services: {len(SERVICE_MAP)}")
    logger.info("   Waiting for orders...")

    # Keep alive
    try:
        while True:
            await asyncio.sleep(1)
            err = stream.err()
            if err:
                logger.error(f"Stream error: {err}")
                break
    except KeyboardInterrupt:
        logger.info("Shutting down...")
    finally:
        await stream.close()
        await client.close()
        logger.info("Disconnected.")


def _demo():
    """Offline demo of the fulfill -> format path (no CROO connection needed)."""
    cases = [
        ("Macro Regime Snapshot", ""),
        ("NVDA Deep Dive", "NVDA"),
        ("NVDA Sentiment Scan", "NVDA"),
        ("Weekly Macro Report", ""),
        ("NVDA Sentiment + Deep Dive", "NVDA"),
    ]
    for svc, q in cases:
        result = asyncio.run(fulfill_service(svc, q))
        print(_format_report(svc, q, result))
        print("\n")


if __name__ == "__main__":
    if "--demo" in sys.argv:
        _demo()
    else:
        asyncio.run(main())

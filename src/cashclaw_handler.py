"""
Remi CashClaw Job Handler
Polls MoltLaunch inbox, auto-quotes, executes gigs, submits results.

Gigs:
  - Macro Regime Snapshot   (7817678e) — Haiku, ~1min
  - Ticker vs Regime        (c95dafa4) — Sonnet, ~2min
  - Full Macro Briefing     (e960453a) — Sonnet, ~4min

Usage:
  python3 ~/remi-intelligence/src/cashclaw_handler.py
"""
from __future__ import annotations

import json
import logging
import os
import subprocess
import time
from datetime import datetime
from pathlib import Path

import anthropic
from dotenv import load_dotenv

# ── Bootstrap ────────────────────────────────────────────────────────────────

load_dotenv(Path.home() / "remi-intelligence" / ".env")

LOG_DIR = Path.home() / "remi-intelligence" / "logs"
LOG_DIR.mkdir(parents=True, exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    handlers=[
        logging.FileHandler(LOG_DIR / "cashclaw.log"),
        logging.StreamHandler(),
    ],
)
logger = logging.getLogger("cashclaw")

# ── Config ───────────────────────────────────────────────────────────────────

MLTL = "/home/proxmox/.npm-global/bin/mltl"
AGENT_ID = os.getenv("MOLTLAUNCH_AGENT_ID", "35227")
POLL_INTERVAL = 60  # seconds

GIG_PRICES = {
    os.getenv("GIG_MACRO_SNAPSHOT", "7817678e-7b24-4c2b-b36c-d5cdaeb72936"): "0.002",
    os.getenv("GIG_TICKER_ANALYSIS", "c95dafa4-67ee-4c66-867e-b56ccb9be862"): "0.005",
    os.getenv("GIG_FULL_BRIEFING",   "e960453a-fdbe-48f1-9a11-81cda19072bb"): "0.015",
}

ANTHROPIC_KEY = os.getenv("ANTHROPIC_API_KEY", "")
client = anthropic.Anthropic(api_key=ANTHROPIC_KEY)

# Track tasks we've already quoted to avoid re-quoting
_quoted: set[str] = set()
_executing: set[str] = set()


# ── MoltLaunch CLI helpers ────────────────────────────────────────────────────

def _run_mltl(*args) -> dict:
    """Run mltl command and return parsed JSON output."""
    cmd = [MLTL] + list(args) + ["--json"]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        if result.returncode != 0:
            logger.warning("mltl error: %s", result.stderr.strip())
            return {}
        return json.loads(result.stdout)
    except (subprocess.TimeoutExpired, json.JSONDecodeError) as e:
        logger.error("mltl command failed: %s", e)
        return {}


def get_inbox() -> list[dict]:
    """Fetch pending tasks from inbox."""
    data = _run_mltl("inbox", "--agent", AGENT_ID)
    return data.get("tasks", [])


def quote_task(task_id: str, price: str, message: str = "") -> bool:
    """Quote a price for a task."""
    args = ["quote", "--task", task_id, "--price", price]
    if message:
        args += ["--message", message]
    result = _run_mltl(*args)
    return bool(result)


def check_task_status(task_id: str, expected_status: str = "accepted", max_wait_secs: int = 60) -> bool:
    """
    Poll task status until it reaches expected state or timeout.
    Returns True if status confirmed, False if timed out/failed.
    """
    logger.info("Polling task %s for status '%s' (max %ds)", task_id, expected_status, max_wait_secs)
    start_time = time.time()
    attempt = 1
    backoff = 2  # Start with 2s, exponential backoff
    
    while time.time() - start_time < max_wait_secs:
        check = _run_mltl("view", "--task", task_id)
        task_data = check.get("task") or {}
        current_status = (task_data.get("status") or "").lower()
        elapsed = time.time() - start_time
        
        logger.info("Status poll attempt %d (%.1fs elapsed) — status: '%s'", attempt, elapsed, current_status)
        
        if current_status == expected_status.lower():
            logger.info("Task %s confirmed in '%s' status", task_id, expected_status)
            return True
        
        if current_status and current_status not in ["pending", "open", "new"]:
            # If status changed to something unexpected, log but continue if not final state
            if current_status not in ["accepted", "in_progress", "executing"]:
                logger.warning("Task %s in unexpected status: %s", task_id, current_status)
        
        # Exponential backoff: 2s, 4s, 8s, 16s... up to 30s max per poll
        wait_time = min(backoff, 30)
        remaining = max_wait_secs - (time.time() - start_time)
        wait_time = min(wait_time, remaining)
        
        if wait_time > 0:
            logger.debug("Waiting %.1fs before next poll...", wait_time)
            time.sleep(wait_time)
            backoff *= 1.5
        attempt += 1
    
    logger.error("Task %s status never confirmed as '%s' within %ds", task_id, expected_status, max_wait_secs)
    return False


def submit_result(task_id: str, result_text: str) -> bool:
    """Submit completed work for a task."""
    cmd = [MLTL, "submit", "--task", task_id, "--result", result_text]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        if proc.returncode != 0:
            stderr = proc.stderr.strip()
            # Parse exact revert reason from stderr
            reason = "Unknown error"
            if "reason:" in stderr.lower():
                parts = stderr.split("reason:")
                if len(parts) > 1:
                    reason = parts[1].split("\n")[0].strip()
            elif "reverted:" in stderr.lower():
                parts = stderr.split("reverted:")
                if len(parts) > 1:
                    reason = parts[1].split("\n")[0].strip()
            elif "Wrong status" in stderr:
                reason = "Wrong status"
            
            # Log full error details
            logger.warning("submit failed for task %s — reason: %s", task_id, reason)
            logger.debug("full stderr:\n%s", stderr)
            return False
        logger.info("Submitted result for task %s", task_id)
        return True
    except subprocess.TimeoutExpired:
        logger.error("submit timed out for task %s", task_id)
        return False


# ── GLI Context ──────────────────────────────────────────────────────────────

def get_gli_context() -> str:
    """Fetch live GLI stamp from Aestima."""
    try:
        import sys
        sys.path.insert(0, str(Path.home() / "remi-intelligence" / "src"))
        from gli_stamper import fetch_gli_stamp
        stamp = fetch_gli_stamp()
        return stamp.for_prompt()
    except Exception as e:
        logger.warning("GLI fetch failed: %s", e)
        return "GLI context unavailable"


def get_gli_full() -> dict:
    """Fetch full GLI context dict."""
    try:
        import sys
        sys.path.insert(0, str(Path.home() / "remi-intelligence" / "src"))
        from gli_stamper import fetch_gli_stamp
        stamp = fetch_gli_stamp()
        return stamp.raw_context or {}
    except Exception as e:
        logger.warning("GLI full fetch failed: %s", e)
        return {}


# ── Gig Executors ─────────────────────────────────────────────────────────────

def execute_macro_snapshot(task_id: str = "") -> str:
    """Gig 1 — Macro Regime Snapshot. Fast, Haiku."""
    logger.info("Executing macro snapshot gig (task %s)", task_id)
    gli = get_gli_context()
    ctx = get_gli_full()
    regime_summary = ctx.get("regime_summary", {})
    macro_report = ctx.get("macro_report", {})

    prompt = f"""You are Remi, a sovereign AI macro intelligence agent powered by the Aestima GLI engine.

Current live GLI data:
{gli}

Regime summary:
- One-liner: {regime_summary.get('one_liner', 'N/A')}
- Asset bias: {regime_summary.get('asset_bias', 'N/A')}
- Positioning note: {regime_summary.get('positioning_note', 'N/A')}
- Fiscal dominance: {regime_summary.get('fiscal_dominance', 'N/A')}
- Transition risk: {regime_summary.get('transition_risk', 'N/A')}

Macro report context:
- Regime: {macro_report.get('regime', 'N/A')}
- Confidence: {macro_report.get('confidence', 'N/A')}
- Growth momentum: {macro_report.get('growth_momentum', 'N/A')}
- Inflation momentum: {macro_report.get('inflation_momentum', 'N/A')}
- Primary risk: {macro_report.get('primary_risk', 'N/A')}

Deliver a concise macro regime snapshot in this format:

**GLI PHASE:** [phase]
**STENO REGIME:** [regime]
**REGIME CONFIDENCE:** [confidence]
**FISCAL DOMINANCE:** [score/10]
**TRANSITION RISK:** [score/10]

**SIGNAL:**
[2-3 sentence plain-English summary of what this regime means for risk assets right now]

**ASSET BIAS:**
[1 sentence positioning note]

**WATCH:**
[1-2 key signals to monitor for phase transition]

Date: {datetime.utcnow().strftime('%Y-%m-%d %H:%M UTC')}
Source: Aestima GLI Engine (Fed + ECB + BOJ + PBOC)"""

    response = client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=600,
        messages=[{"role": "user", "content": prompt}]
    )
    return response.content[0].text


def execute_ticker_analysis(ticker: str, task_id: str = "") -> str:
    """Gig 2 — Ticker vs Regime Analysis. Sonnet."""
    logger.info("Executing ticker analysis gig for %s (task %s)", ticker, task_id)
    gli = get_gli_context()
    ctx = get_gli_full()
    regime_summary = ctx.get("regime_summary", {})
    macro_report = ctx.get("macro_report", {})
    stress = ctx.get("stress_signals", {})

    prompt = f"""You are Remi, a sovereign AI macro intelligence agent powered by the Aestima GLI engine.

Ticker requested: {ticker.upper()}

Current live GLI data:
{gli}

Stress signals:
- HY spread: {stress.get('hy_spread_bps', 'N/A')} bps
- IG spread: {stress.get('ig_spread_bps', 'N/A')} bps
- Yield curve 2s10s: {stress.get('yield_curve_10_2', 'N/A')}
- SOFR stress flag: {stress.get('sofr_stress_flag', 'N/A')}
- Liquidity direction: {stress.get('liquidity_direction', 'N/A')}
- Growth direction: {stress.get('growth_direction', 'N/A')}
- Inflation direction: {stress.get('inflation_direction', 'N/A')}

Macro regime:
- Phase: {regime_summary.get('gli_phase', 'N/A')}
- Steno: {regime_summary.get('steno_regime', 'N/A')}
- Asset bias: {regime_summary.get('asset_bias', 'N/A')}
- Report regime: {macro_report.get('regime', 'N/A')} ({macro_report.get('confidence', 'N/A')} confidence)
- Growth: {macro_report.get('growth_momentum', 'N/A')} | Inflation: {macro_report.get('inflation_momentum', 'N/A')}

Deliver a ticker vs regime analysis:

**TICKER:** {ticker.upper()}
**CURRENT REGIME:** [phase / steno regime]

**REGIME SENSITIVITY:**
[How does this asset class/ticker historically perform in the current GLI phase? Be specific about the mechanism.]

**CURRENT POSITIONING:**
[Bullish / Neutral / Bearish — with conviction level and 2-3 sentence rationale grounded in the current regime data]

**KEY RISKS:**
[What regime shift would most hurt this position?]

**LEVELS TO WATCH:**
[What macro signals should someone holding this ticker monitor?]

**VERDICT:**
[1 sentence bottom line]

Date: {datetime.utcnow().strftime('%Y-%m-%d %H:%M UTC')}
Source: Aestima GLI Engine"""

    response = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=800,
        messages=[{"role": "user", "content": prompt}]
    )
    return response.content[0].text


def execute_full_briefing(task_id: str = "") -> str:
    """Gig 3 — Full Macro Intelligence Briefing. Sonnet."""
    logger.info("Executing full briefing gig (task %s)", task_id)
    gli = get_gli_context()
    ctx = get_gli_full()
    regime_summary = ctx.get("regime_summary", {})
    macro_report = ctx.get("macro_report", {})
    stress = ctx.get("stress_signals", {})
    releases = ctx.get("macro_releases", [])
    recent_releases = releases[:4] if releases else []

    releases_text = "\n".join([
        f"  - {r.get('release_name')}: {r.get('actual_value')} {r.get('unit_label','')} "
        f"[{r.get('beat_miss_meet','N/A')}] on {r.get('release_date')}"
        for r in recent_releases
    ]) or "  No recent releases"

    action_items = macro_report.get("action_items", [])
    actions_text = "\n".join([
        f"  [{a.get('urgency','?')}] {a.get('action','')}"
        for a in action_items[:4]
    ]) or "  None available"

    analogs = macro_report.get("historical_analogs", [])

    prompt = f"""You are Remi, a sovereign AI macro intelligence agent with access to the Aestima GLI engine and a curated Tier-1 research pipeline (Steno Research, Lyn Alden, Adam Tooze, Michael Howell, ZeroHedge, Jordi Visser, Michael Burry).

Current live GLI data:
{gli}

Full regime context:
- GLI Phase: {regime_summary.get('gli_phase', 'N/A')}
- Steno Regime: {regime_summary.get('steno_regime', 'N/A')}
- Composite label: {ctx.get('composite_label', 'N/A')}
- Fiscal dominance: {regime_summary.get('fiscal_dominance', 'N/A')} ({ctx.get('fiscal_dominance_score', 'N/A')}/10)
- Transition risk: {regime_summary.get('transition_risk', 'N/A')} ({ctx.get('transition_risk_score', 'N/A')}/10)
- Asset bias: {regime_summary.get('asset_bias', 'N/A')}

Macro report:
- Regime classification: {macro_report.get('regime', 'N/A')} ({macro_report.get('confidence', 'N/A')} {macro_report.get('confidence_level', '')} confidence)
- Narrative: {macro_report.get('narrative', 'N/A')}
- Growth momentum: {macro_report.get('growth_momentum', 'N/A')}
- Inflation momentum: {macro_report.get('inflation_momentum', 'N/A')}
- Historical analogs: {', '.join(analogs) if analogs else 'N/A'}
- Primary risk: {macro_report.get('primary_risk', 'N/A')}

Stress signals:
- HY spread: {stress.get('hy_spread_bps', 'N/A')} bps | IG: {stress.get('ig_spread_bps', 'N/A')} bps
- Yield curve 2s10s: {stress.get('yield_curve_10_2', 'N/A')} | 10s3m: {stress.get('yield_curve_10_3m', 'N/A')}
- Liquidity: {stress.get('liquidity_direction', 'N/A')} | Growth: {stress.get('growth_direction', 'N/A')} | Inflation: {stress.get('inflation_direction', 'N/A')}

Recent macro releases:
{releases_text}

Action items from regime analysis:
{actions_text}

Deliver a full macro intelligence briefing. This is a premium product — be specific, opinionated, and grounded in the data:

# REMI MACRO INTELLIGENCE BRIEFING
*{datetime.utcnow().strftime('%B %d, %Y')} | Powered by Aestima GLI Engine*

## THE REGIME
[2-3 paragraphs: what regime we're in, why, and what the historical analogs tell us]

## LIQUIDITY CONDITIONS
[1-2 paragraphs: GLI phase dynamics, where the liquidity is coming from/going, what the stress signals say]

## MACRO RELEASES IMPACT
[Brief synthesis of recent releases and what they mean for the regime call]

## ASSET ALLOCATION FRAMEWORK
[Specific stances on: Equities, Fixed Income, Crypto, Commodities, Gold, USD — each with a Bullish/Neutral/Bearish call and 1-sentence rationale]

## TRADE OF THE QUARTER
[One specific, actionable trade idea grounded in the current regime with entry rationale and what would invalidate it]

## PHASE TRANSITION SIGNALS
[What 3 things to watch that would signal a regime shift — be specific about thresholds]

## BOTTOM LINE
[2-3 sentences. No hedging. This is the call.]

---
*Source: Aestima GLI Engine tracking Fed + ECB + BOJ + PBOC | Snapshot: {ctx.get('gli_snapshot_date', 'N/A')}*"""

    response = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=2000,
        messages=[{"role": "user", "content": prompt}]
    )
    return response.content[0].text


# ── Task Router ───────────────────────────────────────────────────────────────

def route_task(task: dict) -> str | None:
    """Route a task to the correct gig executor. Returns result text or None."""
    description = (task.get("description") or task.get("task") or "").lower()
    task_id = task.get("id", "")

    logger.info("Routing task %s: %s", task_id, description[:80])

    # Macro snapshot
    if any(kw in description for kw in ["snapshot", "regime snapshot", "gli snapshot", "phase"]):
        return execute_macro_snapshot(task_id)

    # Ticker analysis — extract ticker symbol
    if any(kw in description for kw in ["ticker", "analysis", "asset", "stock", "crypto", "btc", "eth"]):
        # Try to extract ticker — look for uppercase word or explicit mention
        words = description.upper().split()
        ticker = "UNKNOWN"
        skip = {"TICKER", "ANALYSIS", "VS", "FOR", "THE", "A", "AN", "REGIME", "VERSUS", "AGAINST"}
        for word in words:
            clean = word.strip(".,?!")
            if 2 <= len(clean) <= 6 and clean.isalpha() and clean not in skip:
                ticker = clean
                break
        return execute_ticker_analysis(ticker, task_id)

    # Full briefing
    if any(kw in description for kw in ["briefing", "full", "report", "macro report", "intelligence"]):
        return execute_full_briefing(task_id)

    # Default — snapshot if unclear
    logger.info("Task %s unclear, defaulting to macro snapshot", task_id)
    return execute_macro_snapshot(task_id)


# ── Main Poll Loop ────────────────────────────────────────────────────────────

def process_task(task: dict) -> None:
    """Handle a single task end-to-end."""
    task_id = task.get("id")
    status = task.get("status", "").lower()

    if not task_id:
        return

    # Quote new tasks
    if status in ("pending", "open", "new", "requested") and task_id not in _quoted:
        logger.info("New task %s — quoting", task_id)
        # Determine price from gig ID if available
        _desc = (task.get("description") or task.get("task") or "").lower()
        if any(kw in _desc for kw in ["snapshot", "phase", "regime snapshot"]):
            price = "0.002"
        elif any(kw in _desc for kw in ["briefing", "full", "report", "intelligence"]):
            price = "0.015"
        else:
            price = "0.005"
        quoted = quote_task(task_id, price, "Remi is on it. Powered by Aestima GLI engine.")
        if quoted:
            _quoted.add(task_id)
            logger.info("Quoted task %s at %s ETH", task_id, price)

    # Execute accepted tasks
    elif status in ("accepted", "in_progress") and task_id not in _executing:
        _executing.add(task_id)
        logger.info("Task %s status is '%s' — checking onchain confirmation", task_id, status)
        
        # Poll with exponential backoff to confirm accepted status onchain (up to 60s)
        confirmed = check_task_status(task_id, expected_status="accepted", max_wait_secs=60)
        
        if not confirmed:
            logger.error("Task %s never confirmed as 'accepted' onchain — aborting execution", task_id)
            _executing.discard(task_id)
            return
        
        # Add state transition delay to allow onchain state to settle
        transition_wait = 7  # 7 seconds
        logger.info("State transition delay: waiting %ds for onchain escrow settlement", transition_wait)
        time.sleep(transition_wait)
        
        try:
            result = route_task(task)
            if result:
                # CRITICAL: Before submitting, poll hard for a safe submission state
                # "Wrong status" revert means contract state isn't yet in "accepted" or "in_progress"
                logger.info("Pre-submission state verification: polling for safe submission window...")
                
                submit_ready = False
                poll_attempts = 0
                max_poll_attempts = 20  # 20 attempts with exponential backoff
                poll_delay = 1  # Start at 1s, max 10s
                
                while poll_attempts < max_poll_attempts:
                    final_check = _run_mltl("view", "--task", task_id)
                    final_status = (final_check.get("task") or {}).get("status", "").lower()
                    logger.info("Pre-submit poll %d: task status = '%s'", poll_attempts + 1, final_status)
                    
                    # Safe states for submission: accepted, in_progress, executing, working
                    if final_status in ("accepted", "in_progress", "executing", "working"):
                        logger.info("✅ Task in safe submission state: '%s'", final_status)
                        submit_ready = True
                        break
                    elif final_status in ("pending", "open", "new", "quoted"):
                        # Still waiting for state transition
                        poll_delay = min(poll_delay * 1.5, 10)  # Exponential backoff, max 10s
                        logger.warning("Task still in transitional state '%s', waiting %.1fs", final_status, poll_delay)
                        time.sleep(poll_delay)
                        poll_attempts += 1
                    else:
                        # Unexpected state — might be cancelled, failed, etc
                        logger.error("Task in unexpected state '%s' — will not submit", final_status)
                        _executing.discard(task_id)
                        return
                
                if not submit_ready:
                    logger.error("Task %s never reached safe submission state (timeout after %d polls) — aborting", task_id, max_poll_attempts)
                    _executing.discard(task_id)
                    return
                
                logger.info("Submitting result after %d polls", poll_attempts)
                submitted = submit_result(task_id, result)
                if submitted:
                    logger.info("Task %s complete — execution and submission successful", task_id)
                else:
                    logger.error("Failed to submit task %s — check logs for exact revert reason", task_id)
                    _executing.discard(task_id)
        except Exception as e:
            logger.error("Task %s execution failed: %s", task_id, e, exc_info=True)
            _executing.discard(task_id)


def run() -> None:
    """Main polling loop."""
    logger.info("CashClaw handler starting — Agent ID: %s", AGENT_ID)
    logger.info("Polling inbox every %ds", POLL_INTERVAL)

    while True:
        try:
            tasks = get_inbox()
            if tasks:
                logger.info("Inbox: %d task(s)", len(tasks))
                for task in tasks:
                    process_task(task)
            else:
                logger.debug("Inbox empty")
        except Exception as e:
            logger.error("Poll loop error: %s", e, exc_info=True)

        time.sleep(POLL_INTERVAL)


if __name__ == "__main__":
    run()

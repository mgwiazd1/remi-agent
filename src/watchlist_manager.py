"""watchlist_manager.py — Manages the active ticker watchlist."""
import json
import os
import logging
from datetime import datetime, timezone
from pathlib import Path
import httpx
from ticker_intel import get_ticker_intelligence, format_dossier_for_telegram

logger = logging.getLogger(__name__)
WATCHLIST_PATH = Path(__file__).parent.parent / "config" / "watchlist.json"
AESTIMA_INTERNAL = "http://192.168.1.198:8000"
AESTIMA_EXTERNAL = "https://aestima.ai"


def _aestima_base():
    try:
        r = httpx.get(f"{AESTIMA_INTERNAL}/health", timeout=3)
        if r.status_code == 200:
            return AESTIMA_INTERNAL
    except Exception:
        pass
    return AESTIMA_EXTERNAL


def load_watchlist():
    if not WATCHLIST_PATH.exists():
        return {"_meta": {}, "tickers": {}}
    with open(WATCHLIST_PATH) as f:
        return json.load(f)


def save_watchlist(wl):
    wl["_meta"]["last_updated"] = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    with open(WATCHLIST_PATH, "w") as f:
        json.dump(wl, f, indent=2)


def add_ticker(ticker, company, thesis, conviction="medium", sizing="standard",
               target_return="2X", horizon_years=2, risks=None, catalysts=None,
               source="", added_by="MG"):
    wl = load_watchlist()
    tu = ticker.upper().strip()
    if tu in wl["tickers"]:
        return f"Already on watchlist: {tu}"
    wl["tickers"][tu] = {
        "company": company, "thesis_summary": thesis, "conviction": conviction,
        "sizing": sizing, "target_return": target_return, "horizon_years": horizon_years,
        "key_risks": risks or [], "catalysts": catalysts or [],
        "source_attribution": source, "added_by": added_by,
        "added_date": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
        "last_eval_date": None, "last_eval_gli_phase": None, "tags": []}
    save_watchlist(wl)
    return f"{tu} ({company}) added to watchlist."


def add_ticker_from_pick(ticker, company, thesis, conviction="medium",
                         pick_type="long", target_return="2X", time_horizon="2y",
                         key_risks=None, catalysts=None, gli_phase=None,
                         evidence_chain=None):
    """Add ticker to watchlist from an approved pick. Returns status string."""
    wl = load_watchlist()
    tu = ticker.upper().strip()
    if tu in wl["tickers"]:
        return f"{tu} already on watchlist — no change"

    horizon_years = 2
    try:
        horizon_years = int(time_horizon.rstrip("y")) if time_horizon else 2
    except (ValueError, AttributeError):
        pass

    wl["tickers"][tu] = {
        "company": company or tu,
        "thesis_summary": (thesis or "Remi pick — see picks engine")[:500],
        "conviction": conviction,
        "sizing": "standard",
        "target_return": target_return or "2X",
        "horizon_years": horizon_years,
        "key_risks": key_risks or [],
        "catalysts": catalysts or [],
        "source_attribution": "remi_picks_engine",
        "added_by": "pick_approval_auto",
        "added_date": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
        "last_eval_date": None,
        "last_eval_gli_phase": gli_phase,
        "pick_type": pick_type,
        "tags": ["remi_pick"],
    }
    if evidence_chain:
        wl["tickers"][tu]["evidence_chain"] = evidence_chain[:1000]
    save_watchlist(wl)
    return f"Added {tu} to watchlist ({pick_type}, {conviction})"


def remove_ticker(ticker):
    wl = load_watchlist()
    tu = ticker.upper().strip()
    if tu not in wl["tickers"]:
        return f"{tu} not on watchlist."
    del wl["tickers"][tu]
    save_watchlist(wl)
    return f"{tu} removed."


def list_watchlist():
    wl = load_watchlist()
    tickers = wl.get("tickers", {})
    if not tickers:
        return "Watchlist empty. Use /watch add <TICKER>"
    order = {"high": 0, "medium": 1, "speculative": 2, "low": 3}
    lines = [f"REMI ACTIVE COVERAGE — {len(tickers)} tickers\n"]
    for tk, e in sorted(tickers.items(), key=lambda x: order.get(x[1].get("conviction","low"),3)):
        lines.append(f"{tk} — {e.get('company', tk)}")
        lines.append(f"  {e.get('conviction','?').upper()} | Target: {e.get('target_return','?')} | Eval: {e.get('last_eval_date') or 'never'}")
        lines.append("")
    return "\n".join(lines)


def get_dossier(ticker):
    wl = load_watchlist()
    tu = ticker.upper().strip()
    if tu not in wl["tickers"]:
        return f"{tu} not on watchlist."
    intel = get_ticker_intelligence(tu, lookback_days=30)
    return format_dossier_for_telegram(tu, wl["tickers"][tu], intel)


async def run_thesis_eval(ticker, force_refresh=False):
    wl = load_watchlist()
    tu = ticker.upper().strip()
    if tu not in wl["tickers"]:
        return f"{tu} not on watchlist."
    entry = wl["tickers"][tu]
    base = _aestima_base()
    key = os.environ.get("AESTIMA_AGENT_KEY", "")
    payload = {"theme": f"{tu} {entry.get('company', tu)}",
               "target_return": entry.get("target_return", "2X"),
               "time_horizon_years": entry.get("horizon_years", 2),
               "specific_tickers": [tu],
               "thesis_statement": entry.get("thesis_summary", ""),
               "force_refresh": force_refresh}
    try:
        async with httpx.AsyncClient(timeout=300) as client:
            resp = await client.post(f"{base}/api/analysis/thesis-eval",
                json=payload, headers={"X-Agent-Key": key, "Content-Type": "application/json"})
        if resp.status_code != 200:
            return f"Thesis eval failed: HTTP {resp.status_code} — {resp.text[:200]}"
        data = resp.json()
        wl["tickers"][tu]["last_eval_date"] = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        gli = data.get("gli_phase") or data.get("context", {}).get("gli_phase", "unknown")
        wl["tickers"][tu]["last_eval_gli_phase"] = gli
        save_watchlist(wl)
        verdict = data.get("verdict") or data.get("recommendation") or {}
        vl = verdict.get("label") or data.get("verdict_label") or "UNKNOWN"
        conf = verdict.get("confidence") or data.get("confidence") or 0
        hl = verdict.get("headline") or data.get("headline") or "See full report"
        return (f"THESIS EVAL — {tu}\n{entry.get('company', tu)}\n\n"
                f"Verdict: {vl}\nConfidence: {conf:.0%}\nGLI Phase: {gli}\n\n{hl}")
    except httpx.TimeoutException:
        return f"Thesis eval for {tu} timed out (>300s)."
    except Exception as e:
        logger.error(f"Thesis eval error for {tu}: {e}")
        return f"Error: {str(e)[:200]}"

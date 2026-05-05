"""
trade_analysis_handler.py — Two-mode trade analysis for /analyze and /take commands.

Deep Dive (/analyze): 23-step institutional analysis via GLM-4.7 → Aestima publish.
Quick Take (/take): Abbreviated analysis + tweet thread draft via GLM-5 → TG reply.

See ~/.hermes/skills/investing/trade-analysis/references/prompts-spec.md for full spec.
"""
import json
import logging
import os
import re
import sqlite3
from datetime import datetime
from pathlib import Path

import httpx
from dotenv import load_dotenv

load_dotenv(os.path.expanduser("~/remi-intelligence/.env"))

logger = logging.getLogger(__name__)

AESTIMA_BASE = os.getenv("AESTIMA_BASE_URL", "http://192.168.1.198:8000")
AESTIMA_AGENT_KEY = os.getenv("AESTIMA_AGENT_KEY", "")
DASHBOARD_BASE = "http://192.168.1.100:8501"
GLM_API_KEY = os.getenv("GLM_API_KEY", "")
GLM_BASE_URL = os.getenv("GLM_BASE_URL", "https://api.z.ai/api/coding/paas/v4")
DB_PATH = os.getenv("DB_PATH", os.path.expanduser("~/remi-intelligence/remi_intelligence.db"))
LOG_PATH = os.path.expanduser("~/remi-intelligence/logs/trade_analysis.log")
SPEC_PATH = os.path.expanduser(
    "~/.hermes/skills/investing/trade-analysis/references/prompts-spec.md"
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _db():
    conn = sqlite3.connect(DB_PATH, timeout=10)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.row_factory = sqlite3.Row
    return conn


def _log(msg: str):
    ts = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC")
    with open(LOG_PATH, "a") as f:
        f.write(f"{ts} — {msg}\n")


def _bot_reply(chat_id: int, text: str, reply_to: int = None):
    """Send a message via the Remi bot."""
    import requests
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = {"chat_id": chat_id, "text": text, "parse_mode": "Markdown"}
    if reply_to:
        payload["reply_to_message_id"] = reply_to
    try:
        requests.post(url, json=payload, timeout=10)
    except Exception as e:
        logger.error(f"Bot reply failed: {e}")


def _parse_command(text: str) -> dict | None:
    """Parse /analyze or /take command into components."""
    text = text.strip()
    parts = text.split()
    if not parts:
        return None
    cmd = parts[0].lower()
    if cmd not in ("/analyze", "/take"):
        return None
    if len(parts) < 2:
        return None

    ticker = parts[1].upper().lstrip("$")
    horizon = parts[2].lower() if len(parts) > 2 else "swing"
    # Default horizons mapping
    horizon_map = {
        "tactical": "Tactical 2-10d",
        "swing": "Swing 2-8w",
        "position": "Position 2-9m",
    }
    horizon_full = horizon_map.get(horizon, f"{horizon.capitalize()}")

    price = parts[3] if len(parts) > 3 else "market"
    # Gather remaining as user note
    user_note = " ".join(parts[4:]) if len(parts) > 4 else ""

    return {
        "cmd": cmd,
        "ticker": ticker,
        "horizon": horizon,
        "horizon_full": horizon_full,
        "price": price,
        "user_note": user_note,
    }


# ---------------------------------------------------------------------------
# Data Enrichment Layers
# ---------------------------------------------------------------------------

def _fetch_aestima_context() -> dict:
    """Layer 1: Aestima GLI context + delta."""
    context = {}
    try:
        r = httpx.get(
            f"{AESTIMA_BASE}/api/agent/context",
            headers={"X-Agent-Key": AESTIMA_AGENT_KEY},
            timeout=10,
        )
        if r.status_code == 200:
            context["gli"] = r.json()
        else:
            _log(f"Aestima /context failed: HTTP {r.status_code}")
    except Exception as e:
        _log(f"Aestima /context error: {e}")

    try:
        r = httpx.get(
            f"{AESTIMA_BASE}/api/agent/context/delta",
            headers={"X-Agent-Key": AESTIMA_AGENT_KEY},
            timeout=10,
        )
        if r.status_code == 200:
            context["delta"] = r.json()
    except Exception as e:
        _log(f"Aestima /delta error: {e}")

    return context


def _fetch_dossier(ticker: str) -> dict:
    """Layer 2: Ticker dossier from watchlist."""
    try:
        r = httpx.get(
            f"{DASHBOARD_BASE}/api/watchlist/dossier/{ticker}",
            headers={"X-Agent-Key": AESTIMA_AGENT_KEY},
            timeout=10,
        )
        if r.status_code == 200:
            return r.json()
    except Exception as e:
        _log(f"Dossier fetch error for {ticker}: {e}")
    return {}


def _fetch_sector_themes(sector: str, limit: int = 5) -> list:
    """Layer 3: Top sector themes from vault."""
    conn = _db()
    try:
        rows = conn.execute("""
            SELECT theme_key, theme_label, velocity_score, velocity_delta, sentiment, last_seen_at
            FROM themes
            WHERE sector = ? AND last_seen_at > datetime('now', '-14 days')
            ORDER BY velocity_score DESC LIMIT ?
        """, (sector, limit)).fetchall()
        return [dict(r) for r in rows]
    except Exception as e:
        _log(f"Sector themes query error: {e}")
        return []
    finally:
        conn.close()


def _fetch_prior_deep_dives(ticker: str, limit: int = 3) -> list:
    """Layer 4: Prior deep dives on same ticker."""
    try:
        r = httpx.get(
            f"{AESTIMA_BASE}/api/agent/remi-intel/research",
            params={"ticker": ticker, "limit": limit},
            headers={"X-Agent-Key": AESTIMA_AGENT_KEY},
            timeout=10,
        )
        if r.status_code == 200:
            return r.json() if isinstance(r.json(), list) else r.json().get("reports", [])
    except Exception as e:
        _log(f"Prior deep dives fetch error: {e}")
    return []


def _fetch_recent_docs(ticker: str, limit: int = 5) -> list:
    """Layer 5: Recent documents mentioning ticker (Pablo PDF drops)."""
    conn = _db()
    try:
        rows = conn.execute("""
            SELECT d.title, d.source_name, dt.theme_key, dt.sentiment
            FROM documents d
            JOIN document_themes dt ON d.id = dt.document_id
            WHERE dt.tickers_mentioned LIKE ?
              AND d.ingested_at > datetime('now', '-30 days')
            ORDER BY d.ingested_at DESC LIMIT ?
        """, (f'%{ticker}%', limit)).fetchall()
        return [dict(r) for r in rows]
    except Exception as e:
        _log(f"Recent docs query error: {e}")
        return []
    finally:
        conn.close()


def _fetch_book_frameworks(sector: str, company_type: str = "", limit: int = 3) -> list:
    """Layer 6: Book/framework intelligence (Lynch archetypes etc)."""
    conn = _db()
    try:
        rows = conn.execute("""
            SELECT concept, category, source_book
            FROM investing_concepts
            WHERE concept LIKE ? OR concept LIKE ?
            LIMIT ?
        """, (f'%{company_type}%', f'%{sector}%', limit)).fetchall()
        return [dict(r) for r in rows]
    except Exception as e:
        # Table may not exist yet
        _log(f"Book frameworks query skipped: {e}")
        return []
    finally:
        conn.close()


def _infer_sector(ticker: str) -> str:
    """Infer sector for a ticker — used to query sector themes."""
    # Check watchlist first
    try:
        wl_path = os.path.expanduser("~/remi-intelligence/watchlist.json")
        if os.path.exists(wl_path):
            wl = json.loads(open(wl_path).read())
            entry = wl.get("tickers", {}).get(ticker.upper(), {})
            if entry.get("sector"):
                return entry["sector"].lower()
    except Exception:
        pass
    # Check ticker hub
    hub_path = f"/docker/obsidian/investing/Intelligence/Tickers/TICKER_{ticker.upper()}.md"
    if os.path.exists(hub_path):
        content = open(hub_path).read(500)
        m = re.search(r'sector:\s*(\w+)', content, re.I)
        if m:
            return m.group(1).lower()
    return "macro"


def _check_prior_fresh(ticker: str) -> str | None:
    """Check if there's a recent (< 7 day) deep dive. Returns message or None."""
    priors = _fetch_prior_deep_dives(ticker, limit=3)
    if not priors:
        return None
    for p in priors:
        created = p.get("created_at") or p.get("published_at") or ""
        if not created:
            continue
        try:
            dt = datetime.fromisoformat(created.replace("Z", "+00:00"))
            age_days = (datetime.now(dt.tzinfo) - dt).days
            if age_days < 7:
                score = p.get("conviction_score", "?")
                return (
                    f"I have a deep dive on {ticker} from {created[:10]} "
                    f"(conviction: {score}). "
                    f"Want a fresh run or should I surface the existing one?"
                )
        except Exception:
            continue
    return None


# ---------------------------------------------------------------------------
# GLM API calls
# ---------------------------------------------------------------------------

def _call_glm(messages: list, model: str = "glm-4.7",
              max_tokens: int = 8000, temperature: float = 0.3) -> tuple[str, str] | None:
    """Direct GLM call for trade analysis. Returns (text, model_used) or None."""
    if not GLM_API_KEY:
        logger.error("GLM_API_KEY not set")
        return None

    def _req(m):
        return httpx.post(
            f"{GLM_BASE_URL}/chat/completions",
            headers={"Authorization": f"Bearer {GLM_API_KEY}",
                      "Content-Type": "application/json"},
            json={"model": m, "messages": messages,
                  "max_tokens": max_tokens, "temperature": temperature},
            timeout=300,
        )

    r = _req(model)
    model_used = model

    if r.status_code == 429:
        fallback = "glm-5" if model.startswith("glm-4") else "glm-4.7"
        _log(f"429 on {model}, falling back to {fallback}")
        r = _req(fallback)
        model_used = fallback

    if r.status_code != 200:
        _log(f"GLM call failed ({model_used}): {r.status_code} {r.text[:300]}")
        return None

    data = r.json()
    content = data["choices"][0]["message"].get("content", "")
    if not content:
        content = data["choices"][0]["message"].get("reasoning_content", "")
    if not content:
        return None

    return content, model_used


# ---------------------------------------------------------------------------
# Prompt Building
# ---------------------------------------------------------------------------

def _build_context_block(parsed: dict, aestima: dict, dossier: dict,
                         sector_themes: list, prior_dives: list,
                         recent_docs: list, book_frameworks: list,
                         chart_context: str = "") -> str:
    """Build the enrichment context block injected into the prompt."""
    gli = aestima.get("gli", {})
    delta = aestima.get("delta", {})

    gli_phase = gli.get("gli_phase", "unknown")
    gli_value = gli.get("gli_value_trn", "?")
    steno = gli.get("steno_regime", "unknown")
    fiscal = gli.get("fiscal_dominance_score", "?")
    trans_risk = gli.get("transition_risk_score", "?")
    delta_24h = delta.get("gli_value_trn", {}).get("delta_24h", "?") if isinstance(delta.get("gli_value_trn"), dict) else "?"
    risk_delta = delta.get("transition_risk_score", {}).get("delta_24h", "?") if isinstance(delta.get("transition_risk_score"), dict) else "?"
    phase_changed = delta.get("phase_changed", False)

    stamp = (
        f"GLI CONTEXT STAMP:\n"
        f"Phase: {gli_phase} | GLI: ${gli_value}T | Regime: {steno}\n"
        f"Fiscal Dominance: {fiscal}/10 | Transition Risk: {trans_risk}/10\n"
        f"24h GLI Δ: {delta_24h} | 24h Risk Δ: {risk_delta} | Phase Changed: {phase_changed}\n"
    )

    # Dossier block
    dossier_block = ""
    if dossier:
        on_wl = dossier.get("on_watchlist", False)
        has_intel = dossier.get("intelligence", {}).get("found", False)
        if on_wl:
            thesis = dossier.get("thesis_summary", "")
            catalysts = dossier.get("catalysts", [])
            risks = dossier.get("key_risks", [])
            conviction = dossier.get("conviction", "?")
            dossier_block = (
                f"\nDOSSIER (on watchlist, conviction: {conviction}):\n"
                f"Thesis: {thesis}\n"
                f"Catalysts: {json.dumps(catalysts[:5])}\n"
                f"Key Risks: {json.dumps(risks[:5])}\n"
            )
        elif has_intel:
            intel = dossier.get("intelligence", {})
            themes = intel.get("themes", [])
            mentions = intel.get("mentioning_accounts", [])
            dossier_block = (
                f"\nDOSSIER (not on watchlist, intelligence found):\n"
                f"Themes: {json.dumps(themes[:5])}\n"
                f"Mentioning: {json.dumps(mentions[:5])}\n"
            )

    # Sector themes block
    sector_block = ""
    if sector_themes:
        lines = [f"  {t['theme_label']} (velocity: {t['velocity_score']}, sentiment: {t['sentiment']})"
                 for t in sector_themes[:5]]
        sector_block = "\nSECTOR INTELLIGENCE:\n" + "\n".join(lines) + "\n"

    # Prior deep dives block
    prior_block = ""
    if prior_dives:
        for p in prior_dives[:2]:
            prior_block += (
                f"\nPRIOR DEEP DIVE: {p.get('created_at', '?')[:10]} — "
                f"Conviction: {p.get('conviction_score', '?')}, "
                f"Decision: {p.get('decision', '?')}\n"
            )

    # Recent docs block
    docs_block = ""
    if recent_docs:
        docs_block = "\nRECENT DOCUMENTS:\n"
        for d in recent_docs[:5]:
            docs_block += f"  {d['title']} ({d['source_name']}) — {d['sentiment']}\n"

    # Book frameworks block
    books_block = ""
    if book_frameworks:
        books_block = "\nBOOK/FRAMEWORK INTELLIGENCE:\n"
        for b in book_frameworks:
            books_block += f"  {b['concept']} ({b['category']}) — {b['source_book']}\n"

    # Chart block
    chart_block = ""
    if chart_context:
        chart_block = f"\nCHART CONTEXT:\n{chart_context}\n"
    else:
        chart_block = "\nNo chart provided. HTF score = 0 by rule. LTF score = 0 by rule.\n"

    return stamp + dossier_block + sector_block + prior_block + docs_block + books_block + chart_block


def _load_prompt_section(spec_text: str, start_marker: str, end_marker: str) -> str:
    """Extract a section from the spec file between two markers."""
    start_idx = spec_text.find(start_marker)
    end_idx = spec_text.find(end_marker)
    if start_idx < 0 or end_idx < 0:
        return ""
    return spec_text[start_idx:end_idx].strip()


def _build_deep_dive_prompt(parsed: dict, context_block: str) -> str:
    """Build the full deep dive prompt with context injected."""
    ticker = parsed["ticker"]
    horizon = parsed["horizon_full"]
    price = parsed["price"]
    note = parsed["user_note"]

    prompt = (
        f"You are Remi — a sovereign macro intelligence agent operating at the intersection "
        f"of global liquidity analysis and narrative intelligence.\n\n"
        f"Your job is to produce a capital allocation decision memo on the asset below. "
        f"This is not a retail opinion. This is a structured risk-adjusted assessment that "
        f"will be published to intel.gwizcloud.com and reviewed by MG before distribution.\n\n"
        f"Think like a macro PM at a multi-strategy fund. Prioritize downside control first. "
        f"Be decisive and specific. Never hedge with vague language unless confidence is explicitly low.\n\n"
        f"INPUTS:\n"
        f"Asset: {ticker}\n"
        f"Horizon: {horizon}\n"
        f"Current Price: {price}\n"
        f"Additional Context: {note}\n\n"
        f"---\n\n"
        f"CONTEXT DATA (fetched before analysis):\n"
        f"{context_block}\n\n"
        f"---\n\n"
        f"INSTRUCTIONS:\n"
        f"Execute all 23 steps from the trade analysis framework below. "
        f"Every scoring step must reference the GLI context stamp above. "
        f"Show your math in Step 20 (Scoring Engine). "
        f"Apply all applicable penalties in Step 21. "
        f"End with the FINAL OUTPUT BLOCK in the exact format specified.\n\n"
        f"STEP FRAMEWORK:\n"
        f"Steps 1-10: Asset classification, structural edge, sensitivity map, cycle & regime, "
        f"macro & liquidity, positioning & flows, valuation, HTF technical (chart-dependent), "
        f"LTF execution (chart-dependent), GLI signal alignment.\n"
        f"Steps 11-19: Catalysts, trend persistence, fair value range, Monte Carlo thinking, "
        f"scenarios, expected value, execution plan, portfolio fit, invalidation.\n"
        f"Step 20: Scoring engine with weights — Structural Edge 8%, Sensitivity 8%, "
        f"Cycle 9%, Macro 10%, Flows 9%, Valuation 8%, HTF 10%, LTF 8%, "
        f"GLI Alignment 12%, Catalysts 10%, Trend 4%, Monte Carlo 4%. "
        f"Each scored 0-5. Weighted = (score/5) × weight. Sum → Final /100.\n"
        f"Step 21: Penalties — turbulence+long: -3, phase_changed: -2, transition_risk>7: -2, "
        f"sector_velocity>8: -2, no_chart: -3, event_risk: -1to-3, overextension: -2, "
        f"balance_sheet_fragility: -3to-5, regulatory/geopolitical: -1to-4.\n"
        f"Step 22: Decision — 85-100=Full Buy(A), 70-84=Half Buy(B), 55-69=No Trade(C), <55=Avoid(D).\n"
        f"Step 23: Position sizing — base: 85+=8-12%, 70-84=4-8%, 55-69=0-3%, <55=0%. "
        f"Adjust: turbulence×0.75, no_chart×0.85, transition_risk>7×0.80, event_risk×0.85.\n\n"
        f"FINAL OUTPUT BLOCK format:\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"REMI DEEP DIVE — {ticker}\n"
        f"GLI Phase: [phase] | Regime: [regime]\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"Final Score: [X/100]\n"
        f"Decision: [Full Buy / Half Buy / No Trade / Exit]\n"
        f"Tier: [A/B/C/D]\n"
        f"EV: [Strong/Moderate/Weak/Negative]\n"
        f"Position Size: [X%]\n"
        f"Entry: [price or condition]\n"
        f"Stop: [price]\n"
        f"TP1: [price]\n"
        f"TP2: [price]\n"
        f"Runner: [price or open]\n"
        f"Key Catalyst: [one line]\n"
        f"Main Risk: [one line]\n"
        f"GLI Alignment: [Tailwind/Neutral/Headwind]\n"
        f"Publish to Intel: YES — awaiting MG approval\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
    )
    return prompt


def _build_quick_take_prompt(parsed: dict, context_block: str) -> str:
    """Build the quick take prompt."""
    ticker = parsed["ticker"]
    horizon = parsed["horizon_full"]
    price = parsed["price"]

    prompt = (
        f"You are Remi — macro intelligence agent. Give a fast, decisive take on this asset.\n\n"
        f"Asset: {ticker}\n"
        f"Horizon: {horizon}\n"
        f"Price: {price}\n\n"
        f"{context_block}\n\n"
        f"Respond in this exact format — tight and specific, no filler:\n\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"REMI QUICK TAKE — {ticker} | {horizon}\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
        f"🌊 MACRO CONTEXT\n"
        f"GLI: [phase] | Regime: [regime] | Risk: [score]/10\n"
        f"Bias for this asset: [TAILWIND/NEUTRAL/HEADWIND] — 1 sentence why\n\n"
        f"📐 STRUCTURE\n"
        f"[2-3 sentences on where price sits structurally, cycle position, key level]\n\n"
        f"⚡ THESIS\n"
        f"[2-3 sentences — trade idea, catalyst, why now]\n\n"
        f"📊 LEVELS\n"
        f"Entry: [price or condition]\n"
        f"Stop: [price] (invalidation: [one line])\n"
        f"TP1: [price]\n"
        f"TP2: [price]\n\n"
        f"📈 SCENARIOS\n"
        f"Bull ([%]): [one line]\n"
        f"Base ([%]): [one line]\n"
        f"Bear ([%]): [one line]\n"
        f"EV: [Strong/Moderate/Weak/Negative]\n\n"
        f"⚠️ MAIN RISK\n"
        f"[One specific risk. No generic answers.]\n\n"
        f"🎯 CALL: [BUY/WAIT FOR PULLBACK/NO TRADE/AVOID]\n"
        f"Conviction: [HIGH/MEDIUM/LOW]\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
        f"Then generate a tweet thread draft:\n\n"
        f"TWEET THREAD — {ticker}\n"
        f"1/ [Hook — bold specific claim. Never open with 'let's talk about.']\n"
        f"2/ [Macro context — GLI phase + regime framing]\n"
        f"3/ [The setup — structure + key level]\n"
        f"4/ [The trade — entry/stop/target. Accountability format.]\n"
        f"5/ [The risk — what kills the thesis. Shows discipline.]\n"
        f"6/ [CTA — reference Aestima or GLI concept]\n"
    )
    return prompt


# ---------------------------------------------------------------------------
# Quality Gate
# ---------------------------------------------------------------------------

def _quality_gate(output: str) -> tuple[bool, list]:
    """Verify deep dive output has all required components."""
    issues = []
    if "GLI CONTEXT STAMP" not in output:
        issues.append("Missing GLI Context Stamp")
    if "Scoring Engine" not in output and "STEP 20" not in output.lower():
        issues.append("Missing Scoring Engine")
    if "REMI DEEP DIVE" not in output:
        issues.append("Missing Final Output Block header")
    # Check decision is present
    has_decision = any(d in output for d in ["Full Buy", "Half Buy", "No Trade", "Exit", "Avoid"])
    if not has_decision:
        issues.append("Missing valid Decision")
    return len(issues) == 0, issues


def _extract_score(output: str) -> str:
    """Extract final score from output."""
    m = re.search(r'Final Score:\s*(\d+(?:\.\d+)?)/100', output)
    if m:
        return m.group(1)
    return "?"


def _extract_decision(output: str) -> str:
    """Extract decision from output."""
    for d in ["Full Buy", "Half Buy", "No Trade", "Exit", "Avoid"]:
        if d in output:
            return d
    return "Unknown"


# ---------------------------------------------------------------------------
# Publish to Aestima
# ---------------------------------------------------------------------------

def _publish_to_aestima(ticker: str, content: str, score: str,
                        gli_phase: str, steno_regime: str) -> bool:
    """POST deep dive to Aestima agent-reports."""
    try:
        conviction = float(score) / 10 if score != "?" else None
    except ValueError:
        conviction = None

    payload = {
        "report_type": "trade_analysis",
        "title": f"Deep Dive — {ticker}",
        "body_md": content,
        "tags": [ticker.lower(), "deep_dive", "trade_analysis"],
        "gli_phase": gli_phase,
        "conviction_score": conviction,
        "steno_regime": steno_regime,
    }
    try:
        r = httpx.post(
            f"{AESTIMA_BASE}/api/research/agent-reports",
            headers={"X-Agent-Key": AESTIMA_AGENT_KEY, "Content-Type": "application/json"},
            json=payload,
            timeout=15,
        )
        if r.status_code in (200, 201):
            _log(f"Published {ticker} deep dive to Aestima")
            return True
        _log(f"Aestima publish failed: HTTP {r.status_code} {r.text[:200]}")
    except Exception as e:
        _log(f"Aestima publish error: {e}")
    return False


def _log_to_db(ticker: str, analysis_type: str, score: str, decision: str,
               gli_phase: str, published: bool):
    """Store analysis result in remi_intelligence.db."""
    conn = _db()
    try:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS trade_analyses (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ticker TEXT NOT NULL,
                analysis_type TEXT NOT NULL,
                score REAL,
                decision TEXT,
                gli_phase TEXT,
                published INTEGER DEFAULT 0,
                created_at TEXT DEFAULT (datetime('now'))
            )
        """)
        score_val = float(score) if score != "?" else None
        conn.execute("""
            INSERT INTO trade_analyses (ticker, analysis_type, score, decision, gli_phase, published)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (ticker, analysis_type, score_val, decision, gli_phase, int(published)))
        conn.commit()
    except Exception as e:
        _log(f"DB log error: {e}")
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Main handlers
# ---------------------------------------------------------------------------

async def handle_trade_analysis(text: str, chat_id: int, reply_to: int,
                                sender_id: int, photo_description: str = None):
    """Route /analyze and /take commands."""
    parsed = _parse_command(text)
    if not parsed:
        _bot_reply(chat_id, "Usage: `/analyze TICKER [horizon] [price]` or `/take TICKER [horizon] [price]`",
                   reply_to=reply_to)
        return

    cmd = parsed["cmd"]
    ticker = parsed["ticker"]

    _log(f"{cmd} {ticker} initiated")

    if cmd == "/analyze":
        await _run_deep_dive(parsed, chat_id, reply_to, photo_description)
    elif cmd == "/take":
        await _run_quick_take(parsed, chat_id, reply_to)


async def _run_deep_dive(parsed: dict, chat_id: int, reply_to: int,
                          chart_context: str = None):
    """Execute full 23-step deep dive analysis."""
    import asyncio
    ticker = parsed["ticker"]

    # Check for fresh prior deep dive (< 7 days)
    fresh_msg = _check_prior_fresh(ticker)
    if fresh_msg:
        _bot_reply(chat_id, fresh_msg, reply_to=reply_to)
        _log(f"{ticker}: prior deep dive found, asking MG")
        return

    _bot_reply(chat_id, f"🔬 Deep dive on {ticker} — fetching context...", reply_to=reply_to)

    # Fetch all enrichment layers (in thread to not block event loop)
    aestima = await asyncio.to_thread(_fetch_aestima_context)
    dossier = await asyncio.to_thread(_fetch_dossier, ticker)
    sector = _infer_sector(ticker)
    sector_themes = await asyncio.to_thread(_fetch_sector_themes, sector)
    prior_dives = await asyncio.to_thread(_fetch_prior_deep_dives, ticker)
    recent_docs = await asyncio.to_thread(_fetch_recent_docs, ticker)
    book_frameworks = await asyncio.to_thread(_fetch_book_frameworks, sector)

    # Build context block
    context_block = _build_context_block(
        parsed, aestima, dossier, sector_themes, prior_dives,
        recent_docs, book_frameworks, chart_context or ""
    )

    # Build prompt
    prompt = _build_deep_dive_prompt(parsed, context_block)

    # Extract GLI info for later
    gli_phase = aestima.get("gli", {}).get("gli_phase", "unknown")
    steno_regime = aestima.get("gli", {}).get("steno_regime", "unknown")

    # Call GLM-4.7 (primary)
    messages = [
        {"role": "system", "content": "You are Remi, a sovereign macro intelligence agent. Produce a complete 23-step capital allocation decision memo."},
        {"role": "user", "content": prompt},
    ]

    _log(f"{ticker}: calling GLM-4.7 for deep dive")
    result = await asyncio.to_thread(_call_glm, messages, "glm-4.7", 8000, 0.3)

    if not result:
        _log(f"{ticker}: GLM-4.7 failed, trying fallback")
        _bot_reply(chat_id, f"⚠️ GLM-4.7 failed for {ticker}, trying fallback...", reply_to=reply_to)
        result = await asyncio.to_thread(_call_glm, messages, "glm-5", 8000, 0.3)

    if not result:
        _bot_reply(chat_id, f"❌ Deep dive failed for {ticker} — all models unavailable. Check logs.", reply_to=reply_to)
        _log(f"{ticker}: deep dive failed — all models unavailable")
        return

    output, model_used = result
    _log(f"{ticker}: GLM returned {len(output)} chars via {model_used}")

    # Quality gate
    passed, issues = _quality_gate(output)

    if not passed:
        _log(f"{ticker}: quality gate failed — {issues}. Attempting two-call split.")
        # Two-call split protocol
        # Call 1: Steps 1-10
        prompt_1 = (
            prompt +
            "\n\nIMPORTANT: Complete ONLY Steps 1 through 10 (Asset Classification through GLI Signal Alignment). "
            "Stop after Step 10. I will provide this output as context for Steps 11-23."
        )
        result_1 = await asyncio.to_thread(_call_glm,
            [{"role": "system", "content": "You are Remi, a macro intelligence agent. Complete Steps 1-10 only."},
             {"role": "user", "content": prompt_1}],
            "glm-4.7", 4000, 0.3)

        if result_1:
            output_1, _ = result_1
            # Call 2: Steps 11-23 with Call 1 output as context
            prompt_2 = (
                f"CONTEXT — Steps 1-10 already completed:\n\n{output_1}\n\n"
                f"---\n\n"
                f"Continue with Steps 11 through 23. The GLI context stamp:\n"
                f"Phase: {gli_phase} | Regime: {steno_regime}\n"
                f"Complete Steps 11-23 including the Scoring Engine (Step 20), "
                f"Penalties (Step 21), Decision (Step 22), Position Sizing (Step 23), "
                f"and the FINAL OUTPUT BLOCK."
            )
            result_2 = await asyncio.to_thread(_call_glm,
                [{"role": "system", "content": "You are Remi, a macro intelligence agent. Complete Steps 11-23."},
                 {"role": "user", "content": prompt_2}],
                "glm-4.7", 4000, 0.3)

            if result_2:
                output_2, _ = result_2
                output = output_1 + "\n\n---\n\n" + output_2
                passed, issues = _quality_gate(output)
                _log(f"{ticker}: two-call split completed. Quality gate: {'PASS' if passed else 'FAIL — ' + str(issues)}")

    if not passed:
        # Final fallback — notify MG instead of publishing degraded output
        _bot_reply(chat_id,
            f"⚠️ Deep dive for {ticker} failed quality gate: {', '.join(issues)}. "
            f"Output saved to logs. Manual review recommended.",
            reply_to=reply_to)
        _log(f"{ticker}: quality gate final fail — {issues}")
        # Still log to file
        ts = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
        fail_path = os.path.expanduser(f"~/remi-intelligence/logs/deep_dive_{ticker}_{ts}.md")
        with open(fail_path, "w") as f:
            f.write(output)
        return

    # Extract score and decision
    score = _extract_score(output)
    decision = _extract_decision(output)

    # Publish to Aestima
    published = _publish_to_aestima(ticker, output, score, gli_phase, steno_regime)

    # Log to DB
    _log_to_db(ticker, "deep_dive", score, decision, gli_phase, published)

    # Notify MG in home channel
    admin_chat = os.getenv("TELEGRAM_CHAT_ID", "6625574871")
    _bot_reply(int(admin_chat),
        f"📊 Deep dive complete: {ticker} — Score: {score}/100 | {decision} | Awaiting your approval to publish.")

    # Reply in group
    _bot_reply(chat_id,
        f"✅ Deep dive complete: {ticker} — Score: {score}/100 | {decision} | "
        f"GLI: {gli_phase} | Published to intel dashboard. MG reviewing.",
        reply_to=reply_to)

    _log(f"{ticker}: deep dive complete — score {score}, {decision}, published={published}")


async def _run_quick_take(parsed: dict, chat_id: int, reply_to: int):
    """Execute quick take analysis."""
    import asyncio
    ticker = parsed["ticker"]

    _bot_reply(chat_id, f"⚡ Quick take on {ticker} — pulling macro context...", reply_to=reply_to)

    # Quick take: fetch Layer 1 ONLY (one call)
    aestima = await asyncio.to_thread(_fetch_aestima_context)

    gli = aestima.get("gli", {})
    gli_phase = gli.get("gli_phase", "unknown")
    steno = gli.get("steno_regime", "unknown")
    fiscal = gli.get("fiscal_dominance_score", "?")
    trans_risk = gli.get("transition_risk_score", "?")

    context_block = (
        f"GLI CONTEXT STAMP:\n"
        f"Phase: {gli_phase} | GLI: ${gli.get('gli_value_trn', '?')}T | Regime: {steno}\n"
        f"Fiscal Dominance: {fiscal}/10 | Transition Risk: {trans_risk}/10\n"
    )

    # Build prompt
    prompt = _build_quick_take_prompt(parsed, context_block)

    # Call GLM-5
    messages = [
        {"role": "system", "content": "You are Remi, a macro intelligence agent. Give a fast, decisive take."},
        {"role": "user", "content": prompt},
    ]

    _log(f"{ticker}: calling GLM-5 for quick take")
    result = await asyncio.to_thread(_call_glm, messages, "glm-5", 2000, 0.3)

    if not result:
        _bot_reply(chat_id, f"❌ Quick take failed for {ticker} — model unavailable.", reply_to=reply_to)
        _log(f"{ticker}: quick take failed — model unavailable")
        return

    output, model_used = result
    _log(f"{ticker}: quick take returned {len(output)} chars via {model_used}")

    # Split output — quick take block + tweet thread
    quick_take = output
    tweet_thread = ""
    tweet_marker = "TWEET THREAD"
    idx = output.find(tweet_marker)
    if idx > 0:
        quick_take = output[:idx].strip()
        tweet_thread = output[idx:].strip()

    # Post quick take to investing group immediately
    _bot_reply(chat_id, quick_take, reply_to=reply_to)

    # Send tweet thread to MG home channel
    if tweet_thread:
        admin_chat = os.getenv("TELEGRAM_CHAT_ID", "6625574871")
        _bot_reply(int(admin_chat),
            f"📝 Tweet thread draft for {ticker}:\n\n{tweet_thread}\n\n"
            f"Reply 'approve' to post via BogWizard.")
        _log(f"{ticker}: tweet thread sent to MG for approval")

    # Log
    _log_to_db(ticker, "quick_take", "?", "quick_take", gli_phase, False)
    _log(f"{ticker}: quick take complete")

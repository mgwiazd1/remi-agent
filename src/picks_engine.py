"""picks_engine.py — Remi's autonomous ticker picks engine."""
import sqlite3
import json
import math
import os
import logging
import httpx
from datetime import datetime, timedelta, timezone
from pathlib import Path

logger = logging.getLogger(__name__)
DB_PATH = Path(__file__).parent.parent / "remi_intelligence.db"
HIGH_THRESHOLD = 0.65
MEDIUM_THRESHOLD = 0.40
EMERGING_THRESHOLD = 0.20
MIN_SIGNALS = 3
MIN_SOURCES = 2


def _conn():
    c = sqlite3.connect(DB_PATH)
    c.row_factory = sqlite3.Row
    return c


def _decay(created_at):
    try:
        age = (datetime.now(timezone.utc) - datetime.fromisoformat(created_at.replace("Z","+00:00"))).days
    except (ValueError, TypeError):
        return 0.1
    if age <= 7: return 1.0
    if age <= 14: return 0.7
    if age <= 30: return 0.4
    return 0.1


def _call_llm(prompt: str, model: str = "glm-4.7") -> str:
    """Call GLM API and return response text. Returns empty string on failure."""
    api_key = os.environ.get("GLM_API_KEY", "")
    base_url = os.environ.get("GLM_BASE_URL", "https://api.z.ai/api/coding/paas/v4")
    if not api_key:
        return ""
    try:
        r = httpx.post(
            f"{base_url}/chat/completions",
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            json={"model": model, "messages": [{"role": "user", "content": prompt}],
                  "max_tokens": 2000, "temperature": 0.3},
            timeout=60,
        )
        if r.status_code == 200:
            msg = r.json()["choices"][0]["message"]
            text = msg.get("content") or msg.get("reasoning_content") or ""
            return text.strip()
    except Exception as e:
        logger.warning(f"LLM call failed: {e}")
    return ""


def _synthesize_thesis(ticker: str, score: dict) -> str:
    """Generate a natural-language thesis from the evidence chain."""
    conn = _conn()
    sig_ids = score.get("signal_ids", [])[:15]
    if not sig_ids:
        conn.close()
        return ""
    placeholders = ",".join("?" * len(sig_ids))
    sigs = conn.execute(f"SELECT signal_type, source, content, conviction_weight, created_at "
                        f"FROM ticker_signals WHERE id IN ({placeholders})", sig_ids).fetchall()
    conn.close()

    evidence_lines = []
    for s in sigs:
        evidence_lines.append(
            f"- [{s['signal_type']}] {s['source']}: {(s['content'] or '')[:120]} "
            f"(weight: {s['conviction_weight'] or 0:.2f})")
    evidence = "\n".join(evidence_lines)

    prompt = f"""You are a macro research analyst. Given the following evidence signals 
for {ticker} (conviction score: {score['conviction_score']:.3f}), write a 2-3 sentence thesis 
explaining why this ticker is generating high conviction. Be specific about the 
narrative drivers — don't just restate that signals exist.

Evidence:
{evidence}

Write ONLY the thesis, no preamble."""
    return _call_llm(prompt)


def score_ticker(ticker):
    conn = _conn()
    cutoff = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat()
    sigs = conn.execute("SELECT * FROM ticker_signals WHERE ticker=? AND created_at>? ORDER BY created_at DESC",
                        (ticker.upper(), cutoff)).fetchall()
    if not sigs:
        conn.close()
        return {"ticker": ticker.upper(), "conviction_score": 0.0, "signal_count": 0, "enough_data": False}
    bull = bear = neut = total_w = 0.0
    src_types = set()
    sig_ids = []
    for s in sigs:
        d = _decay(s["created_at"])
        w = (s["conviction_weight"] or 0.0) * d
        if s["signal_type"] == "pdf_insight":
            w *= 2.0
        # Boost bear weight if signal direction is explicitly bearish
        sig_direction = s["direction"] or "neutral"
        if sig_direction == "bearish":
            w *= 1.3
        total_w += w
        src_types.add(s["signal_type"])
        sig_ids.append(s["id"])
        sent = s["sentiment"] or "neutral"
        # Combine sentiment + direction for classification
        is_bearish = sent == "bearish" or sig_direction == "bearish"
        is_bullish = sent == "bullish" and sig_direction != "bearish"
        if is_bullish: bull += w
        elif is_bearish: bear += w
        else: neut += w
    raw = (bull - bear) / total_w if total_w > 0 else 0.0
    div_bonus = min(0.2, len(src_types) * 0.05)
    vol_bonus = min(0.15, math.log(len(sigs) + 1) * 0.05)
    final = max(-1.0, min(1.0, raw + div_bonus + vol_bonus))
    top_theme = None
    top_vel = 0
    for s in sigs:
        if s["theme_key"]:
            try:
                rd = json.loads(s["raw_data"] or "{}")
                v = rd.get("velocity", 0)
                if v > top_vel:
                    top_vel = v
                    top_theme = s["theme_key"]
            except (json.JSONDecodeError, TypeError):
                pass
    conn.close()
    return {"ticker": ticker.upper(), "conviction_score": round(final, 3),
            "signal_count": len(sigs), "source_diversity": len(src_types),
            "bullish_signals": sum(1 for s in sigs if (s["sentiment"] or "") == "bullish"),
            "bearish_signals": sum(1 for s in sigs if (s["sentiment"] or "") == "bearish"),
            "neutral_signals": sum(1 for s in sigs if (s["sentiment"] or "neutral") == "neutral"),
            "top_theme": top_theme, "top_theme_velocity": top_vel,
            "signal_ids": sig_ids,
            "enough_data": len(sigs) >= MIN_SIGNALS and len(src_types) >= MIN_SOURCES}


def update_all_convictions():
    conn = _conn()
    cutoff = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat()
    tickers = [r[0] for r in conn.execute(
        "SELECT DISTINCT ticker FROM ticker_signals WHERE created_at>?", (cutoff,)).fetchall()]
    conn.close()
    alerts = []
    conn = _conn()
    now = datetime.now(timezone.utc).isoformat()
    for tk in tickers:
        sc = score_ticker(tk)
        conn.execute("""INSERT INTO ticker_conviction
            (ticker, conviction_score, signal_count, source_diversity, bullish_signals,
             bearish_signals, neutral_signals, top_theme, top_theme_velocity, last_scored_at, raw_scores)
            VALUES (?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(ticker) DO UPDATE SET
                conviction_score=excluded.conviction_score, signal_count=excluded.signal_count,
                source_diversity=excluded.source_diversity, bullish_signals=excluded.bullish_signals,
                bearish_signals=excluded.bearish_signals, neutral_signals=excluded.neutral_signals,
                top_theme=excluded.top_theme, top_theme_velocity=excluded.top_theme_velocity,
                last_scored_at=excluded.last_scored_at, raw_scores=excluded.raw_scores""",
            (tk, sc["conviction_score"], sc["signal_count"], sc["source_diversity"],
             sc["bullish_signals"], sc["bearish_signals"], sc["neutral_signals"],
             sc["top_theme"], sc["top_theme_velocity"], now, json.dumps(sc)))
        if not sc["enough_data"]:
            continue
        abscore = abs(sc["conviction_score"])
        if abscore >= HIGH_THRESHOLD:
            alerts.append({"ticker": tk, "level": "high", "score": sc})
        elif abscore >= MEDIUM_THRESHOLD:
            alerts.append({"ticker": tk, "level": "medium", "score": sc})
    conn.commit()
    conn.close()
    logger.info(f"Scored {len(tickers)} tickers, {len(alerts)} crossed thresholds")
    return alerts


def generate_pick(ticker, score, level):
    conn = _conn()
    existing = conn.execute("SELECT 1 FROM remi_picks WHERE ticker=? AND status='pending'", (ticker,)).fetchone()
    if existing:
        conn.close()
        return None
    direction = "long" if score["conviction_score"] > 0 else "short"
    # Synthesize thesis via LLM before inserting
    thesis = _synthesize_thesis(ticker, score)
    conn.execute("""INSERT INTO remi_picks
        (ticker, company, pick_type, conviction_level, thesis, evidence_chain, status, pick_direction)
        VALUES (?, '', ?, ?, ?, ?, 'pending', ?)""",
        (ticker, direction, level, thesis, json.dumps(score.get("signal_ids", [])), direction))
    pick_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    conn.commit()
    conn.close()
    return {"id": pick_id, "ticker": ticker, "direction": direction, "level": level,
            "thesis": thesis}


def format_pick_alert(ticker, score, level, thesis=None):
    is_short = score["conviction_score"] < 0
    d = "SHORT" if is_short else "LONG"
    emoji = "⚠️" if is_short else "🎯"
    lines = [f"{emoji} REMI PICK — {ticker}", f"Direction: {d}",
             f"Conviction: {level.upper()} ({score['conviction_score']:.2f})",
             f"Signals: {score['signal_count']} from {score['source_diversity']} source types",
             f"Bull/Bear/Neutral: {score['bullish_signals']}/{score['bearish_signals']}/{score['neutral_signals']}"]
    if score.get("top_theme"):
        lines.append(f"Top theme: {score['top_theme']} (vel: {score.get('top_theme_velocity',0):.1f})")
    if thesis:
        lines.append(f"\nThesis: {thesis[:300]}")
    lines.append("")
    lines.append(f"Reply /watch add {ticker} to add to watchlist")
    lines.append(f"Reply /pick approve {ticker} to approve")
    return "\n".join(lines)


def format_weekly_digest(include_emerging=False):
    conn = _conn()
    cutoff = (datetime.now(timezone.utc) - timedelta(days=7)).isoformat()
    thresh = EMERGING_THRESHOLD if include_emerging else MEDIUM_THRESHOLD
    rows = conn.execute("""SELECT ticker, conviction_score, signal_count, source_diversity,
        bullish_signals, bearish_signals, top_theme, top_theme_velocity
        FROM ticker_conviction WHERE last_scored_at>? AND ABS(conviction_score)>=? AND signal_count>=?
        ORDER BY ABS(conviction_score) DESC""",
        (cutoff, thresh, MIN_SIGNALS)).fetchall()
    if not rows:
        conn.close()
        return "REMI WEEKLY PICKS DIGEST\n\nNo tickers crossed conviction thresholds this week."
    lines = [f"REMI WEEKLY PICKS DIGEST", f"{datetime.now(timezone.utc).strftime('%B %d, %Y')}\n"]
    high = [r for r in rows if abs(r["conviction_score"]) >= HIGH_THRESHOLD]
    med = [r for r in rows if MEDIUM_THRESHOLD <= abs(r["conviction_score"]) < HIGH_THRESHOLD]
    emg = [r for r in rows if EMERGING_THRESHOLD <= abs(r["conviction_score"]) < MEDIUM_THRESHOLD]
    for label, grp in [("HIGH CONVICTION", high), ("MEDIUM", med), ("EMERGING", emg)]:
        if grp:
            lines.append(f"\n{label}")
            for r in grp[:5]:
                d = "LONG" if r["conviction_score"] > 0 else "SHORT"
                lines.append(f"  {d} {r['ticker']} — score: {r['conviction_score']:.2f} ({r['signal_count']} sigs, {r['source_diversity']} sources)")
                if r["top_theme"]:
                    lines.append(f"    Theme: {r['top_theme']}")
    pending = conn.execute("SELECT ticker, conviction_level FROM remi_picks WHERE status='pending'").fetchall()
    if pending:
        lines.append(f"\nPENDING APPROVAL ({len(pending)})")
        for p in pending[:5]:
            lines.append(f"  {p['ticker']} — {p['conviction_level']}")
    conn.close()
    return "\n".join(lines)


def run_picks_cycle():
    alerts = update_all_convictions()
    high_alerts = []
    for a in alerts:
        if a["level"] == "high":
            pick = generate_pick(a["ticker"], a["score"], a["level"])
            if pick:
                thesis = pick.get("thesis", "")
                high_alerts.append({"ticker": a["ticker"], "score": a["score"],
                    "level": a["level"], "pick": pick,
                    "message": format_pick_alert(a["ticker"], a["score"], a["level"], thesis)})
    logger.info(f"Picks cycle: {len(high_alerts)} high-conviction alerts")

    # Autonomous report generation for each high-conviction pick
    for a in high_alerts:
        try:
            _autonomous_report_for_pick(a["ticker"], a["score"], a.get("pick", {}))
        except Exception as e:
            logger.warning("Autonomous report failed for %s: %s", a["ticker"], e)

    return high_alerts


def _autonomous_report_for_pick(ticker: str, conviction_score: float, pick: dict):
    """Generate report, save as draft, notify MG for approval."""
    import asyncio
    import sys
    sys.path.insert(0, str(Path(__file__).resolve().parent))

    from report_writer import write_report, save_draft_to_dashboard
    from ticker_profiler import gather_ticker_signals, gather_document_themes, get_gli_phase

    signals = gather_ticker_signals(ticker)
    doc_themes = gather_document_themes(ticker)
    gli_phase = get_gli_phase()

    report_dict = write_report(
        theme=f"{ticker} — High Conviction Pick",
        ticker=ticker,
        signals=signals,
        doc_themes=doc_themes,
        gli_phase=gli_phase,
        conviction_score=conviction_score,
        trigger="autonomous",
    )
    if not report_dict:
        return

    report_id = save_draft_to_dashboard(report_dict)
    if not report_id:
        return

    # Notify MG via investing group
    from telegram_sender import send_investing_alert
    msg = (
        f"📊 *Report Ready for Review*\n\n"
        f"*{report_dict['title']}*\n"
        f"{report_dict.get('summary', '')[:200]}\n\n"
        f"Verdict: {report_dict.get('verdict')} | "
        f"Confidence: {report_dict.get('confidence')}%\n\n"
        f"Approve: `/publish {report_id}`\n"
        f"Deny: `/kill_report {report_id}`"
    )
    send_investing_alert(msg)
    logger.info("Autonomous report #%d queued for %s (conviction %.2f)", report_id, ticker, conviction_score)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    print("Running picks cycle...")
    alerts = run_picks_cycle()
    print(f"High alerts: {len(alerts)}")
    for a in alerts:
        print(a["message"])
    print("\n--- Weekly Digest ---")
    print(format_weekly_digest(include_emerging=True))

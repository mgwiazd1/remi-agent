"""knowledge_collector.py — Harvests ticker intelligence into the knowledge base."""
import sqlite3
import json
import os
import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path
import httpx

logger = logging.getLogger(__name__)
DB_PATH = Path(__file__).parent.parent / "remi_intelligence.db"
AESTIMA_INTERNAL = "http://192.168.1.198:8000"

ALIAS_PATH = Path(__file__).parent.parent / "config" / "ticker_aliases.json"


def _load_aliases() -> dict:
    if ALIAS_PATH.exists():
        with open(ALIAS_PATH) as f:
            return json.load(f).get("aliases", {})
    return {}


TICKER_ALIASES = _load_aliases()


def _normalize_ticker(ticker: str) -> str:
    """Map ticker variants to canonical symbol."""
    return TICKER_ALIASES.get(ticker.upper(), ticker.upper())


BEARISH_KEYWORDS = [
    "short", "bearish", "overvalued", "bubble", "sell", "downgrade",
    "risk", "collapse", "crash", "warning", "fraud", "dilution",
    "bankruptcy", "default", "impairment", "writedown"
]

BULLISH_KEYWORDS = [
    "long", "bullish", "undervalued", "buy", "upgrade", "accumulate",
    "breakout", "catalyst", "upside", "growth", "expansion", "recovery"
]


def _infer_direction(context: str) -> str:
    """Infer signal direction from context text. Returns 'bullish', 'bearish', or 'neutral'."""
    text = context.lower()
    bull_score = sum(1 for kw in BULLISH_KEYWORDS if kw in text)
    bear_score = sum(1 for kw in BEARISH_KEYWORDS if kw in text)
    if bear_score > bull_score and bear_score >= 2:
        return "bearish"
    elif bull_score > bear_score and bull_score >= 2:
        return "bullish"
    return "neutral"


def _conn():
    c = sqlite3.connect(DB_PATH)
    c.row_factory = sqlite3.Row
    return c


def _signal_exists(conn, ticker, sig_type, source, content):
    return conn.execute(
        "SELECT 1 FROM ticker_signals WHERE ticker=? AND signal_type=? AND source=? AND content=?",
        (ticker, sig_type, source, content)).fetchone() is not None


def harvest_theme_mentions(lookback_hours=6):
    conn = _conn()
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=lookback_hours)).isoformat()
    rows = conn.execute("""
        SELECT dt.tickers_mentioned, t.theme_label, t.theme_key,
               t.velocity_score, t.velocity_delta,
               d.source_name, d.source_type, d.ingested_at, d.title
        FROM document_themes dt
        JOIN themes t ON dt.theme_id = t.id
        JOIN documents d ON dt.document_id = d.id
        WHERE d.ingested_at > ? AND dt.tickers_mentioned IS NOT NULL
          AND dt.tickers_mentioned != '[]' AND dt.tickers_mentioned != 'null'
    """, (cutoff,)).fetchall()
    count = 0
    for row in rows:
        try:
            tickers = json.loads(row["tickers_mentioned"] or "[]")
        except (json.JSONDecodeError, TypeError):
            continue
        for raw in tickers:
            tk = raw.upper().strip().lstrip("$")
            tk = _normalize_ticker(tk)
            if len(tk) < 1 or len(tk) > 10:
                continue
            src = row["source_name"] or "unknown"
            content = f"{row['theme_label']}: {(row['title'] or '')[:200]}"
            if _signal_exists(conn, tk, "x_mention" if row["source_type"] == "tweet" else "rss_theme", src, content):
                continue
            vel = row["velocity_score"] or 0
            delta = row["velocity_delta"] or 0
            # Use delta when available; fall back to velocity as momentum proxy
            if delta > 0.1:
                sentiment = "bullish"
            elif delta < -0.1:
                sentiment = "bearish"
            elif vel >= 15.0:
                sentiment = "bullish"   # high velocity = narrative momentum
            elif vel >= 5.0:
                sentiment = "neutral"
            else:
                sentiment = "neutral"
            # Weight from velocity (momentum magnitude) + delta bonus
            weight = min(1.0, max(0.0, (vel / 10.0) * (0.5 + abs(delta))))
            sig_type = "x_mention" if row["source_type"] in ("tweet", "x_tweet") else "rss_theme"
            direction = _infer_direction(content)
            conn.execute("""INSERT INTO ticker_signals
                (ticker, signal_type, source, content, theme_key, sentiment, conviction_weight, raw_data, created_at, direction)
                VALUES (?,?,?,?,?,?,?,?,?,?)""",
                (tk, sig_type, src, content, row["theme_key"], sentiment, round(weight, 3),
                 json.dumps({"velocity": vel, "delta": delta, "source_name": row["source_name"]}),
                 row["ingested_at"], direction))
            count += 1
    conn.commit()
    conn.close()
    logger.info(f"Harvested {count} theme mention signals")
    return count


def harvest_regime_context():
    try:
        key = os.environ.get("AESTIMA_AGENT_KEY", "")
        r = httpx.get(f"{AESTIMA_INTERNAL}/api/agent/context",
                      headers={"X-Agent-Key": key}, timeout=15)
        if r.status_code != 200:
            logger.warning(f"Aestima context failed: {r.status_code}")
            return 0
        ctx = r.json()
    except Exception as e:
        logger.warning(f"Aestima unreachable: {e}")
        return 0
    regime = ctx.get("macro_regime") or ctx.get("regime") or "unknown"
    gli_phase = ctx.get("gli_phase") or "unknown"
    conn = _conn()
    tickers = [r[0] for r in conn.execute(
        "SELECT DISTINCT ticker FROM ticker_signals WHERE created_at > ?",
        ((datetime.now(timezone.utc) - timedelta(days=7)).isoformat(),)).fetchall()]
    count = 0
    now = datetime.now(timezone.utc).isoformat()
    for tk in tickers:
        content = f"Regime: {regime} | GLI: {gli_phase}"
        if _signal_exists(conn, tk, "aestima_regime", "Aestima GLI", content):
            continue
        conn.execute("""INSERT INTO ticker_signals
            (ticker, signal_type, source, content, sentiment, conviction_weight, raw_data, created_at, expires_at, direction)
            VALUES (?, 'aestima_regime', 'Aestima GLI', ?, 'neutral', 0.3, ?, ?, ?, 'neutral')""",
            (tk, content, json.dumps(ctx), now,
             (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()))
        count += 1
    conn.commit()
    conn.close()
    logger.info(f"Harvested {count} regime signals")
    return count


def harvest_pdf_insights(lookback_hours=24):
    conn = _conn()
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=lookback_hours)).isoformat()
    rows = conn.execute("""
        SELECT dt.tickers_mentioned, t.theme_label, t.theme_key,
               d.source_name, d.ingested_at, d.title
        FROM document_themes dt
        JOIN themes t ON dt.theme_id = t.id
        JOIN documents d ON dt.document_id = d.id
        WHERE d.ingested_at > ? AND d.source_type = 'pdf'
          AND dt.tickers_mentioned IS NOT NULL AND dt.tickers_mentioned != '[]'
    """, (cutoff,)).fetchall()
    count = 0
    for row in rows:
        try:
            tickers = json.loads(row["tickers_mentioned"] or "[]")
        except (json.JSONDecodeError, TypeError):
            continue
        for raw in tickers:
            tk = raw.upper().strip().lstrip("$")
            tk = _normalize_ticker(tk)
            if len(tk) < 1 or len(tk) > 10:
                continue
            src = f"PDF:{row['source_name'] or 'Pablo'}"
            content = f"{row['theme_label']}: {(row['title'] or '')[:200]}"
            if _signal_exists(conn, tk, "pdf_insight", src, content):
                continue
            direction = _infer_direction(content)
            conn.execute("""INSERT INTO ticker_signals
                (ticker, signal_type, source, content, theme_key, sentiment, conviction_weight, raw_data, created_at, direction)
                VALUES (?, 'pdf_insight', ?, ?, ?, 'bullish', 0.7, ?, ?, ?)""",
                (tk, src, content, row["theme_key"],
                 json.dumps({"source_name": row["source_name"]}), row["ingested_at"], direction))
            count += 1
    conn.commit()
    conn.close()
    logger.info(f"Harvested {count} PDF signals")
    return count


def run_full_harvest():
    logger.info("Starting knowledge harvest...")
    t1 = harvest_theme_mentions(lookback_hours=6)
    t2 = harvest_pdf_insights(lookback_hours=24)
    t3 = harvest_regime_context()
    total = t1 + t2 + t3
    logger.info(f"Harvest done: {total} new signals (themes={t1}, pdfs={t2}, regime={t3})")
    return total


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    run_full_harvest()

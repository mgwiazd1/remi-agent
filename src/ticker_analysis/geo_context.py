"""geo_context.py — Assemble geopolitical/narrative context for a ticker."""
import json
import logging
import sqlite3
from pathlib import Path

logger = logging.getLogger(__name__)
DB_PATH = Path(__file__).parent.parent.parent / "remi_intelligence.db"
VAULT_BASE = Path("/docker/obsidian/investing/Intelligence")


def _conn():
    c = sqlite3.connect(DB_PATH)
    c.row_factory = sqlite3.Row
    return c


def _get_ticker_signals(ticker: str, lookback_days: int = 30) -> list[dict]:
    """Get recent ticker signals from the knowledge base."""
    from datetime import datetime, timedelta, timezone
    conn = _conn()
    cutoff = (datetime.now(timezone.utc) - timedelta(days=lookback_days)).isoformat()
    rows = conn.execute("""
        SELECT signal_type, source, content, sentiment, direction,
               conviction_weight, theme_key, created_at
        FROM ticker_signals
        WHERE ticker=? AND created_at > ?
        ORDER BY created_at DESC LIMIT 25
    """, (ticker.upper(), cutoff)).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def _get_ticker_themes(ticker: str) -> list[dict]:
    """Get themes associated with this ticker via recent documents."""
    conn = _conn()
    rows = conn.execute("""
        SELECT DISTINCT t.theme_key, t.theme_label, t.velocity_score, t.velocity_delta
        FROM document_themes dt
        JOIN themes t ON dt.theme_id = t.id
        JOIN documents d ON dt.document_id = d.id
        WHERE dt.tickers_mentioned LIKE ?
        ORDER BY t.velocity_score DESC LIMIT 10
    """, (f'%{ticker.upper()}%',)).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def _get_vault_theme_notes(ticker: str) -> list[str]:
    """Search vault for theme notes mentioning this ticker."""
    themes_dir = VAULT_BASE / "Themes"
    if not themes_dir.exists():
        return []
    mentions = []
    for f in themes_dir.glob("THEME_*.md"):
        try:
            content = f.read_text(errors="replace")
            if ticker.upper() in content.upper():
                # Extract first line after frontmatter as summary
                lines = [l.strip() for l in content.split("\n") if l.strip() and not l.startswith("---")]
                summary = lines[0][:100] if lines else f.stem
                mentions.append(f.stem)
        except Exception:
            pass
    return mentions[:5]


def _get_book_insights(ticker: str) -> list[str]:
    """Get relevant book-derived insights for this ticker."""
    conn = _conn()
    rows = conn.execute("""
        SELECT content FROM ticker_signals
        WHERE ticker=? AND signal_type IN ('book_framework', 'book_ticker_concept', 'book_insight')
        ORDER BY created_at DESC LIMIT 5
    """, (ticker.upper(),)).fetchall()
    conn.close()
    return [r["content"][:200] for r in rows if r["content"]]


def _get_watchlist_entry(ticker: str) -> dict | None:
    """Get watchlist data for this ticker if it exists."""
    import json
    wl_path = Path(__file__).parent.parent.parent / "config" / "watchlist.json"
    if not wl_path.exists():
        return None
    try:
        wl = json.loads(wl_path.read_text())
        entry = wl.get("tickers", {}).get(ticker.upper())
        return dict(entry) if entry else None
    except Exception:
        return None


def assemble_geo_context(ticker: str) -> dict:
    """
    Assemble full geopolitical/narrative context for a ticker.
    Returns dict with signals, themes, vault notes, book insights, watchlist.
    """
    signals = _get_ticker_signals(ticker)
    themes = _get_ticker_themes(ticker)
    vault_notes = _get_vault_theme_notes(ticker)
    book_insights = _get_book_insights(ticker)
    watchlist = _get_watchlist_entry(ticker)

    # Signal summary
    bull = sum(1 for s in signals if s.get("sentiment") == "bullish")
    bear = sum(1 for s in signals if s.get("sentiment") == "bearish")
    sources = list(set(s.get("signal_type", "") for s in signals))
    signal_themes = list(set(s.get("theme_key", "") for s in signals if s.get("theme_key")))

    context = {
        "ticker": ticker.upper(),
        "signal_count": len(signals),
        "bullish_signals": bull,
        "bearish_signals": bear,
        "source_types": sources,
        "active_themes": [t["theme_label"] for t in themes],
        "top_theme_keys": signal_themes[:5],
        "vault_theme_notes": vault_notes,
        "book_insights": book_insights,
        "on_watchlist": watchlist is not None,
        "watchlist_thesis": watchlist.get("thesis_summary", "") if watchlist else None,
        "watchlist_conviction": watchlist.get("conviction", "") if watchlist else None,
        "recent_signals": signals[:10],
    }

    logger.info(f"Geo context for {ticker}: {len(signals)} signals, {len(themes)} themes, "
                f"{len(vault_notes)} vault notes, {len(book_insights)} book insights")
    return context

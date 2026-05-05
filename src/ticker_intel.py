"""ticker_intel.py — Query layer over document_themes ticker data."""
import sqlite3
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

DB_PATH = Path(__file__).parent.parent / "remi_intelligence.db"


def get_ticker_intelligence(ticker: str, lookback_days: int = 30) -> dict:
    ticker_clean = ticker.upper().strip().lstrip("$")
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cutoff = (datetime.now(timezone.utc) - timedelta(days=lookback_days)).isoformat()
    rows = conn.execute("""
        SELECT dt.tickers_mentioned, t.theme_label, t.theme_key,
               t.velocity_score, t.velocity_delta,
               d.source_name, d.source_type, d.ingested_at, d.title
        FROM document_themes dt
        JOIN themes t ON dt.theme_id = t.id
        JOIN documents d ON dt.document_id = d.id
        WHERE d.ingested_at > ? AND dt.tickers_mentioned IS NOT NULL
          AND dt.tickers_mentioned != '[]' AND dt.tickers_mentioned != 'null'
        ORDER BY d.ingested_at DESC
    """, (cutoff,)).fetchall()
    matching = []
    for row in rows:
        try:
            tlist = json.loads(row["tickers_mentioned"] or "[]")
            if any(t.upper().lstrip("$") == ticker_clean for t in tlist):
                matching.append(dict(row))
        except (json.JSONDecodeError, TypeError):
            continue
    if not matching:
        conn.close()
        return {"ticker": ticker_clean, "found": False,
                "message": f"No mentions of {ticker_clean} in last {lookback_days}d",
                "lookback_days": lookback_days}
    themes_seen = {}
    accounts = set()
    most_recent = None
    co_tickers = {}
    for row in matching:
        tk = row["theme_key"]
        if tk not in themes_seen:
            themes_seen[tk] = {"theme_label": row["theme_label"],
                "velocity_score": row["velocity_score"], "velocity_delta": row["velocity_delta"],
                "mention_count": 0, "accounts": set()}
        themes_seen[tk]["mention_count"] += 1
        themes_seen[tk]["accounts"].add(row["source_name"] or "unknown")
        accounts.add(row["source_name"] or "unknown")
        if most_recent is None or row["ingested_at"] > most_recent:
            most_recent = row["ingested_at"]
        try:
            for t in json.loads(row["tickers_mentioned"] or "[]"):
                tc = t.upper().lstrip("$")
                if tc != ticker_clean:
                    co_tickers[tc] = co_tickers.get(tc, 0) + 1
        except (json.JSONDecodeError, TypeError):
            pass
    themes_list = sorted(
        [{"theme": v["theme_label"], "velocity_score": v["velocity_score"],
          "velocity_delta": v["velocity_delta"], "mention_count": v["mention_count"],
          "accounts": list(v["accounts"])} for v in themes_seen.values()],
        key=lambda x: (x["velocity_score"] or 0), reverse=True)
    top_co = sorted(co_tickers.items(), key=lambda x: x[1], reverse=True)[:5]
    conn.close()
    return {"ticker": ticker_clean, "found": True, "lookback_days": lookback_days,
            "total_mentions": len(matching), "most_recent_mention": most_recent,
            "accounts_mentioning": list(accounts), "themes": themes_list,
            "co_occurring_tickers": [{"ticker": t, "co_mentions": c} for t, c in top_co]}


def format_dossier_for_telegram(ticker, watchlist_entry, intel):
    lines = [f"\U0001f4cb *REMI DOSSIER — {ticker}*",
             f"_{watchlist_entry.get('company', ticker)}_", "",
             "*THESIS*", watchlist_entry.get("thesis_summary", "No thesis."), ""]
    conv = watchlist_entry.get("conviction", "?").upper()
    lines.append(f"Conviction: `{conv}` | Sizing: `{watchlist_entry.get('sizing','?')}` | Target: `{watchlist_entry.get('target_return','?')}`")
    lines.append("")
    for label, key in [("*CATALYSTS*", "catalysts"), ("*KEY RISKS*", "key_risks")]:
        items = watchlist_entry.get(key, [])
        if items:
            lines.append(label)
            for c in items:
                lines.append(f"\u2022 {c}")
            lines.append("")
    if intel.get("found"):
        lines.append("*REMI INTELLIGENCE (last 30d)*")
        lines.append(f"Mentions: {intel['total_mentions']} | Accounts: {len(intel['accounts_mentioning'])}")
        if intel.get("themes"):
            lines.append("\nActive themes:")
            for th in intel["themes"][:3]:
                vel = th.get("velocity_score") or 0
                delta = th.get("velocity_delta") or 0
                ds = f"+{delta:.2f}" if delta >= 0 else f"{delta:.2f}"
                lines.append(f"  \u2022 {th['theme']} (vel: {vel:.1f}, \u0394{ds})")
        if intel.get("accounts_mentioning"):
            lines.append(f"\nAccounts: {', '.join(a for a in intel['accounts_mentioning'][:4])}")
        if intel.get("co_occurring_tickers"):
            lines.append(f"Co-mentioned: {', '.join(x['ticker'] for x in intel['co_occurring_tickers'][:4])}")
    else:
        lines.append("*REMI INTELLIGENCE*")
        lines.append(f"No X/RSS mentions of {ticker} in last 30 days.")
    lines.append(f"\n_Added by {watchlist_entry.get('added_by','?')} on {watchlist_entry.get('added_date','?')}_")
    return "\n".join(lines)


def format_dossier_for_prompt(ticker, watchlist_entry, intel):
    lines = [f"=== REMI TICKER DOSSIER: {ticker} ===",
             f"Company: {watchlist_entry.get('company', ticker)}",
             f"Conviction: {watchlist_entry.get('conviction', '?')}",
             f"Sizing: {watchlist_entry.get('sizing', '?')}",
             f"Target: {watchlist_entry.get('target_return', '?')}",
             f"Source: {watchlist_entry.get('source_attribution', '?')}",
             "", "THESIS:", watchlist_entry.get("thesis_summary", ""), ""]
    for label, key in [("CATALYSTS:", "catalysts"), ("KEY RISKS:", "key_risks")]:
        items = watchlist_entry.get(key, [])
        if items:
            lines.append(label)
            for c in items:
                lines.append(f"- {c}")
            lines.append("")
    if intel.get("found") and intel.get("themes"):
        lines.append("ACTIVE NARRATIVE THEMES (last 30d):")
        for th in intel["themes"][:4]:
            lines.append(f"- {th['theme']} (velocity: {th.get('velocity_score',0):.1f})")
        lines.append("")
    lines.append("=== END REMI TICKER DOSSIER ===")
    return "\n".join(lines)


if __name__ == "__main__":
    import sys
    ticker = sys.argv[1] if len(sys.argv) > 1 else "PROP"
    result = get_ticker_intelligence(ticker)
    print(json.dumps(result, indent=2, default=str))

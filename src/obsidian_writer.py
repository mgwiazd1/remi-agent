"""
Obsidian Writer — generates .md notes to the investing vault.
Writes Theme notes and Document notes with proper frontmatter for Dataview.
"""
import json
import logging
import os
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Optional
from dotenv import load_dotenv

load_dotenv(os.path.expanduser("~/remi-intelligence/.env"))

logger = logging.getLogger(__name__)

DB_PATH = os.getenv("DB_PATH", os.path.expanduser("~/remi-intelligence/remi_intelligence.db"))
VAULT_PATH = os.getenv("OBSIDIAN_VAULT_PATH",
    "/docker/obsidian/investing/Intelligence")


def safe_filename(s: str) -> str:
    return "".join(c if c.isalnum() or c in "-_ " else "_" for c in s).strip()


def write_theme_note(theme_id: int, conn: sqlite3.Connection) -> Optional[str]:
    cur = conn.cursor()
    cur.execute("""
        SELECT theme_key, theme_label, first_seen_at, last_seen_at,
               velocity_score, velocity_delta, mention_count,
               is_flagged, gli_phase_at_emergence, steno_regime_at_emergence
        FROM themes WHERE id = ?
    """, (theme_id,))
    t = cur.fetchone()
    if not t:
        return None

    (theme_key, theme_label, first_seen, last_seen,
     velocity, v_delta, mentions, is_flagged,
     gli_phase, steno_regime) = t

    # Only write vault file if theme has been mentioned by 2+ sources
    if mentions < 2:
        return None

    # Gather all document-theme data for this theme
    cur.execute("""
        SELECT d.title, d.source_name, d.source_tier, d.source_url,
               dt.facts, dt.opinions, dt.key_quotes, dt.tickers_mentioned,
               dt.sentiment, d.ingested_at, dt.historical_analog
        FROM document_themes dt
        JOIN documents d ON d.id = dt.document_id
        WHERE dt.theme_id = ?
        ORDER BY d.ingested_at DESC
    """, (theme_id,))
    rows = cur.fetchall()

    # Aggregate facts, opinions, tickers
    all_facts, all_opinions, all_tickers = [], [], []
    source_links = []
    sentiments = []
    historical_analogs = []
    for title, sname, stier, surl, facts_j, ops_j, quotes_j, tickers_j, sent, ing, hist_analog in rows:
        try:
            all_facts.extend(json.loads(facts_j or "[]"))
        except Exception:
            pass
        try:
            all_opinions.extend(json.loads(ops_j or "[]"))
        except Exception:
            pass
        try:
            all_tickers.extend(json.loads(tickers_j or "[]"))
        except Exception:
            pass
        sentiments.append(sent or "neutral")
        if hist_analog and hist_analog != "none":
            historical_analogs.append(hist_analog)
        fname = safe_filename(title)
        source_links.append(f"- [[DOC_{fname}]] ({sname}, Tier {stier})")

    unique_tickers = sorted(set(t for t in all_tickers if t))
    flag_badge = "🔍 FLAGGED" if is_flagged else ""

    # Ticker table
    ticker_rows = "\n".join(f"| {tk} | — | — |" for tk in unique_tickers) if unique_tickers else "| — | — | — |"

    # Second-order inferences
    cur.execute("""
        SELECT second_order, third_order, key_variables, gli_regime_context
        FROM second_order_inferences WHERE trigger_theme = ?
        ORDER BY created_at DESC LIMIT 1
    """, (theme_key,))
    inf_row = cur.fetchone()
    second_order_block = ""
    if inf_row:
        try:
            so = json.loads(inf_row[0] or "[]")
            to = json.loads(inf_row[1] or "[]")
            kv = json.loads(inf_row[2] or "[]")
            for item in so:
                second_order_block += f"- **{item.get('description','')}** ({item.get('confidence','?')} confidence, ~{item.get('time_lag_days','?')}d lag)\n"
                second_order_block += f"  - {item.get('mechanism','')}\n"
            if to:
                second_order_block += "\n**Third-order:**\n"
                for item in to:
                    second_order_block += f"- {item.get('description','')} (~{item.get('time_lag_days','?')}d)\n"
            if kv:
                second_order_block += "\n**Variables to monitor:**\n"
                for v in kv:
                    second_order_block += f"- {v}\n"
            if inf_row[3]:
                second_order_block += f"\n*Regime note: {inf_row[3]}*\n"
        except Exception:
            pass
    if not second_order_block:
        second_order_block = "_Not yet computed — threshold not crossed_"

    facts_list = "\n".join(f"- {f}" for f in all_facts) or "_None extracted_"
    opinions_list = "\n".join(f"- {f}" for f in all_opinions) or "_None extracted_"
    sources_block = "\n".join(source_links) or "_None_"

    # Dominant sentiment
    from collections import Counter
    dominant_sent = Counter(sentiments).most_common(1)[0][0] if sentiments else "neutral"

    # Dominant historical analog (most common, excluding "none")
    historical_analog_display = "_No clear historical parallel identified_"
    if historical_analogs:
        analog_counts = Counter(historical_analogs)
        dominant_analog = analog_counts.most_common(1)[0][0]
        historical_analog_display = dominant_analog

    note = f"""---
type: theme
theme_key: {theme_key}
first_seen: "{first_seen}"
last_seen: "{last_seen}"
velocity_score: {velocity}
velocity_delta: {v_delta or 0}
mention_count: {mentions}
gli_phase_at_emergence: "{gli_phase or 'unknown'}"
steno_regime_at_emergence: "{steno_regime or 'unknown'}"
is_flagged: {str(bool(is_flagged)).lower()}
tickers: {json.dumps(unique_tickers)}
sentiment: {dominant_sent}
tags: [investing/theme]
---

# {theme_label} {flag_badge}

## Signal Summary
- **Velocity Score:** {velocity}/100 ({f'+{v_delta:.1f}' if (v_delta or 0) > 0 else f'{v_delta or 0:.1f}'} vs 7d ago)
- **First Detected:** {first_seen[:10]} — GLI Phase: `{gli_phase or 'unknown'}` / Regime: `{steno_regime or 'unknown'}`
- **Last Updated:** {last_seen[:10] if last_seen else 'today'}
- **Mention Count:** {mentions} source(s)
- **Sentiment:** {dominant_sent}

## Historical Analog
{historical_analog_display}

## Facts Extracted
{facts_list}

## Opinions / Forecasts
{opinions_list}

## Tickers
| Ticker | Note | Sentiment |
|--------|------|-----------|
{ticker_rows}

## Second-Order Implications
{second_order_block}

## Source Documents
{sources_block}

---
*Last updated by Remi: {datetime.utcnow().strftime('%Y-%m-%d %H:%M')} UTC*
"""

    # Write file
    themes_dir = Path(VAULT_PATH) / "Themes"
    themes_dir.mkdir(parents=True, exist_ok=True)
    filepath = themes_dir / f"THEME_{safe_filename(theme_key)}.md"
    filepath.write_text(note, encoding="utf-8")

    # Log write
    cur.execute("""
        INSERT OR REPLACE INTO obsidian_writes (filepath, note_type, reference_id, written_at)
        VALUES (?, 'theme', ?, ?)
    """, (str(filepath), theme_id, datetime.utcnow().isoformat()))
    conn.commit()

    logger.info(f"Wrote theme note: {filepath.name}")
    return str(filepath)


def write_document_note(doc_id: int, conn: sqlite3.Connection) -> Optional[str]:
    cur = conn.cursor()
    cur.execute("""
        SELECT source_name, source_tier, source_url, title,
               content_text, ingested_at, published_at,
               gli_phase, steno_regime, fiscal_score
        FROM documents WHERE id = ?
    """, (doc_id,))
    d = cur.fetchone()
    if not d:
        return None

    (sname, stier, surl, title, content, ingested, published,
     gli_phase, steno_regime, fiscal_score) = d

    # Get themes for this doc
    cur.execute("""
        SELECT t.theme_label, t.theme_key, dt.sentiment, dt.facts,
               dt.opinions, dt.key_quotes
        FROM document_themes dt
        JOIN themes t ON t.id = dt.theme_id
        WHERE dt.document_id = ?
    """, (doc_id,))
    theme_rows = cur.fetchall()

    themes_block = ""
    all_facts, all_opinions, key_quote = [], [], ""
    for tlabel, tkey, sent, facts_j, ops_j, quotes_j in theme_rows:
        themes_block += f"- [[THEME_{safe_filename(tkey)}]] — {sent}\n"
        try:
            all_facts.extend(json.loads(facts_j or "[]"))
        except Exception:
            pass
        try:
            all_opinions.extend(json.loads(ops_j or "[]"))
        except Exception:
            pass
        try:
            quotes = json.loads(quotes_j or "[]")
            if quotes and not key_quote:
                key_quote = quotes[0]
        except Exception:
            pass

    facts_list = "\n".join(f"- {f}" for f in all_facts) or "_None extracted_"
    opinions_list = "\n".join(f"- {f}" for f in all_opinions) or "_None extracted_"
    summary = content[:300].replace("\n", " ").strip() + "..." if content else "_No content_"

    fname = safe_filename(title or f"doc_{doc_id}")
    note = f"""---
type: document
source: "{sname}"
source_tier: {stier or 4}
url: "{surl or ''}"
published: "{published or ''}"
ingested: "{ingested or ''}"
gli_phase: "{gli_phase or 'unknown'}"
steno_regime: "{steno_regime or 'unknown'}"
fiscal_score: {fiscal_score or 0}
tags: [investing/document]
---

# {title or 'Untitled'}

**Source:** [{sname}]({surl or '#'}) (Tier {stier or '?'})
**Published:** {published or 'unknown'}
**GLI at ingestion:** `{gli_phase or 'unknown'}` / `{steno_regime or 'unknown'}` / Fiscal: {fiscal_score or '?'}/10

## Summary
{summary}

## Themes Identified
{themes_block or '_None_'}

## Key Facts
{facts_list}

## Key Opinions
{opinions_list}

## Key Quote
> {key_quote or '_None extracted_'}

---
*Ingested by Remi: {ingested or 'unknown'}*
"""

    docs_dir = Path(VAULT_PATH) / "Documents"
    docs_dir.mkdir(parents=True, exist_ok=True)
    filepath = docs_dir / f"DOC_{fname}.md"
    filepath.write_text(note, encoding="utf-8")

    cur.execute("""
        INSERT OR REPLACE INTO obsidian_writes (filepath, note_type, reference_id, written_at)
        VALUES (?, 'document', ?, ?)
    """, (str(filepath), doc_id, datetime.utcnow().isoformat()))
    conn.commit()

    logger.info(f"Wrote document note: {filepath.name}")
    return str(filepath)


def write_all_completed(conn: Optional[sqlite3.Connection] = None) -> dict:
    """Write Obsidian notes for all completed documents and their themes."""
    close_after = conn is None
    if conn is None:
        conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()

    # Docs that are complete but not yet written
    cur.execute("""
        SELECT d.id FROM documents d
        WHERE d.status = 'complete'
        AND d.id NOT IN (SELECT reference_id FROM obsidian_writes WHERE note_type = 'document')
    """)
    doc_ids = [r[0] for r in cur.fetchall()]

    # Themes not yet written or updated since last write (skip single-mention noise)
    cur.execute("""
        SELECT t.id FROM themes t
        WHERE t.id NOT IN (SELECT reference_id FROM obsidian_writes WHERE note_type = 'theme')
        AND t.mention_count >= 2
    """)
    theme_ids = [r[0] for r in cur.fetchall()]

    docs_written, themes_written = 0, 0
    for doc_id in doc_ids:
        if write_document_note(doc_id, conn):
            docs_written += 1
    for theme_id in theme_ids:
        if write_theme_note(theme_id, conn):
            themes_written += 1

    if close_after:
        conn.close()

    return {"docs_written": docs_written, "themes_written": themes_written}

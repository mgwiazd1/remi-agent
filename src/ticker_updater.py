"""
ticker_updater.py — Aggregate all vault intelligence per ticker into a single hub file.

Runs after extraction_worker processes each document. Gathers themes, documents,
5-pass stress tests, and second-order inferences mentioning each ticker, then
synthesizes a TICKER_*.md file using Consuela (local Gemma) for the summary writing.

Wired into extraction_worker.py via update_ticker_files().
"""

import json
import logging
import os
import re
import sqlite3
from datetime import datetime

import httpx

logger = logging.getLogger(__name__)

DB_PATH = os.getenv("DB_PATH", os.path.expanduser("~/remi-intelligence/remi_intelligence.db"))
TICKERS_DIR = os.path.join(
    os.getenv("VAULT_ROOT", "/docker/obsidian/investing/Intelligence"), "Tickers"
)
CONSUELA_URL = "http://127.0.0.1:8080/v1/chat/completions"

# Tickers to track (MG's active positions + watchlist + editorial coverage)
TRACKED_TICKERS = [
    "ZETA", "NBIS", "GLXY", "BE", "JD", "BIDU", "BTC", "HYPE",
    "GEV", "CIFR", "IREN", "CAT", "NVDA", "ORCL", "CRDO",
    "LITE", "COHR", "AMZN", "MSFT", "GOOGL", "META",
    "CGEH",
]


def _call_consuela(prompt: str, max_tokens: int = 1500) -> str | None:
    """Send synthesis work to local Gemma (Consuela)."""
    try:
        r = httpx.post(
            CONSUELA_URL,
            json={
                "model": "gemma",
                "messages": [{"role": "user", "content": prompt}],
                "max_tokens": max_tokens,
                "temperature": 0.3,
            },
            timeout=120.0,
        )
        if r.status_code == 200:
            return r.json()["choices"][0]["message"]["content"]
        logger.info(f"Consuela returned {r.status_code}, falling back to template")
        return None
    except Exception as e:
        logger.info(f"Consuela unavailable ({e}), using template")
        return None


def gather_ticker_intel(conn: sqlite3.Connection, ticker: str) -> dict:
    """Pull all intelligence from DB for a given ticker."""
    cur = conn.cursor()

    # 1. Themes mentioning this ticker
    cur.execute("""
        SELECT t.theme_key, t.theme_label, t.sector, t.velocity_score,
               t.last_seen_at, t.mention_count,
               dt.facts, dt.opinions, dt.sentiment, dt.key_quotes,
               dt.weighted_score, d.source_name, d.title, d.ingested_at
        FROM document_themes dt
        JOIN themes t ON t.id = dt.theme_id
        JOIN documents d ON d.id = dt.document_id
        WHERE dt.tickers_mentioned LIKE ?
        ORDER BY d.ingested_at DESC
        LIMIT 15
    """, (f'%"{ticker}"%',))
    theme_rows = cur.fetchall()

    themes = []
    for row in theme_rows:
        key, label, sector, vel, last_seen, mentions = row[:6]
        facts_raw, opinions_raw, sentiment, quotes_raw = row[6:10]
        weighted_score, source_name, doc_title, ingested_at = row[10:14]

        facts = []
        if facts_raw:
            try:
                facts = json.loads(facts_raw)[:3]
            except (json.JSONDecodeError, TypeError):
                pass

        themes.append({
            "theme_key": key,
            "theme_label": label,
            "sector": sector,
            "velocity": vel,
            "sentiment": sentiment,
            "source": source_name,
            "doc_title": doc_title,
            "date": ingested_at[:10] if ingested_at else "",
            "top_facts": facts[:2],
        })

    # 2. Five-pass extractions mentioning this ticker
    cur.execute("""
        SELECT fp.framework_name, fp.mechanistic_anchor, fp.anchor_type,
               fp.regime_alignment, fp.vault_path, fp.full_result,
               d.source_name, d.title
        FROM five_pass_extractions fp
        JOIN documents d ON d.id = fp.document_id
        WHERE fp.full_result LIKE ?
        ORDER BY fp.created_at DESC
        LIMIT 5
    """, (f'%"{ticker}"%',))
    fp_rows = cur.fetchall()

    five_passes = []
    for row in fp_rows:
        fw_name, anchor, anchor_type, regime_align, vault_path, full_json, src, title = row
        stress_conviction = []
        stress_doubts = []
        try:
            fp_data = json.loads(full_json)
            p4 = fp_data.get("pass_4_stress_test", {})
            for c in p4.get("conviction_builds", []):
                if ticker.upper() in c.get("theme_or_ticker", "").upper():
                    stress_conviction.append(c.get("why", ""))
            for d in p4.get("conviction_doubts", []):
                if ticker.upper() in d.get("theme_or_ticker", "").upper():
                    stress_doubts.append(d.get("why", ""))
        except (json.JSONDecodeError, TypeError):
            pass

        five_passes.append({
            "framework": fw_name or title,
            "anchor": anchor,
            "regime_alignment": regime_align,
            "conviction": stress_conviction,
            "doubts": stress_doubts,
            "vault_path": vault_path,
            "source": src,
        })

    # 3. Second-order inferences mentioning this ticker
    cur.execute("""
        SELECT soi.trigger_theme, soi.primary_impact, soi.second_order,
               soi.third_order, soi.key_variables
        FROM second_order_inferences soi
        WHERE soi.primary_impact LIKE ?
           OR soi.second_order LIKE ?
           OR soi.third_order LIKE ?
        ORDER BY soi.id DESC
        LIMIT 5
    """, (f'%"{ticker}"%', f'%"{ticker}"%', f'%"{ticker}"%'))
    soi_rows = cur.fetchall()

    inferences = []
    for row in soi_rows:
        trigger, primary, second, third, key_vars = row
        inferences.append({
            "trigger_theme": trigger,
            "primary": primary[:200] if primary else "",
        })

    # 4. Document count and recency
    cur.execute("""
        SELECT COUNT(DISTINCT dt.document_id),
               MAX(d.ingested_at)
        FROM document_themes dt
        JOIN documents d ON d.id = dt.document_id
        WHERE dt.tickers_mentioned LIKE ?
    """, (f'%"{ticker}"%',))
    count_row = cur.fetchone()
    doc_count = count_row[0] if count_row else 0
    last_mentioned = count_row[1][:10] if count_row and count_row[1] else ""

    return {
        "ticker": ticker,
        "doc_count": doc_count,
        "last_mentioned": last_mentioned,
        "themes": themes,
        "five_passes": five_passes,
        "inferences": inferences,
    }


def build_ticker_note(intel: dict, consuela_summary: str | None = None) -> str:
    """Build the TICKER_*.md file content from gathered intel."""
    ticker = intel["ticker"]
    now = datetime.utcnow().strftime("%Y-%m-%d")

    # Sector dedup
    sectors = sorted(set(t["sector"] for t in intel["themes"] if t.get("sector")))
    sector_str = ", ".join(sectors) if sectors else "unclassified"

    # Theme keys for tags
    theme_keys = [t["theme_key"] for t in intel["themes"][:5]]

    lines = [
        "---",
        f"type: ticker_hub",
        f"ticker: {ticker}",
        f"status: active",
        f"last_updated: {now}",
        f"doc_mentions: {intel['doc_count']}",
        f"last_mentioned: {intel['last_mentioned']}",
        f"sectors: [{sector_str}]",
        f"themes: [{', '.join(theme_keys)}]",
        f"tags: [ticker, {ticker.lower()}, {', '.join(s.lower() for s in sectors[:3])}]",
        "---",
        "",
        f"# {ticker}",
        "",
    ]

    # Consuela's synthesis if available
    if consuela_summary:
        lines.extend([
            "## Intelligence Synthesis",
            "",
            consuela_summary.strip(),
            "",
            "---",
            "",
        ])
    else:
        lines.extend([
            "## Intelligence Synthesis",
            "",
            f"*(Consuela offline — template mode. {intel['doc_count']} documents, "
            f"{len(intel['themes'])} themes, {len(intel['five_passes'])} deep extractions tracked.)*",
            "",
            "---",
            "",
        ])

    # Theme exposure
    lines.extend([
        "## Theme Exposure",
        "",
        "| Theme | Sector | Velocity | Sentiment | Source | Date |",
        "|-------|--------|----------|-----------|--------|------|",
    ])
    for t in intel["themes"][:12]:
        lines.append(
            f'| [[THEME_{t["theme_key"]}]] | {t["sector"]} | '
            f'{t["velocity"]:.0f} | {t["sentiment"]} | {t["source"]} | {t["date"]} |'
        )
    lines.extend(["", "---", ""])

    # 5-pass stress tests
    if intel["five_passes"]:
        lines.extend([
            "## 5-Pass Stress Tests",
            "",
        ])
        for fp in intel["five_passes"]:
            lines.append(f"### {fp['framework']}")
            lines.append(f"- **Source:** {fp['source']}")
            lines.append(f"- **Regime Alignment:** {fp['regime_alignment']}")
            if fp["anchor"]:
                lines.append(f"- **Mechanistic Anchor:** {fp['anchor'][:150]}")
            if fp["conviction"]:
                lines.append(f"- **Conviction Builds:** {'; '.join(fp['conviction'][:2])}")
            if fp["doubts"]:
                lines.append(f"- **Conviction Doubts:** {'; '.join(fp['doubts'][:2])}")
            if fp["vault_path"]:
                fname = os.path.basename(fp["vault_path"])
                lines.append(f"- **Framework Note:** [[{fname}]]")
            lines.append("")

        lines.extend(["---", ""])

    # Second-order inferences
    if intel["inferences"]:
        lines.extend([
            "## Second-Order Inferences",
            "",
        ])
        for inf in intel["inferences"]:
            lines.append(f"- **Trigger:** {inf['trigger_theme']} — {inf['primary'][:150]}")
        lines.extend(["", "---", ""])

    # Key facts across themes
    all_facts = []
    for t in intel["themes"]:
        all_facts.extend(t.get("top_facts", []))
    if all_facts:
        lines.extend([
            "## Key Facts Across Sources",
            "",
        ])
        seen = set()
        for f in all_facts[:10]:
            if f and f not in seen:
                lines.append(f"- {f}")
                seen.add(f)
        lines.extend(["", "---", ""])

    # Cross-references
    related_tickers = set()
    for t in intel["themes"]:
        # Extract other tickers from the same theme documents
        pass
    lines.extend([
        "## Related Tickers",
        "",
        "*(Auto-populated from shared theme documents. See individual theme notes for context.)*",
        "",
    ])

    return "\n".join(lines)


def generate_consuela_prompt(intel: dict) -> str:
    """Build the prompt for Consuela to synthesize the ticker intelligence."""
    ticker = intel["ticker"]
    themes_summary = "\n".join(
        f"- {t['theme_label']} [{t['sector']}] vel={t['velocity']:.0f} sentiment={t['sentiment']} "
        f"(from {t['source']}, {t['date']})"
        for t in intel["themes"][:8]
    )

    stress_summary = ""
    for fp in intel["five_passes"][:3]:
        stress_summary += f"\nFramework: {fp['framework']}"
        if fp["conviction"]:
            stress_summary += f"\n  Conviction: {'; '.join(fp['conviction'][:2])}"
        if fp["doubts"]:
            stress_summary += f"\n  Doubts: {'; '.join(fp['doubts'][:2])}"

    return f"""Synthesize {ticker} intelligence in 2-3 sentences. Cover: main themes driving it, conviction vs doubts, key watch item.

{ticker} THEMES: {chr(10).join('- ' + t['theme_label'] + ' [' + t['sector'] + '] vel=' + str(int(t['velocity'])) for t in intel['themes'][:5])}
"""


def update_ticker(ticker: str, conn: sqlite3.Connection):
    """Generate or update a single TICKER_*.md file."""
    os.makedirs(TICKERS_DIR, exist_ok=True)

    intel = gather_ticker_intel(conn, ticker)
    if intel["doc_count"] == 0:
        logger.info(f"No intelligence found for {ticker}, skipping")
        return

    # Check if file already exists — preserve manual additions
    filepath = os.path.join(TICKERS_DIR, f"TICKER_{ticker}.md")

    # Generate Consuela synthesis (only for manageable doc counts)
    consuela_summary = None
    if intel["doc_count"] <= 20:
        prompt = generate_consuela_prompt(intel)
        consuela_summary = _call_consuela(prompt, max_tokens=300)

    # Build the note
    content = build_ticker_note(intel, consuela_summary)

    # If file exists, preserve any manual sections (## Position Notes, ## Robo Signals)
    manual_sections = {}
    if os.path.exists(filepath):
        with open(filepath) as f:
            existing = f.read()
        # Extract manual sections (Position Notes, Robo Signals, Thesis Notes)
        for section_name in ["## Position Notes", "## Robo Signals", "## Thesis Notes", "## Manual Notes"]:
            idx = existing.find(section_name)
            if idx != -1:
                # Find next ## or end of file
                next_section = existing.find("\n## ", idx + len(section_name))
                if next_section == -1:
                    manual_sections[section_name] = existing[idx:]
                else:
                    manual_sections[section_name] = existing[idx:next_section]

    # Append preserved manual sections
    if manual_sections:
        content += "\n"
        for section_text in manual_sections.values():
            content += "\n" + section_text.strip() + "\n"

    with open(filepath, "w") as f:
        f.write(content)
    logger.info(f"Updated TICKER_{ticker}.md ({intel['doc_count']} docs, {len(intel['themes'])} themes)")


def update_all_tracked(conn: sqlite3.Connection = None):
    """Update all tracked tickers."""
    close_conn = conn is None
    if close_conn:
        conn = sqlite3.connect(DB_PATH)

    try:
        for ticker in TRACKED_TICKERS:
            try:
                update_ticker(ticker, conn)
            except Exception as e:
                logger.error(f"Failed to update {ticker}: {e}")
    finally:
        if close_conn:
            conn.close()


def _get_existing_ticker_files() -> set:
    """Return set of ticker names that already have hub files."""
    os.makedirs(TICKERS_DIR, exist_ok=True)
    existing = set()
    for f in os.listdir(TICKERS_DIR):
        if f.startswith("TICKER_") and f.endswith(".md"):
            # Extract ticker name: TICKER_BE.md -> BE, TICKER_APR_KR.md -> APR_KR
            name = f[7:-3]  # strip TICKER_ prefix and .md suffix
            # The base ticker is the first part before _KR, _CN etc.
            parts = name.split("_")
            existing.add(name)
            if len(parts) > 1:
                existing.add(parts[0])  # Also add base ticker (APR from APR_KR)
    return existing


def _build_stub_ticker(ticker: str, context: dict, consuela_summary: str | None = None) -> str:
    """Build a stub ticker hub file for a newly discovered ticker."""
    now = datetime.utcnow().strftime("%Y-%m-%d")
    source = context.get("source_name", "unknown")
    doc_title = context.get("doc_title", "")
    theme_key = context.get("theme_key", "")
    theme_label = context.get("theme_label", "")
    sector = context.get("sector", "unclassified")
    sentiment = context.get("sentiment", "")
    facts = context.get("facts", [])

    # Determine suffix for foreign tickers
    suffix = ""
    ticker_upper = ticker.upper()
    # Korean tickers common patterns
    if any(c in ticker for c in ["APR", "WONIK", "PARK", "ISC", "MICO", "HYUNDAI", "ROTEM", "SAM"]):
        suffix = "_KR"

    filename_ticker = f"{ticker_upper}{suffix}"

    lines = [
        "---",
        f"type: ticker_hub",
        f"ticker: {ticker_upper}",
        f"status: auto_discovered",
        f"last_updated: {now}",
        f"doc_mentions: 1",
        f"sectors: [{sector}]",
        f"themes: [{theme_key}]" if theme_key else f"themes: []",
        f"tags: [ticker, {ticker_upper.lower()}, {sector.lower()}, auto-discovered]",
        "---",
        "",
        f"# {ticker_upper}",
        "",
    ]

    if consuela_summary:
        lines.extend(["## Intelligence Synthesis", "", consuela_summary.strip(), "", "---", ""])
    else:
        lines.extend([
            "## Intelligence Synthesis", "",
            f"*(Auto-discovered from {source}. Awaiting deeper research.)*", "",
            f"First seen in: {doc_title}" if doc_title else "",
            "",
            "---", "",
        ])

    # Theme exposure
    if theme_key:
        lines.extend([
            "## Theme Exposure", "",
            "| Theme | Sector | Sentiment | Source | Date |",
            "|-------|--------|-----------|--------|------|",
            f"| [[THEME_{theme_key}]] | {sector} | {sentiment} | {source} | {now} |",
            "", "---", "",
        ])

    # Key facts from the article
    if facts:
        lines.extend(["## Key Facts", ""])
        for f in facts[:5]:
            if f:
                lines.append(f"- {f}")
        lines.extend(["", "---", ""])

    lines.extend([
        "## Watch Items", "",
        "*(Auto-generated — needs manual research to populate)*", "",
        "1. Company fundamentals — revenue, margins, growth trajectory",
        "2. Competitive positioning — moat, market share, pricing power",
        "3. Valuation — EV/Sales, PE, relative to peers",
        "4. Catalyst timeline — earnings, contract wins, listing changes",
        "",
    ])

    return "\n".join(lines)


def discover_new_tickers(doc_id: int, conn: sqlite3.Connection) -> list[str]:
    """Find tickers mentioned in a document that don't have hub files yet.
    Creates stub files and returns list of newly discovered tickers."""
    cur = conn.cursor()

    # Get all tickers mentioned in this document
    cur.execute("""
        SELECT DISTINCT json_each.value
        FROM document_themes, json_each(document_themes.tickers_mentioned)
        WHERE document_themes.document_id = ?
          AND json_each.value IS NOT NULL
          AND length(json_each.value) >= 2
          AND length(json_each.value) <= 15
    """, (doc_id,))
    all_tickers = {row[0].upper() for row in cur.fetchall()}

    if not all_tickers:
        return []

    # Filter out known non-ticker noise (common false positives)
    NOISE = {"THE", "AND", "FOR", "NOT", "BUT", "ARE", "WAS", "HAS", "HAD",
             "HIS", "HER", "ITS", "OUR", "WHO", "WHY", "HOW", "ALL", "ANY",
             "CAN", "WILL", "MAY", "MIGHT", "MUST", "SHALL", "BEEN", "BEING",
             "FROM", "WITH", "THIS", "THAT", "THEY", "THEM", "THEIR", "WHAT",
             "WHEN", "WHERE", "WHICH", "THERE", "THESE", "THOSE", "EACH",
             "ETF", "INC", "LTD", "LLC", "CORP", "NYSE", "OTC", "SEC",
             "GDP", "CPI", "PPI", "FOMC", "YOY", "QOQ", "TTM", "EV", "PE",
             "EPS", "FCF", "EBITDA", "ROE", "ROA", "TAM", "SAM", "SOM",
             "AI", "IT", "IP", "US", "UK", "EU", "AP", "EM", "FM"}
    all_tickers = all_tickers - NOISE

    # Filter out already-tracked tickers
    tracked_set = {t.upper() for t in TRACKED_TICKERS}
    existing_files = _get_existing_ticker_files()
    already_known = tracked_set | existing_files

    new_tickers = all_tickers - already_known
    if not new_tickers:
        return []

    # Get context for each new ticker from the document
    discovered = []
    for ticker in sorted(new_tickers):
        # Get the context from document_themes
        cur.execute("""
            SELECT t.theme_key, t.theme_label, t.sector,
                   dt.sentiment, d.source_name, d.title,
                   dt.facts
            FROM document_themes dt
            JOIN themes t ON t.id = dt.theme_id
            JOIN documents d ON d.id = dt.document_id
            WHERE dt.document_id = ?
              AND dt.tickers_mentioned LIKE ?
            LIMIT 3
        """, (doc_id, f'%"{ticker}"%'))

        rows = cur.fetchall()
        if not rows:
            continue

        # Use first row for primary context
        row = rows[0]
        theme_key, theme_label, sector, sentiment, source_name, doc_title, facts_raw = row

        facts = []
        if facts_raw:
            try:
                facts = json.loads(facts_raw)[:3]
            except (json.JSONDecodeError, TypeError):
                pass

        context = {
            "theme_key": theme_key or "",
            "theme_label": theme_label or "",
            "sector": sector or "unclassified",
            "sentiment": sentiment or "",
            "source_name": source_name or "unknown",
            "doc_title": doc_title or "",
            "facts": facts,
        }

        # Try Consuela synthesis for the new ticker
        consuela_summary = None
        if facts:
            prompt = f"""Write 2-3 sentences summarizing what this intelligence says about {ticker}. Be specific, mention the source.

{ticker} was mentioned by {source_name} in context of {theme_label} ({sector}). Key facts: {'; '.join(facts[:3])}"""
            consuela_summary = _call_consuela(prompt, max_tokens=300)

        # Build and write the stub file
        content = _build_stub_ticker(ticker, context, consuela_summary)

        # Determine filename
        suffix = ""
        filename_ticker = ticker.upper()
        filepath = os.path.join(TICKERS_DIR, f"TICKER_{filename_ticker}{suffix}.md")

        with open(filepath, "w") as f:
            f.write(content)

        discovered.append(ticker)
        logger.info(f"Auto-discovered new ticker: {ticker} → TICKER_{filename_ticker}{suffix}.md")

    return discovered


def update_for_document(doc_id: int, conn: sqlite3.Connection):
    """Called by extraction_worker after processing a document. Updates tracked tickers and discovers new ones."""
    cur = conn.cursor()

    # 1. Update existing tracked tickers mentioned in this doc
    if TRACKED_TICKERS:
        cur.execute("""
            SELECT DISTINCT json_each.value
            FROM document_themes, json_each(document_themes.tickers_mentioned)
            WHERE document_themes.document_id = ?
              AND json_each.value IN ({})
        """.format(",".join(f"'{t}'" for t in TRACKED_TICKERS)), (doc_id,))

        tracked_mentioned = [row[0] for row in cur.fetchall()]
        for ticker in tracked_mentioned:
            try:
                update_ticker(ticker, conn)
            except Exception as e:
                logger.error(f"Failed to update {ticker} after doc {doc_id}: {e}")

    # 2. Discover new tickers not yet tracked
    try:
        new_tickers = discover_new_tickers(doc_id, conn)
        if new_tickers:
            logger.info(f"Doc {doc_id}: discovered {len(new_tickers)} new tickers: {new_tickers}")
    except Exception as e:
        logger.error(f"Ticker discovery failed for doc {doc_id}: {e}")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    update_all_tracked()
    print("Done")

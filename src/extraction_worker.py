import json
import logging
import math
import os
import re
import sqlite3
import hashlib
from datetime import datetime, timedelta
from typing import Optional, Tuple
from dotenv import load_dotenv

load_dotenv(os.path.expanduser("~/remi-intelligence/.env"))

from gli_stamper import fetch_gli_stamp
from llm_extractor import extract_themes, extract_second_order, extract_five_pass

# Dashboard push integration
try:
    from dashboard_push import push_signal, push_document
    HAS_DASHBOARD = True
except ImportError:
    HAS_DASHBOARD = False

logger = logging.getLogger(__name__)

VAULT_ROOT = os.getenv("VAULT_ROOT", "/docker/obsidian/investing/Intelligence")
DB_PATH = os.getenv("DB_PATH", os.path.expanduser("~/remi-intelligence/remi_intelligence.db"))
MAX_PER_RUN = int(os.getenv("MAX_EXTRACTIONS_PER_RUN", 25))
SECOND_ORDER_ENABLED = os.getenv("SECOND_ORDER_ENABLED", "true").lower() == "true"
VELOCITY_FLAG_THRESHOLD = float(os.getenv("VELOCITY_FLAG_THRESHOLD", 15.0))
TIER_WEIGHTS = {1: 1.0, 2: 0.8, 3: 0.5, 4: 0.2}
RECENCY_HALF_LIFE_DAYS = 7


def get_active_anchors(db_path: str, limit: int = 40) -> list:
    """Pull current active theme anchors for prompt injection."""
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    rows = conn.execute("""
        SELECT theme_key, theme_label, sector
        FROM themes
        WHERE last_seen_at > datetime('now', '-14 days')
          AND mention_count >= 2
        ORDER BY velocity_score DESC
        LIMIT ?
    """, (limit,)).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def format_anchors_for_prompt(anchors: list) -> str:
    """Format anchor list for LLM prompt injection."""
    if not anchors:
        return "No active themes yet. Create new theme keys as needed."

    lines = ["ACTIVE THEME ANCHORS (reuse these theme_keys when the article covers the same topic):"]
    for a in anchors:
        sector = a.get("sector", "macro")
        lines.append(f'  - "{a["theme_key"]}" [{sector}]: {a["theme_label"]}')
    lines.append("")
    lines.append("If this article covers a topic already in the list above, REUSE that theme_key exactly.")
    lines.append("Only create a NEW theme_key if the topic is genuinely not covered by any existing anchor.")
    return "\n".join(lines)


def get_or_create_theme(conn: sqlite3.Connection, theme_key: str,
                         theme_label: str, gli_stamp, sector: str = "macro") -> int:
    cur = conn.cursor()
    cur.execute("SELECT id FROM themes WHERE theme_key = ?", (theme_key,))
    row = cur.fetchone()
    if row:
        cur.execute("""
            UPDATE themes SET last_seen_at = ?, mention_count = mention_count + 1
            WHERE theme_key = ?
        """, (datetime.utcnow().isoformat(), theme_key))
        conn.commit()
        return row[0]
    else:
        cur.execute("""
            INSERT INTO themes
            (theme_key, theme_label, sector, first_seen_at, last_seen_at,
             gli_phase_at_emergence, steno_regime_at_emergence)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (theme_key, theme_label, sector,
              datetime.utcnow().isoformat(),
              datetime.utcnow().isoformat(),
              gli_stamp.gli_phase,
              gli_stamp.steno_regime))
        conn.commit()
        return cur.lastrowid


def compute_velocity(conn: sqlite3.Connection, theme_id: int, tier: int) -> float:
    cur = conn.cursor()
    now = datetime.utcnow()
    cutoff = now - timedelta(days=30)
    cur.execute("""
        SELECT dt.weighted_score, d.ingested_at, d.source_tier,
               d.retweets, d.views, d.likes
        FROM document_themes dt
        JOIN documents d ON d.id = dt.document_id
        WHERE dt.theme_id = ?
        AND d.ingested_at > ?
    """, (theme_id, cutoff.isoformat()))
    rows = cur.fetchall()

    from velocity_scorer import engagement_multiplier

    score = 0.0
    for _, ingested_at_str, src_tier, retweets, views, likes in rows:
        try:
            ingested = datetime.fromisoformat(ingested_at_str)
        except Exception:
            ingested = now
        age_days = max(0, (now - ingested).total_seconds() / 86400)
        recency_w = math.exp(-0.693 * age_days / RECENCY_HALF_LIFE_DAYS)
        tier_w = TIER_WEIGHTS.get(src_tier or 4, 0.2)
        engagement_w = engagement_multiplier(retweets or 0, views or 0, likes or 0)
        score += tier_w * recency_w * engagement_w * 10

    return min(100.0, round(score, 2))


def is_low_quality_content(content_text: str) -> Tuple[bool, str]:
    """
    Check if content is low-quality and should skip extraction.
    Returns (is_low_quality: bool, reason: str)
    """
    if not content_text:
        return True, "empty content"
    
    stripped = content_text.strip()
    
    # Check 1: Minimum length
    if len(stripped) < 100:
        return True, f"content too short ({len(stripped)} chars, min 100)"
    
    # Check 2: >80% non-Latin characters (CJK, Korean, Arabic, etc.)
    # Count Latin characters (ASCII letters, numbers, common punctuation)
    latin_pattern = re.compile(r'[a-zA-Z0-9\s\.,!?\'\"\-\(\)\[\]:;]')
    latin_chars = len(latin_pattern.findall(stripped))
    total_chars = len(stripped)
    
    if total_chars > 0:
        non_latin_ratio = 1.0 - (latin_chars / total_chars)
        if non_latin_ratio > 0.80:
            return True, f"non-Latin dominant ({non_latin_ratio:.0%} non-Latin)"
    
    # Check 3: Low-signal patterns (entire content matches)
    lower_content = stripped.lower()
    low_signal_patterns = [
        "register now",
        "learn more",
        "click here",
        "subscribe now",
        "gm",
        "gn"
    ]
    
    for pattern in low_signal_patterns:
        if lower_content == pattern:
            return True, f"low-signal pattern match: '{pattern}'"
    
    return False, ""


def write_framework_note(five_pass_result: dict, doc_id: int,
                         source_name: str, title: str,
                         gli_stamp, source_tier: int) -> str | None:
    """Write a 5-pass Framework note to the Obsidian vault.

    Returns the vault file path on success, None on failure.
    """
    frameworks_dir = os.path.join(VAULT_ROOT, "Frameworks")
    os.makedirs(frameworks_dir, exist_ok=True)

    p1 = five_pass_result.get("pass_1_framework", {})
    p2 = five_pass_result.get("pass_2_regime_positioning", {})
    p3 = five_pass_result.get("pass_3_second_order", {})
    p4 = five_pass_result.get("pass_4_stress_test", {})
    p5 = five_pass_result.get("pass_5_mechanistic_anchor", {})
    contradictions = five_pass_result.get("contradictions_with_vault", [])
    assessment = five_pass_result.get("overall_assessment", "")

    framework_name = p1.get("framework_name", title[:60])
    safe_name = re.sub(r'[^a-zA-Z0-9-]', '-', framework_name.lower())[:80]
    date_str = datetime.utcnow().strftime("%Y-%m-%d")
    filename = f"{safe_name}-{date_str}.md"
    filepath = os.path.join(frameworks_dir, filename)

    sectors = set()
    for inf in p3.get("cross_sector_inferences", []):
        s = inf.get("sector", "")
        if s:
            sectors.add(s)
    if not sectors:
        sectors = {"macro"}

    tags = ["five-pass", source_name.replace(".", "-")]
    anchor_type = p5.get("anchor_type", "")
    if anchor_type:
        tags.append(anchor_type)

    lines = [
        "---",
        f"type: framework",
        f"source: {source_name}",
        f"document_id: {doc_id}",
        f"source_tier: {source_tier}",
        f"date: {date_str}",
        f"gli_phase: {gli_stamp.gli_phase}",
        f"steno_regime: {gli_stamp.steno_regime}",
        f"sectors: [{', '.join(sorted(sectors))}]",
        f"status: active",
        f"tags: [{', '.join(tags)}]",
        "---",
        "",
        f"# {framework_name}",
        "",
        "## Pass 1: Framework Extraction",
        "",
        f"**Framework:** {framework_name}",
        f"**Core Premise:** {p1.get('core_premise', 'N/A')}",
        f"**Inputs:** {', '.join(p1.get('inputs', []))}",
        f"**Outputs:** {', '.join(p1.get('outputs', []))}",
        f"**Persistence:** {p1.get('persistence', 'N/A')}",
        "",
        "---",
        "",
        "## Pass 2: Regime Positioning",
        "",
        f"**GLI Phase Alignment:** {p2.get('gli_phase_alignment', 'N/A')}",
        f"**GLI Rationale:** {p2.get('gli_rationale', 'N/A')}",
        f"**Steno Regime Alignment:** {p2.get('steno_regime_alignment', 'N/A')}",
        f"**Steno Rationale:** {p2.get('steno_rationale', 'N/A')}",
        f"**Novel Signal:** {p2.get('regime_novel_signal', 'None')}",
        "",
        "---",
        "",
        "## Pass 3: Second-Order Connections",
        "",
    ]

    for i, inf in enumerate(p3.get("cross_sector_inferences", []), 1):
        lines.append(f"### Inference {i}: {inf.get('sector', '?')}")
        lines.append(f"- **Mechanism:** {inf.get('mechanism', 'N/A')}")
        lines.append(f"- **Tickers:** {', '.join(inf.get('ticker_implications', []))}")
        lines.append(f"- **Confidence:** {inf.get('confidence', 'N/A')}")
        lines.append("")

    lines.extend([
        "---",
        "",
        "## Pass 4: Active Position Stress Test",
        "",
        "### Conviction Builds",
    ])
    for c in p4.get("conviction_builds", []):
        lines.append(f"- **{c.get('theme_or_ticker', '?')}:** {c.get('why', 'N/A')}")

    lines.append("")
    lines.append("### Conviction Doubts")
    for d in p4.get("conviction_doubts", []):
        lines.append(f"- **{d.get('theme_or_ticker', '?')}:** {d.get('why', 'N/A')}")

    lines.extend([
        "",
        f"**Net Assessment:** {p4.get('net_assessment', 'N/A')}",
        "",
        "---",
        "",
        "## Pass 5: Mechanistic Anchor",
        "",
        f"**Relationship:** {p5.get('anchor_description', 'N/A')}",
        f"**Type:** {p5.get('anchor_type', 'N/A')}",
        f"**Current Value:** {p5.get('current_value', 'N/A')}",
        f"**Watch Direction:** {p5.get('watch_direction', 'N/A')}",
        f"**Invalidation:** {p5.get('invalidation_condition', 'N/A')}",
        "",
    ])

    if contradictions:
        lines.extend([
            "---",
            "",
            "## Contradictions with Vault",
            "",
        ])
        for cx in contradictions:
            lines.append(f"- **vs {cx.get('existing_framework', '?')}:** {cx.get('nature_of_contradiction', 'N/A')}")

    lines.extend([
        "",
        "---",
        "",
        "## Overall Assessment",
        "",
        assessment,
        "",
        f"## Source",
        f"- {source_name} (doc_id: {doc_id}, tier {source_tier})",
    ])

    try:
        with open(filepath, "w") as f:
            f.write("\n".join(lines))
        logger.info(f"Wrote framework note: {filepath}")
        return filepath
    except Exception as e:
        logger.error(f"Failed to write framework note: {e}")
        return None


def process_document(doc_id: int, conn: sqlite3.Connection,
                     gli_stamp) -> bool:
    cur = conn.cursor()
    cur.execute("""
        SELECT source_name, source_tier, content_text, title, source_url
        FROM documents WHERE id = ?
    """, (doc_id,))
    row = cur.fetchone()
    if not row:
        return False

    source_name, source_tier, content_text, title, source_url = row

    # Mark as processing
    cur.execute("UPDATE documents SET status = 'processing' WHERE id = ?", (doc_id,))
    conn.commit()

    try:
        # Content quality pre-filter
        is_low_quality, reason = is_low_quality_content(content_text)
        if is_low_quality:
            logger.info(f"Skipping low-quality document {doc_id}: {reason}")
            cur.execute("UPDATE documents SET status = 'complete' WHERE id = ?", (doc_id,))
            conn.commit()
            return True

        # Extract themes
        anchors = get_active_anchors(DB_PATH)
        anchors_block = format_anchors_for_prompt(anchors)
        result = extract_themes(
            content_text, source_name, source_tier or 4,
            gli_context=gli_stamp.for_prompt(),
            active_anchors_block=anchors_block
        )

        if not result or not result.get("themes"):
            cur.execute("UPDATE documents SET status = 'complete' WHERE id = ?", (doc_id,))
            conn.commit()
            return True

        # Update document with GLI stamp
        cur.execute("""
            UPDATE documents SET
                gli_phase = ?, gli_value_bn = ?, steno_regime = ?,
                fiscal_score = ?, transition_risk = ?
            WHERE id = ?
        """, (gli_stamp.gli_phase, gli_stamp.gli_value_bn,
              gli_stamp.steno_regime, gli_stamp.fiscal_score,
              gli_stamp.transition_risk, doc_id))

        # Process each theme
        for theme_data in result.get("themes", []):
            theme_key = theme_data.get("theme_key", "unknown")
            theme_label = theme_data.get("theme_label", theme_key)
            theme_sector = theme_data.get("sector", "macro")
            tier_w = TIER_WEIGHTS.get(source_tier or 4, 0.2)

            theme_id = get_or_create_theme(conn, theme_key, theme_label, gli_stamp, sector=theme_sector)

            # Insert document-theme junction
            cur.execute("""
                INSERT INTO document_themes
                (document_id, theme_id, facts, opinions, key_quotes,
                 tickers_mentioned, sentiment, weighted_score, historical_analog)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                doc_id, theme_id,
                json.dumps(theme_data.get("facts", [])),
                json.dumps(theme_data.get("opinions", [])),
                json.dumps([theme_data.get("key_quote", "")]),
                json.dumps(theme_data.get("tickers_mentioned", [])),
                theme_data.get("sentiment", "neutral"),
                tier_w,
                theme_data.get("historical_analog", "none")
            ))
            conn.commit()

            # Update velocity score
            velocity = compute_velocity(conn, theme_id, source_tier or 4)
            is_flagged = velocity >= VELOCITY_FLAG_THRESHOLD
            cur.execute("""
                UPDATE themes SET velocity_score = ?, is_flagged = ?,
                flagged_at = CASE WHEN ? AND flagged_at IS NULL
                             THEN ? ELSE flagged_at END
                WHERE id = ?
            """, (velocity, is_flagged, is_flagged,
                  datetime.utcnow().isoformat(), theme_id))
            conn.commit()

            # DripStack thin-sourcing trigger: high velocity, few sources
            if velocity >= 7:
                source_count = conn.execute(
                    "SELECT COUNT(*) FROM document_themes WHERE theme_id = ?",
                    (theme_id,)
                ).fetchone()[0]
                if source_count < 3:
                    logger.info(f"Thin-sourced high-velocity theme '{theme_key}' (v={velocity}, sources={source_count}) — querying DripStack")
                    try:
                        from dripstack_buyer import buy_for_theme
                        ds_ids = buy_for_theme(theme_key=theme_key, sector=theme_sector)
                        if ds_ids:
                            logger.info(f"DripStack ingested {len(ds_ids)} articles for '{theme_key}'")
                    except Exception as ds_err:
                        logger.error(f"DripStack buy_for_theme failed: {ds_err}")

            # Run second-order inference on flagged themes
            if SECOND_ORDER_ENABLED and is_flagged:
                logger.info(f"Running second-order inference for: {theme_label}")
                inference = extract_second_order(
                    theme_label, theme_data.get("summary", ""),
                    source_name, gli_stamp.for_prompt()
                )
                if inference:
                    cur.execute("""
                        INSERT INTO second_order_inferences
                        (trigger_document_id, trigger_theme, primary_impact,
                         second_order, third_order, tickers_by_hop,
                         key_variables, perishable, narrative_saturation,
                         gli_regime_context, full_inference)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """, (
                        doc_id, theme_key,
                        json.dumps(inference.get("primary_impact", {})),
                        json.dumps(inference.get("second_order", [])),
                        json.dumps(inference.get("third_order", [])),
                        json.dumps({}),
                        json.dumps(inference.get("key_variables_to_monitor", [])),
                        inference.get("perishable", False),
                        inference.get("narrative_saturation_estimate", "low"),
                        inference.get("regime_conditional_note", ""),
                        json.dumps(inference)
                    ))
                    conn.commit()

        cur.execute("UPDATE documents SET status = 'complete' WHERE id = ?", (doc_id,))
        conn.commit()
        logger.info(f"Processed: {title[:60]} → {len(result.get('themes', []))} themes")

        # --- Five-pass deep extraction (SOUL.md protocol) ---
        try:
            # Get active themes with tickers for stress test pass
            active_themes_for_fp = []
            fp_rows = conn.execute("""
                SELECT t.theme_key, t.theme_label, t.sector,
                       GROUP_CONCAT(DISTINCT dt.tickers_mentioned) as tickers_json
                FROM themes t
                LEFT JOIN document_themes dt ON dt.theme_id = t.id
                WHERE t.last_seen_at > datetime('now', '-14 days')
                  AND t.mention_count >= 2
                GROUP BY t.id
                ORDER BY t.velocity_score DESC
                LIMIT 20
            """).fetchall()
            for fp_r in fp_rows:
                tk, lb, sec, tj = fp_r
                tickers = []
                if tj:
                    try:
                        tickers = json.loads(tj)
                    except (json.JSONDecodeError, TypeError):
                        tickers = []
                active_themes_for_fp.append({
                    "theme_key": tk, "theme_label": lb,
                    "sector": sec, "tickers_mentioned": tickers
                })

            five_pass = extract_five_pass(
                content_text, source_name,
                gli_context=gli_stamp.for_prompt(),
                active_themes=active_themes_for_fp
            )

            if five_pass:
                vault_path = write_framework_note(
                    five_pass, doc_id, source_name, title,
                    gli_stamp, source_tier or 4
                )
                # Log to DB
                conn.execute("""
                    INSERT INTO five_pass_extractions
                    (document_id, source_name, framework_name,
                     mechanistic_anchor, anchor_type, regime_alignment,
                     vault_path, full_result)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    doc_id, source_name,
                    five_pass.get("pass_1_framework", {}).get("framework_name", ""),
                    five_pass.get("pass_5_mechanistic_anchor", {}).get("anchor_description", ""),
                    five_pass.get("pass_5_mechanistic_anchor", {}).get("anchor_type", ""),
                    five_pass.get("pass_2_regime_positioning", {}).get("gli_phase_alignment", ""),
                    vault_path,
                    json.dumps(five_pass)
                ))
                conn.commit()
                logger.info(f"Five-pass extraction complete for doc {doc_id}")
            else:
                logger.warning(f"Five-pass extraction returned None for doc {doc_id}")
        except Exception as fp_err:
            logger.error(f"Five-pass extraction failed for doc {doc_id}: {fp_err}")

        # --- Update ticker hub files ---
        try:
            from ticker_updater import update_for_document
            update_for_document(doc_id, conn)
        except Exception as tk_err:
            logger.error(f"Ticker update failed for doc {doc_id}: {tk_err}")

        # Push to dashboard (non-blocking)
        if HAS_DASHBOARD:
            try:
                # Create content hash
                content_hash = hashlib.sha256(content_text.encode()).hexdigest() if content_text else None
                
                # Extract themes list
                themes_list = [t.get("theme_label", t.get("theme_key", "")) for t in result.get("themes", [])]
                
                # Push document
                push_document(
                    source_name=source_name,
                    source_type="article",
                    title=title,
                    content_text=content_text[:5000] if content_text else None,  # Truncate for storage
                    content_hash=content_hash,
                    tier=source_tier,
                    clusters=[],  # Will be filled by theme cluster mapping
                    themes=themes_list,
                    gli_phase=gli_stamp.gli_phase,
                    steno_regime=gli_stamp.steno_regime,
                    published_at=datetime.utcnow(),
                    obsidian_path=None
                )
                
                # Push signal for all extracted themes (velocity calculated later)
                # Note: velocity_score is not available in extraction result yet
                # It's calculated in compute_velocity() below, so we push all valid themes
                for theme_data in result.get("themes", []):
                    if theme_data.get("theme_label"):  # Ensure theme has a valid label
                        push_signal(
                            source=source_name,
                            source_name=source_name,
                            title=theme_data.get("theme_label", ""),
                            summary=f"Extracted theme from {source_name}",
                            content_preview=content_text[:500] if content_text else None,
                            tier=source_tier,
                            clusters=theme_data.get("clusters", []),
                            gli_phase=gli_stamp.gli_phase
                        )
            except Exception as e:
                logger.error(f"Dashboard push failed: {e}")
        
        return True

    except Exception as e:
        logger.error(f"Error processing doc {doc_id}: {e}")
        cur.execute("UPDATE documents SET status = 'failed', error_msg = ? WHERE id = ?",
                    (str(e), doc_id))
        conn.commit()
        return False


def run_extraction_worker(max_docs: Optional[int] = None) -> dict:
    max_docs = max_docs or MAX_PER_RUN
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()

    cur.execute("""
        SELECT id FROM documents WHERE status = 'pending'
        ORDER BY ingested_at ASC LIMIT ?
    """, (max_docs,))
    pending = [row[0] for row in cur.fetchall()]

    if not pending:
        conn.close()
        return {"processed": 0, "failed": 0, "message": "No pending documents"}

    gli_stamp = fetch_gli_stamp()
    processed = 0
    failed = 0

    for doc_id in pending:
        success = process_document(doc_id, conn, gli_stamp)
        if success:
            processed += 1
        else:
            failed += 1

    conn.close()
    return {"processed": processed, "failed": failed,
            "gli_stamp": gli_stamp.for_prompt()}


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    result = run_extraction_worker()
    print(f"\nResult: {result}")

import json
import logging
import math
import os
import sqlite3
from datetime import datetime, timedelta
from typing import Optional
from dotenv import load_dotenv

load_dotenv(os.path.expanduser("~/remi-intelligence/.env"))

from gli_stamper import fetch_gli_stamp
from llm_extractor import extract_themes, extract_second_order

logger = logging.getLogger(__name__)

DB_PATH = os.getenv("DB_PATH", os.path.expanduser("~/remi-intelligence/remi_intelligence.db"))
MAX_PER_RUN = int(os.getenv("MAX_EXTRACTIONS_PER_RUN", 5))
SECOND_ORDER_ENABLED = os.getenv("SECOND_ORDER_ENABLED", "true").lower() == "true"
VELOCITY_FLAG_THRESHOLD = float(os.getenv("VELOCITY_FLAG_THRESHOLD", 15.0))
TIER_WEIGHTS = {1: 1.0, 2: 0.8, 3: 0.5, 4: 0.2}
RECENCY_HALF_LIFE_DAYS = 7


def get_or_create_theme(conn: sqlite3.Connection, theme_key: str,
                         theme_label: str, gli_stamp) -> int:
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
            (theme_key, theme_label, first_seen_at, last_seen_at,
             gli_phase_at_emergence, steno_regime_at_emergence)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (theme_key, theme_label,
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
        SELECT dt.weighted_score, d.ingested_at, d.source_tier
        FROM document_themes dt
        JOIN documents d ON d.id = dt.document_id
        WHERE dt.theme_id = ?
        AND d.ingested_at > ?
    """, (theme_id, cutoff.isoformat()))
    rows = cur.fetchall()

    score = 0.0
    for _, ingested_at_str, src_tier in rows:
        try:
            ingested = datetime.fromisoformat(ingested_at_str)
        except Exception:
            ingested = now
        age_days = max(0, (now - ingested).total_seconds() / 86400)
        recency_w = math.exp(-0.693 * age_days / RECENCY_HALF_LIFE_DAYS)
        tier_w = TIER_WEIGHTS.get(src_tier or 4, 0.2)
        score += tier_w * recency_w * 10

    return min(100.0, round(score, 2))


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
        # Extract themes
        result = extract_themes(
            content_text, source_name, source_tier or 4,
            gli_context=gli_stamp.for_prompt()
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
            tier_w = TIER_WEIGHTS.get(source_tier or 4, 0.2)

            theme_id = get_or_create_theme(conn, theme_key, theme_label, gli_stamp)

            # Insert document-theme junction
            cur.execute("""
                INSERT INTO document_themes
                (document_id, theme_id, facts, opinions, key_quotes,
                 tickers_mentioned, sentiment, weighted_score)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                doc_id, theme_id,
                json.dumps(theme_data.get("facts", [])),
                json.dumps(theme_data.get("opinions", [])),
                json.dumps([theme_data.get("key_quote", "")]),
                json.dumps(theme_data.get("tickers_mentioned", [])),
                theme_data.get("sentiment", "neutral"),
                tier_w
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
    result = run_extraction_worker(max_docs=3)
    print(f"\nResult: {result}")

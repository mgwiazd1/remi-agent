"""
reprocess_overnight.py — Run the full overnight reprocessing sequence.

Queues and drains batches of themeless docs sequentially:
  ZeroHedge (remaining) → OilPrice.com → Doomberg
  
Gates each batch at 10 pending. Triggers extraction cycles to drain.
Sends final summary via Telegram DM when complete.
"""

import os
import sys
import time
import sqlite3
import logging
from datetime import datetime
from dotenv import load_dotenv

load_dotenv(os.path.expanduser("~/remi-intelligence/.env"))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s: %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler(os.path.expanduser("~/remi-intelligence/reprocess_overnight.log")),
    ],
)
logger = logging.getLogger(__name__)

DB_PATH = os.getenv("DB_PATH", os.path.expanduser("~/remi-intelligence/remi_intelligence.db"))

BATCH_SIZE = 50
GATE_THRESHOLD = 10
MAX_PER_CYCLE = 25
CYCLE_PAUSE_SECONDS = 10  # brief pause between extraction cycles

SOURCES = [
    ("zerohedge", "ZeroHedge"),
    ("oilprice", "OilPrice.com"),
    ("doomberg", "Doomberg"),
]

MIN_CONTENT_LENGTH = 500
SINCE_DATE = "2026-03-25"


def count_pending():
    conn = sqlite3.connect(DB_PATH)
    count = conn.execute(
        "SELECT COUNT(*) FROM documents WHERE status IN ('pending', 'processing')"
    ).fetchone()[0]
    conn.close()
    return count


def count_remaining_for_source(source_name):
    conn = sqlite3.connect(DB_PATH)
    count = conn.execute("""
        SELECT COUNT(*)
        FROM documents d
        WHERE d.ingested_at > ?
        AND d.source_name = ?
        AND length(d.content_text) >= ?
        AND d.id NOT IN (SELECT DISTINCT document_id FROM document_themes)
        AND d.status NOT IN ('pending', 'processing')
    """, (SINCE_DATE, source_name, MIN_CONTENT_LENGTH)).fetchone()[0]
    conn.close()
    return count


def queue_batch(source_name, batch_size):
    """Queue a batch of docs for reprocessing. Returns number queued."""
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()

    rows = cur.execute("""
        SELECT d.id
        FROM documents d
        WHERE d.ingested_at > ?
        AND d.source_name = ?
        AND length(d.content_text) >= ?
        AND d.id NOT IN (SELECT DISTINCT document_id FROM document_themes)
        AND d.status NOT IN ('pending', 'processing')
        ORDER BY d.ingested_at DESC
        LIMIT ?
    """, (SINCE_DATE, source_name, MIN_CONTENT_LENGTH, batch_size)).fetchall()

    if not rows:
        conn.close()
        return 0

    doc_ids = [r[0] for r in rows]
    placeholders = ",".join("?" * len(doc_ids))

    # Clear stale theme links
    cur.execute(
        f"DELETE FROM document_themes WHERE document_id IN ({placeholders})",
        doc_ids,
    )

    # Reset to pending
    cur.execute(
        f"UPDATE documents SET status = 'pending', error_msg = NULL WHERE id IN ({placeholders})",
        doc_ids,
    )

    conn.commit()
    conn.close()
    return len(doc_ids)


def drain_pipeline():
    """Run extraction cycles until pending drops below gate threshold."""
    from extraction_worker import run_extraction_worker

    total_processed = 0
    total_failed = 0
    cycle = 0

    while True:
        pending = count_pending()
        if pending < GATE_THRESHOLD:
            break

        cycle += 1
        to_process = min(MAX_PER_CYCLE, pending)
        logger.info(f"Drain cycle {cycle}: {pending} pending, processing {to_process}")

        result = run_extraction_worker(max_docs=to_process)
        processed = result.get("processed", 0)
        failed = result.get("failed", 0)
        total_processed += processed
        total_failed += failed

        logger.info(f"  Cycle {cycle}: {processed} processed, {failed} failed")

        if processed == 0 and failed == 0:
            logger.info("  No docs processed — pipeline empty")
            break

        time.sleep(CYCLE_PAUSE_SECONDS)

    return total_processed, total_failed


def run_source(source_key, source_name):
    """Process all remaining docs for a source."""
    total_queued = 0
    total_processed = 0
    total_failed = 0
    batch_num = 0

    while True:
        remaining = count_remaining_for_source(source_name)
        if remaining == 0:
            logger.info(f"[{source_name}] All docs processed")
            break

        pending = count_pending()
        if pending > GATE_THRESHOLD:
            logger.info(f"[{source_name}] Draining {pending} pending docs...")
            p, f = drain_pipeline()
            total_processed += p
            total_failed += f

        batch_num += 1
        to_queue = min(BATCH_SIZE, remaining)
        queued = queue_batch(source_name, to_queue)
        total_queued += queued

        if queued == 0:
            logger.info(f"[{source_name}] No more docs to queue")
            break

        logger.info(f"[{source_name}] Batch {batch_num}: queued {queued}, remaining {remaining - queued}")

        # Drain what we just queued
        p, f = drain_pipeline()
        total_processed += p
        total_failed += f

    return total_queued, total_processed, total_failed


def send_summary(total_queued, total_processed, total_failed, elapsed):
    """Send completion summary via Telegram."""
    try:
        from telegram_sender import send_ops_report
        hours = elapsed / 3600
        msg = (
            f"📚 Overnight Reprocessing Complete\n\n"
            f"Queued: {total_queued} docs\n"
            f"Processed: {total_processed} | Failed: {total_failed}\n"
            f"Time: {hours:.1f} hours\n\n"
            f"Sources: ZeroHedge, OilPrice.com, Doomberg\n"
            f"Engine: GLM-5 (zero Anthropic calls)"
        )
        send_ops_report(msg)
        logger.info("Summary sent via Telegram")
    except Exception as e:
        logger.error(f"Failed to send summary: {e}")


def main():
    start = time.time()
    grand_queued = 0
    grand_processed = 0
    grand_failed = 0

    logger.info("=" * 60)
    logger.info("OVERNIGHT REPROCESSING START")
    logger.info(f"Sources: {[s[1] for s in SOURCES]}")
    logger.info(f"Batch size: {BATCH_SIZE} | Gate: {GATE_THRESHOLD} | Per cycle: {MAX_PER_CYCLE}")
    logger.info("=" * 60)

    # Pre-flight: fix any stuck 'processing' docs
    conn = sqlite3.connect(DB_PATH)
    fixed = conn.execute("UPDATE documents SET status = 'pending' WHERE status = 'processing'").rowcount
    conn.commit()
    conn.close()
    if fixed:
        logger.info(f"Fixed {fixed} stuck 'processing' docs")

    for source_key, source_name in SOURCES:
        remaining = count_remaining_for_source(source_name)
        logger.info(f"\n--- {source_name}: {remaining} docs to process ---")

        if remaining == 0:
            logger.info(f"[{source_name}] Nothing to do")
            continue

        q, p, f = run_source(source_key, source_name)
        grand_queued += q
        grand_processed += p
        grand_failed += f
        logger.info(f"[{source_name}] Done: queued={q}, processed={p}, failed={f}")

    elapsed = time.time() - start
    logger.info("=" * 60)
    logger.info(f"OVERNIGHT REPROCESSING COMPLETE")
    logger.info(f"Total: queued={grand_queued}, processed={grand_processed}, failed={grand_failed}")
    logger.info(f"Elapsed: {elapsed/3600:.1f} hours")
    logger.info("=" * 60)

    send_summary(grand_queued, grand_processed, grand_failed, elapsed)


if __name__ == "__main__":
    main()

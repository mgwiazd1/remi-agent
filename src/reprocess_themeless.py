"""
reprocess_themeless.py — Reprocess documents that were ingested without themes.

Resets doc status to 'pending' after clearing stale document_themes rows.
Run in controlled batches to avoid pipeline overload.

Usage:
    python3 reprocess_themeless.py --source zerohedge --batch-size 50
    python3 reprocess_themeless.py --source oilprice --batch-size 50
    python3 reprocess_themeless.py --source doomberg --batch-size 50
    python3 reprocess_themeless.py --source all --batch-size 50
"""

import argparse
import sqlite3
import os
import sys
import logging

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)

DB_PATH = os.getenv("DB_PATH", os.path.expanduser("~/remi-intelligence/remi_intelligence.db"))

SOURCE_MAP = {
    "zerohedge": "ZeroHedge",
    "oilprice": "OilPrice.com",
    "doomberg": "Doomberg",
}

MIN_CONTENT_LENGTH = 500
SINCE_DATE = "2026-03-25"


def find_themeless_docs(source: str, batch_size: int, dry_run: bool = False) -> list[int]:
    """Find docs without themes matching source filter."""
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()

    if source == "all":
        sources = list(SOURCE_MAP.values())
    else:
        sources = [SOURCE_MAP[source]]

    placeholders = ",".join("?" * len(sources))
    query = f"""
        SELECT d.id, d.source_name, d.title, length(d.content_text)
        FROM documents d
        WHERE d.ingested_at > ?
        AND d.source_name IN ({placeholders})
        AND length(d.content_text) >= ?
        AND d.id NOT IN (SELECT DISTINCT document_id FROM document_themes)
        AND d.status NOT IN ('pending', 'processing')
        ORDER BY d.ingested_at DESC
        LIMIT ?
    """
    params = [SINCE_DATE] + sources + [MIN_CONTENT_LENGTH, batch_size]
    rows = cur.execute(query, params).fetchall()
    conn.close()

    if dry_run:
        logger.info(f"DRY RUN: Would reprocess {len(rows)} docs from {sources}")
        for doc_id, src, title, clen in rows[:10]:
            logger.info(f"  [{doc_id}] {src} — {title[:60]} ({clen} chars)")
        if len(rows) > 10:
            logger.info(f"  ... and {len(rows) - 10} more")
        return []

    return [r[0] for r in rows]


def reprocess_docs(doc_ids: list[int]) -> int:
    """Reset docs to pending after clearing stale theme links."""
    if not doc_ids:
        return 0

    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()

    # Clear existing document_themes for these docs (stale/incomplete)
    placeholders = ",".join("?" * len(doc_ids))
    deleted = cur.execute(
        f"DELETE FROM document_themes WHERE document_id IN ({placeholders})",
        doc_ids,
    ).rowcount

    # Also clear second_order_inferences linked to those themes
    # (themes themselves are shared — only delete the junction rows)

    # Reset status to pending
    cur.execute(
        f"UPDATE documents SET status = 'pending', error_msg = NULL WHERE id IN ({placeholders})",
        doc_ids,
    )

    conn.commit()
    conn.close()

    logger.info(f"Requeued {len(doc_ids)} docs (cleared {deleted} stale theme links)")
    return len(doc_ids)


def count_remaining(source: str) -> int:
    """Count remaining themeless docs for a source."""
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()

    if source == "all":
        sources = list(SOURCE_MAP.values())
    else:
        sources = [SOURCE_MAP[source]]

    placeholders = ",".join("?" * len(sources))
    count = cur.execute(f"""
        SELECT COUNT(*)
        FROM documents d
        WHERE d.ingested_at > ?
        AND d.source_name IN ({placeholders})
        AND length(d.content_text) >= ?
        AND d.id NOT IN (SELECT DISTINCT document_id FROM document_themes)
        AND d.status NOT IN ('pending', 'processing')
    """, [SINCE_DATE] + sources + [MIN_CONTENT_LENGTH]).fetchone()[0]

    conn.close()
    return count


def count_pending() -> int:
    """Count docs currently in pending/processing status."""
    conn = sqlite3.connect(DB_PATH)
    count = conn.execute(
        "SELECT COUNT(*) FROM documents WHERE status IN ('pending', 'processing')"
    ).fetchone()[0]
    conn.close()
    return count


def main():
    parser = argparse.ArgumentParser(description="Reprocess themeless documents")
    parser.add_argument("--source", required=True,
                        choices=list(SOURCE_MAP.keys()) + ["all"],
                        help="Source to reprocess")
    parser.add_argument("--batch-size", type=int, default=50,
                        help="Number of docs to requeue per batch")
    parser.add_argument("--dry-run", action="store_true",
                        help="Show what would be reprocessed without doing it")
    args = parser.parse_args()

    if args.dry_run:
        find_themeless_docs(args.source, args.batch_size, dry_run=True)
        return

    remaining = count_remaining(args.source)
    pending = count_pending()
    logger.info(f"Remaining themeless: {remaining} | Currently pending/processing: {pending}")

    if pending > 10:
        logger.warning(f"Pipeline has {pending} pending docs — wait for drain before adding more")
        logger.info("Run again when pending drops below 10")
        return

    doc_ids = find_themeless_docs(args.source, args.batch_size)
    if not doc_ids:
        logger.info("No docs to reprocess")
        return

    reprocessed = reprocess_docs(doc_ids)
    remaining_after = count_remaining(args.source)
    logger.info(f"Batch queued: {reprocessed} | Remaining after: {remaining_after}")


if __name__ == "__main__":
    main()

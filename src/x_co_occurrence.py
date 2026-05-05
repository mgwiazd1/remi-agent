"""
Narrative Co-Occurrence Detector.
Detects when 2+ Tier 1 accounts mention the same theme within a 48-hour window.
"""

import sqlite3
import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path

logger = logging.getLogger("remi.x_co_occurrence")

DB_PATH = Path(__file__).parent.parent / "remi_intelligence.db"

def detect_co_occurrences(db_conn=None, window_hours=48):
    """Detect co-occurrences: 2+ T1 accounts mentioning same theme within window."""
    close_db = False
    if db_conn is None:
        db_conn = sqlite3.connect(str(DB_PATH))
        close_db = True

    cursor = db_conn.cursor()
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=window_hours)).isoformat()

    cursor.execute("""
        SELECT 
            dt.theme_id,
            t.theme_key,
            t.theme_label,
            GROUP_CONCAT(DISTINCT d.source_name) as sources,
            COUNT(DISTINCT d.id) as source_count,
            COUNT(*) as total_mentions
        FROM document_themes dt
        JOIN documents d ON dt.document_id = d.id
        JOIN themes t ON dt.theme_id = t.id
        WHERE d.source_type = 'x_tweet'
          AND d.source_tier = 1
          AND d.ingested_at >= ?
          AND d.status = 'complete'
        GROUP BY dt.theme_id
        HAVING source_count >= 2
        ORDER BY source_count DESC, total_mentions DESC
    """, (cutoff,))

    co_occurrences = []
    for row in cursor.fetchall():
        co_occurrences.append({
            "theme_id": row[0],
            "theme_key": row[1],
            "theme_label": row[2],
            "t1_sources": row[3].split(",") if row[3] else [],
            "t1_count": row[4],
            "total_mentions": row[5],
            "window_hours": window_hours
        })

    if close_db:
        db_conn.close()

    logger.info("Co-occurrence check: %d clusters found (window=%dh)", len(co_occurrences), window_hours)
    
    # Format for display
    if co_occurrences:
        lines = []
        lines.append("=" * 60)
        lines.append("CO-OCCURRENCE DETECTION")
        lines.append("=" * 60)
        for co in co_occurrences:
            lines.append(f"Theme: {co['theme_label']}")
            lines.append(f"  T1 Sources ({co['t1_count']}): {', '.join(co['t1_sources'])}")
            lines.append(f"  Total Mentions: {co['total_mentions']}")
            lines.append(f"  Window: {co['window_hours']}h")
            lines.append("")
        logger.info("\n".join(lines))
    
    return co_occurrences


def push_co_occurrence_to_dashboard(co_occurrences):
    """Push co-occurrences to dashboard signal_feed table."""
    if not co_occurrences:
        return

    try:
        from src.dashboard_push import push_signal
        for co in co_occurrences:
            push_signal(
                signal_type="x_co_occurrence",
                signal_data=co,
                summary=f"{co['theme_label']} — {co['t1_count']} T1 sources"
            )
    except Exception as e:
        logger.warning("Dashboard push failed for co-occurrence: %s", e)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    result = detect_co_occurrences()
    print(f"\nFound {len(result)} co-occurrences")
    for co in result:
        print(f"  - {co['theme_label']}: {co['t1_count']} T1 sources")

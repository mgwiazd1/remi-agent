#!/usr/bin/env python3
"""
One-time migration: Sync document_themes from SQLite → PostgreSQL documents.themes[]

Reads all rows from SQLite document_themes joined with documents (content_hash)
and themes (theme_label), then updates PostgreSQL documents.themes arrays to
include any missing theme_labels.

Usage:
    python scripts/migrate_document_themes_to_pg.py [--dry-run]
"""

import argparse
import os
import sqlite3
import sys
from collections import defaultdict

import psycopg2
from dotenv import load_dotenv

# ── Paths ────────────────────────────────────────────────────────────────
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_DIR = os.path.dirname(SCRIPT_DIR)
SQLITE_PATH = os.path.join(PROJECT_DIR, "remi_intelligence.db")
ENV_PATH = os.path.join(PROJECT_DIR, ".env")


def main():
    parser = argparse.ArgumentParser(description="Migrate document_themes from SQLite to PG")
    parser.add_argument("--dry-run", action="store_true", help="Show what would change without writing")
    args = parser.parse_args()

    load_dotenv(ENV_PATH)
    pg_url = os.environ.get("DASHBOARD_DATABASE_URL")
    if not pg_url:
        print("ERROR: DASHBOARD_DATABASE_URL not found in .env", file=sys.stderr)
        sys.exit(1)

    # ── 1. Read SQLite: build {content_hash: [theme_labels]} ─────────────
    print(f"Reading SQLite: {SQLITE_PATH}")
    sl = sqlite3.connect(SQLITE_PATH)
    sl.row_factory = sqlite3.Row
    sl_cur = sl.cursor()

    sl_cur.execute("""
        SELECT d.content_hash, t.theme_label
        FROM document_themes dt
        JOIN documents d ON dt.document_id = d.id
        JOIN themes t ON dt.theme_id = t.id
        ORDER BY d.content_hash, t.theme_label
    """)
    rows = sl_cur.fetchall()
    sl.close()

    # Group themes by content_hash
    hash_to_labels = defaultdict(list)
    seen = set()
    for row in rows:
        key = (row["content_hash"], row["theme_label"])
        if key not in seen:
            seen.add(key)
            hash_to_labels[row["content_hash"]].append(row["theme_label"])

    print(f"  SQLite document_themes rows: {len(rows)}")
    print(f"  Unique (content_hash, theme_label) pairs: {len(seen)}")
    print(f"  Unique documents with themes: {len(hash_to_labels)}")

    # ── 2. Connect to PostgreSQL ─────────────────────────────────────────
    print(f"\nConnecting to PostgreSQL...")
    pg = psycopg2.connect(pg_url)
    pg_cur = pg.cursor()

    # Load existing PG themes per document
    pg_cur.execute("""
        SELECT content_hash, themes
        FROM documents
        WHERE content_hash = ANY(%s)
    """, (list(hash_to_labels.keys()),))

    pg_rows = pg_cur.fetchall()
    print(f"  Matching documents in PG: {len(pg_rows)}")

    # ── 3. Compute updates ───────────────────────────────────────────────
    updates = []  # (content_hash, new_themes_list)
    themes_added_total = 0

    for content_hash, existing_themes in pg_rows:
        existing_set = set(existing_themes) if existing_themes else set()
        sqlite_labels = hash_to_labels.get(content_hash, [])
        new_labels = [l for l in sqlite_labels if l not in existing_set]

        if new_labels:
            merged = list(existing_themes or []) + new_labels
            updates.append((content_hash, merged))
            themes_added_total += len(new_labels)

    print(f"  Documents needing update: {len(updates)}")
    print(f"  New theme labels to add: {themes_added_total}")

    # Also count documents in SQLite with themes but NOT in PG
    pg_hash_set = {r[0] for r in pg_rows}
    missing_in_pg = set(hash_to_labels.keys()) - pg_hash_set
    print(f"  Documents in SQLite but missing from PG: {len(missing_in_pg)} (skipped)")

    if not updates:
        print("\nNo updates needed. All PG documents already have their themes.")
        pg.close()
        return

    # ── 4. Execute updates ───────────────────────────────────────────────
    if args.dry_run:
        print(f"\n[DRY RUN] Would update {len(updates)} documents. Sample:")
        for content_hash, new_themes in updates[:5]:
            short_hash = content_hash[:16] + "..."
            print(f"  {short_hash} → {new_themes}")
        pg.close()
        return

    print(f"\nApplying {len(updates)} updates...")
    for content_hash, new_themes in updates:
        pg_cur.execute("""
            UPDATE documents
            SET themes = %s
            WHERE content_hash = %s
        """, (new_themes, content_hash))

    pg.commit()
    print(f"✅ Committed {len(updates)} document updates ({themes_added_total} theme labels added)")

    # ── 5. Verify ────────────────────────────────────────────────────────
    pg_cur.execute("""
        SELECT COUNT(*)
        FROM documents
        WHERE themes IS NOT NULL AND array_length(themes, 1) > 0
    """)
    docs_with_themes = pg_cur.fetchone()[0]
    print(f"\nPost-migration: {docs_with_themes} documents now have themes")

    pg.close()
    print("Done.")


if __name__ == "__main__":
    main()

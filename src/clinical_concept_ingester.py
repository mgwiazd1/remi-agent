"""
clinical_concept_ingester.py -- Parses book chapter extractions into clinical_concepts table.
Works with the 7-pass extraction format (key_arguments, mental_models, etc.)
"""
import sqlite3
import json
import re
import logging
from pathlib import Path

logger = logging.getLogger(__name__)
DB_PATH = Path(__file__).parent.parent / "remi_intelligence.db"


def _slugify(text):
    return re.sub(r'[^a-z0-9]+', '-', text.lower()).strip('-')[:80]


def ingest_from_book(book_job_id: int, book_title: str):
    """Parse all completed chapters from a clinical book into clinical_concepts."""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    chapters = conn.execute(
        "SELECT chapter_number, chapter_title, extraction_json "
        "FROM book_chapters WHERE book_job_id=? AND status='completed'",
        (book_job_id,)
    ).fetchall()

    count = 0
    for ch in chapters:
        if not ch["extraction_json"]:
            continue
        try:
            extraction = json.loads(ch["extraction_json"])
        except (json.JSONDecodeError, TypeError):
            continue

        chapter_title = ch["chapter_title"] or f"Chapter {ch['chapter_number']}"

        # Extract concepts from key_arguments
        for arg in extraction.get("key_arguments", []):
            argument = arg.get("argument", "")
            if len(argument) < 20:
                continue
            key = _slugify(argument[:60])
            try:
                conn.execute(
                    """INSERT OR IGNORE INTO clinical_concepts
                    (concept_key, concept_label, category, subcategory, board_relevance,
                     core_fact, mechanism, source_book, source_chapter)
                    VALUES (?, ?, 'general', ?, 'medium', ?, '', ?, ?)""",
                    (key, argument[:100], None, argument, book_title, chapter_title))
                count += 1
            except Exception as e:
                logger.debug(f"Skip concept {key}: {e}")

        # Extract from mental_models -- these are high-value clinical concepts
        for model in extraction.get("mental_models", []):
            name = model.get("name", "")
            if not name:
                continue
            key = _slugify(name)
            mechanism = model.get("mechanism", "")
            practical = model.get("practical_use", model.get("when_to_apply", ""))
            limitations = model.get("limitations", "")
            core = f"{mechanism} Application: {practical}" if practical else mechanism
            try:
                conn.execute(
                    """INSERT OR IGNORE INTO clinical_concepts
                    (concept_key, concept_label, category, subcategory, board_relevance,
                     core_fact, mechanism, clinical_pearls, source_book, source_chapter)
                    VALUES (?, ?, 'clinical_framework', ?, 'high', ?, ?, ?, ?, ?)""",
                    (key, name, None, core, mechanism,
                     json.dumps([practical, limitations]) if practical else None,
                     book_title, chapter_title))
                count += 1
            except Exception as e:
                logger.debug(f"Skip model {key}: {e}")

        # Extract from historical_episodes -- useful for understanding clinical history
        for ep in extraction.get("historical_episodes", []):
            name = ep.get("name", "")
            if not name:
                continue
            key = _slugify(name)
            core = f"{name} ({ep.get('date_range', '?')}): {ep.get('resolution', '')}"
            try:
                conn.execute(
                    """INSERT OR IGNORE INTO clinical_concepts
                    (concept_key, concept_label, category, subcategory, board_relevance,
                     core_fact, mechanism, source_book, source_chapter)
                    VALUES (?, ?, 'clinical_history', ?, 'low', ?, ?, ?, ?)""",
                    (key, name, None, core, ep.get("conditions", ""),
                     book_title, chapter_title))
                count += 1
            except Exception as e:
                logger.debug(f"Skip episode {key}: {e}")

        # Extract from key_quotes -- teaching pearls
        for quote in extraction.get("key_quotes", []):
            q = quote.get("quote", "")
            if len(q) < 20:
                continue
            key = _slugify(q[:50])
            try:
                conn.execute(
                    """INSERT OR IGNORE INTO clinical_concepts
                    (concept_key, concept_label, category, subcategory, board_relevance,
                     core_fact, mechanism, source_book, source_chapter)
                    VALUES (?, ?, 'clinical_pearl', ?, 'medium', ?, ?, ?, ?)""",
                    (key, q[:100], None,
                     f'"{q}" -- {quote.get("why_it_matters", "")}',
                     quote.get("context", ""),
                     book_title, chapter_title))
                count += 1
            except Exception as e:
                logger.debug(f"Skip quote {key}: {e}")

    conn.commit()
    conn.close()
    logger.info(f"Ingested {count} clinical concepts from {book_title}")
    return count


if __name__ == "__main__":
    import sys
    logging.basicConfig(level=logging.INFO)
    # Default: ingest the ICU Book (most recent clinical book)
    conn = sqlite3.connect(DB_PATH)
    row = conn.execute(
        "SELECT id, title FROM book_jobs "
        "WHERE title LIKE '%ICU%' ORDER BY id DESC LIMIT 1"
    ).fetchone()
    if row:
        print(f"Ingesting from: {row[1]} (job {row[0]})")
        count = ingest_from_book(row[0], row[1])
        print(f"Ingested {count} concepts")
        for r in conn.execute(
            "SELECT category, COUNT(*) FROM clinical_concepts "
            "GROUP BY category ORDER BY COUNT(*) DESC"
        ).fetchall():
            print(f"  {r[0]}: {r[1]}")
    else:
        print("No ICU Book found in book_jobs")
    conn.close()

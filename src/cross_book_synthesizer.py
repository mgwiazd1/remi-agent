"""
cross_book_synthesizer.py — Monthly cross-book thematic synthesis.
Runs first Sunday of each month. Reads FRAMEWORK and EPISODE notes across
all books in a domain, calls GLM-5 to find conceptual connections, writes
SYNTHESIS_YYYY-MM.md to the domain's vault.

Domain-scoped: 'investing' and 'clinical' run as separate jobs.
Skips synthesis if fewer than 2 completed books exist in the domain.
"""

import os
import re
import json
import logging
import sqlite3
import httpx
from pathlib import Path
from datetime import datetime
from dotenv import load_dotenv

load_dotenv(os.path.expanduser("~/remi-intelligence/.env"))

logger = logging.getLogger(__name__)

DB_PATH = os.getenv("DB_PATH", os.path.expanduser("~/remi-intelligence/remi_intelligence.db"))

VAULT_PATHS = {
    "investing": "/docker/obsidian/investing/Intelligence/Books",
    "clinical": "/docker/obsidian/MG/Study/Books",
}

NOTIFICATION_FUNCS = {
    "investing": "_notify_investing",
    "clinical": "_notify_clinical",
}

MAX_CONTENT_PER_FILE = 3000
MIN_BOOKS_FOR_SYNTHESIS = 2


def _get_completed_books(domain: str) -> list[dict]:
    """Return completed book titles for a domain from book_jobs."""
    conn = sqlite3.connect(DB_PATH)
    rows = conn.execute(
        "SELECT DISTINCT title, filename FROM book_jobs WHERE status = 'completed' AND domain = ?",
        (domain,),
    ).fetchall()
    conn.close()
    return [{"title": r[0], "filename": r[1]} for r in rows]


def _slug_from_filename(filename: str) -> str:
    """Derive the vault slug from a book filename (matches book_ingestor logic)."""
    # Strip extension, lowercase, replace non-alnum with hyphens
    name = os.path.splitext(filename)[0]
    slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    return slug


def collect_book_content(domain: str) -> dict[str, list[dict]]:
    """Gather FRAMEWORK and EPISODE files for a domain, grouped by book.

    Uses frontmatter `source` or `tags` to map each file back to its parent book.
    """
    books_dir = Path(VAULT_PATHS[domain])
    if not books_dir.exists():
        logger.warning(f"Books directory not found: {books_dir}")
        return {}

    content_by_book: dict[str, list[dict]] = {}

    for pattern in ("FRAMEWORK_*.md", "EPISODE_*.md"):
        for md in books_dir.glob(pattern):
            raw = md.read_text(errors="replace")

            # Extract book source from frontmatter
            source_match = re.search(r'^source:\s*[""]?(.+?)[""]?\s*$', raw, re.MULTILINE)
            tags_match = re.search(r'^tags:\s*\[(.+?)\]', raw, re.MULTILINE)

            book_label = None
            if source_match:
                book_label = source_match.group(1).strip().strip('"')
            elif tags_match:
                # tags: [framework, book-slug-here] — second element is the book slug
                tags = [t.strip() for t in tags_match.group(1).split(",")]
                if len(tags) >= 2:
                    book_label = tags[1]

            if not book_label:
                book_label = "uncategorized"

            content_by_book.setdefault(book_label, []).append({
                "type": "framework" if "FRAMEWORK" in md.stem else "episode",
                "name": md.stem,
                "content": raw[:MAX_CONTENT_PER_FILE],
            })

    return content_by_book


def _call_llm(prompt: str, model: str = "glm-5") -> str:
    """Call GLM API for synthesis. Uses same pattern as book_ingestor."""
    api_key = os.environ.get("GLM_API_KEY", "")
    base_url = os.environ.get("GLM_BASE_URL", "https://api.z.ai/api/coding/paas/v4")

    if not api_key:
        logger.error("GLM_API_KEY not set — cannot run synthesis")
        return ""

    try:
        r = httpx.post(
            f"{base_url}/chat/completions",
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            json={
                "model": model,
                "messages": [
                    {"role": "system", "content": prompt},
                    {"role": "user", "content": "Produce the synthesis now."},
                ],
                "max_tokens": 8000,
                "temperature": 0.3,
            },
            timeout=300,
        )

        if r.status_code == 429:
            # Retry with glm-4.7
            logger.warning("429 on glm-5 — retrying with glm-4.7")
            r = httpx.post(
                f"{base_url}/chat/completions",
                headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
                json={
                    "model": "glm-4.7",
                    "messages": [
                        {"role": "system", "content": prompt},
                        {"role": "user", "content": "Produce the synthesis now."},
                    ],
                    "max_tokens": 8000,
                    "temperature": 0.3,
                },
                timeout=300,
            )

        if r.status_code != 200:
            logger.error(f"GLM call failed: {r.status_code} {r.text[:300]}")
            return ""

        return r.json()["choices"][0]["message"]["content"]

    except Exception as e:
        logger.error(f"GLM synthesis call failed: {e}")
        return ""


def _build_synthesis_prompt(content_by_book: dict, domain: str) -> str:
    """Build the synthesis prompt with all book content injected."""
    n_books = len(content_by_book)
    domain_label = "investing/macro finance" if domain == "investing" else "clinical medicine"

    book_sections = []
    for book_label, items in content_by_book.items():
        frameworks = [i for i in items if i["type"] == "framework"]
        episodes = [i for i in items if i["type"] == "episode"]
        section = f"\n## Book: {book_label}\n{len(frameworks)} frameworks, {len(episodes)} episodes\n"

        for fw in frameworks[:8]:  # cap per book
            section += f"\n### {fw['name']}\n{fw['content']}\n"

        for ep in episodes[:5]:  # cap per book
            section += f"\n### {ep['name']}\n{ep['content']}\n"

        book_sections.append(section)

    all_content = "\n---\n".join(book_sections)

    return f"""You are a research analyst performing cross-book synthesis for {domain_label}.

Below are frameworks and historical episodes extracted from {n_books} different books.

Your task: Find genuine conceptual connections BETWEEN books — not within a single book.
Look for:
1. Overlapping principles expressed differently (e.g., one book's "margin of safety" vs another's "beautiful deleveraging")
2. Historical episodes in one book that illustrate frameworks from another
3. Tensions or contradictions between books on the same topic
4. Practical implications: how combining insights from multiple books changes the investment or clinical decision

Output format:
- Start with a 2-3 sentence executive summary of the strongest cross-book connection
- Then list 3-5 cross-book insights, each with: the books involved, the connection, and practical relevance
- End with a "Watches" section: what to monitor that would validate or invalidate these connections

DO NOT just summarize each book separately. The value is in the CROSS-BOOK connections.

BOOK CONTENT:
{all_content}
"""


def run_synthesis(domain: str) -> str | None:
    """Run cross-book synthesis for a domain. Returns output path or None."""
    # Check we have enough books
    books = _get_completed_books(domain)
    if len(books) < MIN_BOOKS_FOR_SYNTHESIS:
        logger.info(f"Skipping {domain} synthesis: only {len(books)} completed books (need {MIN_BOOKS_FOR_SYNTHESIS})")
        return None

    logger.info(f"Starting {domain} synthesis across {len(books)} books: {[b['title'] for b in books]}")

    # Collect content
    content_by_book = collect_book_content(domain)
    if len(content_by_book) < MIN_BOOKS_FOR_SYNTHESIS:
        logger.warning(f"Only {len(content_by_book)} books with vault content found — skipping")
        return None

    # Build prompt and call LLM
    prompt = _build_synthesis_prompt(content_by_book, domain)
    logger.info(f"Synthesis prompt built: {len(prompt)} chars across {len(content_by_book)} books")

    result = _call_llm(prompt)
    if not result:
        logger.error("LLM synthesis returned empty — aborting")
        return None

    # Write output
    now = datetime.now()
    output_dir = Path(VAULT_PATHS[domain]) / "Synthesis"
    output_dir.mkdir(parents=True, exist_ok=True)

    filename = f"SYNTHESIS_{now.strftime('%Y-%m')}.md"
    output_path = output_dir / filename

    frontmatter = f"""---
type: synthesis
domain: {domain}
date: {now.strftime('%Y-%m-%d')}
books: {[b['title'] for b in books]}
---

"""
    output_path.write_text(frontmatter + result, encoding="utf-8")
    logger.info(f"Synthesis written: {output_path} ({len(result)} chars)")
    return str(output_path)


def job_investing_synthesis():
    """Scheduler entry point for investing synthesis."""
    try:
        path = run_synthesis("investing")
        if path:
            from telegram_sender import send_investing_alert
            send_investing_alert(
                f"📚 Monthly Cross-Book Synthesis (Investing)\n"
                f"New synthesis generated — {datetime.now().strftime('%B %Y')}\n"
                f"Full report → intel.gwizcloud.com"
            )
    except Exception as e:
        logger.error(f"Investing synthesis job failed: {e}")


def job_clinical_synthesis():
    """Scheduler entry point for clinical synthesis."""
    try:
        path = run_synthesis("clinical")
        if path:
            from telegram_sender import send_ops_report
            send_ops_report(
                f"📚 Monthly Cross-Book Synthesis (Clinical)\n"
                f"New synthesis generated — {datetime.now().strftime('%B %Y')}\n"
            )
    except Exception as e:
        logger.error(f"Clinical synthesis job failed: {e}")

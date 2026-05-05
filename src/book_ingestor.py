"""
book_ingestor.py — Book ingestion pipeline.
Detects book PDFs, splits into chapters, runs per-chapter extraction,
cross-chapter synthesis, and writes structured notes to Obsidian vault.
"""
import sqlite3
import json
import os
import re
import logging
import shutil
import time
from datetime import datetime, timezone
from pathlib import Path

import fitz  # PyMuPDF
from ebooklib import epub, ITEM_DOCUMENT
from bs4 import BeautifulSoup

from dotenv import load_dotenv
from clinical_concept_ingester import ingest_from_book
load_dotenv(Path(__file__).parent.parent / ".env")

logger = logging.getLogger(__name__)


class DRMError(Exception):
    """Raised when an EPUB is DRM-protected and cannot be parsed."""


DB_PATH = Path(__file__).parent.parent / "remi_intelligence.db"
CONFIG_PATH = Path(__file__).parent.parent / "config" / "book_config.json"
WATCH_DIR = Path(__file__).parent.parent / "watch" / "books" / "incoming"
PROCESSED_DIR = Path(__file__).parent.parent / "watch" / "books" / "processed"

# Domain-specific vault paths
VAULT_PATHS = {
    "clinical": {
        "vault_base": "/docker/obsidian/MG/Study/Books",
        "history_base": None,  # No history dual-write for clinical
    },
    "investing": {
        "vault_base": "/docker/obsidian/investing/Intelligence/Books",
        "history_base": "/docker/obsidian/investing/Intelligence/History",
    },
}

# Local Consuela (Gemma 26B-A4B) — free, no rate limits
CONSUELA_URL = "http://127.0.0.1:8080/v1/chat/completions"
CONSUELA_MODEL = "gemma"
CONSUELA_TIMEOUT = 300  # 5 min — Consuela is slow (5-10 tok/s) but thorough

CLINICAL_KEYWORDS = [
    "icu", "critical care", "pulmonary", "respiratory", "ventilat", "mechanical ventilation",
    "medicine", "clinical", "pathophysiology", "pharmacology", "diagnosis", "treatment",
    "surgery", "anatomy", "physiology", "board review", "mksap", "uworld",
    "chest", "cardiology", "nephrology", "infectious", "hematology", "oncology",
    "emergency", "anesthesia", "radiology", "pediatric", "obstetric", "neurology",
    "nursing", "patient", "hospital", "intensive care",
]


def detect_domain(title: str, toc_entries: list = None) -> str:
    """Classify a book as 'clinical' or 'investing' based on title and TOC."""
    toc_text = " ".join(str(e) for e in (toc_entries or []))
    combined = (title + " " + toc_text).lower()
    clinical_hits = sum(1 for kw in CLINICAL_KEYWORDS if kw in combined)
    if clinical_hits >= 2:
        return "clinical"
    return "investing"


def _ensure_domain_column(conn):
    """Add domain column to book_jobs if it doesn't exist."""
    cols = [r[1] for r in conn.execute("PRAGMA table_info(book_jobs)").fetchall()]
    if "domain" not in cols:
        conn.execute("ALTER TABLE book_jobs ADD COLUMN domain TEXT DEFAULT 'investing'")
        conn.commit()
        logger.info("Added 'domain' column to book_jobs")
    if "format" not in cols:
        conn.execute("ALTER TABLE book_jobs ADD COLUMN format TEXT")
        conn.commit()
        logger.info("Added 'format' column to book_jobs")


def _load_config():
    with open(CONFIG_PATH) as f:
        return json.load(f)


def _conn():
    c = sqlite3.connect(DB_PATH)
    c.row_factory = sqlite3.Row
    return c


def detect_new_books():
    """Scan watch/books/incoming/ for new PDFs and EPUBs."""
    if not WATCH_DIR.exists():
        return []
    conn = _conn()
    existing = {r[0] for r in conn.execute("SELECT filename FROM book_jobs").fetchall()}
    conn.close()
    new_books = []
    for f in WATCH_DIR.glob("*"):
        if f.suffix.lower() in (".pdf", ".epub") and f.name not in existing:
            new_books.append(f)
    return new_books


def is_book(pdf_path: Path, config: dict) -> dict:
    """Check if a PDF is a book (vs article/report). Returns metadata."""
    doc = fitz.open(str(pdf_path))
    page_count = len(doc)
    min_pages = config.get("detection", {}).get("min_pages_for_book", 80)
    # Check TOC
    toc = doc.get_toc()
    has_toc = len(toc) > 3
    # Try to extract title/author from metadata
    meta = doc.metadata or {}
    title = meta.get("title") or pdf_path.stem.replace("-", " ").replace("_", " ")
    author = meta.get("author") or "Unknown"
    doc.close()
    return {
        "is_book": page_count >= min_pages or (has_toc and page_count > 40),
        "page_count": page_count,
        "has_toc": has_toc,
        "toc_entries": len(toc),
        "title": title,
        "author": author,
    }


def split_chapters(pdf_path: Path, config: dict) -> list:
    """
    Split a book PDF into chapters. Uses TOC if available, falls back
    to heading pattern detection.
    Returns list of dicts: {chapter_number, title, page_start, page_end, text}
    """
    doc = fitz.open(str(pdf_path))
    toc = doc.get_toc()
    chapters = []

    if len(toc) > 3:
        # Use TOC — entries are [level, title, page_number]
        # Prefer level 2 entries for proper chapter granularity
        top_level = [e for e in toc if e[0] == 2]
        if len(top_level) < 5:
            # Fall back to level 1 if L2 doesn't give enough granularity
            top_level = [e for e in toc if e[0] == 1]
            if len(top_level) < 3:
                top_level = [e for e in toc if e[0] <= 2]
        for i, entry in enumerate(top_level):
            start_page = entry[2] - 1  # 0-indexed
            end_page = (top_level[i + 1][2] - 2) if i + 1 < len(top_level) else len(doc) - 1
            text = ""
            for pg in range(max(0, start_page), min(end_page + 1, len(doc))):
                text += doc[pg].get_text() + "\n"
            chapters.append({
                "chapter_number": i + 1,
                "title": entry[1].strip(),
                "page_start": start_page + 1,
                "page_end": end_page + 1,
                "text": text.strip(),
                "word_count": len(text.split()),
            })
    else:
        # Fallback: scan for chapter heading patterns
        patterns = config.get("detection", {}).get("chapter_heading_patterns", [])
        compiled = [re.compile(p, re.IGNORECASE | re.MULTILINE) for p in patterns]
        # Extract all text first, then split
        all_pages = []
        for pg in range(len(doc)):
            all_pages.append({"page": pg, "text": doc[pg].get_text()})
        # Find chapter boundaries
        boundaries = []
        for pg_data in all_pages:
            for line in pg_data["text"].split("\n"):
                line_clean = line.strip()
                if len(line_clean) < 5 or len(line_clean) > 100:
                    continue
                for pat in compiled:
                    if pat.match(line_clean):
                        boundaries.append({"page": pg_data["page"], "title": line_clean})
                        break
        # Build chapters from boundaries
        if len(boundaries) < 3:
            # Can't detect chapters — treat entire book as one chunk
            full_text = "\n".join(pg["text"] for pg in all_pages)
            chapters.append({
                "chapter_number": 1, "title": "Full Text",
                "page_start": 1, "page_end": len(doc),
                "text": full_text, "word_count": len(full_text.split()),
            })
        else:
            for i, b in enumerate(boundaries):
                start = b["page"]
                end = (boundaries[i + 1]["page"] - 1) if i + 1 < len(boundaries) else len(doc) - 1
                text = ""
                for pg in range(start, min(end + 1, len(doc))):
                    text += all_pages[pg]["text"] + "\n"
                chapters.append({
                    "chapter_number": i + 1, "title": b["title"],
                    "page_start": start + 1, "page_end": end + 1,
                    "text": text.strip(), "word_count": len(text.split()),
                })

    doc.close()
    return chapters


def _get_epub_metadata(book, field: str) -> str:
    """Safely extract Dublin Core metadata from EPUB."""
    try:
        values = book.get_metadata("DC", field)
        if values:
            return values[0][0]  # ebooklib returns list of (value, attrs) tuples
    except Exception:
        pass
    return "Unknown"


def _split_by_headings(html_content: bytes) -> list:
    """
    Split a single HTML blob into chapters based on h1/h2 tags.
    Used as fallback when an EPUB has the entire book in one XHTML file.
    Returns list of {chapter_number, title, page_start, page_end, text, word_count}.
    """
    soup = BeautifulSoup(html_content, "html.parser")
    headings = soup.find_all(["h1", "h2"])
    if not headings:
        return []

    chapters = []
    for i, heading in enumerate(headings):
        title = heading.get_text(strip=True)
        # Collect all siblings until the next heading of same or higher level
        parts = []
        for sibling in heading.next_siblings:
            if sibling.name in ("h1", "h2"):
                break
            if hasattr(sibling, "get_text"):
                text = sibling.get_text(strip=True)
                if text:
                    parts.append(text)
        text = "\n".join(parts)
        if len(text.strip()) < 100:
            continue  # skip empty sections
        chapters.append({
            "chapter_number": len(chapters) + 1,
            "title": title,
            "page_start": 0,
            "page_end": 0,
            "text": text,
            "word_count": len(text.split()),
        })

    return chapters


def _extract_epub_chapters(filepath: Path) -> tuple:
    """
    Extract chapters from an EPUB file.
    Returns (chapters, metadata) where chapters is a list of dicts matching
    the same shape as split_chapters() output:
      {chapter_number, title, page_start, page_end, text, word_count}
    and metadata is {title, author, language}.

    Fallback: if < 3 chapters found, tries heading-based splitting on the
    largest document item (handles single-file EPUBs).
    """
    try:
        book = epub.read_epub(str(filepath))
    except Exception as e:
        err_msg = str(e).lower()
        if "drm" in err_msg or "encrypt" in err_msg or "obfuscation" in err_msg:
            raise DRMError(f"DRM-protected EPUB: {filepath.name}")
        # ebooklib can also raise KeyError or AttributeError on corrupt files
        raise RuntimeError(f"Failed to read EPUB {filepath.name}: {e}")
    chapters = []
    order = 0

    for item in book.get_items_of_type(ITEM_DOCUMENT):
        # Parse HTML content to plain text
        soup = BeautifulSoup(item.get_content(), "html.parser")
        text = soup.get_text(separator="\n", strip=True)

        # Skip near-empty items (cover pages, copyright, TOC stubs, nav)
        if len(text.strip()) < 500:
            continue

        # Try to extract chapter title from first heading
        title = None
        for tag in ["h1", "h2", "h3"]:
            heading = soup.find(tag)
            if heading:
                title = heading.get_text(strip=True)
                break

        if not title:
            # Fallback: use item ID or filename
            title = item.get_name().replace(".xhtml", "").replace(".html", "")
            # Clean up filenames like "ch01" or "chapter_1"
            title = title.replace("_", " ").replace("-", " ").title()

        order += 1
        chapters.append({
            "chapter_number": order,
            "title": title,
            "page_start": 0,   # N/A for EPUB
            "page_end": 0,
            "text": text,
            "word_count": len(text.split()),
        })

    # Fallback: single-file EPUB — split on headings inside the largest item
    if len(chapters) < 3:
        doc_items = list(book.get_items_of_type(ITEM_DOCUMENT))
        if doc_items:
            largest = max(doc_items, key=lambda x: len(x.get_content()))
            heading_chapters = _split_by_headings(largest.get_content())
            if len(heading_chapters) > len(chapters):
                logger.info(f"EPUB fallback: heading split gave {len(heading_chapters)} chapters vs {len(chapters)} spine items")
                chapters = heading_chapters

    # Extract metadata for book_jobs record
    metadata = {
        "title": _get_epub_metadata(book, "title") or filepath.stem.replace("-", " ").replace("_", " "),
        "author": _get_epub_metadata(book, "creator") or "Unknown",
        "language": _get_epub_metadata(book, "language") or "en",
    }

    logger.info(f"EPUB split: {len(chapters)} content chapters from {filepath.name}")
    return chapters, metadata


_last_model_used = ""  # Set by _call_llm for caller logging


def _call_llm(prompt: str, content: str, model: str = "glm-5.1", max_output_tokens: int = 4000) -> dict:
    """
    Call GLM API for extraction. Returns parsed JSON or empty dict on failure.
    Per-call model fallback: always tries configured model first, falls back to
    glm-4.7 on 429 for that specific call only (NOT a sticky switch).
    Sets _last_model_used for caller logging.
    """
    import httpx

    FALLBACK_MODEL = "glm-4.7"
    global _last_model_used

    # Safety net truncation (chapter-level cap at 20K should prevent this)
    max_chars = 25000
    if len(content) > max_chars:
        content = content[:max_chars] + "\n\n[TRUNCATED — remaining text omitted]"

    api_key = os.environ.get("GLM_API_KEY", "")
    base_url = os.environ.get("GLM_BASE_URL", "https://api.z.ai/api/coding/paas/v4")

    if not api_key:
        logger.error("GLM_API_KEY not set — cannot call LLM")
        return {}

    def _do_request(m: str) -> httpx.Response:
        messages = [
            {"role": "system", "content": prompt},
            {"role": "user", "content": content},
        ]
        # Try Consuela first (local laborer)
        try:
            local_r = httpx.post("http://127.0.0.1:8080/v1/chat/completions",
                json={"model": "gemma", "messages": messages,
                      "max_tokens": max_output_tokens, "temperature": 0.2},
                timeout=300.0)
            if local_r.status_code == 200:
                return local_r
            logger.info(f"Consuela returned {local_r.status_code}, falling back to GLM")
        except Exception:
            logger.info("Consuela unavailable, falling back to GLM")
        # Existing GLM call as fallback
        return httpx.post(
            f"{base_url}/chat/completions",
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            json={"model": m, "messages": messages,
                  "max_tokens": max_output_tokens, "temperature": 0.2},
            timeout=300,
        )

    try:
        # --- Always try primary model first ---
        r = _do_request(model)
        model_used = model

        # --- Per-call 429 fallback: immediately retry with glm-4.7 ---
        if r.status_code == 429:
            logger.warning(f"429 on {model} — falling back to {FALLBACK_MODEL} for this call")
            r = _do_request(FALLBACK_MODEL)
            model_used = FALLBACK_MODEL

        # --- Final status check ---
        if r.status_code == 429:
            logger.error(f"429 persists even on {FALLBACK_MODEL} — giving up this call")
            return {}
        elif r.status_code != 200:
            logger.error(f"GLM call failed ({model_used}): {r.status_code} {r.text[:300]}")
            return {}

        logger.info(f"LLM call succeeded via {model_used}")
        _last_model_used = model_used

        text = r.json()["choices"][0]["message"]["content"]
        text = re.sub(r'^```json\s*', '', text.strip())
        text = re.sub(r'\s*```$', '', text.strip())

        # Extract first complete JSON object (handle trailing text after JSON)
        start = text.find('{')
        if start >= 0:
            depth = 0
            end = -1
            for i in range(start, len(text)):
                if text[i] == '{':
                    depth += 1
                elif text[i] == '}':
                    depth -= 1
                    if depth == 0:
                        end = i
                        break
            if end >= 0:
                text = text[start:end+1]
            else:
                # Truncated — attempt repair
                truncated = text[start:]
                if truncated.count('"') % 2 == 1:
                    truncated += '"'
                for closer, opener in [('}', '{'), (']', '[')]:
                    unclosed = truncated.count(opener) - truncated.count(closer)
                    truncated += closer * max(0, unclosed)
                text = truncated

        return json.loads(text)

    except json.JSONDecodeError as e:
        logger.warning(f"JSON parse failed ({model_used}), retrying with strict prompt: {e}")
        # Retry with a more explicit prompt demanding valid JSON (same model that succeeded)
        try:
            retry_prompt = (
                "Return ONLY valid JSON. No commentary, no markdown, no text before or after. "
                "Ensure all strings are properly escaped, all keys quoted, all brackets closed. "
                + prompt
            )
            r = httpx.post(
                f"{base_url}/chat/completions",
                headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
                json={
                    "model": model_used,
                    "messages": [
                        {"role": "system", "content": retry_prompt},
                        {"role": "user", "content": content[:30000]},  # truncate harder for retry
                    ],
                    "max_tokens": max_output_tokens,
                    "temperature": 0.1,
                },
                timeout=300,
            )
            if r.status_code == 200:
                text2 = r.json()["choices"][0]["message"]["content"]
                text2 = re.sub(r'^```json\s*', '', text2.strip())
                text2 = re.sub(r'\s*```$', '', text2.strip())
                start2 = text2.find('{')
                if start2 >= 0:
                    depth2, end2 = 0, -1
                    for i2 in range(start2, len(text2)):
                        if text2[i2] == '{': depth2 += 1
                        elif text2[i2] == '}':
                            depth2 -= 1
                            if depth2 == 0: end2 = i2; break
                    if end2 >= 0:
                        text2 = text2[start2:end2+1]
                return json.loads(text2)
        except Exception as retry_e:
            logger.error(f"Retry also failed: {retry_e}")
        return {}
    except Exception as e:
        logger.error(f"GLM call error ({model_used}): {e}")
        return {}


def _truncate_at_paragraph(text: str, max_chars: int = 20000) -> str:
    """Truncate text to max_chars at the last paragraph boundary (double newline)."""
    if len(text) <= max_chars:
        return text
    # Find last paragraph break before the limit
    cut = text.rfind("\n\n", 0, max_chars)
    if cut < max_chars * 0.5:
        # No good paragraph break — fall back to last single newline
        cut = text.rfind("\n", 0, max_chars)
    if cut < max_chars * 0.5:
        # Last resort: hard cut at max_chars
        cut = max_chars
    return text[:cut] + "\n\n[TRUNCATED — chapter continues beyond this point]"


def _build_pass_prompts() -> list[dict]:
    """Return the 7 focused extraction pass definitions (name, system_prompt)."""
    return [
        {
            "name": "KEY_ARGUMENTS",
            "prompt": (
                "You are a macro investment research analyst. What are the key arguments, claims, "
                "and core ideas in this chapter? For each, state the argument clearly in 1-3 sentences "
                "and note whether it's backed by data/evidence or is the author's opinion.\n\n"
                'Return JSON: {"key_arguments": [{"argument": "...", "evidence_type": "empirical|theoretical|anecdotal"}]}\n'
                "Return ONLY valid JSON. No other text."
            ),
            "key": "key_arguments",
            "max_tokens": 4000,
        },
        {
            "name": "HISTORICAL_EPISODES",
            "prompt": (
                "You are a macro investment research analyst. What historical events, crises, market "
                "episodes, or case studies are described in this chapter? For each, extract: event name, "
                "date range, what conditions preceded it, what triggered it, how it resolved, which "
                "assets/sectors benefited or suffered, and what early warning signals were visible.\n\n"
                'Return JSON: {"historical_episodes": [{"name": "...", "date_range": "...", '
                '"conditions": "...", "trigger": "...", "resolution": "...", '
                '"assets_affected": "...", "early_signals": "..."}]}\n'
                "Return ONLY valid JSON. No other text."
            ),
            "key": "historical_episodes",
            "max_tokens": 4000,
        },
        {
            "name": "MENTAL_MODELS",
            "prompt": (
                "You are a macro investment research analyst. What mental models, analytical frameworks, "
                "decision-making tools, or investment methodologies are introduced or explained in this "
                "chapter? For each: name it, explain the core mechanism, when to apply it, when it fails, "
                "and how to use it for actual investment decisions.\n\n"
                'Return JSON: {"mental_models": [{"name": "...", "mechanism": "...", '
                '"when_to_apply": "...", "limitations": "...", "practical_use": "..."}]}\n'
                "Return ONLY valid JSON. No other text."
            ),
            "key": "mental_models",
            "max_tokens": 4000,
        },
        {
            "name": "TICKERS_AND_ASSETS",
            "prompt": (
                "You are a macro investment research analyst. What specific companies, tickers, asset "
                "classes, sectors, or commodities are discussed in this chapter? Many of these may be "
                "historical examples where the company no longer exists or the ticker has changed. "
                "For each, extract:\n"
                "- The ticker/company as mentioned in the book\n"
                "- Whether it's still active or historical\n"
                "- The CONCEPT the author is illustrating by using this example — why did the author "
                "choose this example? What investing principle does it demonstrate?\n"
                "- The archetype: what TYPE of opportunity does this represent that a modern investor "
                "should watch for?\n"
                "- A modern equivalent: what current company or sector would fit the same pattern today?\n\n"
                'Return JSON: {"tickers_and_assets": [{"name": "...", "status": "active|historical|acquired", '
                '"concept_illustrated": "...", "archetype": "...", "modern_equivalent": "..."}]}\n'
                "Return ONLY valid JSON. No other text."
            ),
            "key": "tickers_and_assets",
            "max_tokens": 4000,
        },
        {
            "name": "KEY_QUOTES",
            "prompt": (
                "You are a macro investment research analyst. Extract the 3-5 most important, memorable, "
                "or instructive direct quotes from this chapter. Include enough context to understand "
                "why the quote matters.\n\n"
                'Return JSON: {"key_quotes": [{"quote": "...", "context": "...", "why_it_matters": "..."}]}\n'
                "Return ONLY valid JSON. No other text."
            ),
            "key": "key_quotes",
            "max_tokens": 4000,
        },
        {
            "name": "CONTRARIAN_INSIGHTS",
            "prompt": (
                "You are a macro investment research analyst. What are the non-obvious, contrarian, or "
                "counterintuitive insights in this chapter? What would surprise most readers? What goes "
                "against conventional wisdom? What hidden connections does the author make?\n\n"
                'Return JSON: {"contrarian_insights": [{"insight": "...", "why_contrarian": "...", '
                '"implication": "..."}]}\n'
                "Return ONLY valid JSON. No other text."
            ),
            "key": "contrarian_insights",
            "max_tokens": 4000,
        },
        {
            "name": "CROSS_REFERENCES",
            "prompt": (
                "You are a macro investment research analyst. How does this chapter connect to broader "
                "themes? What concepts from this chapter would link to other investing/macro topics like "
                "monetary policy, market cycles, behavioral finance, risk management, portfolio "
                "construction, or valuation?\n\n"
                'Return JSON: {"cross_references": [{"concept": "...", "connects_to": "...", '
                '"relationship": "..."}]}\n'
                "Return ONLY valid JSON. No other text."
            ),
            "key": "cross_references",
            "max_tokens": 4000,
        },
    ]


def extract_chapter(chapter: dict, config: dict, book_title: str = "", book_author: str = "") -> dict:
    """
    Run per-chapter extraction via 7 focused LLM passes.
    Each pass asks ONE question and returns ONE flat JSON array.
    Returns merged dict from all successful passes + status metadata.
    """
    model = config["_meta"]["models"]["chapter_extraction"]
    header = f"BOOK: {book_title} by {book_author} | CHAPTER {chapter['chapter_number']}: {chapter['title']}\n"
    header += f"Pages {chapter['page_start']}-{chapter['page_end']} ({chapter['word_count']} words)\n\n"
    content = _truncate_at_paragraph(header + chapter["text"], max_chars=20000)

    passes = _build_pass_prompts()
    merged = {}
    pass_results = []  # list of (pass_name, status, model)

    for i, p in enumerate(passes):
        result = _call_llm(p["prompt"], content, model=model, max_output_tokens=p["max_tokens"])
        status = "ok" if result and p["key"] in result else "empty"
        model_tag = _last_model_used or model

        if status == "ok":
            merged.update(result)

        pass_results.append((p["name"], status, model_tag))
        logger.info(f"Chapter {chapter['chapter_number']} pass {p['name']}: {status} via {model_tag}")

        # Sleep between passes (not after the last one)
        if i < len(passes) - 1:
            time.sleep(5)

    # Determine overall status
    successful = sum(1 for _, s, _ in pass_results if s == "ok")
    if successful >= 4:
        overall_status = "completed"
    elif successful > 0:
        overall_status = "partial"
    else:
        overall_status = "failed"

    merged["_meta"] = {
        "passes_successful": successful,
        "passes_total": 7,
        "status": overall_status,
        "pass_log": [{"pass": n, "status": s, "model": m} for n, s, m in pass_results],
    }

    return merged


def synthesize_book(book_title: str, book_author: str, chapter_extractions: list, config: dict) -> dict:
    """Run cross-chapter synthesis — split into 3 focused calls to avoid truncation."""
    model = config["_meta"]["models"]["cross_chapter_synthesis"]
    extraction_summary = ""
    for i, ext in enumerate(chapter_extractions):
        extraction_summary += f"=== CHAPTER {i+1} ===\n"
        extraction_summary += json.dumps(ext, indent=1)[:3000] + "\n\n"

    # Call 1: Book overview
    overview_prompt = (
        "You are a macro investment research analyst. Given chapter-by-chapter extractions from a book, "
        f"produce a BOOK OVERVIEW for '{book_title}' by {book_author}.\n\n"
        "Return JSON: {\"book_overview\": {\"thesis\": \"...\", \"key_takeaways\": [...], \"current_relevance\": \"...\"}}\n"
        "Keep it concise. 200-300 words max. Return ONLY valid JSON."
    )
    overview = _call_llm(overview_prompt, extraction_summary, model=model, max_output_tokens=4000)

    # Rate-limit cooldown between synthesis calls
    logger.info("Synthesis call 1/3 (overview) complete. Sleeping 15s before frameworks...")
    time.sleep(15)

    # Call 2: Frameworks
    fw_prompt = (
        "You are a macro investment research analyst. Given chapter-by-chapter extractions from a book, "
        "identify all MENTAL MODELS or FRAMEWORKS introduced.\n\n"
        "Return JSON: {\"frameworks\": [{\"name\": \"...\", \"core_mechanism\": \"...\", "
        "\"application_conditions\": \"...\", \"limitations\": \"...\", \"current_relevance\": \"...\"}]}\n"
        "Only include genuinely useful frameworks. Return ONLY valid JSON."
    )
    frameworks = _call_llm(fw_prompt, extraction_summary, model=model, max_output_tokens=6000)

    # Rate-limit cooldown before episodes call
    logger.info("Synthesis call 2/3 (frameworks) complete. Sleeping 15s before episodes...")
    time.sleep(15)

    # Call 3: Episodes
    ep_prompt = (
        "You are a macro investment research analyst. Given chapter-by-chapter extractions from a book, "
        "identify all HISTORICAL EPISODES described.\n\n"
        "Return JSON: {\"episodes\": [{\"event_name\": \"...\", \"date_range\": \"...\", "
        "\"preceding_conditions\": \"...\", \"trigger\": \"...\", \"resolution\": \"...\", "
        "\"assets_that_worked\": [...], \"assets_that_failed\": [...], "
        "\"gli_phase_analog\": \"...\", \"early_signals\": \"...\", \"lessons\": \"...\"}]}\n"
        "Only include episodes with investment-relevant lessons. Return ONLY valid JSON."
    )
    episodes = _call_llm(ep_prompt, extraction_summary, model=model, max_output_tokens=6000)

    # Merge results
    result = {}
    if overview:
        result["book_overview"] = overview.get("book_overview", {})
    if frameworks:
        result["frameworks"] = frameworks.get("frameworks", [])
    if episodes:
        result["episodes"] = episodes.get("episodes", [])
    return result if result else {}


def _write_vault_note(filepath: str, content: str):
    """Write a note to the Obsidian vault as proxmox user."""
    path = Path(filepath)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    # Fix ownership for LiveSync
    try:
        import subprocess
        subprocess.run(["chown", "proxmox:proxmox", str(path)], check=False, capture_output=True)
    except Exception:
        pass
    logger.info(f"Wrote vault note: {filepath}")


def write_book_to_vault(synthesis: dict, book_title: str, book_author: str,
                         config: dict = None, vault_base: str = None, history_base: str = None):
    """Write synthesized book intelligence to Obsidian vault."""
    # Accept explicit paths, fall back to config for backward compat
    if vault_base is None and config:
        vault_base = config["output"]["vault_base"]
    elif vault_base is None:
        vault_base = VAULT_PATHS["investing"]["vault_base"]
    if history_base is None and config:
        history_base = config["output"].get("history_base")
    elif history_base is None:
        history_base = VAULT_PATHS["investing"]["history_base"]
    slug = re.sub(r'[^a-z0-9]+', '-', book_title.lower()).strip('-')
    notes_written = []

    # BOOK overview
    overview = synthesis.get("book_overview", {})
    if overview:
        ov_content = f"---\ntags: [book, {slug}]\nauthor: {book_author}\n---\n\n"
        ov_content += f"# {book_title}\n*{book_author}*\n\n"
        if isinstance(overview, dict):
            ov_content += overview.get("thesis", overview.get("core_thesis", "")) + "\n\n"
            takeaways = overview.get("key_takeaways", [])
            if takeaways:
                ov_content += "## Key Takeaways\n"
                for t in takeaways:
                    ov_content += f"- {t}\n"
                ov_content += "\n"
            relevance = overview.get("relevance", overview.get("current_relevance", ""))
            if relevance:
                ov_content += f"## Current Relevance\n{relevance}\n"
        elif isinstance(overview, str):
            ov_content += overview
        fp = f"{vault_base}/BOOK_{slug}.md"
        _write_vault_note(fp, ov_content)
        notes_written.append(fp)

    # FRAMEWORK notes
    for fw in synthesis.get("frameworks", []):
        name = fw.get("name", "unnamed")
        fw_slug = re.sub(r'[^a-z0-9]+', '-', name.lower()).strip('-')
        content = f"---\ntags: [framework, {slug}]\nsource: \"{book_title}\"\n---\n\n"
        content += f"# {name}\n*From: {book_title} ({book_author})*\n\n"
        for field in ["core_mechanism", "application_conditions", "limitations", "current_relevance"]:
            val = fw.get(field, "")
            if val:
                content += f"## {field.replace('_', ' ').title()}\n{val}\n\n"
        fp = f"{vault_base}/FRAMEWORK_{fw_slug}.md"
        _write_vault_note(fp, content)
        notes_written.append(fp)

    # EPISODE notes — write to Books/ (and History/ if history_base is set)
    for ep in synthesis.get("episodes", []):
        name = ep.get("event_name", ep.get("name", "unnamed"))
        ep_slug = re.sub(r'[^a-z0-9]+', '-', name.lower()).strip('-')
        content = f"---\ntags: [episode, historical, {slug}]\nsource: \"{book_title}\"\n---\n\n"
        content += f"# {name}\n*Source: {book_title} ({book_author})*\n\n"
        for field in ["date_range", "preceding_conditions", "trigger", "progression",
                       "resolution", "assets_that_worked", "assets_that_failed",
                       "gli_phase_analog", "early_signals", "lessons"]:
            val = ep.get(field, "")
            if val:
                label = field.replace("_", " ").title()
                if isinstance(val, list):
                    content += f"## {label}\n"
                    for v in val:
                        content += f"- {v}\n"
                    content += "\n"
                else:
                    content += f"## {label}\n{val}\n\n"
        # Write to Books/
        fp = f"{vault_base}/EPISODE_{ep_slug}.md"
        _write_vault_note(fp, content)
        notes_written.append(fp)
        # Also write to History/ for cross-reference (only if history_base is set)
        if history_base:
            fp2 = f"{history_base}/EPISODE_{ep_slug}.md"
            _write_vault_note(fp2, content)

    return notes_written


def feed_knowledge_base(synthesis: dict, chapter_extractions: list, book_title: str):
    """Feed book insights into the ticker knowledge base for the picks engine."""
    conn = _conn()
    conn.row_factory = sqlite3.Row
    count = 0
    now = datetime.now(timezone.utc).isoformat()

    # 1. Mental models / frameworks from synthesis → book_framework signals
    for fw in synthesis.get("frameworks", []):
        name = fw.get("name", "")
        if not name:
            continue
        content = f"Framework: {name} | From: {book_title}"
        mechanism = fw.get("core_mechanism", fw.get("mechanism", ""))
        if mechanism:
            content += f" | {mechanism[:200]}"
        conn.execute("""INSERT INTO ticker_signals
            (ticker, signal_type, source, content, sentiment, conviction_weight, created_at)
            VALUES ('_FRAMEWORK', 'book_framework', ?, ?, 'neutral', 0.3, ?)""",
            (f"Book: {book_title}", content, now))
        count += 1

    # 2. From chapter extractions — ticker concepts with archetypes
    for ext in chapter_extractions:
        if not isinstance(ext, dict):
            continue
        for ticker_info in ext.get("tickers_and_assets", []):
            name = ticker_info.get("name", "")
            if not name or len(name) < 2:
                continue
            concept = ticker_info.get("concept_illustrated", "")
            archetype = ticker_info.get("archetype", "")
            modern = ticker_info.get("modern_equivalent", "")
            content = f"{name}: {concept}"
            if archetype:
                content += f" | Archetype: {archetype}"
            if modern:
                content += f" | Modern: {modern}"
            # Use the modern equivalent as the ticker if available
            ticker_symbol = modern.split(",")[0].strip().split(" ")[0].upper() if modern else name.upper()
            # Only insert if it looks like a ticker (1-5 uppercase chars)
            STOPWORDS = {"THE", "AND", "FOR", "BUT", "NOT", "GDP", "CPI", "FED",
                         "ECB", "IMF", "FROM", "WITH", "THIS", "THAT", "ARE", "WAS"}
            if re.match(r'^[A-Z]{1,5}$', ticker_symbol) and ticker_symbol not in STOPWORDS:
                conn.execute("""INSERT INTO ticker_signals
                    (ticker, signal_type, source, content, sentiment, conviction_weight, raw_data, created_at)
                    VALUES (?, 'book_ticker_concept', ?, ?, 'neutral', 0.4, ?, ?)""",
                    (ticker_symbol, f"Book: {book_title}", content,
                     json.dumps(ticker_info), now))
                count += 1

        # 3. Contrarian insights → book_insight signals
        for ci in ext.get("contrarian_insights", []):
            insight = ci.get("insight", "")
            if len(insight) < 20:
                continue
            content = f"Contrarian: {insight[:300]}"
            implication = ci.get("implication", "")
            if implication:
                content += f" | Implication: {implication[:200]}"
            conn.execute("""INSERT INTO ticker_signals
                (ticker, signal_type, source, content, sentiment, conviction_weight, created_at)
                VALUES ('_INSIGHT', 'book_insight', ?, ?, 'neutral', 0.3, ?)""",
                (f"Book: {book_title}", content, now))
            count += 1

    conn.commit()
    conn.close()
    logger.info(f"Fed {count} book insights into knowledge base")
    return count


def process_book(pdf_path: Path) -> dict:
    """Full pipeline: detect → split → extract → synthesize → write → feed KB."""
    config = _load_config()
    conn = _conn()
    is_epub = pdf_path.suffix.lower() == ".epub"
    logger.info(f"Processing book ({'EPUB' if is_epub else 'PDF'}): {pdf_path.name}")

    # 1. Classification + chapter splitting — branch on format
    if is_epub:
        try:
            chapters, epub_meta = _extract_epub_chapters(pdf_path)
        except DRMError as e:
            logger.warning(str(e))
            return {"status": "failed", "reason": "drm", "error": str(e)}
        except RuntimeError as e:
            logger.error(str(e))
            return {"status": "failed", "reason": "read_error", "error": str(e)}
        title = epub_meta["title"]
        author = epub_meta["author"]
        chapter_count = len(chapters)
        if chapter_count == 0:
            logger.info(f"{pdf_path.name}: EPUB had no extractable chapters. Skipping.")
            return {"status": "skipped", "reason": "no_chapters"}
    else:
        meta = is_book(pdf_path, config)
        if not meta["is_book"]:
            logger.info(f"{pdf_path.name}: Not a book ({meta['page_count']} pages). Skipping.")
            return {"status": "skipped", "reason": "not_a_book", "meta": meta}
        chapters = split_chapters(pdf_path, config)
        title = meta["title"]
        author = meta["author"]
        chapter_count = len(chapters)

    # 2. Ensure domain column exists
    _ensure_domain_column(conn)

    # 3. Create job record
    page_count_for_db = 0 if is_epub else meta.get("page_count", 0)
    file_format = pdf_path.suffix.lower()  # ".pdf" or ".epub"
    conn.execute("""INSERT INTO book_jobs (filename, filepath, title, author, page_count, status, format)
        VALUES (?, ?, ?, ?, ?, 'processing', ?)""",
        (pdf_path.name, str(pdf_path), title, author, page_count_for_db, file_format))
    conn.commit()
    job_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]

    try:
        conn.execute("UPDATE book_jobs SET chapter_count=? WHERE id=?", (chapter_count, job_id))
        conn.commit()
        logger.info(f"Split into {chapter_count} chapters")

        # 4b. Detect domain (clinical vs investing)
        toc_raw = [ch["title"] for ch in chapters]
        domain = detect_domain(title, toc_raw)
        conn.execute("UPDATE book_jobs SET domain=? WHERE id=?", (domain, job_id))
        conn.commit()
        logger.info(f"Domain detected: {domain}")

        # 4c. Resolve vault paths by domain
        paths = VAULT_PATHS.get(domain, VAULT_PATHS["investing"])
        vault_base = paths["vault_base"]
        history_base = paths.get("history_base")

        # 4. Per-chapter extraction (7-pass system)
        chapter_extractions = []
        for ch in chapters:
            conn.execute("""INSERT INTO book_chapters
                (book_job_id, chapter_number, chapter_title, page_start, page_end, word_count, raw_text, status)
                VALUES (?, ?, ?, ?, ?, ?, ?, 'processing')""",
                (job_id, ch["chapter_number"], ch["title"], ch["page_start"],
                 ch["page_end"], ch["word_count"], ch["text"][:1000]))
            conn.commit()
            ch_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]

            logger.info(f"Extracting chapter {ch['chapter_number']}/{len(chapters)}: {ch['title']}")
            extraction = extract_chapter(ch, config, book_title=title, book_author=author)
            # Extract status metadata from the 7-pass system
            meta_info = extraction.pop("_meta", {})
            ch_status = meta_info.get("status", "failed")
            passes_ok = meta_info.get("passes_successful", 0)
            pass_log = meta_info.get("pass_log", [])

            if extraction and ch_status != "failed":
                chapter_extractions.append(extraction)
                # Build a summary error_msg from the pass log
                failed_passes = [p["pass"] for p in pass_log if p["status"] != "ok"]
                err_msg = f"{passes_ok}/7 passes ok" + (f", failed: {','.join(failed_passes)}" if failed_passes else "")
                conn.execute("UPDATE book_chapters SET extraction_json=?, status=?, error_msg=?, completed_at=? WHERE id=?",
                    (json.dumps(extraction), ch_status, err_msg, datetime.now(timezone.utc).isoformat(), ch_id))
            else:
                conn.execute("UPDATE book_chapters SET status='failed', error_msg='0/7 passes succeeded' WHERE id=?",
                    (ch_id,))
            conn.commit()
            logger.info(f"Chapter {ch['chapter_number']} done: {ch_status} ({passes_ok}/7)")
            # Rate-limit throttle between chapters (10s)
            time.sleep(10)

        if not chapter_extractions:
            raise ValueError("All chapter extractions failed")

        # 5. Cross-chapter synthesis
        logger.info(f"Synthesizing {len(chapter_extractions)} chapter extractions...")
        synthesis = synthesize_book(title, author, chapter_extractions, config)
        if not synthesis:
            raise ValueError("Cross-chapter synthesis returned empty")

        # 6. Write to vault
        notes = write_book_to_vault(synthesis, title, author,
                                     vault_base=vault_base, history_base=history_base)
        logger.info(f"Wrote {len(notes)} vault notes")

        # 7. Feed knowledge base (investing only — clinical uses concept table)
        kb_count = 0
        if domain == "investing":
            kb_count = feed_knowledge_base(synthesis, chapter_extractions, title)
        elif domain == "clinical":
            cc_count = ingest_from_book(job_id, title)
            logger.info(f"Ingested {cc_count} clinical concepts")

        # 8. Move PDF to processed/
        dest = PROCESSED_DIR / pdf_path.name
        shutil.move(str(pdf_path), str(dest))

        # Update job
        conn.execute("""UPDATE book_jobs SET status='completed', completed_at=? WHERE id=?""",
            (datetime.now(timezone.utc).isoformat(), job_id))
        conn.commit()

        result = {
            "status": "completed", "title": title, "author": author,
            "domain": domain,
            "chapters": len(chapters), "extractions": len(chapter_extractions),
            "notes_written": len(notes), "kb_signals": kb_count,
        }
        logger.info(f"Book processing complete: {result}")
        return result

    except Exception as e:
        logger.error(f"Book processing failed: {e}")
        conn.execute("UPDATE book_jobs SET status='failed', error_msg=? WHERE id=?",
            (str(e)[:500], job_id))
        conn.commit()
        return {"status": "failed", "error": str(e)}
    finally:
        conn.close()


def job_book_watcher():
    """Scheduler job: scan for new books and process them."""
    new_books = detect_new_books()
    if not new_books:
        return
    logger.info(f"Found {len(new_books)} new book(s) to process")
    for pdf in new_books:
        process_book(pdf)


if __name__ == "__main__":
    import sys
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    if len(sys.argv) > 1:
        # Process a specific book (PDF or EPUB)
        pdf = Path(sys.argv[1])
        if not pdf.exists():
            print(f"File not found: {pdf}")
            sys.exit(1)
        result = process_book(pdf)
        print(json.dumps(result, indent=2))
    else:
        # Run watcher
        job_book_watcher()

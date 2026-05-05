#!/usr/bin/env python3
"""
consuela_overnight.py — Overnight batch job runner
"""
import os, sys, json, time, sqlite3, logging, subprocess, re, traceback, difflib
from pathlib import Path
from dotenv import load_dotenv; load_dotenv()
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from task_router import route_and_call, is_laborer_available, status as router_status

logging.basicConfig(level=logging.INFO, format="%(asctime)s [CONSUELA] %(message)s",
    handlers=[logging.FileHandler(os.path.expanduser("~/remi-intelligence/logs/consuela_overnight.log")), logging.StreamHandler()])
logger = logging.getLogger("consuela.overnight")

DB_PATH = os.path.expanduser("~/remi-intelligence/remi_intelligence.db")
VAULT_PATH = "/docker/obsidian/investing/Intelligence"
CLINICAL_VAULT = "/docker/obsidian/MG"

report = {
    "start_time": None, "end_time": None,
    "youtube_jobs": {"attempted": 0, "completed": 0, "failed": 0, "details": []},
    "icu_reprocess": {"attempted": 0, "completed": 0, "failed": 0, "details": []},
    "vault_hygiene": {"orphan_notes": 0, "broken_links": 0, "missing_frontmatter": 0},
    "vault_fixes": {"orphans_deleted": 0, "links_repaired": 0, "links_removed": 0, "merges_completed": 0, "merge_links_redirected": 0},
    "errors": [],
}

def fix_youtube_jobs():
    logger.info("=== TASK 1: YouTube Jobs ===")
    try:
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()
        cur.execute("SELECT id, media_url, title FROM media_jobs WHERE status='pending' AND media_type='youtube' ORDER BY created_at ASC")
        stuck = cur.fetchall()
        if not stuck:
            logger.info("No stuck YouTube jobs."); return
        report["youtube_jobs"]["attempted"] = len(stuck)
        logger.info(f"Found {len(stuck)} stuck YouTube jobs")
        for job in stuck:
            try:
                cur.execute("UPDATE media_jobs SET status='processing' WHERE id=?", (job["id"],))
                conn.commit()
                r = subprocess.run(["python3", "-c", f"from youtube_transcript_api import YouTubeTranscriptApi; import re; vid=re.search(r'(?:v=|/)([a-zA-Z0-9_-]{{11}})','{job['media_url']}'); t=YouTubeTranscriptApi.get_transcript(vid.group(1)); print('SUCCESS')"], capture_output=True, text=True, timeout=60)
                if "SUCCESS" in r.stdout:
                    cur.execute("UPDATE media_jobs SET status='completed' WHERE id=?", (job["id"],))
                    report["youtube_jobs"]["completed"] += 1
                    report["youtube_jobs"]["details"].append(f"done: {job['title']}")
                else:
                    cur.execute("UPDATE media_jobs SET status='failed' WHERE id=?", (job["id"],))
                    report["youtube_jobs"]["failed"] += 1
                conn.commit()
            except Exception as e:
                cur.execute("UPDATE media_jobs SET status='failed' WHERE id=?", (job["id"],))
                conn.commit()
                report["youtube_jobs"]["failed"] += 1
                report["errors"].append(f"YT {job['id']}: {e}")
        conn.close()
    except Exception as e:
        report["errors"].append(f"YouTube: {e}")

def reprocess_icu_book():
    logger.info("=== TASK 2: ICU Book Reprocess ===")
    if not is_laborer_available():
        report["errors"].append("Consuela down — skipped ICU"); return
    try:
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()
        cur.execute("SELECT bc.id, bc.chapter_number, bc.chapter_title, bc.raw_text, bc.status FROM book_chapters bc JOIN book_jobs bj ON bc.book_job_id=bj.id WHERE (bj.filename LIKE '%ICU%' OR bj.filename LIKE '%icu%') AND bc.status != 'completed' ORDER BY bc.chapter_number")
        chapters = cur.fetchall()
        if not chapters:
            logger.info("No ICU chapters needing reprocess."); conn.close(); return
        report["icu_reprocess"]["attempted"] = len(chapters)
        SYS = "You are a critical care medicine expert. Extract: KEY CONCEPTS, CLINICAL PEARLS, FRAMEWORKS, HIGH-YIELD FACTS. Structured markdown. Focus on PCCM boards and bedside practice. No financial framing."
        for ch in chapters:
            if not ch["raw_text"] or len(ch["raw_text"]) < 100: continue
            try:
                result = route_and_call("clinical_concept_extraction",
                    messages=[{"role":"system","content":SYS},{"role":"user","content":f"# {ch['chapter_title']}\n\n{ch['raw_text'][:6000]}"}],
                    max_tokens=2048, timeout=300.0)
                if result:
                    title = ch["chapter_title"] or f"Chapter_{ch['chapter_number']}"
                    out = Path(CLINICAL_VAULT) / "ICU_Book" / f"Ch{ch['chapter_number']:02d}_{title.replace(' ','_')[:40]}.md"
                    out.parent.mkdir(parents=True, exist_ok=True)
                    out.write_text(f"---\nsource: ICU Book\nchapter: {ch['chapter_number']}\ntitle: \"{title}\"\nreprocessed: {datetime.now().strftime('%Y-%m-%d')}\nmodel: gemma-4-26b-a4b\n---\n\n{result}")
                    subprocess.run(["chown","proxmox:proxmox",str(out)], capture_output=True)
                    cur.execute("UPDATE book_chapters SET status='completed', completed_at=? WHERE id=?", (datetime.now().strftime('%Y-%m-%dT%H:%M:%SZ'), ch["id"]))
                    conn.commit()
                    report["icu_reprocess"]["completed"] += 1
                else:
                    report["icu_reprocess"]["failed"] += 1
                time.sleep(2)
            except Exception as e:
                cur.execute("UPDATE book_chapters SET status='failed', error_msg=? WHERE id=?", (str(e)[:200], ch["id"]))
                conn.commit()
                report["icu_reprocess"]["failed"] += 1
                report["errors"].append(f"ICU Ch{ch['chapter_number']}: {e}")
                logger.error(traceback.format_exc())
        conn.close()
    except Exception as e:
        report["errors"].append(f"ICU: {e}")

_MAX_AUTOFIX_ORPHANS = 50
_MAX_AUTOFIX_LINKS = 50
_MAX_AUTOFIX_MERGES = 10

def _fix_orphans(orphans: list) -> dict:
    """Delete theme-scoped orphans with zero DB mentions. Cap at 50 per run."""
    result = {"deleted": 0, "skipped": 0}
    eligible = [o for o in orphans if o.get("scope") == "theme" and o.get("db_mentions", 99) == 0]
    if not eligible:
        logger.info("No eligible orphans to delete."); return result
    to_fix = eligible[:_MAX_AUTOFIX_ORPHANS]
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    for o in to_fix:
        fpath = Path(VAULT_PATH) / o["path"]
        if not fpath.exists():
            result["skipped"] += 1; continue
        try:
            fpath.unlink()
            logger.info(f"  Deleted orphan: {o['path']}")
            stem = Path(o["file"]).stem
            cur.execute("UPDATE triage_items SET status='resolved', resolved_at=?, resolved_by='consuela_autofix' WHERE item_type='ORPHAN' AND files_involved LIKE ? AND status='pending'",
                        (datetime.now().strftime('%Y-%m-%dT%H:%M:%SZ'), f'%{stem}%'))
            conn.commit()
            result["deleted"] += 1
        except Exception as e:
            logger.error(f"  Failed to delete {o['path']}: {e}")
            result["skipped"] += 1
    conn.close()
    logger.info(f"Orphan fix: {result['deleted']} deleted, {result['skipped']} skipped")
    return result

def _fix_broken_links(broken: list, vault_path: str) -> dict:
    """Fuzzy-match broken links (>85%) or remove them. Cap at 50 per run."""
    result = {"repaired": 0, "removed": 0, "skipped": 0}
    if not broken:
        logger.info("No broken links to fix."); return result
    vault = Path(vault_path)
    # Build stem index
    all_stems = set()
    for f in vault.rglob("*.md"):
        if f.parent.name in _SKIP_DIRS: continue
        all_stems.add(f.stem)
    stem_list = sorted(all_stems)
    link_re = re.compile(r'\[\[([^\]|]+?)(?:\|[^\]]+?)?\]\]')
    # Group broken links by source file for batch rewrites
    by_source = {}
    for b in broken[:_MAX_AUTOFIX_LINKS]:
        by_source.setdefault(b["source"], []).append(b)
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    for source_name, links in by_source.items():
        source_files = list(vault.rglob(source_name))
        if not source_files:
            result["skipped"] += len(links); continue
        source_path = source_files[0]
        content = source_path.read_text(errors="replace")
        modified = False
        for b in links:
            target = b["target"]
            # Try fuzzy match
            matches = difflib.get_close_matches(target, stem_list, n=1, cutoff=0.85)
            old_link = f"[[{target}]]"
            # Also handle aliased links [[target|display]]
            alias_re = re.compile(rf'\[\[{re.escape(target)}\|[^\]]+?\]\]')
            plain_re = re.compile(rf'\[\[{re.escape(target)}\]\]')
            if matches:
                new_stem = matches[0]
                new_link = f"[[{new_stem}]]"
                # Replace aliased: keep display text
                m = alias_re.search(content)
                if m:
                    display = m.group(0).split("|")[1].rstrip("]")
                    content = alias_re.sub(f"[[{new_stem}|{display}]]", content)
                else:
                    content = plain_re.sub(new_link, content)
                logger.info(f"  Repaired link: [[{target}]] -> [[{new_stem}]] in {source_name}")
                result["repaired"] += 1
                modified = True
            else:
                # Remove dead link — replace with display text or empty
                m = alias_re.search(content)
                if m:
                    display = m.group(0).split("|")[1].rstrip("]")
                    content = alias_re.sub(display, content)
                else:
                    content = plain_re.sub("", content)
                logger.info(f"  Removed dead link: [[{target}]] from {source_name}")
                result["removed"] += 1
                modified = True
            cur.execute("UPDATE triage_items SET status='resolved', resolved_at=?, resolved_by='consuela_autofix' WHERE item_type='BROKEN_LINK' AND files_involved LIKE ? AND status='pending'",
                        (datetime.now().strftime('%Y-%m-%dT%H:%M:%SZ'), f'%{source_name}%'))
        if modified:
            source_path.write_text(content)
        conn.commit()
    conn.close()
    logger.info(f"Link fix: {result['repaired']} repaired, {result['removed']} removed, {result['skipped']} skipped")
    return result

def _fix_merge_candidates(merges: list, vault_path: str) -> dict:
    """Merge high-overlap pairs (>80%). Append shorter into longer, delete shorter, redirect links."""
    result = {"merged": 0, "links_redirected": 0}
    eligible = [m for m in merges if m.get("overlap_pct", 0) >= 80]
    if not eligible:
        logger.info("No merge candidates >= 80% overlap."); return result
    to_fix = eligible[:_MAX_AUTOFIX_MERGES]
    vault = Path(vault_path)
    link_re = re.compile(r'\[\[([^\]|]+?)(?:\|[^\]]+?)?\]\]')
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    for m in to_fix:
        name_a, name_b = m["files"][0], m["files"][1]
        path_a = vault / "Themes" / name_a
        path_b = vault / "Themes" / name_b
        if not path_a.exists() or not path_b.exists():
            logger.warning(f"  Merge skip — file missing: {name_a} or {name_b}"); continue
        content_a = path_a.read_text(errors="replace")
        content_b = path_b.read_text(errors="replace")
        # Determine longer and shorter
        if len(content_a) >= len(content_b):
            longer_path, shorter_path = path_a, path_b
            longer_name, shorter_name = name_a, name_b
            append_content = content_b
        else:
            longer_path, shorter_path = path_b, path_a
            longer_name, shorter_name = name_b, name_a
            append_content = content_a
        # Strip frontmatter from content being appended
        body = append_content
        if body.startswith("---"):
            parts = body.split("---", 2)
            body = parts[2] if len(parts) >= 3 else body
        # Append to longer note with separator
        merged = longer_path.read_text(errors="replace").rstrip() + f"\n\n---\n## Merged from {shorter_name}\n{body.strip()}\n"
        longer_path.write_text(merged)
        shorter_path.unlink()
        logger.info(f"  Merged {shorter_name} into {longer_name}")
        result["merged"] += 1
        # Redirect all wiki-links pointing to shorter note
        shorter_stem = Path(shorter_name).stem
        longer_stem = Path(longer_name).stem
        redirects = 0
        for md in vault.rglob("*.md"):
            if md.parent.name in _SKIP_DIRS: continue
            if md == longer_path: continue  # already handled
            text = md.read_text(errors="replace")
            if f"[[{shorter_stem}]]" in text or f"[[{shorter_stem}|" in text:
                new_text = text.replace(f"[[{shorter_stem}]]", f"[[{longer_stem}]]")
                new_text = new_text.replace(f"[[{shorter_stem}|", f"[[{longer_stem}|")
                md.write_text(new_text)
                redirects += 1
        result["links_redirected"] += redirects
        logger.info(f"  Redirected {redirects} links from {shorter_stem} -> {longer_stem}")
        # Mark triage items resolved
        cur.execute("UPDATE triage_items SET status='resolved', resolved_at=?, resolved_by='consuela_autofix' WHERE item_type='MERGE' AND files_involved LIKE ? AND status='pending'",
                    (datetime.now().strftime('%Y-%m-%dT%H:%M:%SZ'), f'%{shorter_name}%'))
        conn.commit()
    conn.close()
    logger.info(f"Merge fix: {result['merged']} merged, {result['links_redirected']} links redirected")
    return result

STOPWORDS = frozenset({
    "the","be","to","of","and","a","in","that","have","i","it","for","not","on","with",
    "he","as","you","do","at","this","but","his","by","from","they","we","her","she","or",
    "an","will","my","one","all","would","there","their","what","so","up","out","if","about",
    "who","get","which","go","me","when","make","can","like","time","no","just","him","know",
    "take","people","into","year","your","good","some","could","them","see","other","than",
    "then","now","look","only","come","its","over","think","also","back","after","use","two",
    "how","our","work","first","well","way","even","new","want","because","any","these",
    "give","day","most","us","is","are","was","were","been","being","has","had","did","does",
    "said","each","tell","set","three","put","too","much","more","very","own","still",
    "should","may","might","shall","must","need","here","where","both","between","under",
    "never","same","another","while","last","since","before","through","during","without",
    "again","once","further","however","though","although","yet","already","always",
    "often","still","such","those","every","many","several","next","early","late","long",
    "short","high","low","big","small","large","old","new","great","little","right","left",
    "able","also","else","ever","rather","quite","enough","less","least","nothing",
    "something","anything","everything","someone","anyone","everyone",
})

def _load_stopwords():
    """Return the shared stopwords set for content filtering."""
    return STOPWORDS


_FALSE_TICKERS = frozenset({
    "GLI", "UTC", "PDF", "HTML", "HTTP", "HTTPS", "JSON", "API", "RSS",
    "EDT", "EST", "PDT", "PST", "GMT",
    # Macro abbreviations
    "AI", "CPI", "PCE", "GDP", "PPI", "ISM", "PMI", "NFP", "FOMC", "ETF", "ESG",
    "IPO", "SEC", "IRS", "DOJ", "FTC",
    # Geo/org
    "USA", "EU", "UK", "UN", "NATO", "OPEC", "G7", "G20", "WTO", "IMF",
    # Cities/states
    "NY", "LA", "SF", "DC",
    # Titles
    "CEO", "CFO", "CTO", "COO", "VP", "SVP", "EVP", "MD", "PHD",
    # Currencies
    "USD", "EUR", "GBP", "JPY", "CNY", "INR", "BRL", "MXN", "CAD", "AUD",
    # Time periods
    "Q1", "Q2", "Q3", "Q4", "YoY", "MoM", "WoW", "YTD", "MTD", "QTD",
    # Misc
    "ETA", "TBD", "N/A", "AKA", "IE", "EG", "OF", "TO", "AP", "BP",
})

def _detect_merge_candidates(vault_path: str) -> list:
    """Compare all THEME_*.md files for content overlap (stopword-filtered, 50% threshold)."""
    stops = _load_stopwords()
    themes = {}
    for md in Path(vault_path).glob("Themes/THEME_*.md"):
        content = md.read_text(errors="replace")
        # Strip YAML frontmatter before analysis
        if content.startswith("---"):
            parts = content.split("---", 2)
            content = parts[2] if len(parts) >= 3 else content
        # Extract tickers, exclude false positives
        tickers = set(re.findall(r'\b[A-Z]{2,5}\b', content)) - _FALSE_TICKERS
        # Filtered word set for similarity
        words = set(content.lower().split()) - stops
        themes[md.name] = {"path": md, "tickers": tickers, "words": words}

    candidates = []
    names = sorted(themes.keys())
    for i, name_a in enumerate(names):
        for name_b in names[i + 1:]:
            a, b = themes[name_a], themes[name_b]
            shared_tickers = a["tickers"] & b["tickers"]
            if len(shared_tickers) < 2:
                continue
            # Jaccard on stopword-filtered word sets
            union = a["words"] | b["words"]
            if not union:
                continue
            overlap = len(a["words"] & b["words"]) / len(union)
            if overlap >= 0.50:
                candidates.append({
                    "type": "MERGE",
                    "files": [name_a, name_b],
                    "shared_tickers": sorted(shared_tickers)[:5],
                    "overlap_pct": round(overlap * 100),
                })
    return candidates


_SKIP_DIRS = {"_triage", "_context", "_meta", "_resources"}
_SKIP_NAMES = {"README", "INDEX", "Home"}


def _detect_orphans(vault_path: str, db_path: str) -> list:
    """Find notes with zero inbound wiki-links AND <3 DB mentions (via themes JOIN)."""
    vault = Path(vault_path)
    # Build stem→file map, skipping excluded dirs/names
    all_notes = {}
    for f in vault.rglob("*.md"):
        if f.parent.name in _SKIP_DIRS:
            continue
        if f.parent == vault and f.name.startswith("_"):
            continue
        stem = f.stem
        if stem in _SKIP_NAMES or stem.startswith("_"):
            continue
        all_notes[stem] = f

    # Count inbound [[links]] per note stem
    inbound = {stem: 0 for stem in all_notes}
    link_re = re.compile(r'\[\[([^\]|]+?)(?:\|[^\]]+?)?\]\]')
    for md in vault.rglob("*.md"):
        content = md.read_text(errors="replace")
        for match in link_re.findall(content):
            target = match.strip()
            if target in inbound:
                inbound[target] += 1

    # Zero-inbound notes: check DB mention count
    conn = sqlite3.connect(db_path)
    orphans = []
    for stem, count in inbound.items():
        if count > 0:
            continue
        # Match theme_label against the stem (with and without THEME_ prefix)
        like_key = stem.replace("THEME_", "")
        cur = conn.execute(
            "SELECT COUNT(*) FROM document_themes dt "
            "JOIN themes t ON dt.theme_id = t.id "
            "WHERE t.theme_label LIKE ?",
            (f"%{like_key}%",)
        )
        db_mentions = cur.fetchone()[0]
        if db_mentions < 3:
            fpath = all_notes[stem]
            rel_path = str(fpath.relative_to(vault))
            # Scope: theme orphans are high priority, leaf notes are expected
            if rel_path.startswith("Themes/"):
                scope = "theme"
            elif any(rel_path.startswith(d + "/") for d in
                     ("Books", "Documents", "Media", "History")):
                scope = "leaf"
            else:
                scope = "other"
            orphans.append({
                "type": "ORPHAN",
                "file": stem + ".md",
                "path": rel_path,
                "scope": scope,
                "db_mentions": db_mentions,
                "last_modified": datetime.fromtimestamp(
                    fpath.stat().st_mtime
                ).strftime("%Y-%m-%d"),
            })
    conn.close()
    return orphans


def _detect_broken_links(vault_path: str) -> list:
    """Find [[links]] pointing to notes that don't exist, skipping template placeholders."""
    vault = Path(vault_path)
    # Build set of all note stems (skip excluded dirs)
    all_stems = set()
    for f in vault.rglob("*.md"):
        if f.parent.name in _SKIP_DIRS:
            continue
        all_stems.add(f.stem)

    link_re = re.compile(r'\[\[([^\]|]+?)(?:\|[^\]]+?)?\]\]')
    # Template placeholder patterns — not real cleanup work
    placeholder_re = re.compile(
        r'^(?:Name\d|Ticker\d|Theme-Name|TICKER|'
        r'Thesis-Name\d|COMPANY|SECTOR|THEME_NAME|'
        r'\.{3}|TODO|FIXME|XXX|PLACEHOLDER)$',
        re.IGNORECASE
    )
    is_all_caps_short = lambda t: len(t) <= 4 and t.isalpha() and t.isupper()

    broken = []
    seen = set()  # deduplicate (source, target) pairs
    for md in vault.rglob("*.md"):
        if md.parent.name in _SKIP_DIRS:
            continue
        rel = str(md.relative_to(vault))
        # Skip template directories
        if "/_templates/" in rel or "/Templates/" in rel:
            continue
        content = md.read_text(errors="replace")
        for target in link_re.findall(content):
            target = target.strip()
            if not target or target in all_stems:
                continue
            # Skip template placeholders
            if placeholder_re.match(target) or is_all_caps_short(target):
                continue
            # Skip .md-suffixed links (Obsidian strips suffix)
            if target.endswith(".md") and target[:-3] in all_stems:
                continue
            key = (md.name, target)
            if key in seen:
                continue
            seen.add(key)
            broken.append({
                "type": "BROKEN_LINK",
                "source": md.name,
                "target": target,
            })
    return broken


def _detect_stale(vault_path: str, db_path: str, stale_days: int = 30) -> list:
    """Find THEME files with no recent DB mentions (via themes JOIN)."""
    vault = Path(vault_path)
    conn = sqlite3.connect(db_path)
    stale = []
    for md in vault.glob("Themes/THEME_*.md"):
        theme_key = md.stem.replace("THEME_", "")
        # Last mention via document_themes -> themes JOIN
        cur = conn.execute(
            "SELECT MAX(dt.extracted_at), t.theme_label "
            "FROM document_themes dt "
            "JOIN themes t ON dt.theme_id = t.id "
            "WHERE t.theme_label LIKE ?",
            (f"%{theme_key}%",)
        )
        row = cur.fetchone()
        last_mention = row[0] if row and row[0] else None
        theme_label = row[1] if row and row[1] else theme_key
        if not last_mention:
            # No DB record at all — check file mtime
            last_mention = datetime.fromtimestamp(
                md.stat().st_mtime
            ).isoformat()
        try:
            last_dt = datetime.fromisoformat(last_mention.replace("Z", "+00:00"))
            days_ago = (datetime.now() - last_dt.replace(tzinfo=None)).days
        except (ValueError, AttributeError):
            continue
        if days_ago > stale_days:
            cur2 = conn.execute(
                "SELECT COUNT(*) FROM document_themes dt "
                "JOIN themes t ON dt.theme_id = t.id "
                "WHERE t.theme_label LIKE ?",
                (f"%{theme_key}%",)
            )
            total = cur2.fetchone()[0]
            stale.append({
                "type": "STALE",
                "file": md.name,
                "theme_label": theme_label,
                "last_mention": last_mention[:10],
                "days_ago": days_ago,
                "total_mentions": total,
            })
    conn.close()
    # Sort by days_ago descending (stalest first)
    stale.sort(key=lambda x: x["days_ago"], reverse=True)
    return stale


def _detect_missing_frontmatter(vault_path: str) -> list:
    """Find .md files without YAML frontmatter block."""
    vault = Path(vault_path)
    missing = []
    for md in vault.rglob("*.md"):
        if md.parent.name in _SKIP_DIRS:
            continue
        if md.name.startswith("_"):
            continue
        rel = str(md.relative_to(vault))
        if "/_templates/" in rel or "/Templates/" in rel:
            continue
        content = md.read_text(errors="replace")
        if not content.strip().startswith("---"):
            # Scope classification (same as orphans)
            if rel.startswith("Themes/"):
                scope = "theme"
            elif any(rel.startswith(d + "/") for d in
                     ("Books", "Documents", "Media", "History")):
                scope = "leaf"
            else:
                scope = "other"
            missing.append({
                "type": "FRONTMATTER",
                "file": md.name,
                "path": rel,
                "scope": scope,
            })
    return missing


def _write_triage_report(all_items: list, vault_path: str, db_path: str) -> str:
    """
    Persist all triage items to SQLite, then write prioritized markdown report.
    Assigns sequential T-IDs, returns path to the written markdown file.
    """
    # 1. Assign T-IDs sequentially
    for i, item in enumerate(all_items):
        item["id"] = f"T{i + 1:03d}"

    # 2. Persist ALL items to SQLite first
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()
    date_str = datetime.now().strftime("%Y-%m-%d")
    persisted = 0
    for item in all_items:
        files_involved = json.dumps(item.get("files", [item.get("file", "")]))
        detail = {k: v for k, v in item.items()
                  if k not in ("id", "type", "files", "file")}
        cur.execute(
            "INSERT OR REPLACE INTO triage_items "
            "(triage_id, report_date, item_type, files_involved, detail, "
            " recommendation, status) "
            "VALUES (?, ?, ?, ?, ?, 'pending', 'pending')",
            (item["id"], date_str, item["type"],
             files_involved, json.dumps(detail))
        )
        persisted += 1
    conn.commit()
    conn.close()
    logger.info(f"Triage: persisted {persisted} items to triage_items")

    # 3. Build markdown — actionable categories first
    triage_dir = Path(vault_path) / "_triage"
    triage_dir.mkdir(exist_ok=True)
    filepath = triage_dir / f"TRIAGE_{date_str}.md"

    # Priority order: actionable first, then bulk
    type_order = [
        ("MERGE", "MERGE CANDIDATES"),
        ("ORPHAN", "ORPHAN NOTES — Themes"),
        ("FRONTMATTER", "MISSING FRONTMATTER — Themes"),
        ("BROKEN_LINK", "BROKEN LINKS"),
        ("STALE", "STALE THEMES"),
        ("ORPHAN_LEAF", "ORPHAN NOTES — Leaf (low priority)"),
        ("ORPHAN_OTHER", "ORPHAN NOTES — Other"),
        ("FRONTMATTER_LEAF", "MISSING FRONTMATTER — Leaf/Other"),
    ]

    # Partition items into display groups
    def bucket(item):
        t = item["type"]
        if t == "ORPHAN":
            scope = item.get("scope", "other")
            if scope == "theme":
                return "ORPHAN"
            elif scope == "leaf":
                return "ORPHAN_LEAF"
            else:
                return "ORPHAN_OTHER"
        if t == "FRONTMATTER":
            scope = item.get("scope", "other")
            if scope in ("theme",):
                return "FRONTMATTER"
            else:
                return "FRONTMATTER_LEAF"
        return t

    groups = {}
    for item in all_items:
        b = bucket(item)
        groups.setdefault(b, []).append(item)

    lines = [
        "---",
        f"date: {date_str}",
        "scanned_by: consuela",
        "status: pending_review",
        f"items_total: {len(all_items)}",
        f"items_resolved: 0",
        "---",
        "",
        f"## Vault Triage — {datetime.now().strftime('%B %d, %Y')}",
        "",
        f"> {persisted} items persisted to `triage_items` table. "
        f"Markdown shows top 20 per category.",
        "",
    ]

    for bucket_key, label in type_order:
        group = groups.get(bucket_key, [])
        if not group:
            continue
        lines.append(f"### {label} ({len(group)})")
        lines.append("")
        for item in group[:20]:
            tid = item["id"]
            t = item["type"]
            if t == "MERGE":
                lines.append(
                    f"- [ ] **{tid}** | MERGE | "
                    f"`{item['files'][0]}` + `{item['files'][1]}`")
                lines.append(
                    f"  - Overlap: {item['overlap_pct']}% | "
                    f"Shared tickers: {', '.join(item['shared_tickers'])}")
                lines.append(f"  - Recommendation: pending (Remi)")
            elif t == "ORPHAN":
                lines.append(
                    f"- [ ] **{tid}** | ORPHAN | `{item['file']}`")
                lines.append(
                    f"  - DB mentions: {item['db_mentions']} | "
                    f"Last modified: {item['last_modified']}")
                lines.append(f"  - Recommendation: pending (Remi)")
            elif t == "BROKEN_LINK":
                lines.append(
                    f"- [ ] **{tid}** | BROKEN_LINK | "
                    f"`{item['source']}` → `[[{item['target']}]]`")
            elif t == "STALE":
                lines.append(
                    f"- [ ] **{tid}** | STALE | `{item['file']}`")
                lines.append(
                    f"  - Last mention: {item['last_mention']} "
                    f"({item['days_ago']}d ago) | Total: {item['total_mentions']}")
                lines.append(f"  - Recommendation: pending (Remi)")
            elif t == "FRONTMATTER":
                lines.append(
                    f"- [ ] **{tid}** | FRONTMATTER | `{item['path']}`")
            lines.append(f"  - Status: pending")
            lines.append("")
        if len(group) > 20:
            lines.append(
                f"> _{len(group) - 20} more items in database "
                f"(query triage_items where report_date='{date_str}')_")
            lines.append("")
        lines.append("")

    filepath.write_text("\n".join(lines))
    subprocess.run(
        ["chown", "proxmox:proxmox", str(filepath)], capture_output=True)
    subprocess.run(
        ["chown", "proxmox:proxmox", str(triage_dir)], capture_output=True)
    logger.info(f"Triage report written: {filepath} "
                f"({len(all_items)} items, {len(lines)} lines)")
    return str(filepath)


def vault_hygiene():
    logger.info("=== TASK 3: Vault Hygiene ===")
    vault = Path(VAULT_PATH)
    if not vault.exists():
        report["errors"].append(f"Vault not found: {VAULT_PATH}"); return

    # Run all 5 detectors
    merges = _detect_merge_candidates(VAULT_PATH)
    orphans = _detect_orphans(VAULT_PATH, DB_PATH)
    broken = _detect_broken_links(VAULT_PATH)
    stale = _detect_stale(VAULT_PATH, DB_PATH)
    frontmatter = _detect_missing_frontmatter(VAULT_PATH)

    all_items = merges + orphans + broken + stale + frontmatter
    logger.info(f"Triage detectors: {len(merges)} merges, {len(orphans)} orphans, "
                f"{len(broken)} broken links, {len(stale)} stale, {len(frontmatter)} no frontmatter "
                f"= {len(all_items)} total")

    # Write triage report (SQLite + markdown)
    if all_items:
        try:
            report_path = _write_triage_report(all_items, VAULT_PATH, DB_PATH)
            report["vault_hygiene"]["triage_report"] = report_path
        except Exception as e:
            report["errors"].append(f"Triage write failed: {e}")
            logger.error(f"Triage write failed: {e}")

    # Populate legacy counts for send_report()
    report["vault_hygiene"]["orphan_notes"] = len(orphans)
    report["vault_hygiene"]["broken_links"] = len(broken)
    report["vault_hygiene"]["missing_frontmatter"] = len(frontmatter)
    report["vault_hygiene"]["merge_candidates"] = len(merges)
    report["vault_hygiene"]["stale_themes"] = len(stale)
    report["vault_hygiene"]["items_total"] = len(all_items)
    logger.info(f"Vault: {len(orphans)} orphans, {len(broken)} broken links, "
                f"{len(frontmatter)} no frontmatter, {len(merges)} merges, {len(stale)} stale")

def vault_autofix():
    """Run autonomous vault fixers after detection."""
    logger.info("=== TASK 3b: Vault Autofix ===")
    vault = Path(VAULT_PATH)
    if not vault.exists(): return
    vf = report["vault_fixes"]
    try:
        # Re-detect to get current state (post-hygiene might differ)
        orphans = _detect_orphans(VAULT_PATH, DB_PATH)
        r = _fix_orphans(orphans)
        vf["orphans_deleted"] = r["deleted"]
    except Exception as e:
        report["errors"].append(f"Autofix orphans: {e}")
        logger.error(f"Autofix orphans failed: {e}")
    try:
        broken = _detect_broken_links(VAULT_PATH)
        r = _fix_broken_links(broken, VAULT_PATH)
        vf["links_repaired"] = r["repaired"]
        vf["links_removed"] = r["removed"]
    except Exception as e:
        report["errors"].append(f"Autofix links: {e}")
        logger.error(f"Autofix links failed: {e}")
    try:
        merges = _detect_merge_candidates(VAULT_PATH)
        r = _fix_merge_candidates(merges, VAULT_PATH)
        vf["merges_completed"] = r["merged"]
        vf["merge_links_redirected"] = r["links_redirected"]
    except Exception as e:
        report["errors"].append(f"Autofix merges: {e}")
        logger.error(f"Autofix merges failed: {e}")

def send_report():
    report["end_time"] = datetime.now().isoformat()
    try:
        start = datetime.fromisoformat(report["start_time"])
        dur = f"{(datetime.now() - start).total_seconds()/60:.0f}"
    except Exception:
        dur = "?"
    yt = report["youtube_jobs"]
    icu = report["icu_reprocess"]
    vh = report["vault_hygiene"]
    lines = [f"Consuela Overnight Report ({dur} min)"]
    if yt["attempted"]: lines.append(f"YT: {yt['completed']}/{yt['attempted']} fixed, {yt['failed']} failed")
    if icu["attempted"]: lines.append(f"ICU: {icu['completed']}/{icu['attempted']} chapters reprocessed")
    lines.append(f"Vault: {vh['orphan_notes']} orphans, {vh['broken_links']} broken links, {vh['missing_frontmatter']} no frontmatter")
    vf = report["vault_fixes"]
    if vf["orphans_deleted"] or vf["links_repaired"] or vf["links_removed"] or vf["merges_completed"]:
        parts = []
        if vf["orphans_deleted"]: parts.append(f"{vf['orphans_deleted']} orphans deleted")
        if vf["links_repaired"]: parts.append(f"{vf['links_repaired']} links repaired")
        if vf["links_removed"]: parts.append(f"{vf['links_removed']} links removed")
        if vf["merges_completed"]: parts.append(f"{vf['merges_completed']} merges completed")
        lines.append(f"Fixed: {', '.join(parts)}")
    if report["errors"]:
        lines.append(f"Errors ({len(report['errors'])}):")
        for e in report["errors"][:3]: lines.append(f"  {e[:80]}")
    msg = "\n".join(lines)
    logger.info(msg)
    try:
        from telegram_sender import send_ops_report
        send_ops_report(msg)
        logger.info("Report sent via Telegram")
    except Exception as e:
        logger.error(f"Telegram send failed: {e}")

def main():
    report["start_time"] = datetime.now().isoformat()
    logger.info("CONSUELA OVERNIGHT — Starting")
    os.makedirs(os.path.expanduser("~/remi-intelligence/logs"), exist_ok=True)
    fix_youtube_jobs()
    reprocess_icu_book()
    vault_hygiene()
    vault_autofix()
    send_report()
    logger.info("CONSUELA OVERNIGHT — Complete")

if __name__ == "__main__":
    main()

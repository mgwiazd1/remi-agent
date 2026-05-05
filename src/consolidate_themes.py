"""
One-time theme consolidation — cluster duplicate themes via Consuela, then merge into canonical anchors.
Run once: python3 consolidate_themes.py
"""
import json
import logging
import os
import sqlite3
import sys

import httpx
from dotenv import load_dotenv
load_dotenv(os.path.expanduser("~/remi-intelligence/.env"))

sys.path.insert(0, os.path.dirname(__file__))
from llm_extractor import clean_json

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

DB_PATH = os.getenv("DB_PATH", os.path.expanduser("~/remi-intelligence/remi_intelligence.db"))
GLM_API_KEY = os.getenv("GLM_API_KEY", "")
GLM_BASE_URL = os.getenv("GLM_BASE_URL", "https://api.z.ai/api/coding/paas/v4")
BATCH_SIZE = 10  # Small batches — glm-4.7 needs room for full JSON output


CONSOLIDATION_PROMPT = """You are a theme deduplication engine for a macro investment intelligence database.

Here are {n} theme entries. Many describe the SAME underlying topic with different wording.
Each line has: theme_key | label | mentions

THEMES:
{theme_list}

Group them into clusters. For each cluster, pick the BEST theme_key as canonical.
Assign a sector from: geopolitical, macro, fed, credit, commodities, crypto, ai, equities, fiscal, fx

Return JSON:
{{
  "clusters": [
    {{
      "canonical_key": "best-theme-key-from-list",
      "canonical_label": "Best Human-Readable Label",
      "sector": "geopolitical",
      "merged_keys": ["other-key-1", "other-key-2"]
    }}
  ]
}}

Rules:
- Two themes are the SAME if they describe the same real-world event, policy, or trend
- "Strait of Hormuz Closure Impact" and "Geopolitical Risk & Energy Crisis Response" = SAME (both Iran/oil)
- "Private Credit Fragility" and "Private Credit Redemption Wave" = SAME (both PE/credit stress)
- "Fed Rate Pause Signal" and "PBOC RRR Cut" = DIFFERENT (different central banks)
- Pick the most specific, descriptive key as canonical
- A theme with no duplicates = cluster of 1 with empty merged_keys
- Every theme in the input MUST appear exactly once: either as canonical_key or in merged_keys
Return only valid JSON."""


def get_themes_to_consolidate(conn):
    """Pull all active themes (mention_count >= 2, seen in last 30 days)."""
    conn.row_factory = sqlite3.Row
    rows = conn.execute("""
        SELECT theme_key, theme_label, mention_count, velocity_score
        FROM themes
        WHERE mention_count >= 2
          AND last_seen_at > datetime('now', '-30 days')
        ORDER BY mention_count DESC
    """).fetchall()
    return [dict(r) for r in rows]


def format_batch(themes):
    """Format a batch of themes for the LLM prompt."""
    lines = []
    for t in themes:
        lines.append(f"{t['theme_key']} | {t['theme_label']} | {t['mention_count']} mentions")
    return "\n".join(lines)


def cluster_batch(themes):
    """Send one batch to LLM for clustering. Calls API directly to handle
    reasoning_content properly."""
    theme_list = format_batch(themes)
    prompt = CONSOLIDATION_PROMPT.format(n=len(themes), theme_list=theme_list)
    
    # Call GLM-4.7 directly — bypass _call_llm which loses reasoning_content
    try:
        r = httpx.post(
            f"{GLM_BASE_URL}/chat/completions",
            headers={"Authorization": f"Bearer {GLM_API_KEY}", "Content-Type": "application/json"},
            json={"model": "glm-4.7", "messages": [{"role": "user", "content": prompt}],
                  "max_tokens": 6000, "temperature": 0.2},
            timeout=300,
        )
        if r.status_code != 200:
            logger.error(f"GLM API returned {r.status_code}")
            return None
        
        resp = r.json()
        msg = resp["choices"][0]["message"]
        raw = msg.get("content") or msg.get("reasoning_content") or ""
        
        if not raw:
            logger.error("Both content and reasoning_content empty")
            return None
    except Exception as e:
        logger.error(f"API call failed: {e}")
        return None
    
    # Clean and try to parse
    cleaned = clean_json(raw)
    
    # Direct parse attempt
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        pass
    
    # Extract balanced curly-brace block
    depth = 0
    start = None
    for i, c in enumerate(cleaned):
        if c == '{':
            if depth == 0:
                start = i
            depth += 1
        elif c == '}':
            depth -= 1
            if depth == 0 and start is not None:
                try:
                    return json.loads(cleaned[start:i+1])
                except json.JSONDecodeError:
                    start = None
                    continue
    
    # Last resort: find first { and last }
    first = cleaned.find('{')
    last = cleaned.rfind('}')
    if first >= 0 and last > first:
        try:
            return json.loads(cleaned[first:last+1])
        except json.JSONDecodeError:
            pass
    
    logger.error(f"Could not extract JSON from response (len={len(raw)})")
    # Log first 300 chars for debugging
    logger.error(f"Response preview: {raw[:300]}")
    return None


def execute_merge(conn, clusters):
    """Merge duplicate themes into canonical anchors in SQLite."""
    merged_count = 0
    for cluster in clusters:
        canonical = cluster.get("canonical_key")
        canonical_label = cluster.get("canonical_label", canonical)
        sector = cluster.get("sector", "macro")
        merged_keys = cluster.get("merged_keys", [])
        
        if not canonical:
            continue
        
        # All keys involved: canonical + merged
        all_keys = [canonical] + [k for k in merged_keys if k != canonical]
        
        # Check which keys actually exist in DB
        placeholders = ",".join("?" * len(all_keys))
        existing = conn.execute(f"""
            SELECT theme_key FROM themes WHERE theme_key IN ({placeholders})
        """, all_keys).fetchall()
        existing_keys = {r[0] for r in existing}
        
        if not existing_keys:
            continue
        
        # If canonical doesn't exist but a merged key does, promote the first existing one
        if canonical not in existing_keys and merged_keys:
            for mk in merged_keys:
                if mk in existing_keys:
                    canonical = mk
                    break
        
        # Sum mentions, get date bounds across all keys
        all_placeholders = ",".join("?" * len(existing_keys))
        stats = conn.execute(f"""
            SELECT COALESCE(SUM(mention_count), 0),
                   MIN(first_seen_at),
                   MAX(last_seen_at),
                   MAX(velocity_score)
            FROM themes WHERE theme_key IN ({all_placeholders})
        """, list(existing_keys)).fetchone()
        
        total_mentions, first_seen, last_seen, max_velocity = stats
        
        # Update canonical with merged stats + sector
        conn.execute("""
            UPDATE themes SET
                mention_count = ?,
                first_seen_at = ?,
                last_seen_at = ?,
                velocity_score = MAX(velocity_score, ?),
                sector = ?,
                theme_label = ?
            WHERE theme_key = ?
        """, (total_mentions, first_seen, last_seen, max_velocity or 0, sector,
              canonical_label, canonical))
        
        # Get canonical theme ID
        canonical_row = conn.execute(
            "SELECT id FROM themes WHERE theme_key = ?", (canonical,)
        ).fetchone()
        if not canonical_row:
            continue
        canonical_id = canonical_row[0]
        
        # Repoint document_themes and delete merged duplicates
        for old_key in merged_keys:
            if old_key == canonical:
                continue
            old_row = conn.execute(
                "SELECT id FROM themes WHERE theme_key = ?", (old_key,)
            ).fetchone()
            if old_row:
                old_id = old_row[0]
                conn.execute(
                    "UPDATE document_themes SET theme_id = ? WHERE theme_id = ?",
                    (canonical_id, old_id)
                )
                conn.execute("DELETE FROM themes WHERE id = ?", (old_id,))
                merged_count += 1
        
        logger.info(f"Consolidated: {canonical} [{sector}] ← {len(merged_keys)} duplicates, "
                     f"total mentions: {total_mentions}")
    
    conn.commit()
    return merged_count


def main():
    conn = sqlite3.connect(DB_PATH)
    themes = get_themes_to_consolidate(conn)
    logger.info(f"Found {len(themes)} active themes to consolidate")
    
    # Process in batches
    all_clusters = []
    total_batches = (len(themes) + BATCH_SIZE - 1) // BATCH_SIZE
    
    for i in range(0, len(themes), BATCH_SIZE):
        batch = themes[i:i+BATCH_SIZE]
        batch_num = i // BATCH_SIZE + 1
        logger.info(f"Processing batch {batch_num}/{total_batches} ({len(batch)} themes)...")
        
        result = cluster_batch(batch)
        if result and "clusters" in result:
            all_clusters.extend(result["clusters"])
            logger.info(f"  Got {len(result['clusters'])} clusters")
        else:
            logger.error(f"  Batch {batch_num} failed — skipping")
            # Add unclustered themes as solo clusters
            for t in batch:
                all_clusters.append({
                    "canonical_key": t["theme_key"],
                    "canonical_label": t["theme_label"],
                    "sector": "macro",
                    "merged_keys": []
                })
    
    logger.info(f"Total clusters: {len(all_clusters)}")
    
    # Execute merge
    merged = execute_merge(conn, all_clusters)
    logger.info(f"Consolidation complete: {merged} duplicate themes merged")
    
    # Summary
    remaining = conn.execute("""
        SELECT COUNT(*) FROM themes
        WHERE mention_count >= 2 AND last_seen_at > datetime('now', '-30 days')
    """).fetchone()[0]
    logger.info(f"Active themes after consolidation: {remaining}")
    
    conn.close()
    print(f"\nDONE: {merged} duplicates merged, {remaining} canonical themes remain")


if __name__ == "__main__":
    main()

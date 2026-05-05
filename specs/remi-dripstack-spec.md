# Remi — DripStack Integration Spec
**Date:** 2026-05-03
**Scope:** Two-sided integration — Remi publishes articles to DripStack, buys paywalled Substack content for intelligence ingestion
**VM:** 192.168.1.100 (Deb-Remi)
**Working directory:** `~/remi-intelligence/`

---

## What is DripStack

DripStack (`https://dripstack.xyz`) is a Substack content marketplace gated by x402 micropayments. It indexes Substack publications and sells individual articles to AI agents at $0.01 default per article (overridable per-post). API spec at `https://dripstack.xyz/openapi.json`.

Remi's relationship with it is two-sided:

- **Sell side** — BogWizard publishes macro articles to a Substack that DripStack indexes. Other agents buy Remi's content. Micropayment revenue flows to Remi's wallet.
- **Buy side** — Remi autonomously purchases paywalled Substack content when his pipeline needs deeper sourcing on a high-velocity theme. This is the primary intelligence value.

---

## Step 0 — Before Any Code: Fetch the OpenAPI Spec

```bash
curl https://dripstack.xyz/openapi.json | python3 -m json.tool > ~/remi-intelligence/specs/dripstack-openapi.json
```

Read it. Confirm route shapes, 402 response headers, and x402 payment protocol fields before writing any HTTP client code. Do not assume from the SKILL.md alone.

---

## Step 1 — Sell Side Setup (Manual, Not Coded)

This is a one-time manual step, not automated.

1. Create a Substack at `bogwizard.substack.com` (or check if it exists)
2. Post the article "The Soft Landing Is a Mirage" (already written — MG has it)
3. Import to DripStack:
   ```bash
   curl "https://dripstack.xyz/import/https://bogwizard.substack.com"
   ```
4. Confirm the publication appears in the catalog:
   ```bash
   curl https://dripstack.xyz/api/v1/publications | python3 -m json.tool | grep -i bogwizard
   ```

Done. DripStack will index all posts. Future articles publish to Substack → DripStack picks them up automatically.

---

## Step 2 — SQLite Table

Add to `~/remi-intelligence/remi_intelligence.db`. Run from the `~/remi-intelligence/` directory.

```sql
CREATE TABLE IF NOT EXISTS dripstack_purchases (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    publication_slug TEXT NOT NULL,
    post_slug TEXT NOT NULL,
    title TEXT,
    purchased_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    cost_usd REAL DEFAULT 0.01,
    trigger_theme TEXT,
    trigger_sector TEXT,
    ingested BOOLEAN DEFAULT FALSE,
    document_id INTEGER REFERENCES documents(id)
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_dripstack_unique
    ON dripstack_purchases(publication_slug, post_slug);
```

Verify:
```bash
cd ~/remi-intelligence && sqlite3 remi_intelligence.db ".schema dripstack_purchases"
```

---

## Step 3 — `dripstack_buyer.py`

**File:** `~/remi-intelligence/src/dripstack_buyer.py`

Build this in atomic steps. After each function, run:
```bash
python3 -c "import ast; ast.parse(open('src/dripstack_buyer.py').read()); print('SYNTAX OK')"
```

### 3a — Imports and constants (write first, verify before proceeding)

```python
"""
dripstack_buyer.py — DripStack x402 article purchaser

Queries DripStack catalog by topic, purchases articles via x402 micropayments,
ingests content into the Remi intelligence pipeline.
"""

import os
import re
import json
import time
import sqlite3
import logging
import hashlib
import httpx
from datetime import datetime, timezone
from pathlib import Path
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

DRIPSTACK_BASE = "https://dripstack.xyz"
DB_PATH = Path(__file__).parent.parent / "remi_intelligence.db"

# Wallet for x402 payments — load private key from env, never hardcode
WALLET_PRIVATE_KEY = os.environ.get("REMI_WALLET_PRIVATE_KEY")
WALLET_ADDRESS = "0x316252829cd5fDFd2aB4e17E669C8CE8a42794F6"

# Spend guard — never spend more than this per day on DripStack
DAILY_SPEND_LIMIT_USD = 0.50

# Topic taxonomy aligned to Remi's 13-sector closed taxonomy
SECTOR_TO_TOPIC = {
    "macro": "finance",
    "fed": "finance",
    "credit": "finance",
    "fiscal": "finance",
    "fx": "finance",
    "energy": "finance",
    "metals": "finance",
    "agriculture": "finance",
    "crypto": "crypto",
    "ai": "AI",
    "equities": "finance",
    "geopolitical": "geopolitics",
}
```

### 3b — Spend guard

```python
def get_daily_spend(conn: sqlite3.Connection) -> float:
    """Return total USD spent on DripStack today."""
    today = datetime.now(timezone.utc).date().isoformat()
    row = conn.execute(
        "SELECT COALESCE(SUM(cost_usd), 0) FROM dripstack_purchases WHERE date(purchased_at) = ?",
        (today,)
    ).fetchone()
    return row[0] if row else 0.0


def already_purchased(conn: sqlite3.Connection, pub_slug: str, post_slug: str) -> bool:
    """True if this article has already been purchased."""
    row = conn.execute(
        "SELECT id FROM dripstack_purchases WHERE publication_slug = ? AND post_slug = ?",
        (pub_slug, post_slug)
    ).fetchone()
    return row is not None
```

### 3c — Catalog query

```python
def query_catalog(topic: str) -> list[dict]:
    """
    Fetch all indexed publications and return those matching the topic.
    Never returns the full catalog — always filters first.
    """
    resp = httpx.get(f"{DRIPSTACK_BASE}/api/v1/publications", timeout=15)
    resp.raise_for_status()
    publications = resp.json()

    topic_lower = topic.lower()
    matches = []
    for pub in publications:
        desc = (pub.get("description") or "").lower()
        title = (pub.get("title") or "").lower()
        if topic_lower in desc or topic_lower in title:
            matches.append(pub)

    logger.info(f"DripStack catalog: {len(publications)} total, {len(matches)} match topic '{topic}'")
    return matches


def get_publication_posts(pub_slug: str) -> list[dict]:
    """Return post summaries for a publication. Imports on demand if not indexed."""
    resp = httpx.get(f"{DRIPSTACK_BASE}/api/v1/publications/{pub_slug}", timeout=15)
    if resp.status_code == 404:
        # Attempt import
        logger.info(f"Publication {pub_slug} not indexed — attempting import")
        httpx.post(f"{DRIPSTACK_BASE}/api/v1/publications/{pub_slug}", timeout=30)
        resp = httpx.get(f"{DRIPSTACK_BASE}/api/v1/publications/{pub_slug}", timeout=15)
        resp.raise_for_status()
    pub_data = resp.json()
    return pub_data.get("posts", [])
```

### 3d — x402 payment handler

**READ THE OPENAPI SPEC FIRST before implementing this.** The exact 402 response header format and payment proof mechanism must come from the spec, not assumptions.

The pattern is:
1. Send GET request for the article
2. If 402 returned, parse the payment challenge from the response headers
3. Sign and broadcast a micropayment on Base mainnet using `web3.py`
4. Retry the GET with the payment proof in the header

```python
def _sign_x402_payment(payment_details: dict) -> dict:
    """
    Sign an x402 payment challenge using Remi's wallet.
    payment_details comes from parsing the 402 response headers.
    Returns the proof headers to add to the retry request.

    IMPORTANT: Read the DripStack OpenAPI spec for exact header names and
    payment_details structure before implementing this function body.
    Uses web3.py — confirm it's installed: pip3 show web3
    """
    if not WALLET_PRIVATE_KEY:
        raise EnvironmentError("REMI_WALLET_PRIVATE_KEY not set in environment")

    # TODO: implement after reading openapi spec
    # from web3 import Web3
    # w3 = Web3(Web3.HTTPProvider("https://mainnet.base.org"))
    # ... sign payment, broadcast tx, return proof headers
    raise NotImplementedError("Read openapi.json and implement x402 signing")


def purchase_article(pub_slug: str, post_slug: str, trigger_theme: str = None, trigger_sector: str = None) -> dict | None:
    """
    Purchase a single article via x402. Returns dict with title and contentHtml, or None on failure.
    Checks spend limit and deduplication before purchasing.
    """
    conn = sqlite3.connect(DB_PATH)
    try:
        if already_purchased(conn, pub_slug, post_slug):
            logger.info(f"Already purchased {pub_slug}/{post_slug} — skipping")
            return None

        daily_spend = get_daily_spend(conn)
        if daily_spend >= DAILY_SPEND_LIMIT_USD:
            logger.warning(f"Daily DripStack spend limit reached: ${daily_spend:.3f}")
            return None

        url = f"{DRIPSTACK_BASE}/api/v1/publications/{pub_slug}/{post_slug}"

        # First attempt — expect 402 for paid posts
        resp = httpx.get(url, timeout=15)

        if resp.status_code == 402:
            # Parse payment challenge and pay
            payment_details = json.loads(resp.text)  # adjust per openapi spec
            proof_headers = _sign_x402_payment(payment_details)

            # Retry with payment proof
            resp = httpx.get(url, headers=proof_headers, timeout=30)

        resp.raise_for_status()
        article_data = resp.json()

        # Record purchase
        conn.execute(
            """INSERT OR IGNORE INTO dripstack_purchases
               (publication_slug, post_slug, title, cost_usd, trigger_theme, trigger_sector)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (pub_slug, post_slug, article_data.get("title"), 0.01, trigger_theme, trigger_sector)
        )
        conn.commit()

        logger.info(f"Purchased: {article_data.get('title')} from {pub_slug} (${0.01})")
        return article_data

    except Exception as e:
        logger.error(f"DripStack purchase failed for {pub_slug}/{post_slug}: {e}")
        return None
    finally:
        conn.close()
```

### 3e — Pipeline handoff

```python
def html_to_text(html: str) -> str:
    """Strip HTML tags for pipeline ingestion."""
    soup = BeautifulSoup(html, "html.parser")
    return soup.get_text(separator="\n", strip=True)


def ingest_to_pipeline(article_data: dict, pub_slug: str, post_slug: str) -> int | None:
    """
    Insert purchased article content into the documents table for extraction.
    Returns document_id or None on failure.
    Tags source_type as 'dripstack' so velocity scorer can weight appropriately.
    """
    content_html = article_data.get("contentHtml", "")
    content_text = html_to_text(content_html)
    title = article_data.get("title", "")
    url = article_data.get("url") or f"https://{pub_slug}/p/{post_slug}"

    content_hash = hashlib.sha256(content_text.encode()).hexdigest()

    conn = sqlite3.connect(DB_PATH)
    try:
        # Check for duplicate content
        existing = conn.execute(
            "SELECT id FROM documents WHERE content_hash = ?", (content_hash,)
        ).fetchone()
        if existing:
            logger.info(f"Content already in pipeline (hash match): {title}")
            return existing[0]

        cursor = conn.execute(
            """INSERT INTO documents
               (title, url, content, content_hash, source_type, source_name, fetched_at)
               VALUES (?, ?, ?, ?, 'dripstack', ?, ?)""",
            (title, url, content_text, content_hash, pub_slug, datetime.now(timezone.utc).isoformat())
        )
        doc_id = cursor.lastrowid

        # Update purchase record with document_id
        conn.execute(
            "UPDATE dripstack_purchases SET ingested = TRUE, document_id = ? WHERE publication_slug = ? AND post_slug = ?",
            (doc_id, pub_slug, post_slug)
        )
        conn.commit()
        logger.info(f"Ingested '{title}' → document_id={doc_id}")
        return doc_id

    except Exception as e:
        logger.error(f"Pipeline ingestion failed: {e}")
        return None
    finally:
        conn.close()
```

### 3f — Primary entry point

```python
def buy_for_theme(theme_key: str, sector: str, max_articles: int = 2) -> list[int]:
    """
    Main trigger: given a high-velocity thin-sourced theme, find and buy relevant articles.
    Returns list of document_ids ingested.
    Called by extraction_worker.py when source_count is low.
    """
    topic = SECTOR_TO_TOPIC.get(sector, "finance")
    publications = query_catalog(topic)

    if not publications:
        logger.info(f"No DripStack publications match topic '{topic}' for theme '{theme_key}'")
        return []

    ingested_ids = []
    for pub in publications[:3]:  # check top 3 matching publications
        pub_slug = pub["slug"]
        posts = get_publication_posts(pub_slug)

        for post in posts[:max_articles]:
            post_slug = post["slug"]

            article_data = purchase_article(
                pub_slug, post_slug,
                trigger_theme=theme_key,
                trigger_sector=sector
            )
            if not article_data:
                continue

            doc_id = ingest_to_pipeline(article_data, pub_slug, post_slug)
            if doc_id:
                ingested_ids.append(doc_id)

            if len(ingested_ids) >= max_articles:
                break

        if len(ingested_ids) >= max_articles:
            break

    return ingested_ids


def buy_by_topic(topic: str, max_articles: int = 3) -> list[int]:
    """
    Manual trigger: called from Telegram command handler.
    /dripstack <topic> → buy_by_topic(topic)
    """
    publications = query_catalog(topic)
    if not publications:
        logger.info(f"No publications found for topic '{topic}'")
        return []

    ingested_ids = []
    for pub in publications[:2]:
        pub_slug = pub["slug"]
        posts = get_publication_posts(pub_slug)
        for post in posts[:2]:
            article_data = purchase_article(pub_slug, post["slug"])
            if article_data:
                doc_id = ingest_to_pipeline(article_data, pub_slug, post["slug"])
                if doc_id:
                    ingested_ids.append(doc_id)
            if len(ingested_ids) >= max_articles:
                break
        if len(ingested_ids) >= max_articles:
            break

    return ingested_ids
```

### 3g — Verify final syntax

```bash
python3 -c "import ast; ast.parse(open('src/dripstack_buyer.py').read()); print('SYNTAX OK')"
```

---

## Step 4 — Trigger Wiring in `extraction_worker.py`

After a theme is processed, check if it has thin sourcing and high velocity. If so, call `buy_for_theme`.

Find the post-extraction block in `extraction_worker.py`. After theme velocity is updated, add:

```python
from dripstack_buyer import buy_for_theme

# After velocity scoring:
if theme.get("velocity_score", 0) >= 7 and theme.get("source_count", 999) < 3:
    logger.info(f"Thin-sourced high-velocity theme '{theme_key}' — querying DripStack")
    doc_ids = buy_for_theme(theme_key=theme_key, sector=theme.get("sector", "macro"))
    if doc_ids:
        logger.info(f"DripStack ingested {len(doc_ids)} articles for theme '{theme_key}'")
```

Read `extraction_worker.py` before adding this — find the exact location where velocity scoring completes and insert there. Do not add to the scheduler or any cron path.

---

## Step 5 — Telegram Command Handler

In the Hermes investing profile skill or the signals listener, wire `/dripstack <topic>`:

```python
from dripstack_buyer import buy_by_topic

# In command handler:
if text.startswith("/dripstack "):
    topic = text.replace("/dripstack ", "").strip()
    if not topic:
        await send("Usage: /dripstack <topic>")
        return
    doc_ids = buy_by_topic(topic)
    if doc_ids:
        await send(f"DripStack: bought {len(doc_ids)} articles on '{topic}' → queued for extraction")
    else:
        await send(f"DripStack: no articles found for '{topic}' or spend limit reached")
```

---

## Step 6 — Publications to Import

These are T1 RSS sources already in the pipeline. Import them so Remi can buy their paywalled posts that don't appear in the free RSS feed:

```bash
curl "https://dripstack.xyz/import/https://steno.substack.com"
curl "https://dripstack.xyz/import/https://prometheusresearch.substack.com"
curl "https://dripstack.xyz/import/https://crossbordercapital.substack.com"
curl "https://dripstack.xyz/import/https://www.lynalden.com"
```

Verify each appears in the catalog after import.

---

## Step 7 — Environment Variables

Add to `~/remi-intelligence/.env`:

```bash
# DripStack / x402 payments
REMI_WALLET_PRIVATE_KEY=<private key — retrieve from secure offline storage>
DRIPSTACK_DAILY_LIMIT_USD=0.50
```

**Do not put the private key in any handoff document or chat. Retrieve from secure offline storage when wiring this up.**

---

## Dependencies

Check before building:

```bash
pip3 show web3 httpx beautifulsoup4
```

Install if missing:
```bash
pip3 install web3 httpx beautifulsoup4 --break-system-packages
```

---

## Build Order Summary

| Step | Action | Verify |
|---|---|---|
| 0 | Fetch and read openapi.json | File exists, routes match SKILL.md |
| 1 | Create BogWizard Substack + import | Publication appears in catalog |
| 2 | Create SQLite table | `.schema dripstack_purchases` returns schema |
| 3a | Write imports/constants | `ast.parse` SYNTAX OK |
| 3b | Write spend guard functions | `ast.parse` SYNTAX OK |
| 3c | Write catalog query functions | `ast.parse` SYNTAX OK |
| 3d | Stub x402 handler (read spec first) | `ast.parse` SYNTAX OK |
| 3e | Write pipeline handoff | `ast.parse` SYNTAX OK |
| 3f | Write entry points | `ast.parse` SYNTAX OK |
| 3g | Final syntax check | SYNTAX OK |
| 4 | Wire trigger in extraction_worker | Read file before editing |
| 5 | Wire Telegram command | Test with `/dripstack macro` |
| 6 | Import T1 publications | Each appears in catalog |
| 7 | Add env vars | `grep REMI_WALLET` in .env |

---

## Known Unknowns (Resolve at Step 0)

- Exact 402 response format — headers vs body, payment challenge structure
- Whether x402 uses ETH directly or a specific Base token
- Per-post price override mechanism (can Remi charge more than $0.01 for BogWizard articles?)
- Whether DripStack's import endpoint requires authentication

All of these are in `openapi.json`. Read it before writing the x402 handler.

---

*Spec: 2026-05-03*
*Depends on: llm_extractor.py, extraction_worker.py, documents table, velocity_scorer.py*
*Blocks: BogWizard Substack publish pipeline*

"""
dripstack_buyer.py — DripStack x402 article purchaser

Queries DripStack catalog by topic, purchases articles via x402 micropayments,
ingests content into the Remi intelligence pipeline.

OpenAPI spec summary at: ~/remi-intelligence/specs/dripstack-openapi-summary.md
"""

import os
import re
import json
import time
import sqlite3
import logging
import hashlib
import secrets
import base64
import subprocess
import httpx
from datetime import datetime, timezone
from pathlib import Path
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

DRIPSTACK_BASE = "https://dripstack.xyz"
DB_PATH = Path(__file__).parent.parent / "remi_intelligence.db"

# Wallet for x402 payments — load private key from env, never hardcode
WALLET_PRIVATE_KEY = os.environ.get("REMI_WALLET_PRIVATE_KEY")
WALLET_ADDRESS = os.environ.get("REMI_WALLET_ADDRESS", "0x316252829cd5fDFd2aB4e17E669C8CE8a42794F6")

# Spend guard — never spend more than this per day on DripStack
DAILY_SPEND_LIMIT_USD = float(os.environ.get("DRIPSTACK_DAILY_LIMIT_USD", "0.50"))

# Per-article price ceiling — abort if 402 challenge exceeds this
MAX_ARTICLE_PRICE_USD = 1.00

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


# ---------------------------------------------------------------------------
# Spend guard
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# Catalog query
# ---------------------------------------------------------------------------

def query_catalog(topic: str) -> list[dict]:
    """
    Fetch all indexed publications and return those matching the topic.
    Never returns the full catalog unfiltered.
    """
    resp = httpx.get(f"{DRIPSTACK_BASE}/api/v1/publications", timeout=15)
    resp.raise_for_status()
    body = resp.json()
    publications = body.get("publications", body) if isinstance(body, dict) else body

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
        logger.info(f"Publication {pub_slug} not indexed — attempting import")
        import_resp = httpx.post(
            f"{DRIPSTACK_BASE}/api/v1/publications/{pub_slug}",
            json={"forceRefresh": True}, timeout=30
        )
        logger.info(f"Import response: {import_resp.status_code}")
        resp = httpx.get(f"{DRIPSTACK_BASE}/api/v1/publications/{pub_slug}", timeout=15)
    resp.raise_for_status()
    pub_data = resp.json()
    return pub_data.get("posts", [])


# ---------------------------------------------------------------------------
# x402 payment handler
# ---------------------------------------------------------------------------

def _parse_402_challenge(resp: httpx.Response) -> dict | None:
    """
    Parse the 402 Payment Required response from DripStack.
    
    DripStack returns:
    - PAYMENT-REQUIRED header: base64-encoded JSON with x402 v2 payment requirements
    - application/problem+json body with {type, title, status, detail, challengeId}
    
    Returns challenge dict with price info and payment requirements, or None.
    """
    # Parse body for challengeId
    try:
        body = resp.json()
    except Exception:
        logger.error(f"402 response body not JSON: {resp.text[:200]}")
        return None

    challenge_id = body.get("challengeId")
    if not challenge_id:
        logger.error(f"402 response missing challengeId: {body}")
        return None

    # Parse PAYMENT-REQUIRED header (base64 JSON with x402 v2 requirements)
    pr_header = resp.headers.get("payment-required", "")
    if not pr_header:
        logger.error("402 response missing PAYMENT-REQUIRED header")
        return None

    try:
        requirements = json.loads(base64.b64decode(pr_header))
    except Exception as e:
        logger.error(f"Failed to decode PAYMENT-REQUIRED header: {e}")
        return None

    accepts = requirements.get("accepts", [])
    if not accepts:
        logger.error("PAYMENT-REQUIRED has no accepts")
        return None

    payment_req = accepts[0]
    # USDC has 6 decimals
    amount_raw = int(payment_req.get("amount", "0"))
    price_usd = amount_raw / 1_000_000

    return {
        "challenge_id": challenge_id,
        "price": price_usd,
        "amount_raw": amount_raw,
        "scheme": payment_req.get("scheme", "exact"),
        "network": payment_req.get("network", "eip155:8453"),
        "asset": payment_req.get("asset", ""),
        "pay_to": payment_req.get("payTo", ""),
        "max_timeout_seconds": payment_req.get("maxTimeoutSeconds", 300),
        "extra": payment_req.get("extra", {}),
        "body": body,
    }


def _sign_x402_payment(challenge: dict) -> dict:
    """
    Sign an x402 payment challenge using EIP-3009 transferWithAuthorization.
    
    Constructs an EIP-712 typed data signature for gasless USDC transfer on Base.
    Returns dict of headers to add to the retry request.
    
    Payment flow:
    1. Build authorization {from, to, value, validAfter, validBefore, nonce}
    2. Sign with EIP-712 typed data (domain = USDC contract on Base)
    3. Package into x402 v2 payload, base64 encode
    4. Return as PAYMENT-SIGNATURE header
    """
    if not WALLET_PRIVATE_KEY:
        raise EnvironmentError("REMI_WALLET_PRIVATE_KEY not set in environment")

    from web3 import Web3

    w3 = Web3()

    # USDC on Base mainnet
    USDC_CONTRACT = "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913"
    BASE_CHAIN_ID = 8453

    # Build authorization
    from_addr = w3.to_checksum_address(WALLET_ADDRESS)
    to_addr = w3.to_checksum_address(challenge["pay_to"])
    value = challenge["amount_raw"]
    now = int(time.time())
    valid_after = now
    valid_before = now + challenge.get("max_timeout_seconds", 300)
    nonce_bytes = secrets.token_bytes(32)
    nonce_hex = "0x" + nonce_bytes.hex()

    # EIP-712 domain (USDC contract)
    domain = {
        "name": challenge.get("extra", {}).get("name", "USD Coin"),
        "version": challenge.get("extra", {}).get("version", "2"),
        "chainId": BASE_CHAIN_ID,
        "verifyingContract": USDC_CONTRACT,
    }

    # EIP-712 types for transferWithAuthorization
    types = {
        "TransferWithAuthorization": [
            {"name": "from", "type": "address"},
            {"name": "to", "type": "address"},
            {"name": "value", "type": "uint256"},
            {"name": "validAfter", "type": "uint256"},
            {"name": "validBefore", "type": "uint256"},
            {"name": "nonce", "type": "bytes32"},
        ]
    }

    # All values as hex/str — bytes32 needs "0x..." hex string for encode_typed_data
    message = {
        "from": from_addr,
        "to": to_addr,
        "value": value,
        "validAfter": valid_after,
        "validBefore": valid_before,
        "nonce": nonce_hex,
    }

    # Sign with EIP-712
    private_key = WALLET_PRIVATE_KEY
    if not private_key.startswith("0x"):
        private_key = "0x" + private_key

    from eth_account.messages import encode_typed_data
    structured = {
        "types": types,
        "domain": domain,
        "primaryType": "TransferWithAuthorization",
        "message": message,
    }

    # DEBUG: print full EIP-712 payload before signing
    logger.debug("=" * 60)
    logger.debug("EIP-712 STRUCTURED DATA:")
    logger.debug(f"  domain: {json.dumps(domain)}")
    logger.debug(f"  types: {json.dumps(types, default=str)}")
    logger.debug(f"  primaryType: TransferWithAuthorization")
    logger.debug(f"  message: {json.dumps(message, default=str)}")
    logger.debug(f"  signer: {from_addr}")
    logger.debug(f"  private_key: {private_key[:8]}...{private_key[-4:]}")
    logger.debug("=" * 60)

    encoded = encode_typed_data(full_message=structured)
    signed = w3.eth.account.sign_message(encoded, private_key=private_key)

    # Build x402 v2 payment payload
    authorization = {
        "from": from_addr,
        "to": to_addr,
        "value": str(value),
        "validAfter": str(valid_after),
        "validBefore": str(valid_before),
        "nonce": nonce_hex,
    }

    payload = {
        "x402Version": 2,
        "scheme": challenge.get("scheme", "exact"),
        "network": challenge.get("network", "eip155:8453"),
        "payload": {
            "signature": signed.signature.hex(),
            "authorization": authorization,
        },
    }

    # Base64 encode for header
    payload_json = json.dumps(payload, separators=(",", ":"))
    payload_b64 = base64.b64encode(payload_json.encode()).decode()

    # DEBUG: print full payment payload
    logger.debug("X-PAYMENT PAYLOAD (decoded):")
    logger.debug(f"  {json.dumps(payload, indent=2)}")
    logger.debug(f"X-PAYMENT HEADER (base64, first 300 chars):")
    logger.debug(f"  {payload_b64[:300]}")
    logger.debug(f"  signature hex ({len(signed.signature.hex())} chars): {signed.signature.hex()[:40]}...")
    logger.debug("=" * 60)

    logger.info(
        f"Signed x402 payment: {from_addr} → {to_addr}, "
        f"${challenge.get('price', 0):.2f} USDC, "
        f"nonce={nonce_hex[:18]}..."
    )

    return {"X-PAYMENT": payload_b64}


def _node_bridge_buy(pub_slug: str, post_slug: str) -> dict | None:
    """Call the Node.js x402 bridge to purchase an article. Returns parsed JSON or None."""
    bridge_script = os.path.join(os.path.dirname(__file__), "dripstack_bridge.js")
    env = {**os.environ}
    try:
        result = subprocess.run(
            ["node", bridge_script, pub_slug, post_slug],
            capture_output=True, text=True, timeout=60, env=env,
        )
        if result.returncode != 0:
            logger.error(f"Bridge error: {result.stderr.strip()}")
            return None
        return json.loads(result.stdout)
    except subprocess.TimeoutExpired:
        logger.error(f"Bridge timeout for {pub_slug}/{post_slug}")
        return None
    except json.JSONDecodeError as e:
        logger.error(f"Bridge returned invalid JSON: {e}")
        return None


def purchase_article(pub_slug: str, post_slug: str,
                     trigger_theme: str = None, trigger_sector: str = None) -> dict | None:
    """
    Purchase a single article via x402 (Node.js bridge). Returns dict with title and contentHtml,
    or None on failure / price too high / spend limit reached.
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

        # Pre-flight: check price via free 402 challenge
        url = f"{DRIPSTACK_BASE}/api/v1/publications/{pub_slug}/{post_slug}"
        resp = httpx.get(url, timeout=15)
        if resp.status_code == 402:
            challenge = _parse_402_challenge(resp)
            if not challenge:
                logger.error(f"Could not parse 402 challenge for {pub_slug}/{post_slug}")
                return None
            price = challenge.get("price")
            if price is not None and price > MAX_ARTICLE_PRICE_USD:
                logger.warning(
                    f"Article price ${price:.2f} exceeds ceiling ${MAX_ARTICLE_PRICE_USD:.2f} "
                    f"— aborting {pub_slug}/{post_slug}"
                )
                return None
            if price is None:
                logger.warning(f"Could not determine price from 402 challenge — aborting {pub_slug}/{post_slug}")
                return None
            actual_price = price
        else:
            actual_price = 0.01

        # Purchase via Node.js x402 bridge
        article_data = _node_bridge_buy(pub_slug, post_slug)
        if not article_data:
            return None

        # Record purchase
        conn.execute(
            """INSERT OR IGNORE INTO dripstack_purchases
               (publication_slug, post_slug, title, cost_usd, trigger_theme, trigger_sector)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (pub_slug, post_slug, article_data.get("title"), actual_price, trigger_theme, trigger_sector)
        )
        conn.commit()

        logger.info(f"Purchased: {article_data.get('title')} from {pub_slug} (${actual_price})")
        return article_data

    except Exception as e:
        logger.error(f"DripStack purchase failed for {pub_slug}/{post_slug}: {e}")
        return None
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Pipeline handoff
# ---------------------------------------------------------------------------

def html_to_text(html: str) -> str:
    """Strip HTML tags for pipeline ingestion."""
    soup = BeautifulSoup(html, "html.parser")
    return soup.get_text(separator="\n", strip=True)


def ingest_to_pipeline(article_data: dict, pub_slug: str, post_slug: str) -> int | None:
    """
    Insert purchased article content into the documents table for extraction.
    Returns document_id or None on failure.
    Uses real schema: source_url, source_name, source_tier, source_type,
                      title, content_text, content_hash, published_at, ingested_at
    source_type='paywalled_substack', source_tier=0 (external paid source).
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

        # published_at from article data, or use current time
        published_at = article_data.get("publishedAt") or datetime.now(timezone.utc).isoformat()

        cursor = conn.execute(
            """INSERT INTO documents
               (source_url, source_name, source_tier, source_type,
                title, content_text, content_hash, published_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (url, pub_slug, 0, "paywalled_substack",
             title, content_text, content_hash, published_at)
        )
        doc_id = cursor.lastrowid

        # Update purchase record with document_id
        conn.execute(
            """UPDATE dripstack_purchases
               SET ingested = TRUE, document_id = ?
               WHERE publication_slug = ? AND post_slug = ?""",
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


# ---------------------------------------------------------------------------
# Entry points
# ---------------------------------------------------------------------------

def buy_for_theme(theme_key: str, sector: str, max_articles: int = 2) -> list[int]:
    """
    Automated trigger: given a high-velocity thin-sourced theme,
    find and buy relevant DripStack articles.
    Returns list of document_ids ingested.
    Called by extraction_worker.py when source_count is low.
    """
    topic = SECTOR_TO_TOPIC.get(sector, "finance")
    publications = query_catalog(topic)

    if not publications:
        logger.info(f"No DripStack publications match topic '{topic}' for theme '{theme_key}'")
        return []

    ingested_ids: list[int] = []
    for pub in publications[:3]:
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
    /dripstack <topic> -> buy_by_topic(topic)
    """
    publications = query_catalog(topic)
    if not publications:
        logger.info(f"No publications found for topic '{topic}'")
        return []

    ingested_ids: list[int] = []
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

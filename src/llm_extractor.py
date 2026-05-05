import json
import logging
import os
import re
import httpx
from dotenv import load_dotenv

load_dotenv(os.path.expanduser("~/remi-intelligence/.env"))

logger = logging.getLogger(__name__)

# --- LLM call infrastructure (Consuela → GLM → GLM-4.7 fallback) ---

GLM_API_KEY = os.environ.get("GLM_API_KEY", "")
GLM_BASE_URL = os.environ.get("GLM_BASE_URL", "https://api.z.ai/api/coding/paas/v4")
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")

FALLBACK_MODEL = "glm-4.7"


def clean_json(text: str) -> str:
    """Strip markdown fences and extract JSON."""
    text = re.sub(r"```json\s*", "", text)
    text = re.sub(r"```\s*", "", text)
    return text.strip()


VALID_SECTORS = {"geopolitical", "macro", "fed", "credit", "energy",
                 "metals", "agriculture", "crypto", "ai", "equities", "fiscal", "fx"}

_SECTOR_ALIASES = {
    "geo": "geopolitical", "geopolitics": "geopolitical", "politics": "geopolitical",
    "rates": "fed", "federal_reserve": "fed", "monetary_policy": "fed",
    "bonds": "credit", "fixed_income": "credit", "spreads": "credit",
    "oil": "energy", "energy": "energy", "gas": "energy", "natgas": "energy", "lng": "energy",
    "gold": "metals", "silver": "metals", "copper": "metals", "steel": "metals", "iron": "metals",
    "rare_earth": "metals", "uranium": "metals", "minerals": "metals",
    "wheat": "agriculture", "corn": "agriculture", "soy": "agriculture", "sugar": "agriculture",
    "ethanol": "agriculture", "coffee": "agriculture", "cotton": "agriculture", "grains": "agriculture",
    "commodities": "energy", "commodity": "energy",
    "bitcoin": "crypto", "defi": "crypto", "blockchain": "crypto",
    "semiconductor": "ai", "semiconductors": "ai", "chips": "ai", "compute": "ai",
    "stocks": "equities", "earnings": "equities",
    "deficit": "fiscal", "spending": "fiscal", "treasury": "fiscal",
    "dollar": "fx", "currency": "fx", "currencies": "fx",
}


def normalize_sector(raw: str) -> str:
    """Normalize LLM-returned sector to valid taxonomy value."""
    cleaned = raw.strip().lower().replace(" ", "_")
    if cleaned in VALID_SECTORS:
        return cleaned
    return _SECTOR_ALIASES.get(cleaned, "macro")


def validate_extracted_theme(theme: dict) -> dict:
    """Validate and normalize a single extracted theme."""
    theme["sector"] = normalize_sector(theme.get("sector", "macro"))
    key = theme.get("theme_key", "unknown").strip().lower()
    key = re.sub(r'[^a-z0-9-]', '-', key)
    key = re.sub(r'-+', '-', key).strip('-')
    theme["theme_key"] = key
    return theme


def call_glm(messages: list[dict], max_tokens: int = 2000,
             model: str = "glm-5", temperature: float = 0.2) -> tuple[str, str] | None:
    """Call GLM API with automatic fallback to GLM-4.7 on 429.

    Args:
        messages: OpenAI-style messages list [{"role": ..., "content": ...}]
        max_tokens: Max output tokens.
        model: Primary model string (e.g. "glm-5", "glm-5.1").
        temperature: Sampling temperature.

    Returns:
        (text_response, model_used) on success, None on failure.
    """
    if not GLM_API_KEY:
        logger.error("GLM_API_KEY not set — cannot call GLM")
        return None

    def _glm_request(m: str) -> httpx.Response:
        return httpx.post(
            f"{GLM_BASE_URL}/chat/completions",
            headers={"Authorization": f"Bearer {GLM_API_KEY}", "Content-Type": "application/json"},
            json={"model": m, "messages": messages,
                  "max_tokens": max_tokens, "temperature": temperature},
            timeout=300,
        )

    r = _glm_request(model)
    model_used = model

    if r.status_code == 429:
        logger.warning(f"429 on {model} — falling back to {FALLBACK_MODEL}")
        r = _glm_request(FALLBACK_MODEL)
        model_used = FALLBACK_MODEL

    if r.status_code == 429:
        logger.error(f"429 persists on {FALLBACK_MODEL} — giving up")
        return None
    elif r.status_code != 200:
        logger.error(f"GLM call failed ({model_used}): {r.status_code} {r.text[:300]}")
        return None

    logger.info(f"GLM call succeeded via {model_used}")
    raw = r.json()["choices"][0]["message"]["content"]

    # GLM-5 is a reasoning model — check both content and reasoning_content
    if not raw:
        reasoning = r.json()["choices"][0]["message"].get("reasoning_content", "")
        if reasoning:
            logger.warning("Content empty, checking reasoning_content")
            raw = reasoning

    return raw, model_used


def _call_llm(prompt: str, max_tokens: int = 2000, model: str = "glm-5",
              skip_local: bool = False, force_local: bool = False) -> str | None:
    """Call LLM with Consuela → GLM → GLM-4.7 fallback chain.

    Args:
        skip_local: Skip Consuela (local Gemma). Use for tasks where the prompt
            is too long or latency-sensitive for local inference.
        force_local: Always try Consuela regardless of prompt size. Falls back
            to GLM only if Consuela fails or returns non-200.

    Returns raw text response or None on failure.
    """
    messages = [{"role": "user", "content": prompt}]

    # Tier 1: Consuela (local Gemma)
    # Default: only for short prompts with modest output needs
    # force_local: always try, regardless of size (for chunked extraction)
    use_consuela = (not skip_local) and (force_local or (len(prompt) < 4000 and max_tokens <= 500))
    if use_consuela:
        try:
            local_timeout = 300.0 if force_local else 120.0  # Longer timeout for big prompts
            local_r = httpx.post(
                "http://127.0.0.1:8080/v1/chat/completions",
                json={"model": "gemma", "messages": messages,
                      "max_tokens": max_tokens, "temperature": 0.2},
                timeout=local_timeout,
            )
            if local_r.status_code == 200:
                logger.info(f"LLM call succeeded via Consuela (local) — {len(prompt)} chars")
                return local_r.json()["choices"][0]["message"]["content"]
            logger.info(f"Consuela returned {local_r.status_code}, falling back to GLM")
        except Exception as e:
            logger.info(f"Consuela unavailable ({e}), falling back to GLM")

    result = call_glm(messages, max_tokens, model)
    return result[0] if result else None


# --- Extraction functions ---

def extract_themes(content: str, source_name: str, source_tier: int,
                   gli_context: str = "GLI context unavailable",
                   active_anchors_block: str = "",
                   max_content_len: int = 8000,
                   force_local: bool = False) -> dict:
    """Extract themes, facts, opinions from a document."""
    prompt = f"""You are a macro investment research analyst.

Analyze the following document and extract structured intelligence.

DOCUMENT SOURCE: {source_name} (Tier {source_tier})
CURRENT GLI CONTEXT: {gli_context}

{active_anchors_block}

DOCUMENT CONTENT:
{content[:max_content_len]}

---

Return a JSON object with this exact structure:

{{
  "themes": [
    {{
      "theme_key": "lowercase-hyphenated-key",
      "theme_label": "Human Readable Label",
      "sector": "geopolitical|macro|fed|credit|energy|metals|agriculture|crypto|ai|equities|fiscal|fx",
      "summary": "2-3 sentence description of this theme as discussed in the document",
      "facts": [
        "Specific data point, number, or verifiable claim (no hedging language)"
      ],
      "opinions": [
        "Forecast, estimate, price target, or speculative claim"
      ],
      "tickers_mentioned": ["TICKER1", "TICKER2"],
      "sentiment": "bullish|bearish|neutral|mixed",
      "key_quote": "Single most important sentence from the document on this theme"
    }}
  ],
  "overall_document_summary": "2-3 sentence summary of the entire document",
  "narrative_saturation": "low|medium|high",
  "regime_alignment": "aligned|divergent|neutral",
  "regime_alignment_rationale": "One sentence explaining alignment"
}}

RULES:
- THEME CONSOLIDATION IS CRITICAL: If this article discusses a topic already covered by an
  active anchor above, you MUST reuse that exact theme_key. Do NOT create a near-duplicate.
  Example: if "iran-hormuz-oil-supply-disruption" exists, do NOT create
  "strait-of-hormuz-closure-impact" or "geopolitical-risk-energy-crisis" — reuse the anchor.
- Only create a new theme_key when the article covers a genuinely NOVEL topic not in the anchors.
- sector: classify each theme into exactly ONE sector from this closed list:
  geopolitical, macro, fed, credit, energy, metals, agriculture, crypto, ai, equities, fiscal, fx
  Pick the PRIMARY driver. If a theme spans sectors, choose the sector of the causal trigger.
  Example: "China bans rare earth exports" → geopolitical (policy action is the catalyst)
  Example: "Fed holds rates, signals data-dependent" → fed
  Example: "HY spreads blow out on bank stress" → credit
  Example: "NVIDIA earnings crush, data center demand" → ai (not equities — AI buildout is the theme)
  Example: "Oil supply disruption from Hormuz" → energy (not geopolitical — commodity impact is primary)
  Example: "Copper deficit widens on electrification" → metals
  Example: "Brazil sugar crop failure drives prices" → agriculture
- macro is a NARROW category — ONLY use it for broad economic data prints (GDP, CPI, PPI, PMI,
  employment, recession indicators) and non-Fed central bank policy decisions (PBOC, ECB, BOJ, BOE).
  If a theme has ANY more specific sector, use that instead. Iran sanctions disrupting oil supply is
  geopolitical, not macro. Fed cutting rates is fed, not macro. Credit spreads widening is credit,
  not macro. When in doubt, do NOT default to macro — pick the sector of the causal trigger.
- Facts: actual data, earnings numbers, official statistics, confirmed events
- Opinions: any forecast, target, valuation, or speculative scenario
- Extract 1-5 themes maximum — quality over quantity
- narrative_saturation: low = niche/early, medium = gaining traction, high = widely discussed
- regime_alignment: does the document's thesis support or contradict the current GLI regime?
- Return only valid JSON, no preamble or markdown fences"""

    try:
        raw = _call_llm(prompt, max_tokens=4000, force_local=force_local)
        if raw is None:
            return None
        result = json.loads(clean_json(raw))
        # Validate each theme's sector and theme_key format
        for theme in result.get("themes", []):
            validate_extracted_theme(theme)
        return result
    except json.JSONDecodeError as e:
        logger.error(f"JSON parse error for {source_name}: {e}")
        return {"themes": [], "overall_document_summary": "", "narrative_saturation": "low",
                "regime_alignment": "neutral", "regime_alignment_rationale": ""}
    except Exception as e:
        logger.error(f"Theme extraction error for {source_name}: {e}")
        return None


def extract_second_order(theme_label: str, theme_summary: str,
                         source_name: str, gli_context: str = "GLI context unavailable") -> dict:
    """Run second-order supply chain inference. Uses higher-capacity model."""
    prompt = f"""You are a commodity supply chain and geopolitical risk analyst.

A significant theme has been detected:
TRIGGER THEME: {theme_label}
TRIGGER SUMMARY: {theme_summary}
TRIGGER SOURCE: {source_name}

CURRENT MACRO REGIME: {gli_context}

Identify what the crowd is NOT talking about yet. Surface second and third-order
effects that appear in niche trade publications BEFORE they reach mainstream media.

Return a JSON object:
{{
  "primary_impact": {{
    "description": "Direct first-order impact",
    "already_priced": true,
    "tickers": ["TICKER1"]
  }},
  "second_order": [
    {{
      "description": "Supply chain dependency affected",
      "mechanism": "How primary flows to this",
      "time_lag_days": 30,
      "tickers": ["TICKER1"],
      "confidence": "high|medium|low"
    }}
  ],
  "third_order": [
    {{
      "description": "End market effect",
      "mechanism": "How second-order flows to this",
      "time_lag_days": 90,
      "tickers": ["TICKER1"],
      "confidence": "high|medium|low"
    }}
  ],
  "key_variables_to_monitor": ["Specific metric to watch"],
  "perishable": true,
  "perishable_rationale": "Why time-sensitive or structural",
  "narrative_saturation_estimate": "low|medium|high",
  "regime_conditional_note": "Which plays are regime-supported vs headwind"
}}

Return only valid JSON."""

    try:
        raw = _call_llm(prompt, max_tokens=4000)
        if raw is None:
            return None
        return json.loads(clean_json(raw))
    except json.JSONDecodeError as e:
        logger.error(f"JSON parse error in second_order for {theme_label}: {e}")
        return None
    except Exception as e:
        logger.error(f"Second order extraction error: {e}")
        return None


def detect_delta(theme_label: str, previous_summary: str,
                 current_summary: str, days_ago: int = 7) -> dict:
    """Detect what changed in a theme."""
    prompt = f"""You are a macro research analyst tracking narrative evolution.

THEME: {theme_label}

PREVIOUS STATE ({days_ago} days ago):
{previous_summary}

CURRENT STATE:
{current_summary}

Return a JSON object:
{{
  "has_changed": true,
  "change_significance": "major|moderate|minor|none",
  "new_facts": ["genuinely new fact"],
  "sentiment_shift": "bullish_to_bearish|bearish_to_bullish|no_change|mixed",
  "new_tickers": ["TICKER"],
  "saturation_change": "accelerating|peaking|fading|stable",
  "delta_summary": "2-3 sentences on what changed",
  "action_implication": "What this means for thesis confidence"
}}

Return only valid JSON."""

    try:
        raw = _call_llm(prompt, max_tokens=2000)
        if raw is None:
            return None
        return json.loads(clean_json(raw))
    except Exception as e:
        logger.error(f"Delta detection error: {e}")
        return None


# --- Chunked extraction for long transcripts ---

def _split_chunks(text: str, chunk_size: int = 12000, overlap: int = 1000) -> list[str]:
    """Split text into overlapping chunks. Yields chunks of ~chunk_size chars."""
    if len(text) <= chunk_size:
        return [text]
    chunks = []
    start = 0
    while start < len(text):
        end = start + chunk_size
        chunks.append(text[start:end])
        start += chunk_size - overlap
    return chunks


def _merge_themes(all_results: list[dict]) -> dict:
    """Merge theme extraction results from multiple chunks.

    Deduplicates by theme_key, combining facts/opinions/quotes.
    Uses the most detailed version of each theme as the base.
    """
    seen_keys = {}  # theme_key -> index in merged_themes
    merged_themes = []

    for result in all_results:
        for theme in result.get("themes", []):
            key = theme.get("theme_key", "unknown")
            if key in seen_keys:
                # Merge into existing — append unique facts/opinions/quotes/tickers
                idx = seen_keys[key]
                existing = merged_themes[idx]
                # Extend facts (dedup by exact string match)
                for f in theme.get("facts", []):
                    if f not in existing.get("facts", []):
                        existing.setdefault("facts", []).append(f)
                # Extend opinions
                for o in theme.get("opinions", []):
                    if o not in existing.get("opinions", []):
                        existing.setdefault("opinions", []).append(o)
                # Extend tickers
                for t in theme.get("tickers_mentioned", []):
                    if t not in existing.get("tickers_mentioned", []):
                        existing.setdefault("tickers_mentioned", []).append(t)
                # Keep longer summary
                if len(theme.get("summary", "")) > len(existing.get("summary", "")):
                    existing["summary"] = theme["summary"]
                # Keep longer key_quote
                if len(theme.get("key_quote", "")) > len(existing.get("key_quote", "")):
                    existing["key_quote"] = theme["key_quote"]
                # Keep the more specific sentiment
                if theme.get("sentiment", "neutral") != "neutral":
                    existing["sentiment"] = theme["sentiment"]
            else:
                seen_keys[key] = len(merged_themes)
                merged_themes.append(dict(theme))

    # Cap facts/opinions per theme to keep output sane
    for t in merged_themes:
        t["facts"] = t.get("facts", [])[:8]
        t["opinions"] = t.get("opinions", [])[:6]
        t["tickers_mentioned"] = t.get("tickers_mentioned", [])[:10]

    # Pick the best summary and metadata from the most productive chunk
    best_result = max(all_results, key=lambda r: len(r.get("themes", [])))

    return {
        "themes": merged_themes,
        "overall_document_summary": best_result.get("overall_document_summary", ""),
        "narrative_saturation": best_result.get("narrative_saturation", "low"),
        "regime_alignment": best_result.get("regime_alignment", "neutral"),
        "regime_alignment_rationale": best_result.get("regime_alignment_rationale", ""),
    }


def extract_themes_chunked(content: str, source_name: str, source_tier: int,
                           gli_context: str = "GLI context unavailable",
                           chunk_size: int = 12000, overlap: int = 1000) -> dict:
    """Extract themes from long content by chunking, extracting per chunk, then merging.

    Designed for long-form transcripts (60+ min podcasts, multi-hour interviews).
    Each chunk gets its own extract_themes() call, then results are merged.
    """
    chunks = _split_chunks(content, chunk_size=chunk_size, overlap=overlap)
    logger.info(f"Chunked extraction: {len(content)} chars → {len(chunks)} chunks "
                f"(size={chunk_size}, overlap={overlap})")

    all_results = []
    for i, chunk in enumerate(chunks):
        logger.info(f"Extracting chunk {i+1}/{len(chunks)} ({len(chunk)} chars)")
        result = extract_themes(
            content=chunk,
            source_name=f"{source_name} [part {i+1}/{len(chunks)}]",
            source_tier=source_tier,
            gli_context=gli_context,
            max_content_len=chunk_size,  # Don't re-truncate within each chunk
            force_local=True,  # Prefer Consuela (local) for thorough podcast analysis
        )
        if result and result.get("themes"):
            all_results.append(result)
        else:
            logger.warning(f"Chunk {i+1} returned no themes")

    if not all_results:
        logger.warning("All chunks returned empty — falling back to single-pass on first 12K")
        return extract_themes(content, source_name, source_tier, gli_context,
                              max_content_len=12000) or {
            "themes": [], "overall_document_summary": "",
            "narrative_saturation": "low", "regime_alignment": "neutral",
            "regime_alignment_rationale": "",
        }

    merged = _merge_themes(all_results)
    logger.info(f"Chunked extraction merged: {len(merged.get('themes', []))} unique themes "
                f"from {len(all_results)} productive chunks")
    return merged


# --- Five-pass deep extraction (SOUL.md protocol) ---

def extract_five_pass(content: str, source_name: str,
                      gli_context: str = "GLI context unavailable",
                      active_themes: list = None,
                      max_content_len: int = 8000) -> dict:
    """Run the full 5-pass extraction protocol from SOUL.md.

    Passes:
      1. Framework extraction — author's core analytical framework
      2. Regime positioning — GLI/Steno alignment
      3. Second-order connections — cross-sector inferences
      4. Active position stress test — conviction vs doubt on tracked tickers/themes
      5. Mechanistic anchor — the single mechanical relationship to watch

    Returns a dict with all five passes, or None on failure.
    """
    themes_context = ""
    if active_themes:
        lines = ["ACTIVE TRACKED THEMES AND POSITIONS:"]
        for t in active_themes[:20]:
            key = t.get("theme_key", "?")
            label = t.get("theme_label", key)
            sector = t.get("sector", "macro")
            tickers = t.get("tickers_mentioned", [])
            ticker_str = ", ".join(tickers[:5]) if tickers else "none"
            lines.append(f'  - "{key}" [{sector}]: {label} (tickers: {ticker_str})')
        themes_context = "\n".join(lines)

    prompt = f"""You are a macro investment research analyst performing a deep 5-pass extraction.

DOCUMENT SOURCE: {source_name}
CURRENT GLI REGIME CONTEXT: {gli_context}

{themes_context}

DOCUMENT CONTENT:
{content[:max_content_len]}

---

Run ALL FIVE passes. Return a JSON object with this exact structure:

{{
  "pass_1_framework": {{
    "framework_name": "Descriptive name for the author's analytical framework",
    "core_premise": "1-2 sentence description of what the framework measures/produces",
    "inputs": ["Key input variable 1", "Key input variable 2"],
    "outputs": ["What the framework predicts/generates"],
    "persistence": "high|medium|low — how durable is this framework beyond the article's specific calls"
  }},
  "pass_2_regime_positioning": {{
    "gli_phase_alignment": "confirm|contradict|nuance",
    "gli_rationale": "1-2 sentences explaining how the framework's read maps to current GLI phase",
    "steno_regime_alignment": "confirm|contradict|nuance",
    "steno_rationale": "1-2 sentences on Steno regime fit",
    "regime_novel_signal": "Any signal in this article NOT captured by current Aestima read, or empty string"
  }},
  "pass_3_second_order": {{
    "cross_sector_inferences": [
      {{
        "sector": "affected sector",
        "mechanism": "How the article's thesis transmits to this sector",
        "ticker_implications": ["TICKER1"],
        "confidence": "high|medium|low"
      }}
    ],
    "minimum_two": true
  }},
  "pass_4_stress_test": {{
    "conviction_builds": [
      {{
        "theme_or_ticker": "THEME_KEY or TICKER",
        "why": "Why this article strengthens conviction"
      }}
    ],
    "conviction_doubts": [
      {{
        "theme_or_ticker": "THEME_KEY or TICKER",
        "why": "Why this article creates doubt or risk"
      }}
    ],
    "net_assessment": "1-2 sentence summary of whether the article is net bullish/bearish/neutral for tracked positions"
  }},
  "pass_5_mechanistic_anchor": {{
    "anchor_description": "The single most important mechanical relationship identified",
    "anchor_type": "ratio|level|spread|sequence|trigger",
    "current_value": "Current reading if mentioned, or 'not specified'",
    "watch_direction": "What direction signals the thesis is working vs breaking",
    "invalidation_condition": "Specific reading/event that breaks the author's thesis"
  }},
  "contradictions_with_vault": [
    {{
      "existing_framework": "Name of vault framework this contradicts",
      "nature_of_contradiction": "What specifically disagrees"
    }}
  ],
  "overall_assessment": "2-3 sentence synthesis of the article's intelligence value and positioning implications"
}}

RULES:
- pass_3 MUST have at least 2 cross-sector inferences — force yourself to find connections the author didn't discuss
- pass_4 MUST reference specific active themes/tickers from the tracked list where possible
- pass_5 is the most important output — the mechanistic anchor is what you'd put on a sticky note to monitor going forward
- contradictions_with_vault is MORE VALUABLE than confirmations — flag disagreements aggressively
- Return only valid JSON, no preamble or markdown fences"""

    try:
        raw = _call_llm(prompt, max_tokens=4000, skip_local=True, model="glm-4.7")
        if raw is None:
            return None
        return json.loads(clean_json(raw))
    except json.JSONDecodeError as e:
        logger.error(f"JSON parse error in five_pass for {source_name}: {e}")
        return None
    except Exception as e:
        logger.error(f"Five-pass extraction error for {source_name}: {e}")
        return None

import anthropic
import json
import logging
import os
import re
from dotenv import load_dotenv

load_dotenv(os.path.expanduser("~/remi-intelligence/.env"))

logger = logging.getLogger(__name__)

client = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))

HAIKU_MODEL = "claude-haiku-4-5-20251001"
SONNET_MODEL = "claude-sonnet-4-6"


def clean_json(text: str) -> str:
    """Strip markdown fences and extract JSON."""
    text = re.sub(r"```json\s*", "", text)
    text = re.sub(r"```\s*", "", text)
    return text.strip()


def extract_themes(content: str, source_name: str, source_tier: int,
                   gli_context: str = "GLI context unavailable") -> dict:
    """Extract themes, facts, opinions from a document. Uses Haiku."""
    prompt = f"""You are a macro investment research analyst.

Analyze the following document and extract structured intelligence.

DOCUMENT SOURCE: {source_name} (Tier {source_tier})
CURRENT GLI CONTEXT: {gli_context}

DOCUMENT CONTENT:
{content[:8000]}

Return a JSON object with this exact structure:
{{
  "themes": [
    {{
      "theme_key": "lowercase-hyphenated-key",
      "theme_label": "Human Readable Label",
      "summary": "2-3 sentence description",
      "facts": ["Specific data point or verifiable claim"],
      "opinions": ["Forecast, estimate, or speculative claim"],
      "tickers_mentioned": ["TICKER1"],
      "sentiment": "bullish|bearish|neutral|mixed",
      "key_quote": "Single most important sentence"
    }}
  ],
  "overall_document_summary": "2-3 sentence summary",
  "narrative_saturation": "low|medium|high",
  "regime_alignment": "aligned|divergent|neutral",
  "regime_alignment_rationale": "One sentence explanation"
}}

RULES:
- Extract 1-5 themes maximum
- Facts: actual data, statistics, confirmed events
- Opinions: any forecast, target, or speculative scenario
- Return only valid JSON, no preamble"""

    try:
        response = client.messages.create(
            model=HAIKU_MODEL,
            max_tokens=2000,
            messages=[{"role": "user", "content": prompt}]
        )
        raw = response.content[0].text
        return json.loads(clean_json(raw))
    except json.JSONDecodeError as e:
        logger.error(f"JSON parse error for {source_name}: {e}")
        return {"themes": [], "overall_document_summary": "", "narrative_saturation": "low",
                "regime_alignment": "neutral", "regime_alignment_rationale": ""}
    except Exception as e:
        logger.error(f"Theme extraction error for {source_name}: {e}")
        return None


def extract_second_order(theme_label: str, theme_summary: str,
                         source_name: str, gli_context: str = "GLI context unavailable") -> dict:
    """Run second-order supply chain inference. Uses Sonnet."""
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
        response = client.messages.create(
            model=SONNET_MODEL,
            max_tokens=2000,
            messages=[{"role": "user", "content": prompt}]
        )
        raw = response.content[0].text
        return json.loads(clean_json(raw))
    except json.JSONDecodeError as e:
        logger.error(f"JSON parse error in second_order for {theme_label}: {e}")
        return None
    except Exception as e:
        logger.error(f"Second order extraction error: {e}")
        return None


def detect_delta(theme_label: str, previous_summary: str,
                 current_summary: str, days_ago: int = 7) -> dict:
    """Detect what changed in a theme. Uses Haiku."""
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
        response = client.messages.create(
            model=HAIKU_MODEL,
            max_tokens=1000,
            messages=[{"role": "user", "content": prompt}]
        )
        raw = response.content[0].text
        return json.loads(clean_json(raw))
    except Exception as e:
        logger.error(f"Delta detection error: {e}")
        return None

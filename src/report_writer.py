"""
report_writer.py — Two-stage GLM→Gemma report generation pipeline.

Stage 1: GLM structures all available intelligence into a research brief (JSON)
Stage 2: Gemma 4 (local, port 8080) writes publication-quality prose from the brief

Entry points:
  - write_report(theme, ticker=None, trigger="manual") -> dict (report ready to insert)
  - post_to_aestima(report_dict) -> str | None (aestima_id)
  - save_draft_to_dashboard(report_dict) -> int | None (report id)
  - Used by: dashboard publish handler (manual) and autonomous trigger (picks/narrative)
"""

import os
import json
import logging
import httpx
import psycopg2
from psycopg2.extras import RealDictCursor
from datetime import datetime, timezone
from pathlib import Path
from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent.parent / ".env", override=True)
logger = logging.getLogger(__name__)

# GLM config
GLM_API_KEY = os.getenv("GLM_API_KEY", "")
GLM_BASE_URL = os.getenv("GLM_BASE_URL", "https://api.z.ai/api/paas/v4")
GLM_MODEL = "glm-5"

# Gemma config
GEMMA_ENDPOINT = "http://127.0.0.1:8080/v1/chat/completions"
GEMMA_MODEL = "gemma-4-26B-A4B-it-UD-Q4_K_XL.gguf"

# Aestima
AESTIMA_URL = "https://aestima.ai"
AESTIMA_AGENT_KEY = os.getenv("AESTIMA_AGENT_KEY", "")
DASHBOARD_DATABASE_URL = os.getenv("DASHBOARD_DATABASE_URL", "")


# ─── STAGE 1: GLM RESEARCHER ───────────────────────────────────────────────

GLM_RESEARCH_SYSTEM = """You are a macro research analyst. Given a theme or ticker and available
intelligence signals, produce a structured research brief as JSON.

Output ONLY valid JSON, no markdown, no preamble. Schema:
{
  "title": "compelling report title",
  "report_type": "market_brief|thesis_eval|narrative|portfolio_risk|velocity_report",
  "summary": "2-3 sentence executive summary",
  "thesis": "core investment/macro thesis in 2-3 sentences",
  "evidence": ["key evidence point 1", "key evidence point 2", ...],
  "catalysts": ["catalyst 1", "catalyst 2", ...],
  "risks": ["risk 1", "risk 2", ...],
  "gli_alignment": "how current GLI phase supports or conflicts with this thesis",
  "verdict": "BULLISH|BEARISH|NEUTRAL|MONITOR",
  "confidence": 0-100,
  "tags": ["tag1", "tag2", ...],
  "sources": [{"type": "aestima_api|external_feed|remi_intelligence|remi_vault", "name": "source name", "detail": "detail"}]
}"""


def glm_research_brief(
    theme: str,
    ticker: str | None,
    signals: list[dict],
    doc_themes: list[dict],
    gli_phase: str,
    conviction_score: float = 0.0,
) -> dict | None:
    """Stage 1: GLM structures all intelligence into a research brief (sync)."""
    if not GLM_API_KEY:
        logger.warning("GLM_API_KEY not set")
        return None

    # Build intelligence context
    lines = [f"Theme: {theme}"]
    if ticker:
        lines.append(f"Primary ticker: {ticker}")
    lines.append(f"GLI Phase: {gli_phase}")
    if conviction_score:
        lines.append(f"Conviction score: {conviction_score:.1f}")

    if signals:
        lines.append(f"\nIntelligence signals ({len(signals)}):")
        for s in signals[:15]:
            desc = s.get("content") or s.get("signal_description") or s.get("theme_label", "")
            stype = s.get("signal_type") or s.get("source_type", "unknown")
            weight = s.get("conviction_weight", 1.0)
            lines.append(f"  [{stype}] {desc} (weight: {weight:.1f})")

    if doc_themes:
        lines.append(f"\nNarrative themes ({len(doc_themes)}):")
        for t in doc_themes[:8]:
            label = t.get("theme_label") or t.get("theme_text", "")
            vel = t.get("weighted_score") or t.get("velocity_score", 0)
            lines.append(f"  {label} (velocity: {vel:.1f})")

    context = "\n".join(lines)

    try:
        resp = httpx.post(
            f"{GLM_BASE_URL}/chat/completions",
            headers={"Authorization": f"Bearer {GLM_API_KEY}", "Content-Type": "application/json"},
            json={
                "model": GLM_MODEL,
                "messages": [
                    {"role": "system", "content": GLM_RESEARCH_SYSTEM},
                    {"role": "user", "content": context},
                ],
                "max_tokens": 2000,
                "temperature": 0.2,
            },
            timeout=60,
        )
        resp.raise_for_status()
        data = resp.json()
        msg = data["choices"][0]["message"]
        raw = msg.get("content") or msg.get("reasoning_content") or ""
        # Strip any accidental markdown fences
        raw = raw.strip().lstrip("```json").lstrip("```").rstrip("```").strip()
        # Try direct parse first
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            pass
        # Try to find the JSON object boundaries
        start = raw.find("{")
        end = raw.rfind("}")
        if start >= 0 and end > start:
            try:
                return json.loads(raw[start:end+1])
            except json.JSONDecodeError:
                pass
        # Last resort: fix common issues (truncated strings, missing brackets)
        if not raw.rstrip().endswith("}"):
            raw = raw + "}"
            try:
                return json.loads(raw)
            except json.JSONDecodeError:
                pass
        logger.warning("GLM brief JSON parse failed, raw: %s", raw[:300])
        return None
    except Exception as e:
        logger.warning("GLM research brief failed for '%s': %s", theme, e)
        return None


# ─── STAGE 2: GEMMA WRITER ─────────────────────────────────────────────────

GEMMA_WRITE_SYSTEM = """You are a professional macro research writer for Aestima, an institutional-grade
financial intelligence platform. You write clear, authoritative, data-driven research reports.

Style guidelines:
- 3-5 paragraphs, publication quality
- Lead with the macro thesis, support with specific data points
- Cite frameworks (Howell liquidity cycle, Lyn Alden fiscal dominance, Steno regimes) where relevant
- Acknowledge key risks honestly — do not oversell
- No financial advice, no buy/sell recommendations
- No bullet points in the body — flowing prose only
- End with a forward-looking paragraph on what to watch
- Tone: confident, analytical, institutional"""

GEMMA_WRITE_USER = """Write a research report based on this brief:

{brief_json}

Write the full report body in flowing prose. Do not repeat the title or summary — 
start directly with the first paragraph of analysis."""


def gemma_write_report(brief: dict) -> str:
    """Stage 2: Gemma 4 writes publication prose from the structured brief (sync)."""
    try:
        prompt = GEMMA_WRITE_USER.format(brief_json=json.dumps(brief, indent=2))
        resp = httpx.post(
            GEMMA_ENDPOINT,
            headers={"Content-Type": "application/json"},
            json={
                "model": GEMMA_MODEL,
                "messages": [
                    {"role": "system", "content": GEMMA_WRITE_SYSTEM},
                    {"role": "user", "content": prompt},
                ],
                "max_tokens": 4096,
                "temperature": 0.7,
            },
            timeout=180,
        )
        resp.raise_for_status()
        data = resp.json()
        return data["choices"][0]["message"]["content"].strip()
    except Exception as e:
        logger.warning("Gemma write failed: %s", e)
        return ""


# ─── MAIN PIPELINE ──────────────────────────────────────────────────────────

def write_report(
    theme: str,
    ticker: str | None = None,
    signals: list[dict] | None = None,
    doc_themes: list[dict] | None = None,
    gli_phase: str = "UNKNOWN",
    conviction_score: float = 0.0,
    trigger: str = "manual",  # "manual" | "autonomous"
) -> dict | None:
    """
    Full GLM→Gemma pipeline (sync). Returns report dict ready for DB insert, or None on failure.

    Dict keys match `reports` table: title, report_type, summary, body_md,
    verdict, confidence, gli_phase, tags, sources, theme
    """
    logger.info("report_writer: starting pipeline for '%s' (trigger=%s)", theme, trigger)

    # Stage 1: GLM research brief
    brief = glm_research_brief(
        theme=theme,
        ticker=ticker,
        signals=signals or [],
        doc_themes=doc_themes or [],
        gli_phase=gli_phase,
        conviction_score=conviction_score,
    )
    if not brief:
        logger.warning("report_writer: GLM stage failed for '%s'", theme)
        return None

    # Stage 2: Gemma writes the body
    body_md = gemma_write_report(brief)
    if not body_md:
        logger.warning("report_writer: Gemma stage failed for '%s'", theme)
        # Fall back to brief summary as body if Gemma fails
        body_md = brief.get("thesis", "") + "\n\n" + "\n".join(brief.get("evidence", []))

    return {
        "title": brief.get("title", theme),
        "report_type": brief.get("report_type", "signal"),
        "theme": theme,
        "summary": brief.get("summary", ""),
        "body_md": body_md,
        "verdict": brief.get("verdict", "MONITOR"),
        "confidence": brief.get("confidence", 50),
        "gli_phase": gli_phase,
        "gli_velocity": None,
        "tags": brief.get("tags", []),
        "sources": brief.get("sources", []),
        "trigger": trigger,
    }


def post_to_aestima(report_dict: dict) -> str | None:
    """POST finished report to Aestima. Returns aestima_id or None (sync)."""
    if not AESTIMA_AGENT_KEY:
        return None
    try:
        payload = {
            "report_type": report_dict.get("report_type", "signal"),
            "title": report_dict["title"],
            "summary": report_dict.get("summary", ""),
            "body_md": report_dict["body_md"],
            "sources": report_dict.get("sources", []),
            "tags": report_dict.get("tags", []),
            "gli_phase": report_dict.get("gli_phase"),
            "gli_velocity": report_dict.get("gli_velocity"),
        }
        resp = httpx.post(
            f"{AESTIMA_URL}/api/research/agent-reports",
            headers={"X-Agent-Key": AESTIMA_AGENT_KEY, "Content-Type": "application/json"},
            json=payload,
            timeout=15,
        )
        if resp.status_code in (200, 201):
            return resp.json().get("id") or resp.json().get("report_id")
        else:
            logger.warning("Aestima POST failed: %s %s", resp.status_code, resp.text[:200])
    except Exception as e:
        logger.warning("Aestima POST error: %s", e)
    return None


def save_draft_to_dashboard(report_dict: dict) -> int | None:
    """Save report as draft in dashboard PostgreSQL. Returns report id (sync)."""
    if not DASHBOARD_DATABASE_URL:
        return None
    try:
        conn = psycopg2.connect(DASHBOARD_DATABASE_URL)
        cur = conn.cursor(cursor_factory=RealDictCursor)
        cur.execute("""
            INSERT INTO reports
                (report_type, status, theme, title, summary, body_md,
                 verdict, confidence, gli_phase, tags, sources, created_by)
            VALUES
                (%s, 'draft', %s, %s, %s, %s, %s, %s, %s,
                 %s::jsonb, %s::jsonb, 'remi')
            RETURNING id
        """, (
            report_dict.get("report_type", "signal"),
            report_dict.get("theme", ""),
            report_dict["title"],
            report_dict.get("summary", ""),
            report_dict["body_md"],
            report_dict.get("verdict", "MONITOR"),
            report_dict.get("confidence", 50),
            report_dict.get("gli_phase"),
            json.dumps(report_dict.get("tags", [])),
            json.dumps(report_dict.get("sources", [])),
        ))
        row = cur.fetchone()
        conn.commit()
        cur.close()
        conn.close()
        return row["id"] if row else None
    except Exception as e:
        logger.warning("save_draft_to_dashboard failed: %s", e)
        return None

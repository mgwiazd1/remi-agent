"""
BogWizard Composer — LLM-synthesized tweet drafts, never templated.

Reads VOICE.md as system prompt, sends signal data as user prompt,
writes draft to bogwizard_drafts SQLite table.
"""
from __future__ import annotations

import json
import logging
import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from llm_router import compose

logger = logging.getLogger(__name__)

BASE_DIR = Path(__file__).resolve().parent.parent
DB_PATH = BASE_DIR / "remi_intelligence.db"
VOICE_PATH = BASE_DIR / "config" / "bogwizard_voice.md"

VALID_DRAFT_TYPES = {
    "velocity_spike", "sentiment_drift", "convergence",
    "phase_transition", "deep_dive_thread", "manual",
}

# Regex for extracting the final tweet from GLM-5 reasoning_content
_QUOTED_TWEET_RE = re.compile(r'"([^"]{20,280})"', re.DOTALL)


def _load_voice() -> str:
    """Load VOICE.md. Caller should cache this."""
    if not VOICE_PATH.exists():
        logger.error("VOICE.md not found at %s", VOICE_PATH)
        return "You are BogWizard. Write concise, data-driven macro tweets."
    return VOICE_PATH.read_text()


def _clean_glm_output(raw: str, is_thread: bool = False) -> str:
    """Strip GLM-5 reasoning artifacts from output.

    GLM-5 sometimes puts the chain-of-thought in content or reasoning_content.
    For single tweets: extract the last quoted string matching tweet length.
    For threads: take everything after the last '---' or 'Tweet 1:' marker.
    Falls back to returning raw if nothing cleaner is found.
    """
    if not raw:
        return raw

    # If it looks like clean output (short, no reasoning markers), return as-is
    if len(raw) <= 300 and "Let me" not in raw and "following the" not in raw:
        return raw.strip()

    if is_thread:
        # For threads, look for the actual thread content after reasoning
        markers = ["---", "Tweet 1:", "## Tweet 1", "**Tweet 1"]
        best_start = -1
        for marker in markers:
            idx = raw.rfind(marker)
            if idx > best_start:
                best_start = idx
        if best_start >= 0:
            return raw[best_start:].strip().lstrip("- \n")
        # If no marker found but output is long, it might be clean
        if len(raw) > 200:
            return raw.strip()
        return raw.strip()

    # Single tweet: try to extract quoted tweet from reasoning
    matches = _QUOTED_TWEET_RE.findall(raw)
    if matches:
        # Return the last (most refined) quoted match
        return matches[-1].strip()

    # Fallback: take the last non-empty line that looks like a tweet
    lines = [l.strip() for l in raw.split('\n') if l.strip()]
    for line in reversed(lines):
        if '🐸' in line and len(line) > 20:
            return line

    return raw.strip()


def _insert_draft(draft_type: str, content: str, llm_used: str,
                  sector: str | None = None, signal_source: str | None = None,
                  signal_data_json: str | None = None,
                  is_thread: bool = False,
                  aestima_link: str | None = None) -> int:
    """Insert a draft into bogwizard_drafts. Returns the draft id."""
    conn = sqlite3.connect(DB_PATH)
    try:
        cur = conn.execute(
            """INSERT INTO bogwizard_drafts
               (draft_type, signal_source, sector, signal_data_json, is_thread,
                content, aestima_link, llm_used, status)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'pending')""",
            (draft_type, signal_source, sector, signal_data_json,
             1 if is_thread else 0, content, aestima_link, llm_used),
        )
        conn.commit()
        return cur.lastrowid
    finally:
        conn.close()


class BogWizardComposer:
    """Stateful composer that caches VOICE.md across calls."""

    def __init__(self):
        self._voice = None

    @property
    def voice(self) -> str:
        if self._voice is None:
            self._voice = _load_voice()
        return self._voice

    def _compose_single(self, signal_type: str, user_prompt: str,
                        sector: str | None = None,
                        signal_source: str | None = None,
                        signal_data: dict | None = None) -> int:
        """Compose a single tweet draft. Returns draft id."""
        text, llm_used = compose(self.voice, user_prompt, max_tokens=300)
        text = _clean_glm_output(text, is_thread=False)
        return _insert_draft(
            draft_type=signal_type,
            content=text.strip(),
            llm_used=llm_used,
            sector=sector,
            signal_source=signal_source,
            signal_data_json=json.dumps(signal_data) if signal_data else None,
        )

    def _compose_thread(self, signal_type: str, user_prompt: str,
                        sector: str | None = None,
                        signal_source: str | None = None,
                        signal_data: dict | None = None,
                        aestima_link: str | None = None) -> int:
        """Compose a thread (4-6 tweets, newline-separated). Returns draft id."""
        text, llm_used = compose(self.voice, user_prompt, max_tokens=1500)
        text = _clean_glm_output(text, is_thread=True)
        return _insert_draft(
            draft_type=signal_type,
            content=text.strip(),
            llm_used=llm_used,
            sector=sector,
            signal_source=signal_source,
            signal_data_json=json.dumps(signal_data) if signal_data else None,
            is_thread=True,
            aestima_link=aestima_link,
        )

    # --- Public compose methods per signal type ---

    def compose_velocity_spike(self, sector: str, data: dict) -> int:
        prompt = (
            f"Signal type: velocity_spike\n"
            f"Sector: {sector}\n"
            f"Data: {json.dumps(data, indent=2)}\n\n"
            f"Write ONE tweet (max 280 chars). Lead with the specific numbers, "
            f"state the plain-English implication, close with exactly ONE bog/cauldron/water lore line, "
            f"end with 🐸. No financial advice. No hashtags."
        )
        return self._compose_single("velocity_spike", prompt, sector=sector,
                                    signal_source="sector_velocity", signal_data=data)

    def compose_sentiment_drift(self, sector: str, data: dict) -> int:
        prompt = (
            f"Signal type: sentiment_drift\n"
            f"Sector: {sector}\n"
            f"Data: {json.dumps(data, indent=2)}\n\n"
            f"Write ONE tweet (max 280 chars). Lead with the sentiment shift numbers, "
            f"state what regime is turning, close with exactly ONE lore line, end with 🐸. "
            f"No financial advice. No hashtags."
        )
        return self._compose_single("sentiment_drift", prompt, sector=sector,
                                    signal_source="sector_velocity", signal_data=data)

    def compose_convergence(self, event: dict) -> int:
        prompt = (
            f"Signal type: convergence\n"
            f"Event: {json.dumps(event, indent=2)}\n\n"
            f"Write ONE tweet (max 280 chars). Name the converging signals, "
            f"state risk_on or risk_off direction, close with exactly ONE lore line, end with 🐸. "
            f"No financial advice. No hashtags."
        )
        direction = event.get("direction", "unknown")
        return self._compose_single("convergence", prompt,
                                    signal_source="velocity_aggregator", signal_data=event)

    def compose_phase_transition(self, old_phase: str, new_phase: str, context: dict) -> int:
        prompt = (
            f"Signal type: phase_transition\n"
            f"Old phase: {old_phase}\n"
            f"New phase: {new_phase}\n"
            f"Context: {json.dumps(context, indent=2)}\n\n"
            f"Write ONE tweet (max 280 chars). State old→new phase, key triggering metric, "
            f"what this means for positioning tempo (tighten/widen/hold), "
            f"close with exactly ONE lore line about the cauldron/water/seasons, end with 🐸. "
            f"No financial advice. No hashtags."
        )
        return self._compose_single("phase_transition", prompt,
                                    signal_source="gli_stamper", signal_data=context)

    def compose_deep_dive_thread(self, post: dict) -> int:
        ticker = post.get("ticker", "UNKNOWN")
        prompt = (
            f"Signal type: deep_dive_thread\n"
            f"Post data: {json.dumps(post, indent=2)}\n\n"
            f"Write a 4-6 tweet thread (separate tweets with blank lines).\n"
            f"Tweet 1: HOOK — contrarian angle, why {ticker}, why now. ONE lore touch.\n"
            f"Tweet 2: THESIS — core argument, numbers only.\n"
            f"Tweet 3: DATA — 2-3 key metrics, pure fact.\n"
            f"Tweet 4: REGIME — GLI phase, Steno regime, fiscal dominance context.\n"
            f"Tweet 5: RISK — what would invalidate, one sentence.\n"
            f"Tweet 6: CTA — link to full analysis.\n"
            f"Each tweet ends with 🐸. No financial advice. No hashtags."
        )
        link = f"aestima.ai/analysis/{ticker}?utm_source=bogwizard"
        return self._compose_thread("deep_dive_thread", prompt,
                                    sector=post.get("sector"),
                                    signal_source="signals_group_listener",
                                    signal_data=post, aestima_link=link)

    def compose_manual(self, instruction: str, signal_data: dict | None = None) -> int:
        """Manual compose — /bog compose <instruction> from Telegram."""
        prompt = (
            f"Signal type: manual\n"
            f"Instruction: {instruction}\n\n"
            f"Write ONE tweet (max 280 chars) following the voice rules. "
            f"End with 🐸. No financial advice. No hashtags."
        )
        return self._compose_single("manual", prompt, signal_data=signal_data)

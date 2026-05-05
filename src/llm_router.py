"""
LLM router for BogWizard composer.
Chain: GLM-5 → GLM-4.7 → Claude Sonnet.
Skips Consuela (too weak for voice-heavy creative writing).
"""
from __future__ import annotations

import os
import logging

logger = logging.getLogger(__name__)


class LLMError(Exception):
    pass


def _call_glm(system_prompt: str, user_prompt: str, max_tokens: int = 500) -> tuple[str, str]:
    """Call GLM via the shared call_glm helper from llm_extractor."""
    from llm_extractor import call_glm

    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt},
    ]
    result = call_glm(messages, max_tokens=max_tokens, model="glm-5", temperature=0.7)
    if result is None:
        raise LLMError("GLM call returned None (key missing or all attempts failed)")
    return result  # (text, model_used)


def _call_claude(system_prompt: str, user_prompt: str, max_tokens: int = 500) -> str:
    try:
        import anthropic
    except ImportError as e:
        raise LLMError(f"anthropic SDK not installed: {e}")

    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        raise LLMError("ANTHROPIC_API_KEY not set")

    client = anthropic.Anthropic(api_key=api_key)
    resp = client.messages.create(
        model=os.environ.get("CLAUDE_MODEL", "claude-sonnet-4-5"),
        max_tokens=max_tokens,
        system=system_prompt,
        messages=[{"role": "user", "content": user_prompt}],
    )
    if not resp.content:
        raise LLMError("Claude returned empty content")
    return resp.content[0].text.strip()


def compose(system_prompt: str, user_prompt: str, max_tokens: int = 500) -> tuple[str, str]:
    """Returns (text, llm_used). Chain: GLM-5 → GLM-4.7 → Claude Sonnet."""
    try:
        text, model_used = _call_glm(system_prompt, user_prompt, max_tokens)
        if text and len(text.strip()) > 10:
            return text, model_used
        logger.warning("GLM returned empty/short, falling back to Claude")
    except Exception as e:
        logger.warning("GLM failed: %s — falling back to Claude", e)

    try:
        text = _call_claude(system_prompt, user_prompt, max_tokens)
        return text, "claude-sonnet"
    except Exception as e:
        logger.exception("Both LLMs failed")
        raise LLMError(f"Both GLM and Claude failed; last error: {e}")

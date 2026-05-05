"""Auto-trigger wrapper. Every signal hook goes through this."""
import logging
import os
import sys

import requests

sys.path.insert(0, os.path.expanduser("~/remi-intelligence/src"))

from bogwizard_state import is_auto_enabled

logger = logging.getLogger(__name__)


def _notify_telegram(text: str):
    """Send notification to MG's DM via bot."""
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    # DM with bot: chat_id = user_id (positive integer)
    chat_id = os.environ.get("TELEGRAM_MG_USER_ID") or os.environ.get("TELEGRAM_CHAT_ID")
    if not token or not chat_id:
        logger.warning("telegram env vars missing, skipping notify")
        return
    try:
        requests.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={"chat_id": chat_id, "text": text},
            timeout=10,
        )
    except Exception as e:
        logger.warning("telegram notify failed: %s", e)


def trigger_velocity_spike(sector: str, data: dict):
    if not is_auto_enabled():
        logger.debug("auto-compose paused, skipping velocity_spike/%s", sector)
        return None
    try:
        from bogwizard_composer import BogWizardComposer
        c = BogWizardComposer()
        draft_id = c.compose_velocity_spike(sector, data)
        _notify_telegram(
            f"🧙‍♂️ New BogWizard draft #{draft_id}\n"
            f"[VELOCITY] {sector} +{data.get('acceleration_pct', 0):.0f}% WoW\n"
            f"/bog preview {draft_id}"
        )
        return draft_id
    except Exception as e:
        logger.exception("velocity_spike compose failed for %s: %s", sector, e)
        return None


def trigger_sentiment_drift(sector: str, data: dict):
    if not is_auto_enabled():
        return None
    try:
        from bogwizard_composer import BogWizardComposer
        c = BogWizardComposer()
        draft_id = c.compose_sentiment_drift(sector, data)
        drift = data.get("sentiment", {}).get("drift", "unknown")
        _notify_telegram(
            f"🧙‍♂️ New BogWizard draft #{draft_id}\n"
            f"[SENTIMENT] {sector} {drift}\n"
            f"/bog preview {draft_id}"
        )
        return draft_id
    except Exception as e:
        logger.exception("sentiment_drift compose failed for %s: %s", sector, e)
        return None


def trigger_convergence(event: dict):
    if not is_auto_enabled():
        return None
    try:
        from bogwizard_composer import BogWizardComposer
        c = BogWizardComposer()
        draft_id = c.compose_convergence(event)
        _notify_telegram(
            f"🧙‍♂️ New BogWizard draft #{draft_id}\n"
            f"[CONVERGENCE] {event.get('signal_count')} signals, {event.get('direction')}\n"
            f"/bog preview {draft_id}  ⚡ bypasses rate limit"
        )
        return draft_id
    except Exception as e:
        logger.exception("convergence compose failed: %s", e)
        return None


def trigger_phase_transition(old_phase: str, new_phase: str, context: dict):
    if not is_auto_enabled():
        return None
    try:
        from bogwizard_composer import BogWizardComposer
        c = BogWizardComposer()
        draft_id = c.compose_phase_transition(old_phase, new_phase, context)
        _notify_telegram(
            f"🧙‍♂️ New BogWizard draft #{draft_id}\n"
            f"[PHASE] {old_phase} → {new_phase}\n"
            f"/bog preview {draft_id}  ⚡ bypasses rate limit"
        )
        return draft_id
    except Exception as e:
        logger.exception("phase_transition compose failed: %s", e)
        return None


def trigger_deep_dive_thread(post: dict):
    if not is_auto_enabled():
        return None
    try:
        from bogwizard_composer import BogWizardComposer
        c = BogWizardComposer()
        draft_id = c.compose_deep_dive_thread(post)
        _notify_telegram(
            f"🧙‍♂️ New BogWizard THREAD draft #{draft_id}\n"
            f"[DEEP DIVE] {post.get('ticker', '?')}\n"
            f"/bog thread {draft_id}"
        )
        return draft_id
    except Exception as e:
        logger.exception("deep_dive_thread compose failed: %s", e)
        return None

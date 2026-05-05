"""
vision_relay.py — On-demand image description via local Gemma E4B.

Swaps VRAM: stops laborer → starts E4B → processes image → restores laborer.
All callers get a text description back. ~15-25 seconds total round-trip.
"""

import base64
import subprocess
import time
import logging
import requests
from pathlib import Path

logger = logging.getLogger(__name__)

VISION_URL = "http://127.0.0.1:8082"
VISION_SERVICE = "gemma-vision.service"
LABORER_SERVICE = "consuela-laborer.service"
HEALTH_TIMEOUT = 90  # seconds to wait for E4B to load
HEALTH_INTERVAL = 2  # seconds between health checks
INFERENCE_TIMEOUT = 120  # seconds for completion request

# --- Prompt definitions ---

# Chart-specific prompt (for financial charts)
CHART_PROMPT = """
Analyze this financial chart image. Extract:
1. Chart type (candlestick, line, area, bar)
2. Asset/instrument shown (ticker, index, or metric name)
3. Time range visible (dates or period)
4. Current trend direction (up/down/sideways)
5. Key levels: support, resistance, current price if visible
6. Any indicators shown (RSI, MACD, moving averages) and their readings
7. Notable patterns (breakout, breakdown, divergence, consolidation)
8. Axis labels and scale

Be precise with numbers. If you can't read a value clearly, say so.
Return a structured description, not a wall of text.
"""

# Medical exam-specific prompt (OCR-first for SEEK questions)
MEDICAL_EXAM_PROMPT = """
This image contains a medical exam question. Extract ALL text exactly as written:
the clinical vignette, question stem, and every answer choice (A through E).
Do not describe the image — transcribe the text.
If there are tables or graphs, describe the data values.
Output the complete question text verbatim.
"""

# General image prompt (for regular images)
GENERAL_IMAGE_PROMPT = """
Describe this image in detail.
If it contains text, transcribe all visible text.
If it contains data or charts, extract key information.
If it contains a medical image, describe findings objectively.
Be precise and factual.
"""

def _systemctl(action: str, service: str) -> bool:
    """Run systemctl --user action on a service. Returns True on success."""
    try:
        result = subprocess.run(
            ["systemctl", "--user", action, service],
            capture_output=True, text=True, timeout=30
        )
        if result.returncode != 0:
            logger.warning(f"systemctl {action} {service}: {result.stderr.strip()}")
            return False
        return True
    except subprocess.TimeoutExpired:
        logger.error(f"systemctl {action} {service} timed out")
        return False

def _is_service_active(service: str) -> bool:
    """Check if a systemd user service is active."""
    result = subprocess.run(
        ["systemctl", "--user", "is-active", service],
        capture_output=True, text=True
    )
    return result.stdout.strip() == "active"

def _wait_for_health(timeout: int = HEALTH_TIMEOUT) -> bool:
    """Poll E4B health endpoint until ready or timeout."""
    start = time.time()
    while time.time() - start < timeout:
        try:
            resp = requests.get(f"{VISION_URL}/health", timeout=3)
            if resp.status_code == 200:
                logger.info("Gemma E4B health check passed")
                return True
        except requests.ConnectionError:
            pass
        time.sleep(HEALTH_INTERVAL)
    logger.error(f"Gemma E4B failed to become healthy within {timeout}s")
    return False

def _swap_to_vision() -> bool:
    """Stop laborer, start vision. Returns True if E4B is ready."""
    laborer_was_running = _is_service_active(LABORER_SERVICE)

    if laborer_was_running:
        logger.info("Stopping laborer to free VRAM...")
        _systemctl("stop", LABORER_SERVICE)
        time.sleep(3)  # wait for VRAM release

    logger.info("Starting Gemma E4B vision service...")
    if not _systemctl("start", VISION_SERVICE):
        # Restore laborer on failure
        if laborer_was_running:
            _systemctl("start", LABORER_SERVICE)
        return False

    if not _wait_for_health():
        _systemctl("stop", VISION_SERVICE)
        if laborer_was_running:
            _systemctl("start", LABORER_SERVICE)
        return False
    return True

def _swap_to_laborer() -> None:
    """Stop vision, restart laborer."""
    _systemctl("stop", VISION_SERVICE)
    time.sleep(2)
    _systemctl("start", LABORER_SERVICE)
    logger.info("Laborer restored")

def describe_image(
    image_path: str = None,
    image_bytes: bytes = None,
    prompt: str = None,
    image_type: str = "chart"
) -> str:
    """
    Send an image to Gemma E4B and get a text description.

    Args:
        image_path: Path to image file on disk
        image_bytes: Raw image bytes (alternative to path)
        prompt: Custom prompt (overrides default chart/general prompt)
        image_type: "chart" | "medical" | "general" — selects default prompt
            "medical" is used for dr-remi gateway context (SEEK questions)

    Returns:
        Text description string, or error message prefixed with "ERROR:"
    """
    # Load image
    if image_path:
        img_data = Path(image_path).read_bytes()
    elif image_bytes:
        img_data = image_bytes
    else:
        return "ERROR: No image provided"

    b64_image = base64.b64encode(img_data).decode("utf-8")

    # Select prompt
    if prompt is None:
        if image_type == "medical":
            # Use OCR-first medical exam prompt for SEEK questions
            prompt = MEDICAL_EXAM_PROMPT
        elif image_type == "chart":
            prompt = CHART_PROMPT
        else:
            prompt = GENERAL_IMAGE_PROMPT

    # Detect MIME type (simple heuristic)
    if image_path and image_path.lower().endswith(".png"):
        mime = "image/png"
    else:
        mime = "image/jpeg"

    # VRAM swap
    if not _swap_to_vision():
        return "ERROR: Failed to start vision model"

    try:
        # Call E4B via OpenAI-compatible API
        payload = {
            "model": "gemma-4-E4B-it-Q8_0.gguf",
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "image_url",
                            "image_url": {
                                "url": f"data:{mime};base64,{b64_image}"
                            }
                        },
                        {
                            "type": "text",
                            "text": prompt
                        }
                    ]
                }
            ],
            "max_tokens": 2048,
            "temperature": 0.1
        }
        resp = requests.post(
            f"{VISION_URL}/v1/chat/completions",
            json=payload,
            timeout=INFERENCE_TIMEOUT
        )

        if resp.status_code != 200:
            logger.error(f"E4B returned {resp.status_code}: {resp.text[:200]}")
            return f"ERROR: Vision model returned HTTP {resp.status_code}"

        data = resp.json()
        description = data["choices"][0]["message"]["content"]
        logger.info(f"Vision description: {len(description)} chars")
        return description

    except requests.Timeout:
        return "ERROR: Vision model timed out"
    except Exception as e:
        logger.error(f"Vision relay error: {e}")
        return f"ERROR: {str(e)[:200]}"
    finally:
        # Always restore laborer
        _swap_to_laborer()

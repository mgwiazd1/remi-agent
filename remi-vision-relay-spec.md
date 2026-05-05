# Remi Vision Relay — On-Demand Chart & Image Reading
**Date:** April 14, 2026
**Unlocks:** Remi can "see" charts, screenshots, CXRs, receipts dropped in Telegram
**Hardware:** 1080Ti (11GB VRAM), Gemma E4B (Q8, 7.7GB + 946MB mmproj) — already downloaded
**Constraint:** Laborer (26B-A4B) uses 9.6GB VRAM — can't coexist with E4B. On-demand swap required.

---

## THE PROBLEM

When someone drops a chart image in Telegram (investing group or DM), Remi can't see it:
- **Hermes gateway:** GLM-5 is text-only. Photos arrive as Telegram `photo` objects but the image content is never processed.
- **Signals listener:** `signals_group_listener.py` handles text and PDF documents only — no photo handler exists.

---

## THE ARCHITECTURE

```
Photo arrives in Telegram (group or DM)
    │
    ▼
signals_group_listener.py detects photo message
    │
    ▼
Download image via Telethon client.download_media()
    │
    ▼
vision_relay.py:
  1. Stop consuela-laborer.service (frees VRAM)
  2. Start gemma-vision.service (loads E4B)
  3. Wait for health check (localhost:8081/health)
  4. POST image + chart-reading prompt to localhost:8081/v1/chat/completions
  5. Get text description back
  6. Stop gemma-vision.service
  7. Restart consuela-laborer.service
    │
    ▼
Text description injected into conversation context
    │
    ├──► signals_group_listener: reply with description + optional GLM-5 analysis
    └──► Hermes gateway: description prepended to user message, GLM-5 reasons over it
```

---

## FILE 1: gemma-vision.service

**Location:** `~/.config/systemd/user/gemma-vision.service`

```ini
[Unit]
Description=Gemma E4B Vision — On-Demand Chart/Image Reading
After=network.target

[Service]
Type=simple
ExecStart=/home/proxmox/llama.cpp/build/bin/llama-server \
  -m /home/proxmox/models/gemma-4-e4b/gemma-4-E4B-it-Q8_0.gguf \
  --mmproj /home/proxmox/models/gemma-4-e4b/mmproj-BF16.gguf \
  --host 127.0.0.1 \
  --port 8081 \
  --ctx-size 8192 \
  --n-gpu-layers 99 \
  --threads 4 \
  --flash-attn
Restart=no
TimeoutStartSec=60
TimeoutStopSec=10
KillMode=mixed
KillSignal=SIGTERM
SendSIGKILL=yes

# Don't enable — this is on-demand only
# [Install]
# WantedBy=default.target
```

After creating:
```bash
systemctl --user daemon-reload
```

Do NOT `systemctl --user enable` — this service only starts when vision_relay calls it.

---

## FILE 2: src/vision_relay.py

**Location:** `~/remi-intelligence/src/vision_relay.py`

```python
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

VISION_URL = "http://127.0.0.1:8081"
VISION_SERVICE = "gemma-vision.service"
LABORER_SERVICE = "consuela-laborer.service"
HEALTH_TIMEOUT = 45  # seconds to wait for E4B to load
HEALTH_INTERVAL = 2  # seconds between health checks
INFERENCE_TIMEOUT = 120  # seconds for completion request


# --- Chart-specific prompt ---

CHART_PROMPT = """Analyze this financial chart image. Extract:
1. Chart type (candlestick, line, area, bar)
2. Asset/instrument shown (ticker, index, or metric name)
3. Time range visible (dates or period)
4. Current trend direction (up/down/sideways)
5. Key levels: support, resistance, current price if visible
6. Any indicators shown (RSI, MACD, moving averages) and their readings
7. Notable patterns (breakout, breakdown, divergence, consolidation)
8. Axis labels and scale

Be precise with numbers. If you can't read a value clearly, say so.
Return a structured description, not a wall of text."""

GENERAL_IMAGE_PROMPT = """Describe this image in detail. 
If it contains text, transcribe all visible text.
If it contains data or charts, extract the key information.
If it contains a medical image, describe the findings objectively.
Be precise and factual."""


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
        prompt = CHART_PROMPT if image_type == "chart" else GENERAL_IMAGE_PROMPT

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
            "max_tokens": 1024,
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
```

---

## FILE 3: Signals Listener Patch — Photo Handler

**Location:** `~/remi-intelligence/src/signals_group_listener.py`

Add this handler alongside the existing text/document handlers. Read the file first to find the exact pattern used for `@client.on(events.NewMessage(...))`.

```python
# --- ADD IMPORTS (at top of file) ---
from vision_relay import describe_image
import tempfile
import os

# --- ADD PHOTO HANDLER ---
# Place alongside existing message handlers in signals_group_listener.py
# Match the exact decorator pattern already in use

@client.on(events.NewMessage(
    chats=[INVESTING_GROUP_ID],
    func=lambda e: e.photo is not None
))
async def handle_photo(event):
    """Process chart/image drops in the investing group."""
    sender = await event.get_sender()
    sender_name = getattr(sender, 'first_name', 'Unknown')
    caption = event.message.message or ""

    logger.info(f"Photo from {sender_name} in investing group. Caption: {caption[:80]}")

    # Download image to temp file
    with tempfile.NamedTemporaryFile(suffix=".jpg", delete=False) as tmp:
        tmp_path = tmp.name

    try:
        await client.download_media(event.message, file=tmp_path)

        # Determine image type from caption
        image_type = "chart"  # default for investing group
        if any(kw in caption.lower() for kw in ["cxr", "xray", "x-ray", "ct ", "mri"]):
            image_type = "medical"
        elif any(kw in caption.lower() for kw in ["receipt", "scan", "photo"]):
            image_type = "general"

        # Reply with acknowledgment
        await event.reply("👁️ Processing image...")

        # Get description from Gemma E4B (blocking — runs VRAM swap)
        import asyncio
        description = await asyncio.to_thread(
            describe_image,
            image_path=tmp_path,
            image_type=image_type
        )

        if description.startswith("ERROR:"):
            await event.reply(f"⚠️ Vision failed: {description}")
            return

        # Send description back
        reply_text = f"📊 *Chart Analysis*\n\n{description}"
        if len(reply_text) > 4000:
            reply_text = reply_text[:3997] + "..."
        await event.reply(reply_text, parse_mode='markdown')

        # Optional: forward description to GLM-5 for deeper analysis
        # This would call GLM-5 with the description + any caption context
        # to produce a "what this means for our thesis" take.
        # Uncomment when ready:
        #
        # from llm_extractor import call_glm5
        # analysis = call_glm5(
        #     f"A chart was shared in our investing group.\n"
        #     f"Caption: {caption}\n"
        #     f"Chart description: {description}\n\n"
        #     f"What does this chart tell us? How does it relate to our current "
        #     f"macro thesis and GLI regime?"
        # )
        # await event.reply(f"🧠 *Remi's Take*\n\n{analysis}")

    except Exception as e:
        logger.error(f"Photo handler error: {e}")
        await event.reply(f"⚠️ Failed to process image: {str(e)[:100]}")
    finally:
        if os.path.exists(tmp_path):
            os.unlink(tmp_path)
```

---

## FILE 4: Hermes Gateway Integration (Future — Phase 2)

The gateway integration is trickier because it requires intercepting the photo in Hermes before it reaches GLM-5. Two options:

### Option A: Hermes Skill (Preferred)
Create a skill `~/.hermes/skills/investing/vision-relay/SKILL.md` that triggers on image input. The skill script calls `vision_relay.describe_image()` and returns the description to the conversation context. This keeps the gateway code untouched.

**Requires:** Hermes v0.6.0 skill triggers to support `media_type: image` or similar. Check if this exists:
```bash
grep -r "media_type\|image\|photo\|vision" ~/.hermes/hermes-agent/skills/ --include="*.py" -l
```

### Option B: Gateway Platform Patch
Edit `~/.hermes/hermes-agent/gateway/platforms/base.py` (same file patched for EPUB). Add photo detection in the message handler:
- If message contains photo → download → call vision_relay → prepend description to message text
- Then forward to GLM-5 as normal

**Drawback:** Lost on Hermes update (same as EPUB fix).

### Recommendation
Start with signals listener only (File 3). This covers the main use case: Pablo or MG drops a chart in the group, Remi sees it. The Hermes gateway integration can come later once the core relay is proven.

---

## BUILD ORDER

### Step 1: Create gemma-vision.service (2 min)
```bash
cat > ~/.config/systemd/user/gemma-vision.service << 'EOF'
[Unit]
Description=Gemma E4B Vision — On-Demand Chart/Image Reading
After=network.target

[Service]
Type=simple
ExecStart=/home/proxmox/llama.cpp/build/bin/llama-server \
  -m /home/proxmox/models/gemma-4-e4b/gemma-4-E4B-it-Q8_0.gguf \
  --mmproj /home/proxmox/models/gemma-4-e4b/mmproj-BF16.gguf \
  --host 127.0.0.1 \
  --port 8081 \
  --ctx-size 8192 \
  --n-gpu-layers 99 \
  --threads 4 \
  --flash-attn
Restart=no
TimeoutStartSec=60
TimeoutStopSec=10
KillMode=mixed
KillSignal=SIGTERM
SendSIGKILL=yes
EOF

systemctl --user daemon-reload
```

### Step 2: Test E4B manually (5 min)
```bash
# Stop laborer
systemctl --user stop consuela-laborer.service

# Start E4B
systemctl --user start gemma-vision.service

# Wait for health
curl -s http://127.0.0.1:8081/health

# Test with a real image (grab any chart screenshot)
# Save a test image to /tmp/test-chart.jpg first, then:
curl -s http://127.0.0.1:8081/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{
    "model": "gemma-4-E4B-it-Q8_0.gguf",
    "messages": [{"role": "user", "content": [
      {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64,'$(base64 -w0 /tmp/test-chart.jpg)'"}},
      {"type": "text", "text": "What does this chart show?"}
    ]}],
    "max_tokens": 512
  }'

# Restore laborer
systemctl --user stop gemma-vision.service
systemctl --user start consuela-laborer.service
```

### Step 3: Deploy vision_relay.py (hand to Remi)
```
Create ~/remi-intelligence/src/vision_relay.py with the code from File 2 above.
Use atomic steps — imports first, then each function appended and verified with ast.parse.
```

### Step 4: Wire signals listener photo handler (hand to Remi)
```
Read ~/remi-intelligence/src/signals_group_listener.py fully.
Add the photo handler from File 3, matching existing decorator patterns.
Restart signals-listener service.
Test by dropping a chart screenshot in the investing group.
```

### Step 5: Test end-to-end
1. Drop a chart screenshot in the investing Telegram group
2. Remi should reply "👁️ Processing image..."
3. ~15-25 seconds later: structured chart description appears
4. Verify laborer is restored: `systemctl --user is-active consuela-laborer.service`
5. Verify VRAM: `nvidia-smi` (should show laborer back at ~9.6GB)

---

## EDGE CASES & GUARDS

1. **Concurrent photo drops:** If two photos arrive within seconds, the second one will fail because E4B is already swapping. Add a simple file lock (`/tmp/vision_relay.lock`) in `describe_image()` — second caller waits or returns "Vision busy, try again in 30s."

2. **Laborer wasn't running:** `_swap_to_vision()` tracks whether laborer was active and only restores it if it was. If laborer was already stopped (manual maintenance), vision runs and exits without restarting it.

3. **E4B fails to start:** Laborer is restored on any failure path. The `finally` block in `describe_image()` guarantees restoration.

4. **Large images:** Telegram compresses photos to 1280px max dimension. Base64 of a 1280px JPEG is ~200-500KB. Well within E4B's context window.

5. **Caption context:** The photo handler passes the Telegram caption to help classify image type. "BTC 4h chart" → chart prompt. "CXR" → medical prompt.

6. **Telegram message length:** Chart descriptions capped at 4000 chars (Telegram limit is 4096). Longer descriptions truncated.

---

## WHAT THIS UNLOCKS

- **Investing:** Pablo drops a Steno liquidity chart → Remi describes it → GLM-5 contextualizes against GLI regime
- **Clinical:** Dr. Remi can read CXRs, CT images (with appropriate caveats)
- **Mr. Remi:** Receipt/pantry scanning for grocery intelligence (same relay, different prompt)
- **Pipeline:** Scheduled chart downloads from TradingView webhooks (future)

---

## COST

Zero. Local inference, no API calls. The only cost is ~15-25 seconds of laborer downtime per image.

---

*Spec: April 14, 2026*
*Prerequisites: Gemma E4B downloaded ✅, llama.cpp built ✅, 1080Ti live ✅*
*First test: Step 2 above (manual E4B test with curl)*
*Full integration: Steps 3-5 (hand to Remi as single build prompt)*
*Previous: remi-local-inference-roadmap.md*

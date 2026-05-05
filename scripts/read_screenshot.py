#!/usr/bin/env python3
"""read_screenshot.py — Extract text and interpret images via Claude Haiku Vision API.
Usage:
    python3 read_screenshot.py /path/to/image.jpg
    python3 read_screenshot.py /path/to/image.jpg "What ticker symbols are mentioned?"

Reads ANTHROPIC_API_KEY from ~/remi-intelligence/.env (remi-investing key).
Returns extracted text to stdout. Exits 1 on error with message to stderr."""

import sys
import os
import base64
import json
import mimetypes
from pathlib import Path

# Load env from remi-intelligence
from dotenv import load_dotenv
load_dotenv(Path.home() / "remi-intelligence" / ".env")

ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY")
HAIKU_MODEL = "claude-haiku-4-5-20251001"
API_URL = "https://api.anthropic.com/v1/messages"

# Supported image types
SUPPORTED_MIMES = {
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
    ".gif": "image/gif",
    ".webp": "image/webp",
}

def read_screenshot(image_path: str, prompt: str = None) -> str:
    """
    Send an image to Claude Haiku vision and return the extracted text/analysis.
    """
    import urllib.request
    import urllib.error

    if not ANTHROPIC_API_KEY:
        raise RuntimeError("ANTHROPIC_API_KEY not set in ~/remi-intelligence/.env")

    path = Path(image_path)
    if not path.exists():
        raise FileNotFoundError(f"Image not found: {image_path}")

    # Detect MIME type
    ext = path.suffix.lower()
    media_type = SUPPORTED_MIMES.get(ext)
    if not media_type:
        # Try mimetypes as fallback
        media_type, _ = mimetypes.guess_type(str(path))
        if not media_type or not media_type.startswith("image/"):
            raise ValueError(f"Unsupported image type: {ext}")

    # Base64 encode the image
    with open(path, "rb") as f:
        image_data = base64.standard_b64encode(f.read()).decode("utf-8")

    # Build the prompt
    if not prompt:
        prompt = (
            "Extract ALL text from this screenshot. Preserve the original structure "
            "(tables, lists, paragraphs, headers). If this is a chart or graph, describe "
            "the data it shows including axis labels, values, and trends. "
            "If there are ticker symbols, prices, or financial data, extract them precisely. "
            "Return the extracted content as clean text."
        )

    # Build API request
    payload = json.dumps({
        "model": HAIKU_MODEL,
        "max_tokens": 4096,
        "messages": [
            {
                "role": "user",
                "content": [
                    {
                        "type": "image",
                        "source": {
                            "type": "base64",
                            "media_type": media_type,
                            "data": image_data,
                        },
                    },
                    {
                        "type": "text",
                        "text": prompt,
                    },
                ],
            }
        ],
    }).encode("utf-8")

    req = urllib.request.Request(
        API_URL,
        data=payload,
        headers={
            "Content-Type": "application/json",
            "x-api-key": ANTHROPIC_API_KEY,
            "anthropic-version": "2023-06-01",
        },
        method="POST",
    )

    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            result = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"Anthropic API error {e.code}: {body}")

    # Extract text from response
    text_parts = []
    for block in result.get("content", []):
        if block.get("type") == "text":
            text_parts.append(block["text"])

    if not text_parts:
        raise RuntimeError(f"No text in API response: {json.dumps(result)[:200]}")

    return "\n".join(text_parts)

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python3 read_screenshot.py <image_path> [prompt]", file=sys.stderr)
        sys.exit(1)

    image_path = sys.argv[1]
    custom_prompt = sys.argv[2] if len(sys.argv) > 2 else None

    try:
        result = read_screenshot(image_path, custom_prompt)
        print(result)
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)

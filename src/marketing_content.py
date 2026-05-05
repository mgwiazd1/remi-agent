#!/usr/bin/env python3
"""
Aestima Marketing Content Generator

Generates draft tweets for @aestima_ai based on live GLI data and systematic alerts.
"""

import os
import json
import requests
from datetime import datetime
from pathlib import Path


def load_env_files():
    """Load environment from multiple .env files with fallback."""
    env_files = [
        Path(__file__).parent.parent / ".env",  # ~/remi-intelligence/.env
        Path.home() / ".hermes" / ".env",       # ~/.hermes/.env
    ]
    
    # Placeholder values to skip (indicate missing/invalid key)
    placeholders = {"***", "", "xxx", "placeholder", "your_key_here"}
    
    for env_file in env_files:
        if env_file.exists():
            with open(env_file) as f:
                for line in f:
                    line = line.strip()
                    if not line or line.startswith("#"):
                        continue
                    if "=" in line:
                        key, _, value = line.partition("=")
                        key = key.strip()
                        value = value.strip().strip('"').strip("'")
                        # Skip if key is empty, value is placeholder, or already set with real value
                        if not key or value.lower() in placeholders:
                            continue
                        existing = os.environ.get(key, "")
                        # Only set if not already set OR existing is a placeholder
                        if not existing or existing.lower() in placeholders:
                            os.environ[key] = value


# Load env files at module import
load_env_files()

# Configuration
DRAFTS_DIR = Path("/tmp/aestima_drafts")
PERFORMANCE_LOG = DRAFTS_DIR / "performance_log.json"
TELEGRAM_HOME_CHANNEL = "6625574871"
AESTIMA_BASE = "https://aestima.ai"

# Hardcoded generation rules (system prompt)
GENERATION_SYSTEM_PROMPT = """You are the content strategist for @aestima_ai, a macro intelligence platform.

HARD RULES:
- Lead with data, not opinions
- Cite specific numbers (GLI velocity, COT percentiles, vol readings, systematic pressure)
- No AI buzzwords. No "game-changer". No "delve". No "navigate". No "landscape".
- Max 280 characters for standalone tweets
- This is @aestima_ai voice — product-forward, data-driven
- NOT BogWizard. No frogs. No lore. No cauldrons.
- Threads: max 4 parts, plain-English regime summary

Generate exactly 3 pieces of content:
1. SIGNAL: Data-driven signal post citing specific numbers from the data
2. SPOTLIGHT: Feature spotlight with a live example from the data
3. NARRATIVE: Macro narrative thread (max 4 parts) in plain English

Return ONLY valid JSON array:
[
  {"type": "signal", "body": "...", "thread_items": null},
  {"type": "spotlight", "body": "...", "thread_items": null},
  {"type": "narrative", "body": "...", "thread_items": ["part 2", "part 3"]}
]"""


def fetch_gli_context() -> dict:
    """Fetch GLI context from Aestima delta endpoint."""
    api_key = os.environ.get("AESTIMA_AGENT_KEY")
    if not api_key:
        raise ValueError("AESTIMA_AGENT_KEY not set in environment")
    
    url = f"{AESTIMA_BASE}/api/agent/context/delta"
    headers = {"X-Agent-Key": api_key}
    
    response = requests.get(url, headers=headers, timeout=30)
    response.raise_for_status()
    return response.json()


def fetch_systematic_alerts() -> dict:
    """Fetch systematic floor alerts from Aestima."""
    api_key = os.environ.get("AESTIMA_AGENT_KEY")
    if not api_key:
        raise ValueError("AESTIMA_AGENT_KEY not set in environment")
    
    url = f"{AESTIMA_BASE}/api/smart-money/systematic-floor"
    headers = {"X-Agent-Key": api_key}
    
    response = requests.get(url, headers=headers, timeout=30)
    response.raise_for_status()
    return response.json()


def generate_content_via_claude(gli_data: dict, systematic_data: dict) -> list:
    """Generate marketing content using Claude API."""
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        raise ValueError("ANTHROPIC_API_KEY not set in environment")
    
    # Build the user prompt with data
    user_prompt = f"""Generate @aestima_ai content based on this live data:

GLI CONTEXT:
{json.dumps(gli_data, indent=2)}

SYSTEMATIC ALERTS:
{json.dumps(systematic_data, indent=2)}

Generate 3 pieces now. Remember: data-first, specific numbers, no fluff."""

    url = "https://api.anthropic.com/v1/messages"
    headers = {
        "x-api-key": api_key,
        "anthropic-version": "2023-06-01",
        "content-type": "application/json"
    }
    payload = {
        "model": "claude-sonnet-4-20250514",
        "max_tokens": 1500,
        "system": GENERATION_SYSTEM_PROMPT,
        "messages": [{"role": "user", "content": user_prompt}]
    }
    
    response = requests.post(url, headers=headers, json=payload, timeout=60)
    response.raise_for_status()
    
    result = response.json()
    content_text = result["content"][0]["text"]
    
    # Parse JSON from response
    # Handle potential markdown code blocks
    if "```json" in content_text:
        content_text = content_text.split("```json")[1].split("```")[0]
    elif "```" in content_text:
        content_text = content_text.split("```")[1].split("```")[0]
    
    return json.loads(content_text.strip())


def save_drafts(drafts: list) -> Path:
    """Save drafts to dated JSON file."""
    today = datetime.now().strftime("%Y-%m-%d")
    output_path = DRAFTS_DIR / f"{today}.json"
    
    output_data = {
        "generated_at": datetime.now().isoformat(),
        "drafts": drafts
    }
    
    with open(output_path, "w") as f:
        json.dump(output_data, f, indent=2)
    
    return output_path


def send_telegram_preview(drafts: list, has_critical_alert: bool) -> bool:
    """Send draft previews to MG via Telegram using requests."""
    bot_token = os.environ.get("TELEGRAM_BOT_TOKEN")
    
    today = datetime.now().strftime("%Y-%m-%d")
    
    # Build preview message
    lines = [f"📝 Aestima Content Drafts — {today}"]
    
    if has_critical_alert:
        lines.append("🔴 CRITICAL ALERT ACTIVE — Priority content")
    
    lines.append("")
    
    for draft in drafts:
        draft_type = draft["type"].upper()
        body_preview = draft["body"][:120] + "..." if len(draft["body"]) > 120 else draft["body"]
        lines.append(f"【{draft_type}】")
        lines.append(body_preview)
        if draft.get("thread_items"):
            lines.append(f"  └─ {len(draft['thread_items'])} thread parts")
        lines.append("")
    
    lines.append(f"Full drafts → /tmp/aestima_drafts/{today}.json")
    lines.append("Reply to approve/edit before posting.")
    
    message = "\n".join(lines)
    
    if not bot_token:
        print("   ⚠️ TELEGRAM_BOT_TOKEN not set, skipping notification")
        return False
    
    # Send via Telegram Bot API
    url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_HOME_CHANNEL,
        "text": message,
        "parse_mode": "HTML"
    }
    
    try:
        response = requests.post(url, json=payload, timeout=30)
        response.raise_for_status()
        return True
    except Exception as e:
        print(f"   Telegram error: {e}")
        return False


def check_critical_alert(systematic_data: dict) -> bool:
    """Check if there's a critical systematic alert active."""
    # Check for critical-level alerts in the data
    if isinstance(systematic_data, dict):
        alerts = systematic_data.get("alerts", [])
        for alert in alerts:
            if alert.get("severity", "").lower() == "critical":
                return True
        # Also check top-level severity
        if systematic_data.get("severity", "").lower() == "critical":
            return True
    return False


def main():
    """Main execution."""
    print("🚀 Aestima Marketing Content Generator")
    print("=" * 40)
    
    # Step 1: Fetch GLI context
    print("\n📊 Fetching GLI context...")
    try:
        gli_data = fetch_gli_context()
        gli_phase = gli_data.get("current", {}).get("gli_phase", "unknown")
        print(f"   GLI Phase: {gli_phase}")
    except Exception as e:
        print(f"   ❌ Error: {e}")
        gli_data = {"error": str(e)}
    
    # Step 2: Fetch systematic alerts
    print("\n📡 Fetching systematic alerts...")
    try:
        systematic_data = fetch_systematic_alerts()
        has_critical = check_critical_alert(systematic_data)
        print(f"   Critical alert: {'YES' if has_critical else 'No'}")
    except Exception as e:
        print(f"   ❌ Error: {e}")
        systematic_data = {"error": str(e)}
        has_critical = False
    
    # Step 3: Generate content via Claude
    print("\n🤖 Generating content via Claude...")
    try:
        drafts = generate_content_via_claude(gli_data, systematic_data)
        print(f"   Generated {len(drafts)} drafts")
        for d in drafts:
            print(f"   - {d['type']}: {len(d['body'])} chars")
    except Exception as e:
        print(f"   ❌ Error: {e}")
        return 1
    
    # Step 4: Save drafts
    print("\n💾 Saving drafts...")
    output_path = save_drafts(drafts)
    print(f"   Saved to: {output_path}")
    
    # Step 5: Send Telegram preview
    print("\n📱 Sending Telegram preview to MG...")
    try:
        success = send_telegram_preview(drafts, has_critical)
        print(f"   {'✅ Sent' if success else '❌ Failed'}")
    except Exception as e:
        print(f"   ❌ Error: {e}")
    
    print("\n✅ Done!")
    return 0


if __name__ == "__main__":
    exit(main())

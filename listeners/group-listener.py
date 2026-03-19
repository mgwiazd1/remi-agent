#!/usr/bin/env python3

import asyncio
import json
import os
from datetime import datetime
from telethon import TelegramClient, events
from telethon.tl.types import MessageReplyHeader

# ── CONFIG ──────────────────────────────────────────────────────────────────
API_ID       = 38226786
API_HASH     = "d4c510f92180cca8ca420c13505293ec"
GROUP_ID     = -1001155768296
YOUR_TG_ID   = 6625574871
SESSION_FILE = "/home/proxmox/.tg-listener"
BUFFER_FILE  = "/home/proxmox/signal-buffer.json"

URGENT_KEYWORDS = [
    "urgent", "breaking", "alert", "liquidation", "flash crash",
    "emergency", "immediate", "critical", "dump", "pump",
    "entry now", "buy now", "sell now", "stop loss hit",
]
# ────────────────────────────────────────────────────────────────────────────

client = TelegramClient(SESSION_FILE, API_ID, API_HASH)

def load_buffer():
    if os.path.exists(BUFFER_FILE):
        with open(BUFFER_FILE) as f:
            return json.load(f)
    return []

def save_buffer(buf):
    with open(BUFFER_FILE, "w") as f:
        json.dump(buf, f, indent=2)

def is_urgent(text):
    t = text.lower()
    return any(kw.lower() in t for kw in URGENT_KEYWORDS)

def get_topic_id(message):
    """Extract topic thread ID from message, if any."""
    if message.reply_to and hasattr(message.reply_to, 'reply_to_top_id'):
        return message.reply_to.reply_to_top_id
    if message.reply_to and hasattr(message.reply_to, 'reply_to_msg_id'):
        return message.reply_to.reply_to_msg_id
    return None

async def send_to_me(text):
    await client.send_message(YOUR_TG_ID, text)

@client.on(events.NewMessage(chats=GROUP_ID))
async def handler(event):
    msg = event.message
    text = msg.text or ""
    if not text.strip():
        return

    sender = await event.get_sender()
    sender_name = getattr(sender, "username", None) or getattr(sender, "first_name", "Unknown")
    topic_id = get_topic_id(msg)

    entry = {
        "ts": datetime.utcnow().isoformat(),
        "sender": sender_name,
        "topic_id": topic_id,
        "text": text[:2000],
    }

    buf = load_buffer()
    buf.append(entry)
    save_buffer(buf)

    if is_urgent(text):
        preview = text[:300].replace("\n", " ")
        await send_to_me(
            f"🚨 URGENT SIGNAL\n"
            f"From: @{sender_name}\n"
            f"Topic ID: {topic_id}\n\n"
            f"{preview}"
        )

async def main():
    await client.start()
    print(f"[{datetime.now()}] Listener started — watching group {GROUP_ID} (all topics)")
    await client.run_until_disconnected()

if __name__ == "__main__":
    asyncio.run(main())

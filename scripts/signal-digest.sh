#!/bin/bash
source /home/proxmox/.hermes/.env
BUFFER="/home/proxmox/signal-buffer.json"
BOT_TOKEN="$TELEGRAM_BOT_TOKEN"
YOUR_ID="6625574871"
PABLO_ID="1749701421"
CLAUDE="/home/proxmox/.npm-global/bin/claude"
if [ ! -f "$BUFFER" ] || [ "$(cat "$BUFFER")" = "[]" ]; then
    echo "[$(date)] Buffer empty — nothing to digest."
    exit 0
fi
SUMMARY=$(python3 << 'PYEOF'
import json, subprocess
buf = json.load(open("/home/proxmox/signal-buffer.json"))
msgs = '\n'.join([f"[{e['ts'][11:16]}] {e['sender']}: {e['text'][:1000]}" for e in buf])
prompt = f"""You are a crypto signal extraction assistant. Below are {len(buf)} messages from a VIP signals group.
Your job: extract ONLY the actionable alpha. Each message may be long and verbose — compress ruthlessly.
Output format — one bullet per distinct signal or call, max 15 bullets total:
- [ASSET]: [one-line signal — entry/exit/level/bias/setup]
Rules:
- If a post has no actionable signal (e.g. educational, motivational, general market commentary) — skip it entirely
- Reduce any long post to a single line maximum
- If the same asset appears multiple times, consolidate into one bullet
- End with: "[N] posts monitored, [M] signals extracted"
MESSAGES:
{msgs}"""
result = subprocess.run(
    ["/home/proxmox/.npm-global/bin/claude", "--print", prompt],
    capture_output=True, text=True,
    cwd="/docker/obsidian/investing"
)
print(result.stdout.strip() if result.stdout else "⚠️ Digest generation failed.")
PYEOF
)
DATE=$(date +"%b %d")
FULL_MSG="📊 Morning Brief — ${DATE}
${SUMMARY}"
python3 -c "
import requests
bot = '${BOT_TOKEN}'
msg = '''${FULL_MSG}'''[:4096]
for chat_id in ['${YOUR_ID}', '${PABLO_ID}']:
    r = requests.post(
        f'https://api.telegram.org/bot{bot}/sendMessage',
        json={'chat_id': chat_id, 'text': msg}
    )
    print(f'Sent to {chat_id}.' if r.ok else f'Failed {chat_id}: {r.text}')
"
echo "[]" > "$BUFFER"
echo "[$(date)] Digest sent — buffer cleared."

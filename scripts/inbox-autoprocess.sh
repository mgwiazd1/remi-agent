#!/bin/bash
source /home/proxmox/.hermes/.env
INBOX="/docker/obsidian/MG/Inbox"
TIMESTAMPS_FILE="/home/proxmox/.inbox-timestamps"
PROCESSED_FILE="/home/proxmox/.inbox-processed"
THRESHOLD=$((8 * 3600))
BOT_TOKEN="$TELEGRAM_BOT_TOKEN"
YOUR_ID="6625574871"
touch "$PROCESSED_FILE"
shopt -s nullglob
pdfs=("$INBOX"/*.pdf "$INBOX"/*.PDF)
[ ${#pdfs[@]} -eq 0 ] && exit 0
send_msg() {
    python3 -c "
import requests
requests.post(
    'https://api.telegram.org/bot${BOT_TOKEN}/sendMessage',
    json={'chat_id': '${YOUR_ID}', 'text': '$1'}
)
"
}
now=$(date +%s)
while IFS=' ' read -r epoch filename; do
    [ -z "$filename" ] && continue
    grep -qF "$filename" "$PROCESSED_FILE" && continue
    [ -f "$INBOX/$filename" ] || continue
    age=$((now - epoch))
    if [ $age -ge $THRESHOLD ]; then
        echo "$filename" >> "$PROCESSED_FILE"
        send_msg "⏰ Auto-processing inbox (8hr timeout reached)..."
        cd /docker/obsidian/MG && claude "process the inbox"
        send_msg "✅ Inbox auto-processed. Check your vault for new notes."
        break
    fi
done < "$TIMESTAMPS_FILE"

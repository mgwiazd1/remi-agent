#!/bin/bash
INBOX="/docker/obsidian/MG/Inbox"
READING_LIST="$INBOX/READING LIST.md"
SEEN_FILE="/home/proxmox/.inbox-seen"
TIMESTAMPS_FILE="/home/proxmox/.inbox-timestamps"

touch "$SEEN_FILE"
touch "$TIMESTAMPS_FILE"

for f in "$INBOX"/*.pdf "$INBOX"/*.PDF; do
    [ -f "$f" ] || continue
    filename=$(basename "$f")
    if ! grep -qF "$filename" "$SEEN_FILE"; then
        echo "$filename" >> "$SEEN_FILE"
        date_str=$(date '+%Y-%m-%d %H:%M')
        epoch=$(date +%s)
        echo "$epoch $filename" >> "$TIMESTAMPS_FILE"
        echo "| $date_str | $filename | ⬜ Pending |" >> "$READING_LIST"
        openclaw message send --channel telegram --target 6625574871 --message "📥 New article in inbox: $filename — say 'process the inbox' or I'll auto-process in 8 hours."
    fi
done

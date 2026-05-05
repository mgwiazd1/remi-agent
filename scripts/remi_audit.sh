#!/bin/bash
# Remi System Audit — runs on demand or daily
# Covers: gateway health, skill inventory, vault write safety, response patterns

REPORT="/tmp/remi-audit-$(date +%Y%m%d-%H%M).txt"
VAULT="/docker/obsidian/investing/Intelligence"
SKILLS_DIR="$HOME/.hermes/skills"
SOUL="$HOME/.hermes/SOUL.md"

echo "===== REMI AUDIT $(date) =====" > $REPORT

# 1. Gateway health
echo -e "\n[1] GATEWAY STATUS" >> $REPORT
systemctl --user is-active hermes-gateway >> $REPORT
systemctl --user is-active remi-intelligence >> $REPORT
systemctl --user is-active signals-listener >> $REPORT

# 2. SOUL.md guards present
echo -e "\n[2] SOUL.md GUARDS" >> $REPORT
for guard in "500 char" "NEVER dump" "Maximum 4 lines" "NEVER list related" "Cron Job Policy" "NEVER use browser_navigate"; do
    grep -q "$guard" $SOUL && echo "✅ '$guard'" >> $REPORT || echo "❌ MISSING: '$guard'" >> $REPORT
done

# 3. Skill inventory
echo -e "\n[3] SKILL INVENTORY" >> $REPORT
echo "Skills loaded:" >> $REPORT
find $SKILLS_DIR -name "SKILL.md" | sed "s|$SKILLS_DIR/||" | sort >> $REPORT
echo "Total: $(find $SKILLS_DIR -name 'SKILL.md' | wc -l)" >> $REPORT

# 2b. Hermes cron guard — one-shot jobs allowed, recurring prohibited
echo -e "\n[2b] HERMES CRON GUARD" >> $REPORT
RECURRING=$(python3 -c "
import json, sys
try:
    d = json.load(open('/home/proxmox/.hermes/cron/jobs.json'))
    jobs = d.get('jobs', [])
    recurring = [j for j in jobs if j.get('schedule', {}).get('kind') != 'once']
    for j in recurring:
        print(f'  - {j.get(\"name\",\"?\")} {j.get(\"schedule\",\"?\")}')
    sys.exit(len(recurring))
except: sys.exit(0)
" 2>/dev/null)
RECURRING_COUNT=$?
if [ "$RECURRING_COUNT" = "0" ]; then
    echo "✅  No recurring cron jobs found (one-shot jobs allowed)" >> $REPORT
else
    echo "❌  WARNING: $RECURRING_COUNT recurring cron jobs found — review immediately" >> $REPORT
    echo "$RECURRING" >> $REPORT
fi

# 3b. Code guards
echo -e "
[3b] CODE GUARDS" >> $REPORT
grep -q "mentions < 2" ~/remi-intelligence/src/obsidian_writer.py && echo "✅ mention_count guard in obsidian_writer" >> $REPORT || echo "❌ MISSING: mention_count guard" >> $REPORT
grep -q "mention_count >= 2" ~/remi-intelligence/src/obsidian_writer.py && echo "✅ mention_count guard in write_all_completed" >> $REPORT || echo "❌ MISSING: mention_count guard in write_all_completed" >> $REPORT

# 3c. Content quality guards
echo -e "
[3c] CONTENT QUALITY GUARDS" >> $REPORT
grep -q "is_low_quality_content" ~/remi-intelligence/src/extraction_worker.py && echo "✅  Quality filter in extraction_worker" >> $REPORT || echo "❌  MISSING: quality filter in extraction_worker" >> $REPORT
grep -q "Skipping low-quality message" ~/remi-intelligence/src/signals_group_listener.py && echo "✅  Quality filter in signals_group_listener" >> $REPORT || echo "❌  MISSING: quality filter in signals_group_listener" >> $REPORT

# 3d. Telegram rate limit guard
echo -e "
[3d] TELEGRAM RATE LIMIT GUARD" >> $REPORT
grep -q "retry_after\|_last_send\|_send_counts" ~/remi-intelligence/src/telegram_sender.py && echo "✅  Rate limiting in telegram_sender" >> $REPORT || echo "❌  MISSING: rate limiting in telegram_sender" >> $REPORT

# 4. Vault file safety — check for recursive wikilink patterns
echo -e "\n[4] VAULT CORRUPTION CHECK" >> $REPORT
CORRUPT=$(grep -rl "\[\[.*\]\]" $VAULT/Themes/ 2>/dev/null | xargs -I{} awk 'END{if(NR>300) print FILENAME " — " NR " lines (SUSPICIOUS)"}' {} 2>/dev/null)
if [ -z "$CORRUPT" ]; then
    echo "✅ No suspiciously large theme files" >> $REPORT
else
    echo "⚠️ $CORRUPT" >> $REPORT
fi

# Check for deeply nested wikilink patterns (recursion signature)
echo "Checking for recursion signature..." >> $REPORT
RECURSIVE=$(grep -rl "$(printf '%.0s                ' {1..10})\[\[" $VAULT/Themes/ 2>/dev/null)
if [ -z "$RECURSIVE" ]; then
    echo "✅ No recursive wikilink nesting detected" >> $REPORT
else
    echo "❌ RECURSIVE PATTERN IN: $RECURSIVE" >> $REPORT
fi

# 5. Media job queue health
echo -e "\n[5] MEDIA JOB QUEUE" >> $REPORT
cd ~/remi-intelligence && python3 -c "
import sqlite3
conn = sqlite3.connect('remi_intelligence.db')
rows = conn.execute('SELECT status, COUNT(*) FROM media_jobs GROUP BY status').fetchall()
for r in rows: print(f'  {r[0]:25} {r[1]}')
conn.close()
" >> $REPORT 2>/dev/null

# 6. Recent gateway errors
echo -e "\n[6] GATEWAY ERRORS (last 2h)" >> $REPORT
journalctl --user -u hermes-gateway --since "2 hours ago" --no-pager 2>/dev/null | grep -iE "error|exception|recursion|timeout" | tail -10 >> $REPORT
[ $(journalctl --user -u hermes-gateway --since "2 hours ago" --no-pager 2>/dev/null | grep -icE "error|exception|recursion|timeout") -eq 0 ] && echo "✅ No errors" >> $REPORT

# 7. Vault file count
echo -e "\n[7] VAULT HEALTH" >> $REPORT
echo "Signals: $(ls $VAULT/Signals/ 2>/dev/null | wc -l)" >> $REPORT
echo "Themes:  $(ls $VAULT/Themes/ 2>/dev/null | wc -l)" >> $REPORT
echo "RSS:     $(ls $VAULT/RSS/ 2>/dev/null | wc -l)" >> $REPORT

echo -e "\n===== END AUDIT =====" >> $REPORT
cat $REPORT

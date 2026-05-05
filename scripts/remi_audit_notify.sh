#!/bin/bash
# Remi Audit Notification Script
# Runs daily audit and sends Telegram alert on failures

set -e

# Config
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ENV_FILE="$(dirname "$SCRIPT_DIR")/.env"
AUDIT_SCRIPT="$SCRIPT_DIR/remi_audit.sh"
LOG_FILE="$(dirname "$SCRIPT_DIR")/audit.log"
MG_CHAT_ID="6625574871"

# Load environment
if [[ -f "$ENV_FILE" ]]; then
    source "$ENV_FILE"
else
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] ERROR: .env file not found at $ENV_FILE" >> "$LOG_FILE"
    exit 1
fi

# Run audit and capture output
TIMESTAMP=$(date '+%Y-%m-%d %H:%M:%S')
AUDIT_OUTPUT=$("$AUDIT_SCRIPT" 2>&1)
EXIT_CODE=$?

# Check for failures (❌ in output)
FAILURES=$(echo "$AUDIT_OUTPUT" | grep '❌' || true)

if [[ -n "$FAILURES" ]]; then
    # Build alert message
    ALERT_TEXT="⚠️ REMI AUDIT FAILED"$'\n'"$FAILURES"
    
    # Send Telegram alert to MG
    curl -s -X POST \
        "https://api.telegram.org/bot${INVESTING_BOT_TOKEN}/sendMessage" \
        -d chat_id="$MG_CHAT_ID" \
        -d text="$ALERT_TEXT" \
        -d parse_mode="HTML" \
        > /dev/null
    
    # Log failure
    echo "[$TIMESTAMP] AUDIT FAILED - Alert sent to MG" >> "$LOG_FILE"
    echo "$FAILURES" >> "$LOG_FILE"
    echo "---" >> "$LOG_FILE"
else
    # Log success (no alert sent)
    echo "[$TIMESTAMP] AUDIT PASSED - All checks OK" >> "$LOG_FILE"
fi

exit $EXIT_CODE

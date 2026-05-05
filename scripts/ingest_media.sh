#!/bin/bash
# Remi Media Ingestor — Ad-hoc ingestion script
# Usage: ingest_media.sh <URL>

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VENV_PATH="$SCRIPT_DIR/.venv"
ENV_FILE="$SCRIPT_DIR/.env"

# Activate venv
if [ ! -f "$VENV_PATH/bin/activate" ]; then
    echo "Error: Virtual environment not found at $VENV_PATH"
    exit 1
fi

source "$VENV_PATH/bin/activate"

# Load environment
if [ -f "$ENV_FILE" ]; then
    export $(grep -v '^#' "$ENV_FILE" | xargs)
fi

# Check URL provided
if [ -z "$1" ]; then
    echo "Usage: $0 <URL>"
    echo "  URL: YouTube link, podcast URL, or local audio file path"
    exit 1
fi

URL="$1"

# Run media ingestor
python3 "$SCRIPT_DIR/src/media_ingestor.py" --url "$URL" --mode adhoc

exit $?

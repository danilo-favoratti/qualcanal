#!/usr/bin/env bash
set -euo pipefail

# directories
SRC_DIR="/home/favoratti/dev/qualcanal"
DEST_DIR="/var/www/html/qualcanal"

# ensure destination exists
mkdir -p "$DEST_DIR"

# 1. copy index.html
cp "$SRC_DIR/index.html" "$DEST_DIR/"

# 2. run the Python scheduler and wait
/usr/bin/env python3 "$SRC_DIR/serper_agent_scheduler.py"

# 3. copy the results JSON
cp "$SRC_DIR/match_results.json" "$DEST_DIR/"

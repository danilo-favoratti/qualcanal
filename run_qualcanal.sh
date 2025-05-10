#!/usr/bin/env bash
set -euo pipefail

SRC_DIR="/home/favoratti/dev/qualcanal"
DEST_DIR="/var/www/html/qualcanal"

# 1. activate venv
source "$SRC_DIR/venv/bin/activate"

# 2. (re)install deps if you want the freshest requirements
# pip install --upgrade pip
# pip install -r "$SRC_DIR/requirements.txt"

# 2. copy entire images folder (overwriting)
# rm -rf "$DEST_DIR/images"
# cp -r "$SRC_DIR/images" "$DEST_DIR/"

# 3. copy index.html
# mkdir -p "$DEST_DIR"
cp "$SRC_DIR/index.html" "$DEST_DIR/"

# 4. run the Python scheduler
cd "$SRC_DIR"
sudo "$SRC_DIR/venv/bin/python3" "$SRC_DIR/serper_agent_scheduler.py"

# 5. copy the results JSON
cp "$SRC_DIR/match_results.json" "$DEST_DIR/"

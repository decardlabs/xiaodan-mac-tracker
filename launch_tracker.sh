#!/bin/bash
cd "$(dirname "$0")"
LOG_DIR="$HOME/Library/Logs/XiaoDan"
mkdir -p "$LOG_DIR"
exec "$(python3 -c 'import sys; print(sys.executable)')" -u tracker.py \
  >> "$LOG_DIR/tracker.log" 2>&1

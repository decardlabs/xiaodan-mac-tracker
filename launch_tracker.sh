#!/bin/bash
# 注意：当前 tracker 由 launchd 通过 ~/Library/LaunchAgents/com.user.mactracker.plist 直接启动。
# 本脚本未被 launchd 使用，仅作手动调试备用（例如临时测试时在终端直接执行）。
cd "$(dirname "$0")"
LOG_DIR="$HOME/Library/Logs/XiaoDan"
mkdir -p "$LOG_DIR"
exec "$(python3 -c 'import sys; print(sys.executable)')" -u tracker.py \
  >> "$LOG_DIR/tracker.log" 2>&1

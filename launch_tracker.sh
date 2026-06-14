#!/bin/bash
# 开发者本地启动小蛋（源码模式）
cd "$(dirname "$0")"
exec ./venv/bin/python3 -u tracker.py

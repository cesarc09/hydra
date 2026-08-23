#!/bin/bash
# Launcher for the renderer installed beside this file.

PY="$(command -v python3 || command -v python)"
[ -z "$PY" ] && { echo "[statusline: python not found]"; exit 0; }

export PYTHONIOENCODING=utf-8
exec "$PY" "$(dirname "$0")/hydra_statusline.py"

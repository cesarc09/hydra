#!/bin/bash
# Default Hydra status line - context-window progress bar + model name.
# Copied to ~/.claude/statusline.sh by setup.sh (only on first run; user
# customizations there survive subsequent runs). Parses stdin with python
# (no jq dependency - python is already assumed by the Hydra hooks).

PY="$(command -v python3 || command -v python)"
[ -z "$PY" ] && { echo "[statusline: python not found]"; exit 0; }

export PYTHONIOENCODING=utf-8
exec "$PY" -c '
import sys, json
model, pct = "", 0
try:
    data = json.load(sys.stdin)
    m = data.get("model")
    if isinstance(m, dict):
        model = m.get("display_name") or ""
    cw = data.get("context_window")
    if isinstance(cw, dict):
        pct = int(float(cw.get("used_percentage") or 0))
except Exception:
    pass
pct = max(0, min(100, pct))
filled = pct * 10 // 100
print("[%s] %s %d%%" % (model, "▓" * filled + "░" * (10 - filled), pct))
'

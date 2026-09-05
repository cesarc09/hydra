#!/bin/bash

set -e

command -v codex >/dev/null 2>&1 || exit 0

python -m hydra_cli codex-setup || true
python -m hydra_cli hooks pull --harness codex-cli || true
CODEX_HOOKS="${CODEX_HOME:-$HOME/.codex}/hooks.json"
echo "  Codex hooks: $CODEX_HOOKS"

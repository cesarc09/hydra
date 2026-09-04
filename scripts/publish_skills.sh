#!/usr/bin/env bash
# Seed the Hydra skills store from client/skills or a supplied source directory.
# Once per migrated command, run `python -m hydra_cli commands delete <name>` so
# the next `commands pull` prunes its legacy ~/.claude/commands/<name>.md file.
#
# Usage: scripts/publish_skills.sh [SOURCE_DIR]
set -euo pipefail

cd "$(dirname "$0")/.."
exec python scripts/publish_skills.py "$@"

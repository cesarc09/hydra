#!/bin/bash
# Renders ~/.claude/settings.json by merging the Hydra hooks template with
# the user's local preference file (~/.claude/settings.user.json). On first
# run the user file is scaffolded from client/settings.user.template.json.
# Run on each machine after cloning or pulling updates.

set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
CLAUDE_DIR="$HOME/.claude"

mkdir -p "$CLAUDE_DIR"

# Hydra server URL. Set HYDRA_URL in the shell (or in a .env sourced before
# running setup.sh); defaults to localhost for a Pi running hydra locally.
HYDRA_URL="${HYDRA_URL:-http://localhost:8400}"

# Repo root is the parent of client/. Baked into the rendered settings.json
# so the SessionStart hook can `cd` back here regardless of where you cloned.
HYDRA_REPO_PATH="$(dirname "$SCRIPT_DIR")"

# Install/refresh the hydra CLI into the SAME interpreter the hooks use:
# `python -m pip`, not bare `pip` (which may belong to a different Python, or be
# absent while `pip3` exists). Keep it in an `if` so a pip failure can't make
# `set -e` abort the whole script silently, and let pip's error surface.
if python -m pip install -e "$SCRIPT_DIR" --quiet \
   || python -m pip install --user --break-system-packages -e "$SCRIPT_DIR" --quiet; then
    echo "  Installed: hydra CLI"
else
    echo "  WARNING: pip install failed (also tried --user --break-system-packages) - see output above." >&2
fi

# Gate on the CLI being importable by `python` before apply-settings needs it,
# so a missing install fails loudly and actionably instead of cryptically.
if ! python -c "import hydra_cli" >/dev/null 2>&1; then
    echo "ERROR: hydra_cli is not importable by '$(command -v python || echo python)'." >&2
    echo "  Install it for that interpreter:  python -m pip install -e $SCRIPT_DIR" >&2
    echo "  On externally-managed Python (PEP 668): add --break-system-packages, or use a venv." >&2
    exit 1
fi

export SCRIPT_DIR CLAUDE_DIR HYDRA_URL HYDRA_REPO_PATH
bash "$SCRIPT_DIR/setup_claude.sh"
bash "$SCRIPT_DIR/setup_codex.sh"

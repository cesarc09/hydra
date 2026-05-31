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
if python -m pip install -e "$SCRIPT_DIR" --quiet; then
    echo "  Installed: hydra CLI"
else
    echo "  WARNING: 'python -m pip install -e $SCRIPT_DIR' failed - see output above." >&2
fi

# Gate on the CLI being importable by `python` before apply-settings needs it,
# so a missing install fails loudly and actionably instead of cryptically.
if ! python -c "import hydra_cli" >/dev/null 2>&1; then
    echo "ERROR: hydra_cli is not importable by '$(command -v python || echo python)'." >&2
    echo "  Install it for that interpreter:  python -m pip install -e $SCRIPT_DIR" >&2
    echo "  On externally-managed Python (PEP 668): add --break-system-packages, or use a venv." >&2
    exit 1
fi

TARGET="$CLAUDE_DIR/settings.json"
USER_FILE="$CLAUDE_DIR/settings.user.json"

# Back up an existing rendered settings.json before rewriting it.
if [ -f "$TARGET" ] && [ ! -L "$TARGET" ]; then
    cp "$TARGET" "$TARGET.bak"
fi

python -m hydra_cli apply-settings \
    --hydra-template "$SCRIPT_DIR/settings.json" \
    --user-template "$SCRIPT_DIR/settings.user.template.json" \
    --user-file "$USER_FILE" \
    --output "$TARGET" \
    --hydra-url "$HYDRA_URL" \
    --hydra-repo-path "$HYDRA_REPO_PATH"

# Scaffold the default status-line script. Only on first run - user edits stay.
STATUSLINE_SRC="$SCRIPT_DIR/statusline.sh"
STATUSLINE_DST="$CLAUDE_DIR/statusline.sh"
if [ -f "$STATUSLINE_SRC" ] && [ ! -f "$STATUSLINE_DST" ]; then
    cp "$STATUSLINE_SRC" "$STATUSLINE_DST"
    chmod +x "$STATUSLINE_DST"
    echo "  Scaffolded: $STATUSLINE_DST  (edit to customize; survives re-runs)"
fi

# Deploy Hydra-shipped slash commands. Overwrite on each run so repo edits
# propagate across machines (unlike statusline.sh, these are hydra-owned).
COMMANDS_SRC="$SCRIPT_DIR/commands"
COMMANDS_DST="$CLAUDE_DIR/commands"
if [ -d "$COMMANDS_SRC" ]; then
    mkdir -p "$COMMANDS_DST"
    cp "$COMMANDS_SRC"/*.md "$COMMANDS_DST"/ 2>/dev/null || true
    echo "  Installed: slash commands -> $COMMANDS_DST"
fi

echo "  Installed: settings.json -> $TARGET  (HYDRA_URL=${HYDRA_URL}, repo=${HYDRA_REPO_PATH})"
echo "  User prefs:                 $USER_FILE  (edit to customize; survives re-runs)"
echo ""
echo "Claude config deployed from $SCRIPT_DIR"
echo ""

# Check Hydra env vars
if [ -z "$HYDRA_INSTANCE_ID" ]; then
    echo "NOTE: Hydra hooks are configured but required env vars may not be set."
    echo "Add these to your shell profile (~/.bashrc or ~/.zshrc):"
    echo ""
    echo "  export HYDRA_INSTANCE_ID=\"$(hostname)\"    # unique name for this machine"
    echo "  export HYDRA_AUTH_TOKEN=\"your-token\"       # must match Hydra server .env"
    echo "  export HYDRA_URL=\"https://hydra.example.com\"  # omit for localhost:8400"
    echo ""
else
    echo "Hydra instance: $HYDRA_INSTANCE_ID"
fi

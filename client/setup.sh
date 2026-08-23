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

TARGET="$CLAUDE_DIR/settings.json"
USER_FILE="$CLAUDE_DIR/settings.user.json"

# Back up an existing rendered settings.json before rewriting it.
if [ -f "$TARGET" ] && [ ! -L "$TARGET" ]; then
    cp "$TARGET" "$TARGET.bak"
fi

# Pull server-distributed policy hooks BEFORE rendering, so their wiring layer
# is fresh when the merge below reads it. Keeping the render in one place means
# `hooks pull` never has to re-enter apply-settings itself. Soft-fails: an
# unreachable server leaves the previous layer in place.
python -m hydra_cli hooks pull || true

python -m hydra_cli apply-settings \
    --hydra-template "$SCRIPT_DIR/settings.json" \
    --user-template "$SCRIPT_DIR/settings.user.template.json" \
    --user-file "$USER_FILE" \
    --hooks-layer "$CLAUDE_DIR/settings.hooks.json" \
    --output "$TARGET" \
    --hydra-url "$HYDRA_URL" \
    --hydra-repo-path "$HYDRA_REPO_PATH"

# Refresh the namespaced, Hydra-managed status-line scripts.
for STATUSLINE_FILE in hydra_statusline.sh hydra_statusline.py; do
    STATUSLINE_SRC="$SCRIPT_DIR/$STATUSLINE_FILE"
    [ -f "$STATUSLINE_SRC" ] || continue
    cp "$STATUSLINE_SRC" "$CLAUDE_DIR/$STATUSLINE_FILE"
done
if [ -f "$CLAUDE_DIR/hydra_statusline.sh" ]; then
    chmod +x "$CLAUDE_DIR/hydra_statusline.sh"
fi

# Slash commands are no longer copied from the repo. The Hydra server is the
# single distribution source: the `commands pull` SessionStart hook writes them
# into ~/.claude/commands from the server (seeded via scripts/publish_commands.sh).

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

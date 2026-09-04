#!/bin/bash

set -e

if [ -z "${SCRIPT_DIR:-}" ]; then
    SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
    CLAUDE_DIR="$HOME/.claude"
    HYDRA_URL="${HYDRA_URL:-http://localhost:8400}"
    HYDRA_REPO_PATH="$(dirname "$SCRIPT_DIR")"
fi

mkdir -p "$CLAUDE_DIR"
TARGET="$CLAUDE_DIR/settings.json"
USER_FILE="$CLAUDE_DIR/settings.user.json"

# Back up an existing rendered settings.json before rewriting it.
if [ -f "$TARGET" ] && [ ! -L "$TARGET" ]; then
    cp "$TARGET" "$TARGET.bak"
fi

# Pull server-distributed content before rendering the settings layer.
python -m hydra_cli hooks pull || true
python -m hydra_cli skills pull --harness claude-code || true

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

# Slash commands are pulled from the server by the SessionStart hook.

echo "  Installed: settings.json -> $TARGET  (HYDRA_URL=${HYDRA_URL}, repo=${HYDRA_REPO_PATH})"
echo "  User prefs:                 $USER_FILE  (edit to customize; survives re-runs)"
echo ""
echo "Claude config deployed from $SCRIPT_DIR"
echo ""

# Check Hydra env vars
if [ -z "${HYDRA_INSTANCE_ID:-}" ]; then
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

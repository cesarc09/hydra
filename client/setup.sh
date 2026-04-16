#!/bin/bash
# Copies claude-config files into ~/.claude/
# Run this on each machine after cloning or pulling updates.
#
# On Linux/macOS with symlink support, use: ./setup.sh --link
# On Windows (Git Bash), default copy mode is used.

set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
CLAUDE_DIR="$HOME/.claude"
MODE="copy"

if [ "$1" = "--link" ]; then
    MODE="link"
fi

mkdir -p "$CLAUDE_DIR"

for file in settings.json; do
    src="$SCRIPT_DIR/$file"
    target="$CLAUDE_DIR/$file"

    if [ -f "$target" ] && [ ! -L "$target" ]; then
        if ! diff -q "$src" "$target" > /dev/null 2>&1; then
            echo "Backing up existing $target to $target.bak"
            cp "$target" "$target.bak"
        fi
    fi

    if [ "$MODE" = "link" ]; then
        ln -sf "$src" "$target"
        echo "  Linked: $file -> $target"
    else
        cp "$src" "$target"
        echo "  Copied: $file -> $target"
    fi
done

echo ""
echo "Claude config deployed from $SCRIPT_DIR (mode: $MODE)"

# Install hydra CLI (editable, so git pull keeps it current)
if command -v pip >/dev/null 2>&1; then
    pip install -e "$SCRIPT_DIR" --quiet 2>/dev/null
    echo "  Installed: hydra CLI"
fi

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

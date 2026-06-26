#!/usr/bin/env bash
# Seed the Hydra server's command store from a directory of *.md files.
# The server is the single distribution source for slash commands; this script
# is how repo-authored public commands get into it. Each <name>.md is PUT to
# /api/config/commands/<name> (idempotent upsert).
#
# Usage:
#   scripts/publish_commands.sh [SOURCE_DIR]   # defaults to client/commands
#
# SOURCE_DIR is a parameter so the command source can move without touching this script.
# Uses curl (not `hydra_cli commands put`) on purpose: a server-only host need
# not have the client package installed to seed itself.
#
# Env: HYDRA_AUTH_TOKEN (required unless the server runs with HYDRA_ALLOW_NO_AUTH);
#      HYDRA_URL (defaults to the loopback server on the deploy host).
set -euo pipefail

cd "$(dirname "$0")/.."

SRC="${1:-client/commands}"
HYDRA_URL="${HYDRA_URL:-http://localhost:8400}"

if [ ! -d "$SRC" ]; then
    echo "publish_commands: source dir not found: $SRC" >&2
    exit 1
fi

if [ -z "${HYDRA_AUTH_TOKEN:-}" ]; then
    echo "publish_commands: WARNING: HYDRA_AUTH_TOKEN is empty; PUTs will 401" \
         "unless the server runs with HYDRA_ALLOW_NO_AUTH=1." >&2
fi

shopt -s nullglob
published=0
for f in "$SRC"/*.md; do
    name="$(basename "$f" .md)"
    # --data-binary preserves bytes exactly (no trailing-newline stripping).
    curl -sf -X PUT "$HYDRA_URL/api/config/commands/$name" \
        -H "Authorization: Bearer ${HYDRA_AUTH_TOKEN:-}" \
        -H "Content-Type: text/plain" \
        --data-binary @"$f" >/dev/null
    echo "  published: $name"
    published=$((published + 1))
done

if [ "$published" -eq 0 ]; then
    echo "publish_commands: no *.md files in $SRC"
else
    echo "publish_commands: $published command(s) published to $HYDRA_URL"
fi

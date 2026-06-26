"""Pull server-distributed slash commands into ~/.claude/commands.

The Hydra server is the single distribution source for slash commands. This
module fetches them in one round trip and writes each into ~/.claude/commands.
A small state file records which names this client wrote, so prune only ever
deletes files the puller itself created - never hand-authored or repo-shipped
commands.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

from hydra_cli import api

# A command name maps 1:1 to a filename (<name>.md -> /<name>), so a slug step
# would silently rename it (code-review -> code_review). Names are written
# verbatim; this guard only rejects ones that could escape the dir or break the
# command name. The server enforces the same charset on write.
_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]*$")

# Tracks the names written by the last successful pull, scoping prune to them.
_STATE_FILENAME = ".hydra-commands.json"


def commands_dir() -> Path:
    return Path.home() / ".claude" / "commands"


def _state_path() -> Path:
    return commands_dir().parent / _STATE_FILENAME


def _load_managed() -> set[str]:
    try:
        data = json.loads(_state_path().read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return set()
    managed = data.get("managed", []) if isinstance(data, dict) else []
    return {n for n in managed if isinstance(n, str)}


def _save_managed(names: set[str]) -> None:
    _state_path().write_text(
        json.dumps({"managed": sorted(names)}, indent=2) + "\n", encoding="utf-8"
    )


def run_pull() -> int:
    """Fetch all server commands, write them into ~/.claude/commands, and prune
    previously-managed commands the server no longer serves. Returns 0 on
    success, 1 on a fetch error (non-fatal in the hook, which also appends
    `|| true`)."""
    status, body = api.get("/api/config/commands")
    if status != 200:
        print(f"  commands pull failed ({status}): {body}", file=sys.stderr)
        return 1
    try:
        served: dict[str, str] = json.loads(body)
    except json.JSONDecodeError:
        print("  commands pull failed: invalid JSON from server", file=sys.stderr)
        return 1

    cdir = commands_dir()
    cdir.mkdir(parents=True, exist_ok=True)

    written: set[str] = set()
    for name, content in served.items():
        if not _NAME_RE.match(name):
            print(f"  skip (unsafe command name): {name!r}", file=sys.stderr)
            continue
        (cdir / f"{name}.md").write_text(content, encoding="utf-8")
        written.add(name)

    # Prune only names this client wrote on a previous pull that the server has
    # since dropped - never globs, so hand-authored commands are untouched.
    previously = _load_managed()
    pruned = previously - written
    for name in pruned:
        (cdir / f"{name}.md").unlink(missing_ok=True)
        print(f"  pruned (server-removed): {name}.md")

    _save_managed(written)
    print(f"  commands pull: {len(written)} written, {len(pruned)} pruned")
    return 0

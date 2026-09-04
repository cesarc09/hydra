"""Codex hook setup and SessionStart entry point."""

from __future__ import annotations

import contextlib
import json
import os
import stat
import sys
from pathlib import Path
from typing import Any

from hydra_cli.skills import codex_home, run_pull
from hydra_cli.sync import MEMORY_INDEX, memory_dir_for_cwd, run_sync

# Codex passed an 18KB index through at its default additionalContextLimit
# (measured 2026-09-04, 0.150.1); over the cap, whole lines are kept and the
# remainder is counted so the agent knows to read the file.
_CONTEXT_CAP = 32000
_HOOKS: dict[str, tuple[str, dict[str, Any]]] = {
    "SessionStart": (
        "startup|resume|clear|compact",
        {
            "type": "command",
            "command": "python -m hydra_cli codex-session-start",
            "timeout": 20,
            "statusMessage": "Syncing Hydra context",
        },
    ),
    "PreToolUse": (
        "Bash|apply_patch",
        {
            "type": "command",
            "command": "python -m hydra_cli guard",
            "timeout": 10,
        },
    ),
}


def hooks_path() -> Path:
    return codex_home() / "hooks.json"


def _read_cwd() -> str:
    try:
        raw = sys.stdin.read()
        payload = json.loads(raw) if raw else {}
    except (OSError, UnicodeError, ValueError):
        payload = {}
    cwd = payload.get("cwd") if isinstance(payload, dict) else None
    return cwd if isinstance(cwd, str) and cwd else os.getcwd()


def _index_context(header: str, index: Path) -> str:
    text = index.read_text(encoding="utf-8")
    combined = f"{header}\n\n{text}"
    if len(combined.encode()) <= _CONTEXT_CAP:
        return combined
    lines = text.splitlines()
    kept: list[str] = []
    for shown, line in enumerate(lines, start=1):
        trailer = f"\n\n(+{len(lines) - shown} more lines not shown - read {index})"
        candidate = f"{header}\n\n" + "\n".join([*kept, line]) + trailer
        if len(candidate.encode()) > _CONTEXT_CAP:
            break
        kept.append(line)
    trailer = f"\n\n(+{len(lines) - len(kept)} more lines not shown - read {index})"
    return f"{header}\n\n" + "\n".join(kept) + trailer


def run_session_start() -> int:
    cwd = _read_cwd()
    with contextlib.redirect_stdout(sys.stderr):
        for label, action in (
            ("memory sync", lambda: run_sync(cwd)),
            ("skills pull", lambda: run_pull("codex-cli")),
        ):
            try:
                action()
            except Exception as exc:
                print(f"codex-session-start: {label} failed: {exc}", file=sys.stderr)

    memory_dir = memory_dir_for_cwd(cwd).absolute()
    header = f"Hydra memory index: {memory_dir}"
    context = header
    index = memory_dir / MEMORY_INDEX
    try:
        if index.is_file():
            context = _index_context(header, index)
    except (OSError, UnicodeError) as exc:
        print(f"codex-session-start: memory index read failed: {exc}", file=sys.stderr)
    print(
        json.dumps(
            {
                "hookSpecificOutput": {
                    "hookEventName": "SessionStart",
                    "additionalContext": context,
                }
            }
        )
    )
    return 0


def _write_hooks(path: Path, config: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    mode = stat.S_IMODE(path.stat().st_mode) if path.exists() else 0o600
    tmp = path.with_name(f".{path.name}.tmp")
    try:
        tmp.write_text(json.dumps(config, indent=2) + "\n", encoding="utf-8")
        tmp.chmod(mode)
        tmp.replace(path)
    finally:
        tmp.unlink(missing_ok=True)


def _wire_event(
    hooks: dict[str, Any], event: str, matcher: str, expected: dict[str, Any]
) -> str:
    groups = hooks.setdefault(event, [])
    if not isinstance(groups, list):
        raise ValueError(f"hooks.{event} is not a list")

    entries: list[dict[str, Any]] = []
    for group in groups:
        if not isinstance(group, dict) or not isinstance(group.get("hooks"), list):
            raise ValueError(f"hooks.{event} contains an invalid group")
        for entry in group["hooks"]:
            if not isinstance(entry, dict):
                raise ValueError(f"hooks.{event} contains an invalid entry")
            entries.append(entry)

    command = expected["command"]
    if any(entry.get("command") == command for entry in entries):
        return "already wired"

    stale = [
        entry
        for entry in entries
        if isinstance(entry.get("command"), str)
        and entry["command"].startswith("python -m hydra_cli ")
    ]
    if not stale:
        groups.append({"matcher": matcher, "hooks": [expected]})
        return "wired"

    first = stale[0]
    first.clear()
    first.update(expected)
    extras = {id(entry) for entry in stale[1:]}
    kept_groups = []
    for group in groups:
        group["hooks"] = [entry for entry in group["hooks"] if id(entry) not in extras]
        if group["hooks"]:
            kept_groups.append(group)
    groups[:] = kept_groups
    return "rewritten"


def run_setup() -> int:
    path = hooks_path()
    if path.is_symlink():
        print(f"codex-setup refused symlink: {path}", file=sys.stderr)
        return 1
    try:
        if path.exists():
            config = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(config, dict):
                raise ValueError("top-level value is not an object")
        else:
            config = {}
        hooks = config.setdefault("hooks", {})
        if not isinstance(hooks, dict):
            raise ValueError("hooks is not an object")
        actions = [
            f"{event} {_wire_event(hooks, event, matcher, entry)}"
            for event, (matcher, entry) in _HOOKS.items()
        ]
        changed = any(not action.endswith("already wired") for action in actions)
        if changed:
            _write_hooks(path, config)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"codex-setup refused hooks file: {path}: {exc}", file=sys.stderr)
        return 1

    # New or changed hooks are skipped until the user trusts them via /hooks.
    print(f"  codex-setup: {', '.join(actions)}: {path}")
    return run_pull("codex-cli")

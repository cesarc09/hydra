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

_CONTEXT_CAP = 8000
_COMMAND = "python -m hydra_cli codex-session-start"
_ENTRY: dict[str, Any] = {
    "type": "command",
    "command": _COMMAND,
    "timeout": 20,
    "statusMessage": "Syncing Hydra context",
}
_GROUP: dict[str, Any] = {
    "matcher": "startup|resume|clear|compact",
    "hooks": [_ENTRY],
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
            combined = f"{header}\n\n{index.read_text(encoding='utf-8')}"
            if len(combined.encode()) <= _CONTEXT_CAP:
                context = combined
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
        session_start = hooks.setdefault("SessionStart", [])
        if not isinstance(session_start, list):
            raise ValueError("hooks.SessionStart is not a list")

        entries: list[dict[str, Any]] = []
        for group in session_start:
            if not isinstance(group, dict) or not isinstance(group.get("hooks"), list):
                raise ValueError("hooks.SessionStart contains an invalid group")
            for entry in group["hooks"]:
                if not isinstance(entry, dict):
                    raise ValueError("hooks.SessionStart contains an invalid entry")
                entries.append(entry)

        if any(entry.get("command") == _COMMAND for entry in entries):
            action = "already wired"
            changed = False
        else:
            stale = [
                entry
                for entry in entries
                if isinstance(entry.get("command"), str)
                and entry["command"].startswith("python -m hydra_cli ")
            ]
            if stale:
                first = stale[0]
                first.clear()
                first.update(_ENTRY)
                extras = {id(entry) for entry in stale[1:]}
                kept_groups = []
                for group in session_start:
                    group["hooks"] = [
                        entry for entry in group["hooks"] if id(entry) not in extras
                    ]
                    if group["hooks"]:
                        kept_groups.append(group)
                session_start[:] = kept_groups
                action = "rewritten"
            else:
                session_start.append(_GROUP)
                action = "wired"
            changed = True
        if changed:
            _write_hooks(path, config)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"codex-setup refused hooks file: {path}: {exc}", file=sys.stderr)
        return 1

    # Codex trusts hook bytes by a trusted_hash confirmed by the user via /hooks.
    print(f"  codex-setup: {action}: {path}")
    return run_pull("codex-cli")

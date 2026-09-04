"""Fail-open guard for Hydra memory writes."""

from __future__ import annotations

import json
import os
import re
import shlex
import sys
from collections.abc import Mapping
from pathlib import Path
from typing import Any

_FILE_TOOLS = {"Write", "Edit", "NotebookEdit"}
_MEMORY_VERBS = {"create", "update", "delete"}
_SEPARATORS = frozenset("|;&()")
_ASSIGNMENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=.*$", re.DOTALL)
_PATCH_PATH = re.compile(
    r"^\*\*\* (?:Add File|Update File|Delete File|Move to): (.+)$",
    re.MULTILINE,
)

_GENERIC_REASON = (
    "Memory writes belong to a human-gated flow and are refused mid-session. "
    "The CLI accepts `--flow <name>` only from that flow. Park the fact in the "
    "session scratchpad and raise it at the end of the session."
)
_INDEX_PREFIX = "MEMORY.md is generated and rewritten on every pull. "
_AMBIGUOUS_PREFIX = "This command contains an ambiguous memory write. "


def _reason(env: Mapping[str, str], prefix: str = "") -> str:
    reason = f"{prefix}{_GENERIC_REASON}"
    if "HYDRA_FLOW_HINT" in env:
        reason += f"\n\n{env['HYDRA_FLOW_HINT']}"
    return reason


def _cwd(payload: dict[str, Any]) -> Path:
    cwd = payload.get("cwd")
    return Path(cwd) if isinstance(cwd, str) and cwd else Path(os.getcwd())


def _resolve_path(raw: str, cwd: Path) -> Path:
    path = Path(raw)
    if not path.is_absolute():
        path = cwd / path
    return path.resolve()


def _is_memory_target(path: Path, env: Mapping[str, str], home: Path) -> bool:
    config_value = env.get("CLAUDE_CONFIG_DIR")
    config = Path(config_value) if config_value else home / ".claude"
    projects = (config / "projects").resolve()
    try:
        relative = path.relative_to(projects)
    except ValueError:
        return False
    return len(relative.parts) >= 3 and relative.parts[1] == "memory"


def _split_newlines(command: str) -> str:
    """Turn shell newlines outside quotes into command separators."""
    out: list[str] = []
    quote: str | None = None
    escaped = False
    for char in command:
        if escaped:
            out.append(char)
            escaped = False
            continue
        if char == "\\" and quote != "'":
            out.append(char)
            escaped = True
            continue
        if char in {"'", '"'}:
            if quote is None:
                quote = char
            elif quote == char:
                quote = None
            out.append(char)
            continue
        out.append(";" if char == "\n" and quote is None else char)
    return "".join(out)


def _simple_commands(command: str) -> list[list[str]]:
    lex = shlex.shlex(_split_newlines(command), posix=True, punctuation_chars=True)
    lex.whitespace_split = True
    commands: list[list[str]] = [[]]
    for token in lex:
        if token and all(char in _SEPARATORS for char in token):
            commands.append([])
        else:
            commands[-1].append(token.strip())
    return [words for words in commands if words]


def _basename(word: str) -> str:
    return word.replace("\\", "/").rsplit("/", 1)[-1]


def _is_memory_command(words: list[str]) -> bool:
    while words and _ASSIGNMENT.fullmatch(words[0]):
        words = words[1:]
    if any(word == "--flow" or word.startswith("--flow=") for word in words):
        return False

    module_invocation = any(
        words[index : index + 2] == ["-m", "hydra_cli"]
        for index in range(len(words) - 1)
    )
    for index in range(len(words) - 1):
        if words[index] != "memory" or words[index + 1] not in _MEMORY_VERBS:
            continue
        if index > 0 and _basename(words[index - 1]) == "hydra":
            return True
        if module_invocation:
            return True
    return False


def _guard_bash(command: str, env: Mapping[str, str]) -> str | None:
    try:
        commands = _simple_commands(command)
    except ValueError:
        if "memory" in command and ("hydra" in command or "hydra_cli" in command):
            return _reason(env, _AMBIGUOUS_PREFIX)
        return None
    if any(_is_memory_command(words) for words in commands):
        return _reason(env)
    return None


def run_guard(stdin_text: str, env: Mapping[str, str], *, home: Path) -> str | None:
    """Return a denial reason for guarded memory writes, otherwise None."""
    try:
        payload = json.loads(stdin_text)
    except (json.JSONDecodeError, TypeError):
        return None
    if not isinstance(payload, dict) or "tool_input" not in payload:
        return None
    tool_input = payload["tool_input"]
    tool_name = payload.get("tool_name")
    if not isinstance(tool_input, dict) or not isinstance(tool_name, str):
        return None

    cwd = _cwd(payload)
    if tool_name in _FILE_TOOLS:
        key = "notebook_path" if tool_name == "NotebookEdit" else "file_path"
        raw_path = tool_input.get(key)
        if not isinstance(raw_path, str):
            return None
        target = _resolve_path(raw_path, cwd)
        if not _is_memory_target(target, env, home.resolve()):
            return None
        prefix = _INDEX_PREFIX if target.name == "MEMORY.md" else ""
        return _reason(env, prefix)

    if tool_name == "apply_patch":
        command = tool_input.get("command")
        if not isinstance(command, str):
            return None
        for match in _PATCH_PATH.finditer(command):
            target = _resolve_path(match.group(1).strip(), cwd)
            if _is_memory_target(target, env, home.resolve()):
                return _reason(env)
        return None

    if tool_name == "Bash":
        command = tool_input.get("command")
        return _guard_bash(command, env) if isinstance(command, str) else None
    return None


def main() -> int:
    try:
        reason = run_guard(sys.stdin.read(), os.environ, home=Path.home())
        if reason is not None:
            print(
                json.dumps(
                    {
                        "hookSpecificOutput": {
                            "hookEventName": "PreToolUse",
                            "permissionDecision": "deny",
                            "permissionDecisionReason": reason,
                        }
                    }
                )
            )
    except Exception:
        pass
    return 0


if __name__ == "__main__":
    main()

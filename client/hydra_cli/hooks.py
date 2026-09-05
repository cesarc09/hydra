"""Pull server-distributed policy hooks into Claude Code or Codex."""

from __future__ import annotations

import copy
import json
import os
import re
import shutil
import sys
from pathlib import Path
from typing import Any

from hydra_cli import api
from hydra_cli.skills import HARNESSES, codex_home

_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]*$")
_FILENAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]*\.(?:py|sh)$")
_STATE_FILENAME = ".hydra-hooks.json"
_DISABLE_ENV = "HYDRA_POLICY_HOOKS_DISABLE"

_RUNTIMES: dict[str, tuple[str, str]] = {
    "python": (".py", "python"),
    "bash": (".sh", "bash"),
}


def hooks_dir(harness: str = "claude-code") -> Path:
    if harness == "claude-code":
        return Path.home() / ".claude" / "hooks"
    if harness == "codex-cli":
        return codex_home() / "hooks"
    raise ValueError(f"unsupported harness: {harness}")


def _hooks_dir_for(harness: str) -> Path:
    return hooks_dir() if harness == "claude-code" else hooks_dir(harness)


def wiring_path() -> Path:
    return hooks_dir().parent / "settings.hooks.json"


def _state_path(harness: str = "claude-code") -> Path:
    return _hooks_dir_for(harness).parent / _STATE_FILENAME


def _load_state(harness: str) -> tuple[set[str], set[str]]:
    try:
        data = json.loads(_state_path(harness).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return set(), set()
    if not isinstance(data, dict):
        return set(), set()

    def filenames(key: str) -> set[str]:
        values = data.get(key, [])
        if not isinstance(values, list):
            return set()
        return {
            value
            for value in values
            if isinstance(value, str) and _FILENAME_RE.fullmatch(value)
        }

    return filenames("managed"), filenames("dropped")


def managed_filenames() -> set[str]:
    """Claude hook filenames currently owned by the policy layer."""
    return _load_state("claude-code")[0]


def _atomic_write(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.tmp")
    try:
        tmp.write_bytes(content)
        tmp.replace(path)
    finally:
        tmp.unlink(missing_ok=True)


def _save_state(harness: str, managed: set[str], dropped: set[str]) -> None:
    content = (
        json.dumps(
            {"managed": sorted(managed), "dropped": sorted(dropped)}, indent=2
        )
        + "\n"
    ).encode()
    _atomic_write(_state_path(harness), content)


def _write_claude_wiring(hooks: dict[str, list[dict[str, Any]]]) -> None:
    content = (json.dumps({"hooks": hooks}, indent=2) + "\n").encode()
    _atomic_write(wiring_path(), content)


def _applies_here(instances: Any) -> bool:
    if not instances or not isinstance(instances, list):
        return True
    return os.environ.get("HYDRA_INSTANCE_ID", "").strip() in instances


def _install_script(
    path: Path,
    content: str,
    runtime: str,
    owned: set[str],
    *,
    adopt: bool,
) -> str:
    filename = path.name
    if path.is_symlink():
        print(f"refused: {path} (symlink)", file=sys.stderr)
        return "refused"
    if path.exists() and not path.is_file():
        print(f"refused: {path} (not a regular file)", file=sys.stderr)
        return "refused"
    try:
        existing = path.read_bytes()
    except FileNotFoundError:
        existing = None
    except OSError:
        print(f"refused: {path} (not a regular file)", file=sys.stderr)
        return "refused"
    if existing is not None and filename not in owned and not adopt:
        print(
            f"refused: {path} (unmanaged; rerun with --adopt to take ownership)",
            file=sys.stderr,
        )
        return "refused"
    if runtime == "python":
        try:
            compile(content, f"<hydra hook {path.stem}>", "exec")
        except SyntaxError as exc:
            if existing is not None and filename in owned:
                print(f"  retained (syntax error): {filename}: {exc}", file=sys.stderr)
                return "retained"
            print(f"  refused (syntax error): {filename}: {exc}", file=sys.stderr)
            return "refused"
    if existing != content.encode():
        _atomic_write(path, content.encode())
    path.chmod(0o755)
    return "installed"


def _command_path(filename: str) -> str:
    default = (Path.home() / ".codex").absolute()
    home = codex_home().absolute()
    if home == default:
        return f"$HOME/.codex/hooks/{filename}"
    path = str(home / "hooks" / filename)
    return path.replace("\\", "\\\\").replace('"', '\\"').replace("$", "\\$")


def _entry(
    harness: str, filename: str, interpreter: str, timeout: int
) -> dict[str, Any]:
    path = (
        f"$HOME/.claude/hooks/{filename}"
        if harness == "claude-code"
        else _command_path(filename)
    )
    return {
        "type": "command",
        "command": f'{interpreter} "{path}"',
        "timeout": timeout,
    }


def _group(spec: dict[str, Any], entry: dict[str, Any]) -> dict[str, Any]:
    group: dict[str, Any] = {"hooks": [entry]}
    matcher = spec.get("matcher")
    if isinstance(matcher, str) and matcher:
        group["matcher"] = matcher
    return group


def _owned_filename(group: Any, known: set[str]) -> str | None:
    """Return the state-owned filename for a single-entry group."""
    if not isinstance(group, dict):
        return None
    entries = group.get("hooks")
    if not isinstance(entries, list) or len(entries) != 1:
        return None
    entry = entries[0]
    if not isinstance(entry, dict) or not isinstance(entry.get("command"), str):
        return None
    command = entry["command"]
    for filename in known:
        if command.endswith(f'"{_command_path(filename)}"'):
            return filename
    return None


def _updated_codex_config(
    config: dict[str, Any],
    expected: dict[str, tuple[str, dict[str, Any]]],
    known: set[str],
) -> tuple[dict[str, Any], bool]:
    updated = copy.deepcopy(config)
    raw_hooks = updated.get("hooks")
    if raw_hooks is None:
        if not expected:
            return updated, False
        raw_hooks = {}
        updated["hooks"] = raw_hooks
    if not isinstance(raw_hooks, dict):
        raise ValueError("hooks is not an object")

    remaining = dict(expected)
    for event in list(raw_hooks):
        groups = raw_hooks[event]
        if not isinstance(groups, list):
            raise ValueError(f"hooks.{event} is not a list")
        kept = []
        for group in groups:
            filename = _owned_filename(group, known)
            if filename is None:
                kept.append(group)
                continue
            name = filename.rsplit(".", 1)[0]
            replacement = remaining.pop(name, None)
            if replacement is not None:
                new_event, new_group = replacement
                if new_event == event:
                    kept.append(new_group)
                else:
                    remaining[name] = replacement
        if kept:
            raw_hooks[event] = kept
        else:
            del raw_hooks[event]

    for _name, (event, group) in remaining.items():
        groups = raw_hooks.setdefault(event, [])
        if not isinstance(groups, list):
            raise ValueError(f"hooks.{event} is not a list")
        groups.append(group)
    return updated, updated != config


def _read_codex_config() -> tuple[Path, dict[str, Any]]:
    from hydra_cli.codex import hooks_path

    path = hooks_path()
    if path.is_symlink():
        raise ValueError(f"refused symlink: {path}")
    if not path.exists():
        return path, {}
    config = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(config, dict):
        raise ValueError("top-level value is not an object")
    return path, config


def _write_codex_config(path: Path, config: dict[str, Any]) -> None:
    from hydra_cli.codex import _write_hooks

    _write_hooks(path, config)


def _clear_wiring(harness: str, known: set[str]) -> bool:
    if harness == "claude-code":
        _write_claude_wiring({})
        return True
    path, config = _read_codex_config()
    updated, changed = _updated_codex_config(config, {}, known)
    if changed:
        _write_codex_config(path, updated)
    return changed


def run_pull(harness: str = "claude-code", *, adopt: bool = False) -> int:
    """Install and wire every server policy hook for one harness."""
    if harness not in HARNESSES:
        raise ValueError(f"unsupported harness: {harness}")
    previous, previous_dropped = _load_state(harness)
    known = previous | previous_dropped

    if os.environ.get(_DISABLE_ENV, "").strip():
        try:
            changed = _clear_wiring(harness, known)
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            print(f"  hooks pull [{harness}] failed: {exc}", file=sys.stderr)
            return 1
        print(f"  hooks pull [{harness}]: disabled via {_DISABLE_ENV}, wiring emptied")
        if harness == "codex-cli" and changed:
            print(
                "  hooks pull [codex-cli]: wiring changed; review trust with "
                "/hooks (t accepts all)"
            )
        return 0

    status, body = api.get(f"/api/config/hooks/render/{harness}")
    if status != 200:
        print(f"  hooks pull [{harness}] failed ({status}): {body}", file=sys.stderr)
        return 1
    try:
        served: dict[str, Any] = json.loads(body)
    except json.JSONDecodeError:
        print(f"  hooks pull [{harness}] failed: invalid JSON from server", file=sys.stderr)
        return 1
    if not isinstance(served, dict):
        print(f"  hooks pull [{harness}] failed: unexpected payload shape", file=sys.stderr)
        return 1

    codex_path: Path | None = None
    codex_config: dict[str, Any] | None = None
    if harness == "codex-cli":
        try:
            codex_path, codex_config = _read_codex_config()
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            print(f"  hooks pull [codex-cli] refused hooks file: {exc}", file=sys.stderr)
            return 1

    if not served:
        try:
            changed = _clear_wiring(harness, known)
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            print(f"  hooks pull [{harness}] failed: {exc}", file=sys.stderr)
            return 1
        print(
            f"  hooks pull [{harness}]: server served 0 hooks, keeping "
            f"{len(known)} local script(s) - nothing pruned",
            file=sys.stderr,
        )
        if harness == "codex-cli" and changed:
            print(
                "  hooks pull [codex-cli]: wiring changed; review trust with "
                "/hooks (t accepts all)"
            )
        return 0

    hdir = _hooks_dir_for(harness)
    hdir.mkdir(parents=True, exist_ok=True)
    managed: set[str] = set()
    refused: set[str] = set()
    wired_runtimes: set[str] = set()
    claude_wiring: dict[str, list[dict[str, Any]]] = {}
    codex_expected: dict[str, tuple[str, dict[str, Any]]] = {}
    counts = {"installed": 0, "retained": 0, "refused": 0}

    for name, spec in served.items():
        if not isinstance(name, str) or not _NAME_RE.fullmatch(name) or not isinstance(spec, dict):
            print(f"  skip (unsafe hook name or payload): {name!r}", file=sys.stderr)
            continue
        if not spec.get("enabled", True) or not _applies_here(spec.get("instances")):
            continue
        runtime = spec.get("runtime")
        event = spec.get("event")
        content = spec.get("content")
        matcher = spec.get("matcher")
        timeout = spec.get("timeout")
        if (
            runtime not in _RUNTIMES
            or not isinstance(event, str)
            or re.fullmatch(r"\S{1,64}", event) is None
            or not isinstance(content, str)
            or (matcher is not None and not isinstance(matcher, str))
            or isinstance(timeout, bool)
            or not isinstance(timeout, int)
            or not 1 <= timeout <= 600
        ):
            print(f"  skip (malformed hook): {name}", file=sys.stderr)
            continue

        suffix, interpreter = _RUNTIMES[runtime]
        filename = f"{name}{suffix}"
        outcome = _install_script(
            hdir / filename, content, runtime, known, adopt=adopt
        )
        counts[outcome] += 1
        if outcome == "refused":
            refused.add(filename)
            continue
        managed.add(filename)
        wired_runtimes.add(runtime)
        entry = _entry(harness, filename, interpreter, timeout)
        group = _group(spec, entry)
        if harness == "claude-code":
            claude_wiring.setdefault(event, []).append(group)
        else:
            codex_expected[name] = (event, group)

    try:
        changed = False
        if harness == "claude-code":
            _write_claude_wiring(claude_wiring)
        else:
            assert codex_path is not None and codex_config is not None
            updated, changed = _updated_codex_config(
                codex_config, codex_expected, known
            )
            if changed:
                _write_codex_config(codex_path, updated)

        # Wiring disappears on the first dropped pull. The prior generation is
        # deleted only now, after old sessions and Claude's render window passed.
        pruned = previous_dropped - managed - refused
        for filename in sorted(pruned):
            (hdir / filename).unlink(missing_ok=True)
            print(f"  pruned (server-removed): {filename}")
        dropped = previous - managed - refused
        _save_state(harness, managed, dropped)
    except (OSError, ValueError) as exc:
        print(f"  hooks pull [{harness}] failed: {exc}", file=sys.stderr)
        return 1

    for runtime in sorted(wired_runtimes):
        interpreter = _RUNTIMES[runtime][1]
        if shutil.which(interpreter) is None:
            print(
                f"  hooks pull [{harness}]: WARNING - {interpreter!r} is not on PATH; "
                f"the {runtime} hooks are wired but will not run",
                file=sys.stderr,
            )

    wired = len(managed)
    print(
        f"  hooks pull [{harness}]: {counts['installed']} installed, "
        f"{counts['retained']} retained, {len(pruned)} pruned, "
        f"{counts['refused']} refused, {wired} wired"
    )
    if harness == "codex-cli" and changed:
        print("  hooks pull [codex-cli]: wiring changed; review trust with /hooks (t accepts all)")
    return 1 if refused else 0

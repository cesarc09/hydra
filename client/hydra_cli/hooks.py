"""Pull server-distributed policy hooks into ~/.claude/hooks.

Each server row carries a hook's script body AND its settings.json wiring, and
this module keeps them together on the way down. The wiring is written to
~/.claude/settings.hooks.json, which `apply-settings` merges as a layer - see
setup.sh, which runs this pull immediately before the render so one renderer
stays in charge.

The load-bearing invariant is in `run_pull`: wiring is emitted ONLY for a hook
whose script is on disk afterwards. `python <missing>.py` exits 2, and exit 2
on PreToolUse is the *blocking* code, so wiring that outruns its script would
turn a fail-open guard into a hard deny of every tool call on that machine.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import sys
from pathlib import Path
from typing import Any

from hydra_cli import api

# A hook name maps 1:1 to a filename, so reject anything that could escape the
# directory. Same charset the server enforces on write.
_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]*$")

# Tracks the FILENAMES written by the last successful pull, scoping prune to
# them. Filenames rather than names so prune stays exact when a hook's runtime
# changes and its suffix moves with it.
_STATE_FILENAME = ".hydra-hooks.json"

# Set to disable the server-distributed policy hooks on this machine. Scope is
# exactly those: it empties the generated wiring layer and nothing else. Hydra's
# own telemetry and sync hooks live in client/settings.json, a layer this module
# never writes, so observability keeps working on a machine switched off here.
_DISABLE_ENV = "HYDRA_POLICY_HOOKS_DISABLE"

_RUNTIMES: dict[str, tuple[str, str]] = {
    # runtime -> (file suffix, interpreter)
    # `python`, never `python3`: Git Bash on Windows has no python3 on PATH, so
    # that wiring installed all four policy hooks there and ran none of them.
    # Bare name, not sys.executable - an absolute path churns the layer.
    "python": (".py", "python"),
    "bash": (".sh", "bash"),
}


def hooks_dir() -> Path:
    return Path.home() / ".claude" / "hooks"


def wiring_path() -> Path:
    return hooks_dir().parent / "settings.hooks.json"


def _state_path() -> Path:
    return hooks_dir().parent / _STATE_FILENAME


def _load_managed() -> set[str]:
    try:
        data = json.loads(_state_path().read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return set()
    managed = data.get("managed", []) if isinstance(data, dict) else []
    return {n for n in managed if isinstance(n, str)}


def managed_filenames() -> set[str]:
    """Script filenames the last successful pull installed. `apply-settings`
    reads this to strip user-file wiring the server has taken over."""
    return _load_managed()


def _save_managed(filenames: set[str]) -> None:
    _state_path().write_text(
        json.dumps({"managed": sorted(filenames)}, indent=2) + "\n", encoding="utf-8"
    )


def _write_wiring(hooks: dict[str, list[dict[str, Any]]]) -> None:
    """Write the layer atomically - apply-settings parses it moments later, and
    a torn write would be a JSON error at exactly the wrong time. Only a `hooks`
    key is ever emitted: `merge` replaces non-hooks keys wholesale, so anything
    else here would silently outrank the shipped defaults."""
    path = wiring_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps({"hooks": hooks}, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)


def _applies_here(instances: Any) -> bool:
    """True when this machine is in the hook's instance allowlist. None (or an
    empty list) means every machine."""
    if not instances:
        return True
    if not isinstance(instances, list):
        return True
    return os.environ.get("HYDRA_INSTANCE_ID", "").strip() in instances


def run_pull() -> int:
    """Fetch every server hook, write the ones that apply here into
    ~/.claude/hooks, prune previously-managed scripts the server no longer
    serves, and render the wiring layer. Returns 0 on success, 1 on a fetch
    error (non-fatal in the hook, which also appends `|| true`)."""
    if os.environ.get(_DISABLE_ENV, "").strip():
        _write_wiring({})
        print(f"  hooks pull: disabled via {_DISABLE_ENV}, wiring layer emptied")
        return 0

    status, body = api.get("/api/config/hooks")
    if status != 200:
        print(f"  hooks pull failed ({status}): {body}", file=sys.stderr)
        return 1
    try:
        served: dict[str, Any] = json.loads(body)
    except json.JSONDecodeError:
        print("  hooks pull failed: invalid JSON from server", file=sys.stderr)
        return 1
    if not isinstance(served, dict):
        print("  hooks pull failed: unexpected payload shape", file=sys.stderr)
        return 1

    hdir = hooks_dir()
    hdir.mkdir(parents=True, exist_ok=True)

    written: set[str] = set()
    retained: set[str] = set()
    wired_runtimes: set[str] = set()
    wiring: dict[str, list[dict[str, Any]]] = {}

    for name, spec in served.items():
        if not _NAME_RE.match(name) or not isinstance(spec, dict):
            print(f"  skip (unsafe hook name or payload): {name!r}", file=sys.stderr)
            continue
        runtime = spec.get("runtime")
        if runtime not in _RUNTIMES:
            print(f"  skip (unknown runtime {runtime!r}): {name}", file=sys.stderr)
            continue
        event = spec.get("event")
        content = spec.get("content")
        if not isinstance(event, str) or not event or not isinstance(content, str):
            print(f"  skip (malformed hook): {name}", file=sys.stderr)
            continue
        # A disabled hook, or one scoped to other machines, is not an error -
        # it just produces no wiring, and its script falls out of the managed
        # set below so the stale file is pruned.
        if not spec.get("enabled", True) or not _applies_here(spec.get("instances")):
            continue

        suffix, interpreter = _RUNTIMES[runtime]
        filename = f"{name}{suffix}"
        path = hdir / filename

        installed = True
        if runtime == "python":
            try:
                compile(content, f"<hydra hook {name}>", "exec")
            except SyntaxError as exc:
                installed = False
                print(
                    f"  skip (syntax error, keeping previous): {filename}: {exc}",
                    file=sys.stderr,
                )
        if installed:
            path.write_text(content, encoding="utf-8")
            path.chmod(0o755)
            written.add(filename)
        elif path.exists():
            # Last-good script stays wired. For a fail-open guard, running the
            # previous version beats running nothing.
            retained.add(filename)

        # Wire only what is actually on disk. This is what makes the exit-2
        # blocking failure structurally impossible rather than merely unlikely.
        if not path.exists():
            continue
        entry: dict[str, Any] = {
            "type": "command",
            "command": f'{interpreter} "$HOME/.claude/hooks/{filename}"',
        }
        timeout = spec.get("timeout")
        if isinstance(timeout, int):
            entry["timeout"] = timeout
        group: dict[str, Any] = {}
        matcher = spec.get("matcher")
        if isinstance(matcher, str) and matcher:
            group["matcher"] = matcher
        group["hooks"] = [entry]
        wiring.setdefault(event, []).append(group)
        wired_runtimes.add(runtime)

    previously = _load_managed()
    pruned: set[str] = set()
    if served:
        # Prune only files this client wrote on a previous pull - never a glob,
        # so hand-authored hooks in the same directory are untouched.
        pruned = previously - written - retained
        for filename in pruned:
            (hdir / filename).unlink(missing_ok=True)
            print(f"  pruned (server-removed): {filename}")
        _save_managed(written | retained)
    elif previously:
        # An empty server is never authority to delete. A wrong HYDRA_URL, a
        # fresh DB and a half-restored backup all look exactly like "every hook
        # was deleted", and the wiring layer is already empty, so the stale
        # scripts are inert until a real pull confirms them.
        print(
            f"  hooks pull: server served 0 hooks, keeping {len(previously)} "
            "local script(s) - nothing pruned",
            file=sys.stderr,
        )

    # A missing interpreter is invisible otherwise - scripts install, wiring
    # renders, nothing runs. Still wired: it exits 127, not the blocking 2.
    for runtime in sorted(wired_runtimes):
        interpreter = _RUNTIMES[runtime][1]
        if shutil.which(interpreter) is None:
            print(
                f"  hooks pull: WARNING - {interpreter!r} is not on PATH here, "
                f"so the {runtime} hooks are wired but will not run",
                file=sys.stderr,
            )

    _write_wiring(wiring)
    wired = sum(len(groups) for groups in wiring.values())
    print(
        f"  hooks pull: {len(written)} written, {len(pruned)} pruned, {wired} wired"
    )
    return 0

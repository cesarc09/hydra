"""Pull server-distributed Claude Code plugins into a Hydra-owned local marketplace."""

from __future__ import annotations

import json
import re
import sys
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from hydra_cli import api
from hydra_cli.skills import _applies_here, _atomic_write, _install

MARKETPLACE = "hydra"
_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]*$")


def plugins_root() -> Path:
    return Path.home() / ".claude" / "hydra-plugins"


def layer_path() -> Path:
    return Path.home() / ".claude" / "settings.plugins.json"


def state_path() -> Path:
    return Path.home() / ".claude" / ".hydra-plugins.json"


def path_error(paths: Iterable[str]) -> str | None:
    """Return why a plugin file key is unsafe on any client OS, or None.

    Duplicate of server/routers/config.py:plugin_path_error (the client cannot
    import server.*); tests run one case list against both."""
    seen: dict[str, str] = {}
    for path in paths:
        if not path:
            return "empty file path"
        if path.startswith("/"):
            return f"absolute file path: {path!r}"
        # Backslash is a Windows separator and ':' a drive or ADS marker.
        if "\\" in path or ":" in path or any(ord(ch) < 32 for ch in path):
            return f"forbidden character in file path: {path!r}"
        for segment in path.split("/"):
            if segment in ("", ".", ".."):
                return f"empty, '.' or '..' segment in file path: {path!r}"
            # Windows strips these, so 'a.' and 'a' would alias.
            if segment[-1] in ". ":
                return f"segment ends in '.' or space in file path: {path!r}"
        folded = path.casefold()
        if folded in seen:
            return f"file paths differ only by case: {seen[folded]!r} and {path!r}"
        seen[folded] = path
    return None


def _load_state(root: Path) -> tuple[set[Path], set[Path]]:
    try:
        data = json.loads(state_path().read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return set(), set()
    if not isinstance(data, dict):
        return set(), set()

    def paths(key: str) -> set[Path]:
        values = data.get(key, [])
        if not isinstance(values, list):
            return set()
        # Only paths under the root: prune deletes whatever this file names.
        return {
            Path(value)
            for value in values
            if isinstance(value, str) and root in Path(value).parents
        }

    return paths("managed"), paths("dropped")


def _save_state(managed: set[Path], dropped: set[Path]) -> None:
    content = (
        json.dumps(
            {"managed": sorted(map(str, managed)), "dropped": sorted(map(str, dropped))},
            indent=2,
        )
        + "\n"
    ).encode()
    _atomic_write(state_path(), content)


def _write_generated(root: Path, enabled: set[str]) -> None:
    manifest = {
        "name": MARKETPLACE,
        "owner": {"name": "Hydra"},
        "plugins": [{"name": name, "source": f"./{name}"} for name in sorted(enabled)],
    }
    _atomic_write(
        root / ".claude-plugin" / "marketplace.json",
        (json.dumps(manifest, indent=2) + "\n").encode(),
    )
    layer: dict[str, Any] = {}
    if enabled:
        layer = {
            "extraKnownMarketplaces": {
                MARKETPLACE: {"source": {"source": "directory", "path": root.as_posix()}}
            },
            "enabledPlugins": {f"{name}@{MARKETPLACE}": True for name in sorted(enabled)},
        }
    _atomic_write(layer_path(), (json.dumps(layer, indent=2) + "\n").encode())


def _targets(root: Path, name: str, spec: Any) -> list[tuple[Path, bytes]] | None:
    """Destination paths for one plugin, or None when its payload is unsafe."""
    if not isinstance(spec, dict):
        return None
    files = spec.get("files")
    if (
        not isinstance(files, dict)
        or not files
        or not all(isinstance(text, str) for text in files.values())
        or path_error(files) is not None
    ):
        return None
    base = root / name
    targets = []
    for rel, text in files.items():
        path = base.joinpath(*rel.split("/"))
        if base not in path.parents:
            return None
        targets.append((path, text.encode()))
    return targets


def _remove_empty_dirs(root: Path, path: Path) -> None:
    parent = path.parent
    while parent != root and root in parent.parents:
        try:
            parent.rmdir()
        except OSError:
            break
        parent = parent.parent


def run_pull(*, adopt: bool = False) -> int:
    """Install every applicable server plugin and render its marketplace + layer."""
    status, body = api.get("/api/config/plugins")
    if status != 200:
        print(f"  plugins pull failed ({status}): {body}", file=sys.stderr)
        return 1
    try:
        served = json.loads(body)
    except json.JSONDecodeError:
        print("  plugins pull failed: invalid JSON from server", file=sys.stderr)
        return 1
    if not isinstance(served, dict):
        print("  plugins pull failed: unexpected payload shape", file=sys.stderr)
        return 1

    root = plugins_root().absolute()
    previous, previous_dropped = _load_state(root)
    owned = previous | previous_dropped
    try:
        if not served:
            _write_generated(root, set())
            print(
                f"  plugins pull: server served 0 plugins, keeping {len(owned)} "
                f"local file(s) - nothing pruned",
                file=sys.stderr,
            )
            print("  plugins pull: 0 written, 0 unchanged, 0 pruned, 0 refused, 0 enabled")
            return 0

        managed: set[Path] = set()
        refused: set[Path] = set()
        enabled: set[str] = set()
        counts = {"written": 0, "unchanged": 0, "refused": 0}
        for name, spec in served.items():
            if not isinstance(name, str) or not _NAME_RE.fullmatch(name):
                print(f"  skip (unsafe plugin name): {name!r}", file=sys.stderr)
                continue
            targets = _targets(root, name, spec)
            if targets is None:
                print(f"  skip (malformed plugin): {name}", file=sys.stderr)
                continue
            if not spec.get("enabled", True) or not _applies_here(spec.get("instances")):
                continue
            complete = True
            for path, content in targets:
                result = _install(path, content, owned, adopt=adopt)
                counts[result] += 1
                if result == "refused":
                    refused.add(path)
                    complete = False
                else:
                    managed.add(path)
            # Enable only a fully installed plugin; a partial tree may not load.
            if complete:
                enabled.add(name)

        _write_generated(root, enabled)

        # Deferred like hooks: dropped files are deleted on the next pull, after
        # sessions holding the old set are gone (a removed plugin is disabled now).
        pruned = previous_dropped - managed - refused
        for path in sorted(pruned):
            path.unlink(missing_ok=True)
            _remove_empty_dirs(root, path)
        dropped = previous - managed - refused
        _save_state(managed, dropped)
    except OSError as exc:
        print(f"  plugins pull failed: {exc}", file=sys.stderr)
        return 1

    print(
        f"  plugins pull: {counts['written']} written, {counts['unchanged']} unchanged, "
        f"{len(pruned)} pruned, {counts['refused']} refused, {len(enabled)} enabled"
    )
    return 1 if counts["refused"] else 0

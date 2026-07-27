"""Compose ~/.claude/settings.json from four sources, in priority order:

  1. Hydra hooks template (shipped, always present)
  2. Server-distributed policy hooks (~/.claude/settings.hooks.json, generated
     by `hydra hooks pull`; absent is normal and means "no server hooks")
  3. User-template defaults (shipped: effortLevel, statusLine, ...)
  4. User overrides (~/.claude/settings.user.json - scaffolded as a copy of
     the template on first run so users can see what's customizable)

For each event under `hooks`, Hydra's matcher-groups come first and any user
groups append. For other top-level keys, later sources override earlier ones
- so template defaults beat the Hydra base and user overrides beat both.

Crucially: a key *deleted* from the user file falls back to the template
default rather than disappearing - so users can drop fields they don't want
to customize without losing the default behavior.

User files scaffolded from older templates are migrated in place (and the
migration is reported on stderr): `effortLevel: "max"` is dropped (it targeted
the removed env-var path and can't be overridden in-session), a top-level
`defaultMode` is moved inside `permissions`, where Claude Code expects it, and
wiring for a hook the server now distributes is removed - `merge` concatenates
per-event groups rather than deduping them, so a hook left in both layers would
fire twice.
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path
from typing import Any

from hydra_cli.hooks import managed_filenames


def substitute(text: str, hydra_url: str, hydra_repo_path: str) -> str:
    return text.replace("__HYDRA_URL__", hydra_url).replace(
        "__HYDRA_REPO_PATH__", hydra_repo_path
    )


def merge(hydra: dict[str, Any], user: dict[str, Any]) -> dict[str, Any]:
    """Hooks: per-event arrays concatenate (Hydra first). Other keys: user wins."""
    result: dict[str, Any] = dict(hydra)
    for key, user_val in user.items():
        if key == "hooks" and isinstance(user_val, dict):
            merged_hooks: dict[str, Any] = dict(hydra.get("hooks") or {})
            for event_name, user_groups in user_val.items():
                hydra_groups = merged_hooks.get(event_name) or []
                if isinstance(user_groups, list) and isinstance(hydra_groups, list):
                    merged_hooks[event_name] = list(hydra_groups) + list(user_groups)
                else:
                    merged_hooks[event_name] = user_groups
            result["hooks"] = merged_hooks
        else:
            result[key] = user_val
    return result


def _strip_server_hooks(
    hooks: dict[str, Any], managed: set[str]
) -> tuple[dict[str, Any], bool]:
    """Remove user-file hook entries whose command runs a script the server now
    distributes. Matching is on the full `.claude/hooks/<filename>` path, so a
    hand-authored hook in the same directory is never touched. A group whose
    entries all go is dropped, and an event whose groups all go with it."""
    result: dict[str, Any] = {}
    changed = False
    for event, groups in hooks.items():
        if not isinstance(groups, list):
            result[event] = groups
            continue
        kept_groups = []
        for group in groups:
            entries = group.get("hooks") if isinstance(group, dict) else None
            if not isinstance(entries, list):
                kept_groups.append(group)
                continue
            kept = [
                e
                for e in entries
                if not (
                    isinstance(e, dict)
                    and isinstance(e.get("command"), str)
                    and any(f".claude/hooks/{f}" in e["command"] for f in managed)
                )
            ]
            if len(kept) != len(entries):
                changed = True
            if kept:
                kept_groups.append({**group, "hooks": kept})
        if kept_groups:
            result[event] = kept_groups
    return result, changed


def migrate_user_settings(
    user: dict[str, Any], managed_hooks: set[str] | None = None
) -> tuple[dict[str, Any], bool]:
    """One-time migrations for user files scaffolded from older templates.

    - `effortLevel: "max"` was the old scaffold default that fed the removed
      env-var promotion (CLAUDE_CODE_EFFORT_LEVEL forced the level and could
      not be overridden in-session). Drop it so the template default applies.
      Any other value is a deliberate user choice and is kept.
    - A top-level `defaultMode` predates the `permissions` wrapper Claude Code
      expects; move it inside `permissions`, preserving its value.
    - Wiring for a hook the server now distributes is removed. These hooks were
      hand-wired in the user file before Hydra could distribute them, and
      `merge` concatenates per-event groups rather than deduping, so leaving
      both would run the hook twice.

    Returns the migrated dict and whether anything changed.
    """
    migrated = dict(user)
    changed = False
    if migrated.get("effortLevel") == "max":
        del migrated["effortLevel"]
        changed = True
    if "defaultMode" in migrated:
        permissions = dict(migrated.get("permissions") or {})
        permissions.setdefault("defaultMode", migrated.pop("defaultMode"))
        migrated["permissions"] = permissions
        changed = True
    if managed_hooks and isinstance(migrated.get("hooks"), dict):
        hooks, hooks_changed = _strip_server_hooks(migrated["hooks"], managed_hooks)
        if hooks_changed:
            migrated["hooks"] = hooks
            changed = True
    return migrated, changed


def cmd_apply_settings(args: argparse.Namespace) -> None:
    hydra_template = Path(args.hydra_template)
    user_template = Path(args.user_template)
    user_file = Path(args.user_file)
    output = Path(args.output)

    if not user_file.exists():
        user_file.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(user_template, user_file)
        print(
            f"Scaffolded {user_file} from {user_template} "
            f"(edit values to override; delete a field to fall back to the default)",
            file=sys.stderr,
        )

    raw = hydra_template.read_text(encoding="utf-8")
    raw = substitute(raw, args.hydra_url, args.hydra_repo_path)
    hydra = json.loads(raw)
    defaults = json.loads(user_template.read_text(encoding="utf-8"))
    user = json.loads(user_file.read_text(encoding="utf-8"))

    # Generated by `hooks pull`, which setup.sh runs immediately before this.
    # Absent is the normal state on a machine with no server hooks; unreadable
    # or malformed degrades to "no server hooks" rather than failing the render,
    # because a broken layer must never cost the user their whole settings file.
    server_hooks: dict[str, Any] = {}
    hooks_layer = getattr(args, "hooks_layer", None)
    if hooks_layer:
        try:
            server_hooks = json.loads(Path(hooks_layer).read_text(encoding="utf-8"))
        except FileNotFoundError:
            pass
        except (OSError, json.JSONDecodeError) as exc:
            print(f"Ignoring unreadable hooks layer {hooks_layer}: {exc}", file=sys.stderr)

    user, user_changed = migrate_user_settings(user, managed_filenames())
    if user_changed:
        user_file.write_text(json.dumps(user, indent=2) + "\n", encoding="utf-8")
        print(
            f"Migrated {user_file} to the current format (dropped stale effortLevel / "
            f"moved defaultMode into permissions / removed server-distributed hooks)",
            file=sys.stderr,
        )

    merged = merge(merge(merge(hydra, server_hooks), defaults), user)

    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(merged, indent=2) + "\n", encoding="utf-8")

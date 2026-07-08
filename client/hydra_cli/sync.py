"""Memory sync between a machine's Claude Code filesystem memory dirs and
the Hydra server DB.

Scope rule (locked in plan): memories with type user/feedback are global
(project_slug=NULL); memories with type project/reference are pinned to the
project derived from the session cwd.

Semantics:
- `hydra sync` (bidirectional): push local-only, pull server-only, flag
  diverging pairs as conflicts and skip them.
- `hydra sync --push`: upload all local, overwriting server state. No
  conflict check (local wins by definition).
- `hydra sync --pull`: download all server, overwriting local files, and
  prune local files the server no longer has (server wins by definition).
  Skipped for unregistered cwds, which have no authoritative server view.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path
from typing import Any
from urllib.parse import quote

from hydra_cli import api

GLOBAL_TYPES = {"user", "feedback"}
PROJECT_TYPES = {"project", "reference"}
VALID_TYPES = GLOBAL_TYPES | PROJECT_TYPES
MEMORY_INDEX = "MEMORY.md"

EMITTED_FM_KEYS = ("name", "description", "type")


# --- Filesystem helpers ---


def memory_dir_for_cwd(cwd: str) -> Path:
    """Map a project cwd to Claude Code's local memory dir.

    Claude Code encodes the project path by replacing EVERY non-alphanumeric
    character with `-` (not just the path separators). E.g.
    /home/giosue/projects/hydra → -home-giosue-projects-hydra,
    C:\\Users\\giosu\\projects\\pcb → C--Users-giosu-projects-pcb,
    /home/me/foo_bar → -home-me-foo-bar, /home/me/my.proj → -home-me-my-proj.

    This mirrors Claude Code's own encoder (`x.replace(/[^a-zA-Z0-9]/g, "-")`);
    a slug that keeps `_`/`.` would point at a nonexistent dir and sync 0 files
    silently. CC also truncates + hashes paths over 200 chars - not replicated
    here; run_sync warns instead when the computed dir is missing.
    """
    cwd = os.path.abspath(cwd)
    slug = re.sub(r"[^A-Za-z0-9]", "-", cwd)
    return Path.home() / ".claude" / "projects" / slug / "memory"


def _slugify_filename(name: str) -> str:
    s = re.sub(r"[^A-Za-z0-9]+", "_", name).strip("_").lower()
    return (s or "memory") + ".md"


# --- Frontmatter ---


def parse_memory_file(path: Path) -> dict[str, Any] | None:
    """Parse a memory .md file; return dict with name/description/type/body or
    None if the frontmatter is missing or malformed."""
    text = path.read_text(encoding="utf-8")
    if not text.startswith("---"):
        return None
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return None
    try:
        end = next(i for i in range(1, len(lines)) if lines[i].strip() == "---")
    except StopIteration:
        return None

    fm: dict[str, str] = {}
    for raw in lines[1:end]:
        if ":" not in raw:
            continue
        k, _, v = raw.partition(":")
        fm[k.strip()] = v.strip()

    body = "\n".join(lines[end + 1:]).lstrip("\n")
    if "name" not in fm or "type" not in fm:
        return None
    if fm["type"] not in VALID_TYPES:
        return None
    return {
        "name": fm["name"],
        "description": fm.get("description", ""),
        "type": fm["type"],
        "body": body,
    }


def serialize_memory(mem: dict[str, Any]) -> str:
    """Render a memory as frontmatter + body text (inverse of parse_memory_file)."""
    lines = ["---"]
    for k in EMITTED_FM_KEYS:
        lines.append(f"{k}: {mem.get(k, '')}")
    lines.append("---")
    body = mem.get("body") or ""
    if not body.endswith("\n"):
        body += "\n"
    return "\n".join(lines) + "\n" + body


# --- Scope derivation ---


def scope_is_global(mem_type: str) -> bool:
    return mem_type in GLOBAL_TYPES


def effective_project_slug(mem_type: str, current_slug: str | None) -> str | None:
    """Global types ignore current_slug; project types use it."""
    return None if scope_is_global(mem_type) else current_slug


# --- Server I/O ---


def _api_error(status: int, body: str) -> str:
    try:
        return json.loads(body).get("detail", body)
    except (json.JSONDecodeError, AttributeError):
        return body


def resolve_project_slug(cwd: str, *, auto_attach: bool = True) -> str | None:
    """Look up the project slug for cwd in the Hydra projects registry.

    Matches against any registered path for any machine - a project may live
    at different filesystem paths on different machines, and the cwd itself
    is unambiguous enough that instance_id scoping is unnecessary here.

    If no registered path matches and `auto_attach` is set, defers to the
    server's `/api/projects/auto-register` endpoint, which applies a stoplist
    and either creates a brand-new slug, attaches this machine to an existing
    one, or skips with a reason. Auto-registered entries are flagged in the
    DB so the dashboard can surface them for review.
    """
    status, body = api.get("/api/projects")
    if status != 200:
        raise RuntimeError(f"Failed to fetch projects: {_api_error(status, body)}")
    projects = json.loads(body)
    target = os.path.abspath(cwd)
    for p in projects:
        for entry in p.get("paths", []):
            if os.path.abspath(entry["path"]) == target:
                return p["slug"]

    if not auto_attach:
        return None

    return _auto_register(target)


def _auto_register(cwd: str) -> str | None:
    """POST to /api/projects/auto-register. Returns the slug on
    created/attached/existing; None on skipped (with the server's reason
    printed to stderr)."""
    status, body = api.post("/api/projects/auto-register", {"cwd": cwd})
    if status != 200:
        raise RuntimeError(
            f"Failed to auto-register {cwd}: {_api_error(status, body)}"
        )
    resp = json.loads(body)
    if resp["status"] == "skipped":
        print(
            f"  auto-register skipped for {cwd}: {resp.get('reason') or 'unspecified'}",
            file=sys.stderr,
        )
        return None
    if resp["status"] in ("created", "attached"):
        print(
            f"  auto-{resp['status']} {cwd} as project '{resp['slug']}'"
            " (review on dashboard)",
            file=sys.stderr,
        )
    return resp.get("slug")


def fetch_server_memories(project_slug: str | None) -> list[dict[str, Any]]:
    """Fetch memories relevant to this project: pinned + globals. If no
    project_slug, only globals."""
    if project_slug is None:
        status, body = api.get("/api/memory")
        if status != 200:
            raise RuntimeError(f"Failed to list memories: {_api_error(status, body)}")
        return [m for m in json.loads(body) if m.get("project_slug") is None]

    path = f"/api/memory?project_slug={quote(project_slug)}&include_global=true"
    status, body = api.get(path)
    if status != 200:
        raise RuntimeError(f"Failed to list memories: {_api_error(status, body)}")
    return json.loads(body)


def upsert_memory(mem: dict[str, Any]) -> dict[str, Any]:
    payload = {
        "name": mem["name"],
        "description": mem.get("description", ""),
        "type": mem["type"],
        "body": mem.get("body", ""),
        "project_slug": mem.get("project_slug"),
    }
    status, body = api.post("/api/memory", payload)
    if status != 200:
        raise RuntimeError(f"Failed to upsert '{mem['name']}': {_api_error(status, body)}")
    return json.loads(body)


# --- Comparison ---


def _normalized(value: str | None) -> str:
    """Compare bodies/descriptions after stripping trailing whitespace/newlines.
    A round-trip through serialize→parse strips the trailing newline the
    serializer adds, so exact string equality produces false conflicts."""
    return (value or "").rstrip()


def fields_differ(local: dict[str, Any], remote: dict[str, Any]) -> list[str]:
    """Return field names whose normalized values differ. Ignores timestamps,
    ids, and project_slug (caller handles scope pairing)."""
    diffs = []
    for field in ("name", "description", "type", "body"):
        if _normalized(local.get(field)) != _normalized(remote.get(field)):
            diffs.append(field)
    return diffs


# --- MEMORY.md regeneration ---


def regenerate_index(memory_dir: Path, memories: list[dict[str, Any]]) -> None:
    """Write MEMORY.md as a flat bullet list sorted by name."""
    lines = []
    for mem in sorted(memories, key=lambda m: m["name"]):
        filename = _slugify_filename(mem["name"])
        desc = mem.get("description", "").strip()
        suffix = f" - {desc}" if desc else ""
        lines.append(f"- [{mem['name']}]({filename}){suffix}")
    content = "\n".join(lines) + ("\n" if lines else "")
    (memory_dir / MEMORY_INDEX).write_text(content, encoding="utf-8")


# --- Filesystem walk ---


def walk_local_memories(memory_dir: Path) -> list[tuple[Path, dict[str, Any]]]:
    """Return [(path, parsed_dict)] for every valid memory file. Skips
    MEMORY.md and unparseable files."""
    if not memory_dir.is_dir():
        return []
    out = []
    for p in sorted(memory_dir.iterdir()):
        if p.name == MEMORY_INDEX or p.suffix != ".md" or not p.is_file():
            continue
        parsed = parse_memory_file(p)
        if parsed is not None:
            out.append((p, parsed))
    return out


# --- Orchestrator ---


def run_sync(
    cwd: str,
    *,
    do_pull: bool = True,
    do_push: bool = True,
    dry_run: bool = False,
) -> int:
    """Reconcile local memory dir with server. Returns exit code: 0 clean,
    2 if any conflicts were skipped (only possible in bidirectional mode)."""
    bidirectional = do_pull and do_push
    current_slug = resolve_project_slug(cwd)
    memory_dir = memory_dir_for_cwd(cwd)

    local_files = walk_local_memories(memory_dir)
    if do_push and not memory_dir.is_dir():
        print(
            f"  warning: no memory dir at {memory_dir} - nothing to push"
            " (wrong project, or none saved yet?)",
            file=sys.stderr,
        )
    local_by_key: dict[tuple[str, str | None], tuple[Path, dict[str, Any]]] = {}
    for path, mem in local_files:
        mem["project_slug"] = effective_project_slug(mem["type"], current_slug)
        local_by_key[(mem["name"], mem["project_slug"])] = (path, mem)

    server = fetch_server_memories(current_slug)
    server_by_key: dict[tuple[str, str | None], dict[str, Any]] = {
        (m["name"], m.get("project_slug")): m for m in server
    }

    conflicts: list[tuple[str, list[str]]] = []
    pushed = pulled = pruned = skipped_pinned = 0

    # --- Push side ---
    if do_push:
        for key, (path, mem) in local_by_key.items():
            if mem["project_slug"] is None and not scope_is_global(mem["type"]):
                print(
                    f"  skip (no project registered for cwd): {path.name}",
                    file=sys.stderr,
                )
                skipped_pinned += 1
                continue
            remote = server_by_key.get(key)
            if bidirectional and remote is not None:
                diffs = fields_differ(mem, remote)
                if diffs:
                    conflicts.append((mem["name"], diffs))
                    continue
                # Identical - nothing to do
                continue
            # In --push mode OR local-only in bidirectional → upsert
            if dry_run:
                print(f"  would push: {mem['name']}")
            else:
                upsert_memory(mem)
                print(f"  pushed: {mem['name']}")
            pushed += 1

    # --- Pull side ---
    if do_pull:
        memory_dir.mkdir(parents=True, exist_ok=True)
        for key, remote in server_by_key.items():
            if bidirectional and key in local_by_key:
                continue  # Handled by push branch (identical or conflict)
            # In --pull mode OR server-only in bidirectional → write
            target = memory_dir / _slugify_filename(remote["name"])
            if dry_run:
                print(f"  would pull: {remote['name']} → {target.name}")
            else:
                target.write_text(serialize_memory(remote), encoding="utf-8")
                print(f"  pulled: {remote['name']}")
            pulled += 1

        # Prune local files the server no longer has (server wins). Only in
        # pull-only mode: bidirectional treats local-only files as uploads,
        # not deletions. Only for synced projects: an unregistered cwd has no
        # authoritative server view, so its local files must not be deleted.
        if not do_push and current_slug is not None:
            server_names = {m["name"] for m in server}
            for path, mem in walk_local_memories(memory_dir):
                if mem["name"] in server_names:
                    continue
                if dry_run:
                    print(f"  would prune (server-deleted): {path.name}")
                else:
                    path.unlink()
                    print(f"  pruned (server-deleted): {path.name}")
                pruned += 1

        if not dry_run:
            regenerate_index(
                memory_dir, [m for _, m in walk_local_memories(memory_dir)]
            )

    # --- Summary ---
    print(
        f"\nSummary: {pushed} pushed, {pulled} pulled, {pruned} pruned, "
        f"{len(conflicts)} conflicts, {skipped_pinned} skipped (no project)"
    )
    for name, diffs in conflicts:
        print(f"  conflict: {name} (fields differ: {', '.join(diffs)})", file=sys.stderr)

    return 2 if conflicts else 0


# --- CLI entry point ---


def cmd_sync(args: argparse.Namespace) -> None:
    cwd = args.cwd or os.getcwd()
    do_pull = not args.push
    do_push = not args.pull
    exit_code = run_sync(cwd, do_pull=do_pull, do_push=do_push, dry_run=args.dry_run)
    sys.exit(exit_code)

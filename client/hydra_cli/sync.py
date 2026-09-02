"""Memory sync between a machine's Claude Code filesystem memory dirs and
the Hydra server DB.

Scope rule (locked in plan): memories with type user/feedback are global
(project_slug=NULL); memories with type project/reference are pinned to the
project derived from the session cwd.

Identity: a memory IS its server row id, which pull stamps into the mirror
file's frontmatter as `id:`. Push then updates by id, so a rename or a re-scope
edits the row in place instead of minting a second one, and a row that is gone
from the server (404) tombstones its mirror file instead of being re-inserted.
Before ids, the mirror file was the identity of record and push was upsert-only,
so every server-side delete/rename/re-scope came back as a duplicate row on the
next Stop hook.

Semantics:
- `hydra sync` (bidirectional): push local-only, pull server-only, flag
  diverging pairs as conflicts and skip them.
- `hydra sync --push`: upload local edits by id. NOT "local always wins": a file
  whose recorded `updated_at` no longer matches the server's lost the race (the
  row was edited or re-scoped under it) and is skipped, and a file whose id the
  server no longer has is deleted rather than re-inserted.
- `hydra sync --pull`: download all server, overwriting local files, and prune
  the ones the server has superseded (server wins by definition). Skipped for
  unregistered cwds, which have no authoritative server view. Never prunes a
  file the server has not accepted - an unparseable file, or a local-only memory
  whose name the server refused - since the mirror is its only copy.

Nothing destructive runs against an empty server view: a wrong HYDRA_URL, a
fresh DB and a restored backup all look exactly like "everything was deleted".
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from collections import Counter
from pathlib import Path
from typing import Any
from urllib.parse import quote

from hydra_cli import api
from hydra_cli.paths import is_contained_by, path_shape

GLOBAL_TYPES = {"user", "feedback"}
PROJECT_TYPES = {"project", "reference"}
VALID_TYPES = GLOBAL_TYPES | PROJECT_TYPES
MEMORY_INDEX = "MEMORY.md"

EMITTED_FM_KEYS = ("id", "name", "description", "type", "updated_at")
# Frontmatter keys that record where a mirror file came from, rather than what
# the memory says. Blank ones are never emitted, and they never count as a diff.
PROVENANCE_FM_KEYS = ("id", "updated_at")


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


def _base_slug(name: str) -> str:
    return re.sub(r"[^A-Za-z0-9]+", "_", name).strip("_").lower() or "memory"


def canonical_filenames(memories: list[dict[str, Any]]) -> dict[int, str]:
    """Map each server row id to its mirror filename, over the WHOLE set at once.

    _base_slug is not injective - it lowercases and collapses every run of
    non-alphanumerics to '_', so 'Keep Hydra deployment-agnostic' and
    'keep-hydra-deployment-agnostic' both want keep_hydra_deployment_agnostic.md.
    Computed per row, two memories silently clobber one file and the loser
    becomes a zombie: present in every API listing, absent from the mirror,
    unfixable by editing files. So the first claimant (lowest id) keeps
    <slug>.md and later ones get <slug>-<id>.md - injective by construction
    (base slugs never contain '-'), and identical on every machine.
    """
    out: dict[int, str] = {}
    taken: set[str] = set()
    for mem in sorted(memories, key=lambda m: m["id"]):
        slug = _base_slug(mem["name"])
        filename = f"{slug}.md"
        if filename in taken:
            filename = f"{slug}-{mem['id']}.md"
        taken.add(filename)
        out[mem["id"]] = filename
    return out


def stamp_provenance(path: Path, mem: dict[str, Any]) -> None:
    """Write the server's `id` and `updated_at` into a file's frontmatter, in place.

    Deliberately a surgical line edit, not a re-serialize: the file may carry
    frontmatter keys we don't emit (Claude Code's memory tool writes
    originSessionId) and body bytes we have no business round-tripping.
    """
    lines = path.read_text(encoding="utf-8").splitlines(keepends=True)
    if not lines or lines[0].strip() != "---":
        return
    try:
        end = next(i for i in range(1, len(lines)) if lines[i].strip() == "---")
    except StopIteration:
        return  # unterminated frontmatter; leave it alone
    for key in PROVENANCE_FM_KEYS:
        value = mem.get(key)
        if not value:
            continue
        line = f"{key}: {value}\n"
        for i in range(1, end):
            if lines[i].split(":", 1)[0].strip() == key:
                lines[i] = line
                break
        else:
            lines.insert(1, line)
            end += 1
    path.write_text("".join(lines), encoding="utf-8")


# --- Frontmatter ---


def parse_memory_file(path: Path) -> dict[str, Any] | None:
    """Parse a memory .md file; return dict with id/name/description/type/body or
    None if the frontmatter is missing or malformed.

    A missing or non-integer `id` is NOT a parse failure - it yields id=None and
    the file re-keys itself by name on the next push (names are globally unique,
    so that lands on the same row). This is the safety net for anything that
    rewrites a memory file without preserving our key.
    """
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
    try:
        mem_id = int(fm["id"])
    except (KeyError, ValueError):
        mem_id = None
    return {
        "id": mem_id,
        "name": fm["name"],
        "description": fm.get("description", ""),
        "type": fm["type"],
        "body": body,
        # The row version this mirror file was written from. "" means unknown -
        # a file we've never pulled or pushed - and pushes proceed unconditionally.
        "updated_at": fm.get("updated_at", ""),
    }


def serialize_memory(mem: dict[str, Any]) -> str:
    """Render a memory as frontmatter + body text (inverse of parse_memory_file)."""
    lines = ["---"]
    for k in EMITTED_FM_KEYS:
        if k in PROVENANCE_FM_KEYS and not mem.get(k):
            continue  # a blank id/updated_at parses back as unknown; don't emit one
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

    A read-only lookup falls back to the deepest confirmed ancestor. If
    `auto_attach` is set instead, the server owns containment and registration
    through `/api/projects/auto-register`.
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
        matches = [
            (p["slug"], entry["path"])
            for p in projects
            if p.get("auto_registered_at") is None
            for entry in p.get("paths", [])
            if is_contained_by(target, entry["path"])
        ]
        if not matches:
            return None
        deepest = max(len(path_shape(path)[1]) for _, path in matches)
        slugs = {
            slug
            for slug, path in matches
            if len(path_shape(path)[1]) == deepest
        }
        return slugs.pop() if len(slugs) == 1 else None

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


def _payload(mem: dict[str, Any]) -> dict[str, Any]:
    return {
        "name": mem["name"],
        "description": mem.get("description", ""),
        "type": mem["type"],
        "body": mem.get("body", ""),
        "project_slug": mem.get("project_slug"),
    }


# The three calls below warn and return rather than raise. A hook runs
# `>/dev/null 2>&1 || true`, so an exception is both invisible and fatal to the
# rest of the pass - one 409 would strand every later file.


def upsert_memory(mem: dict[str, Any]) -> dict[str, Any] | None:
    """POST a memory with no known id (a new, model-authored file). Upserts by
    name. Returns None if the server refused it - notably 409, when the name is
    already held in a different scope."""
    status, body = api.post("/api/memory", _payload(mem))
    if status != 200:
        print(
            f"  skip (server refused '{mem['name']}': {_api_error(status, body)})",
            file=sys.stderr,
        )
        return None
    return json.loads(body)


def update_memory(
    mem_id: int, mem: dict[str, Any], *, only: tuple[str, ...] | None = None
) -> tuple[int, dict[str, Any] | None]:
    """PUT a memory by id. By default the full field set - including
    project_slug, which may be null - so a local rename or re-scope updates the
    row in place. `only` restricts the payload to those fields (used for a
    mirror file with no version token, which may not move a row's identity).
    Returns (status, row)."""
    payload = _payload(mem)
    if only is not None:
        payload = {k: v for k, v in payload.items() if k in only}
    status, body = api.put_json(f"/api/memory/{mem_id}", payload)
    if status != 200:
        if status != 404:  # 404 is a tombstone, reported by the caller
            print(
                f"  skip (server refused '{mem['name']}': {_api_error(status, body)})",
                file=sys.stderr,
            )
        return status, None
    return status, json.loads(body)


def fetch_whole_corpus() -> list[dict[str, Any]] | None:
    """Every memory on the server, across every scope. None if unreadable.

    Answers two questions that the project-scoped listing cannot, in one request
    rather than one per file:
    - Is the server empty? A project with no memories of its own is normal and
      prunes fine; a server with NO memories at all is a wrong HYDRA_URL, a
      fresh DB or a half-restored backup, and must never authorize a deletion.
    - Does row N still exist? A row missing from the project-scoped listing may
      simply have been re-scoped to another project, which that listing cannot
      see. Absence here means it is really gone.
    """
    status, body = api.get("/api/memory")
    if status != 200:
        return None
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


def regenerate_index(
    memory_dir: Path, entries: list[tuple[Path, dict[str, Any]]]
) -> None:
    """Write MEMORY.md as a flat bullet list sorted by name.

    Links to the filename each memory was actually found at, rather than
    re-deriving one from its name: the caller passes disk state, which in
    push-only or unregistered-cwd runs is not the server set, so a re-derived
    link could point at a file that doesn't exist (and two files holding the
    same name used to emit two identical lines).
    """
    lines = []
    for path, mem in sorted(entries, key=lambda e: e[1]["name"]):
        desc = mem.get("description", "").strip()
        suffix = f" - {desc}" if desc else ""
        lines.append(f"- [{mem['name']}]({path.name}){suffix}")
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
    for _, mem in local_files:
        mem["project_slug"] = effective_project_slug(mem["type"], current_slug)

    server = fetch_server_memories(current_slug)
    server_by_id = {m["id"]: m for m in server}
    server_by_name = {m["name"]: m for m in server}
    filenames = canonical_filenames(server)
    canonical = set(filenames.values())

    # The whole corpus, fetched at most once and only when the scoped view can't
    # answer the question. An empty server is never authority to delete: a wrong
    # HYDRA_URL, a fresh DB and a restored backup are indistinguishable from
    # "everything was deleted", and the mirror may be the only surviving copy.
    # A project that merely has no memories OF ITS OWN is a different thing and
    # prunes fine, hence corpus-wide rather than scope-wide.
    corpus_cache: dict[str, list[dict[str, Any]] | None] = {}

    def corpus() -> list[dict[str, Any]] | None:
        if "rows" not in corpus_cache:
            corpus_cache["rows"] = fetch_whole_corpus()
        return corpus_cache["rows"]

    authoritative = bool(server) or bool(corpus())
    if do_push and not authoritative and any(m["id"] for _, m in local_files):
        print(
            "  warning: the server reports no memories at all - skipping every"
            " id-keyed update and deletion (wrong HYDRA_URL, or an empty server?)",
            file=sys.stderr,
        )

    # Two files claiming one id (a memory file copied by hand) or one name (a
    # stale mirror left behind by an old rename) must not race: last-write-wins
    # would silently destroy one of them. The file sitting at the canonical
    # filename is the real one, so it still pushes; only the impostors are held.
    id_counts = Counter(m["id"] for _, m in local_files if m["id"] is not None)
    name_counts = Counter(m["name"] for _, m in local_files)
    dup_ids = {i for i, c in id_counts.items() if c > 1}
    dup_names = {n for n, c in name_counts.items() if c > 1}

    conflicts: list[tuple[str, list[str]]] = []
    pushed = pulled = pruned = skipped_pinned = tombstoned = 0
    matched_ids: set[int] = set()

    # --- Push side ---
    if do_push:
        for path, mem in local_files:
            name, mem_id = mem["name"], mem["id"]
            contested = (mem_id is not None and mem_id in dup_ids) or name in dup_names
            # Of a contested group, the file at the canonical filename is the
            # real mirror; the others are stale copies a rename left behind.
            # Holding ALL of them would drop a genuine edit made to the real one.
            if contested and path.name != filenames.get(mem_id or -1):
                print(
                    f"  skip (another local file claims the same id/name): {path.name}",
                    file=sys.stderr,
                )
                continue
            if mem["project_slug"] is None and not scope_is_global(mem["type"]):
                print(
                    f"  skip (no project registered for cwd): {path.name}",
                    file=sys.stderr,
                )
                skipped_pinned += 1
                continue

            # Pair by id; fall back to name for a file that has never been
            # pushed (model-authored, or written before ids existed).
            remote = (
                server_by_id.get(mem_id)
                if mem_id is not None
                else server_by_name.get(name)
            )
            if remote is not None:
                matched_ids.add(remote["id"])

            if remote is None and mem_id is not None:
                # The row is gone from this scope's view. It was either deleted,
                # or re-scoped to another project - and only a targeted lookup
                # can tell those apart. Either way the file no longer belongs
                # here; never re-insert it, which is what minted the duplicates.
                if not authoritative or current_slug is None:
                    continue
                rows = corpus()
                if rows is None:
                    print(
                        f"  skip (could not confirm memory #{mem_id} on server):"
                        f" {path.name}",
                        file=sys.stderr,
                    )
                    continue
                exists = any(m["id"] == mem_id for m in rows)
                gone = "deleted on the server" if not exists else "moved to another project"
                if dry_run:
                    print(f"  would remove ({gone}): {path.name}")
                else:
                    path.unlink()
                    print(f"  removed ({gone}): {path.name}")
                tombstoned += 1
                continue

            if bidirectional and remote is not None:
                diffs = fields_differ(mem, remote)
                if remote.get("project_slug") != mem["project_slug"]:
                    diffs.append("project_slug")
                if diffs:
                    conflicts.append((name, diffs))
                    continue
                if mem_id is None and not dry_run:
                    stamp_provenance(path, remote)  # adopt the id; no server write
                continue

            if remote is not None:
                if not fields_differ(mem, remote) and (
                    remote.get("project_slug") == mem["project_slug"]
                ):
                    continue  # identical - nothing to do
                # The row moved under us: someone edited or re-scoped it on the
                # server (dashboard, /forget, another machine) after this mirror
                # file was written. Pushing the stale file would silently revert
                # them, so the server wins and the next pull refreshes the file.
                if mem["updated_at"] and mem["updated_at"] != remote.get("updated_at"):
                    print(
                        f"  skip (changed on the server since this mirror was"
                        f" written; will refresh on next pull): {name}",
                        file=sys.stderr,
                    )
                    continue
                # A file with no version token has no basis to move a row's
                # IDENTITY - it predates ids (or was hand-written), so its name,
                # type and scope may be arbitrarily stale. Let its content land,
                # but never let it rename or re-scope the row: that is how a
                # pre-upgrade mirror would undo the migration's re-scopes on the
                # first Stop hook. The next pull restores the file's own fields.
                only = None if mem["updated_at"] else ("description", "body")
                if dry_run:
                    print(f"  would push: {name}")
                    pushed += 1
                    continue
                status, saved = update_memory(remote["id"], mem, only=only)
                if status == 404:
                    path.unlink()  # deleted between the list and the write
                    print(f"  removed (deleted on the server): {path.name}")
                    tombstoned += 1
                    continue
                if status != 200 or saved is None:
                    conflicts.append((name, ["name"]))
                    continue
                stamp_provenance(path, saved)  # record the version we just wrote
                print(f"  pushed: {name}")
                pushed += 1
                continue

            # No id and no name match: a genuinely new, locally-authored memory.
            if dry_run:
                print(f"  would push (new): {name}")
                pushed += 1
                continue
            saved = upsert_memory(mem)
            if saved is None:
                conflicts.append((name, ["name"]))
                continue
            stamp_provenance(path, saved)
            print(f"  pushed (new): {name}")
            pushed += 1

    # --- Pull side ---
    if do_pull:
        memory_dir.mkdir(parents=True, exist_ok=True)
        # Filenames already occupied by a local memory file. In bidirectional
        # mode the push pass has considered every one of them, so anything still
        # sitting here belongs to a different memory than the one we're about to
        # write - two names can collide on one base slug.
        held = {p.name for p, _ in local_files} if bidirectional else set()
        for remote in server:
            if bidirectional and remote["id"] in matched_ids:
                continue  # handled by push (identical, conflict, or updated)
            target = memory_dir / filenames[remote["id"]]
            # In bidirectional mode a local file we already matched may be
            # sitting at this exact path (two names can collide on one base
            # slug). Writing over it would destroy a file the push side just
            # accepted as current.
            if bidirectional and target.name in held:
                print(
                    f"  skip pull (a local memory already holds {target.name}):"
                    f" {remote['name']}",
                    file=sys.stderr,
                )
                continue
            if dry_run:
                print(f"  would pull: {remote['name']} → {target.name}")
            else:
                target.write_text(serialize_memory(remote), encoding="utf-8")
                print(f"  pulled: {remote['name']}")
            pulled += 1

        # Prune files the server has superseded (server wins): rows deleted or
        # re-scoped away, and orphans a rename left at a stale filename. Only in
        # pull-only mode - bidirectional treats local-only files as uploads, not
        # deletions - and only for a registered cwd against a non-empty server,
        # the two conditions under which the server's view is authoritative.
        if not do_push and current_slug is not None and authoritative:
            for path in sorted(memory_dir.glob("*.md")):
                if path.name == MEMORY_INDEX or path.name in canonical:
                    continue
                parsed = parse_memory_file(path)
                if parsed is None:
                    # Never delete what we cannot read. An unparseable .md here
                    # is not ours - hand-written notes, or a file some other tool
                    # wrote - and the server has no opinion on it.
                    print(
                        f"  keep (not a memory file, leaving alone): {path.name}",
                        file=sys.stderr,
                    )
                    continue
                if parsed["id"] is None and parsed["name"] not in server_by_name:
                    # Never pushed and not on the server: either a memory written
                    # this session, or one the server refused (409 - its name is
                    # held in another scope). Deleting it would destroy the only
                    # copy of content nobody has accepted yet.
                    print(
                        f"  keep (local-only, not on the server - rename it or"
                        f" resolve the name clash): {path.name}",
                        file=sys.stderr,
                    )
                    continue
                if dry_run:
                    print(f"  would prune (superseded by the server): {path.name}")
                else:
                    path.unlink()
                    print(f"  pruned (superseded by the server): {path.name}")
                pruned += 1

    # MEMORY.md must be rebuilt whenever the file set changed - including a
    # push-only run, where tombstones unlinked files. Otherwise the index goes on
    # advertising memories that are no longer there.
    if not dry_run and (do_pull or tombstoned) and memory_dir.is_dir():
        regenerate_index(memory_dir, walk_local_memories(memory_dir))

    # --- Summary ---
    print(
        f"\nSummary: {pushed} pushed, {pulled} pulled, {pruned} pruned, "
        f"{tombstoned} removed (gone from server), {len(conflicts)} conflicts, "
        f"{skipped_pinned} skipped (no project)"
    )
    for name, diffs in conflicts:
        print(f"  conflict: {name} (fields differ: {', '.join(diffs)})", file=sys.stderr)

    return 2 if conflicts else 0


# --- CLI entry point ---


def cmd_sync(args: argparse.Namespace) -> None:
    # CLAUDE_PROJECT_DIR is the session's launch dir and stays put; $PWD/getcwd
    # follow the model's `cd` and would auto-register subdirs as projects.
    cwd = args.cwd or os.environ.get("CLAUDE_PROJECT_DIR") or os.getcwd()
    do_pull = not args.push
    do_push = not args.pull
    exit_code = run_sync(cwd, do_pull=do_pull, do_push=do_push, dry_run=args.dry_run)
    sys.exit(exit_code)

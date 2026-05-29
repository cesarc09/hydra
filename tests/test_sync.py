"""Tests for the hydra sync CLI module. Exercises run_sync with a fake api
module (no live server) and a tmp memory dir."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from hydra_cli import sync as sync_mod

from server.services.slug import derive_slug_from_cwd

# --- Fake API fixture ------------------------------------------------------


class FakeAPI:
    """Stand-in for hydra_cli.api with controllable state.

    Tests inject a list of projects and a list of server-side memories;
    run_sync-triggered POSTs append/update the memory list with upsert
    semantics keyed on (name, project_slug).
    """

    def __init__(
        self,
        projects: list[dict[str, Any]] | None = None,
        memories: list[dict[str, Any]] | None = None,
    ):
        self.projects = projects or []
        self.memories = memories or []
        self._next_id = (max((m["id"] for m in self.memories), default=0)) + 1

    # Mimic hydra_cli.api.get (full path including query string)
    def get(self, path: str) -> tuple[int, str]:
        if path == "/api/projects":
            # Normalize old-shape test fixtures ({slug, path}) to the new
            # {slug, paths:[{instance_id, path}]} shape the client expects.
            normalized = []
            for p in self.projects:
                if "paths" in p:
                    normalized.append(p)
                else:
                    normalized.append({
                        "slug": p["slug"],
                        "paths": [{"instance_id": "test", "path": p["path"]}],
                    })
            return 200, json.dumps(normalized)
        if path == "/api/memory":
            return 200, json.dumps(self.memories)
        if path.startswith("/api/memory?"):
            qs = path.split("?", 1)[1]
            params = dict(p.split("=", 1) for p in qs.split("&"))
            slug = params.get("project_slug")
            include_global = params.get("include_global", "false").lower() == "true"
            filtered = [
                m for m in self.memories
                if m.get("project_slug") == slug
                or (include_global and m.get("project_slug") is None)
            ]
            return 200, json.dumps(filtered)
        return 404, json.dumps({"detail": f"no fake handler for {path}"})

    def post(self, path: str, payload: dict) -> tuple[int, str]:
        if path == "/api/projects":
            # Idempotent project upsert - mirrors the server. If the slug
            # exists, append/update the path row; otherwise create a new entry.
            slug = payload["slug"]
            new_path = payload["path"]
            for p in self.projects:
                if p["slug"] == slug:
                    paths = p.setdefault("paths", [])
                    if not any(e["path"] == new_path for e in paths):
                        paths.append({"instance_id": "test", "path": new_path})
                    return 201, json.dumps(p)
            created = {
                "slug": slug,
                "paths": [{"instance_id": "test", "path": new_path}],
            }
            self.projects.append(created)
            return 201, json.dumps(created)
        if path == "/api/projects/auto-register":
            cwd = payload["cwd"]
            for p in self.projects:
                for e in p.get("paths", []):
                    if e.get("path") == cwd and e.get("instance_id") == "test":
                        return 200, json.dumps(
                            {"status": "existing", "slug": p["slug"]}
                        )
            slug, reason = derive_slug_from_cwd(cwd)
            if slug is None:
                return 200, json.dumps({"status": "skipped", "reason": reason})
            for p in self.projects:
                if p["slug"] == slug:
                    p.setdefault("paths", []).append(
                        {"instance_id": "test", "path": cwd}
                    )
                    return 200, json.dumps({"status": "attached", "slug": slug})
            self.projects.append({
                "slug": slug,
                "paths": [{"instance_id": "test", "path": cwd}],
            })
            return 200, json.dumps({"status": "created", "slug": slug})
        if path == "/api/memory":
            key = (payload["name"], payload.get("project_slug"))
            for i, existing in enumerate(self.memories):
                if (existing["name"], existing.get("project_slug")) == key:
                    self.memories[i] = {**existing, **payload, "updated_at": "now"}
                    return 200, json.dumps(self.memories[i])
            new = {
                "id": self._next_id,
                "created_at": "now",
                "updated_at": "now",
                **payload,
            }
            self._next_id += 1
            self.memories.append(new)
            return 200, json.dumps(new)
        return 404, json.dumps({"detail": f"no fake handler for {path}"})


@pytest.fixture
def fake_api(monkeypatch: pytest.MonkeyPatch) -> FakeAPI:
    fake = FakeAPI()
    monkeypatch.setattr(sync_mod.api, "get", fake.get)
    monkeypatch.setattr(sync_mod.api, "post", fake.post)
    return fake


@pytest.fixture
def memory_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Redirect Claude Code's memory dir for cwd '/test/proj' to tmp_path."""
    target = tmp_path / "memory"
    monkeypatch.setattr(sync_mod, "memory_dir_for_cwd", lambda _cwd: target)
    return target


# --- Unit: parse / serialize ----------------------------------------------


def test_parse_valid_memory(tmp_path: Path):
    p = tmp_path / "m.md"
    p.write_text(
        "---\nname: foo\ndescription: d\ntype: user\n---\nbody text\n",
        encoding="utf-8",
    )
    parsed = sync_mod.parse_memory_file(p)
    assert parsed == {"name": "foo", "description": "d", "type": "user", "body": "body text"}


def test_parse_rejects_bad_type(tmp_path: Path):
    p = tmp_path / "m.md"
    p.write_text("---\nname: foo\ntype: invalid\n---\nbody\n", encoding="utf-8")
    assert sync_mod.parse_memory_file(p) is None


def test_parse_rejects_no_frontmatter(tmp_path: Path):
    p = tmp_path / "m.md"
    p.write_text("just body\n", encoding="utf-8")
    assert sync_mod.parse_memory_file(p) is None


def test_roundtrip_preserves_content(tmp_path: Path):
    original = {
        "name": "foo",
        "description": "a desc",
        "type": "project",
        "body": "line1\nline2",
    }
    p = tmp_path / "m.md"
    p.write_text(sync_mod.serialize_memory(original), encoding="utf-8")
    parsed = sync_mod.parse_memory_file(p)
    assert parsed is not None
    # Compare via the same normalization the sync uses
    assert sync_mod.fields_differ(original, parsed) == []


def test_memory_dir_for_cwd_posix(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    d = sync_mod.memory_dir_for_cwd("/home/giosue/projects/hydra")
    assert d == tmp_path / ".claude" / "projects" / "-home-giosue-projects-hydra" / "memory"


def test_memory_dir_for_cwd_windows(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    """Claude Code encodes `:` and `\\` as `-`. Before this fix the slug only
    replaced `/`, yielding a nonsense dir on Windows."""
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    # os.path.abspath is a no-op on absolute Windows-style paths when run on
    # Linux, so it's safe to drive this test cross-platform with a raw string.
    monkeypatch.setattr(sync_mod.os.path, "abspath", lambda p: p)
    d = sync_mod.memory_dir_for_cwd(r"C:\Users\giosu\projects\pcb")
    assert (
        d == tmp_path / ".claude" / "projects" / "C--Users-giosu-projects-pcb" / "memory"
    )


def test_resolve_project_slug_matches_any_registered_path(
    fake_api: FakeAPI, monkeypatch: pytest.MonkeyPatch
):
    """A project with paths from two machines resolves from either cwd."""
    fake_api.projects = [{
        "slug": "hydra",
        "paths": [
            {"instance_id": "vps", "path": "/home/giosue/projects/hydra"},
            {"instance_id": "laptop", "path": r"C:\Users\giosu\projects\hydra"},
        ],
    }]
    assert sync_mod.resolve_project_slug(
        "/home/giosue/projects/hydra", auto_attach=False
    ) == "hydra"
    # Stub abspath so the backslash path doesn't get mangled on Linux
    monkeypatch.setattr(sync_mod.os.path, "abspath", lambda p: p)
    assert sync_mod.resolve_project_slug(
        r"C:\Users\giosu\projects\hydra", auto_attach=False
    ) == "hydra"
    assert sync_mod.resolve_project_slug(
        "/unregistered/path", auto_attach=False
    ) is None


def test_resolve_project_slug_auto_attaches_on_basename_match(
    fake_api: FakeAPI, capsys: pytest.CaptureFixture[str]
):
    """Cwd whose basename matches an existing slug gets auto-attached via
    the server's /api/projects/auto-register endpoint."""
    fake_api.projects = [{
        "slug": "hydra",
        "paths": [{"instance_id": "vps", "path": "/home/giosue/projects/hydra"}],
    }]
    slug = sync_mod.resolve_project_slug("/Users/me/work/hydra")
    assert slug == "hydra"
    err = capsys.readouterr().err
    assert "auto-attached" in err


def test_resolve_project_slug_auto_creates_new_slug(
    fake_api: FakeAPI, capsys: pytest.CaptureFixture[str]
):
    """Cwd whose basename doesn't match any existing slug auto-creates one
    via /api/projects/auto-register (the dashboard surfaces it for review)."""
    fake_api.projects = [{
        "slug": "hydra",
        "paths": [{"instance_id": "vps", "path": "/home/giosue/projects/hydra"}],
    }]
    assert sync_mod.resolve_project_slug("/Users/me/scratch/unrelated") == "unrelated"
    err = capsys.readouterr().err
    assert "auto-created" in err


def test_resolve_project_slug_skipped_for_stoplisted_basename(
    fake_api: FakeAPI, capsys: pytest.CaptureFixture[str]
):
    """Stoplist basenames (Downloads, tmp, ...) return None and don't write
    anything to the registry."""
    assert sync_mod.resolve_project_slug("/home/giosue/Downloads") is None
    assert fake_api.projects == []
    err = capsys.readouterr().err
    assert "skipped" in err


def test_resolve_project_slug_auto_attach_disabled(fake_api: FakeAPI):
    fake_api.projects = [{
        "slug": "hydra",
        "paths": [{"instance_id": "vps", "path": "/home/giosue/projects/hydra"}],
    }]
    assert sync_mod.resolve_project_slug(
        "/Users/me/work/hydra", auto_attach=False
    ) is None


def test_scope_rule():
    assert sync_mod.scope_is_global("user")
    assert sync_mod.scope_is_global("feedback")
    assert not sync_mod.scope_is_global("project")
    assert sync_mod.effective_project_slug("user", "myproj") is None
    assert sync_mod.effective_project_slug("project", "myproj") == "myproj"


# --- Pull-only -----------------------------------------------------------


def test_pull_writes_files_and_index(
    fake_api: FakeAPI, memory_dir: Path, capsys: pytest.CaptureFixture[str]
):
    fake_api.projects = [{"slug": "proj", "path": "/test/proj"}]
    fake_api.memories = [
        {"id": 1, "name": "g1", "description": "global", "type": "user", "body": "gb",
         "project_slug": None, "created_at": "t", "updated_at": "t"},
        {"id": 2, "name": "p1", "description": "proj", "type": "project", "body": "pb",
         "project_slug": "proj", "created_at": "t", "updated_at": "t"},
    ]
    code = sync_mod.run_sync("/test/proj", do_pull=True, do_push=False)
    assert code == 0
    files = sorted(p.name for p in memory_dir.iterdir() if p.suffix == ".md")
    assert "g1.md" in files and "p1.md" in files and "MEMORY.md" in files

    index = (memory_dir / "MEMORY.md").read_text()
    assert "[g1](g1.md)" in index
    assert "[p1](p1.md)" in index


# --- Push-only -----------------------------------------------------------


def test_push_uploads_local(fake_api: FakeAPI, memory_dir: Path):
    fake_api.projects = [{"slug": "proj", "path": "/test/proj"}]
    memory_dir.mkdir(parents=True)
    (memory_dir / "g1.md").write_text(
        "---\nname: g1\ndescription: glob\ntype: user\n---\nbody g\n",
        encoding="utf-8",
    )
    (memory_dir / "p1.md").write_text(
        "---\nname: p1\ndescription: proj\ntype: project\n---\nbody p\n",
        encoding="utf-8",
    )

    code = sync_mod.run_sync("/test/proj", do_pull=False, do_push=True)
    assert code == 0
    names = {(m["name"], m.get("project_slug")) for m in fake_api.memories}
    assert ("g1", None) in names
    assert ("p1", "proj") in names


def test_push_skips_project_memory_when_cwd_stoplisted(
    fake_api: FakeAPI, memory_dir: Path, capsys: pytest.CaptureFixture[str]
):
    """A stoplist basename (Downloads, tmp, ...) returns no slug from
    auto-register, so project-scoped memories from that cwd are skipped."""
    memory_dir.mkdir(parents=True)
    (memory_dir / "p1.md").write_text(
        "---\nname: p1\ndescription: d\ntype: project\n---\nbody\n",
        encoding="utf-8",
    )
    code = sync_mod.run_sync("/home/me/Downloads", do_pull=False, do_push=True)
    assert code == 0
    assert fake_api.memories == []
    err = capsys.readouterr().err
    assert "no project registered" in err


# --- Bidirectional --------------------------------------------------------


def test_bidirectional_identical_is_noop(fake_api: FakeAPI, memory_dir: Path):
    fake_api.projects = [{"slug": "proj", "path": "/test/proj"}]
    fake_api.memories = [{
        "id": 1, "name": "shared", "description": "d", "type": "user",
        "body": "content", "project_slug": None,
        "created_at": "t", "updated_at": "t",
    }]
    memory_dir.mkdir(parents=True)
    (memory_dir / "shared.md").write_text(
        sync_mod.serialize_memory({
            "name": "shared", "description": "d", "type": "user", "body": "content",
        }),
        encoding="utf-8",
    )

    code = sync_mod.run_sync("/test/proj")
    assert code == 0
    assert len(fake_api.memories) == 1


def test_bidirectional_detects_conflict(
    fake_api: FakeAPI, memory_dir: Path, capsys: pytest.CaptureFixture[str]
):
    fake_api.projects = [{"slug": "proj", "path": "/test/proj"}]
    fake_api.memories = [{
        "id": 1, "name": "shared", "description": "d", "type": "user",
        "body": "server-version", "project_slug": None,
        "created_at": "t", "updated_at": "t",
    }]
    memory_dir.mkdir(parents=True)
    (memory_dir / "shared.md").write_text(
        "---\nname: shared\ndescription: d\ntype: user\n---\nlocal-version\n",
        encoding="utf-8",
    )

    code = sync_mod.run_sync("/test/proj")
    assert code == 2
    err = capsys.readouterr().err
    assert "conflict: shared" in err
    # Neither side was modified
    server_body = fake_api.memories[0]["body"]
    assert server_body == "server-version"
    local = (memory_dir / "shared.md").read_text()
    assert "local-version" in local


def test_bidirectional_pushes_local_only_and_pulls_server_only(
    fake_api: FakeAPI, memory_dir: Path
):
    fake_api.projects = [{"slug": "proj", "path": "/test/proj"}]
    fake_api.memories = [{
        "id": 1, "name": "onlyserver", "description": "", "type": "user",
        "body": "from server", "project_slug": None,
        "created_at": "t", "updated_at": "t",
    }]
    memory_dir.mkdir(parents=True)
    (memory_dir / "onlylocal.md").write_text(
        "---\nname: onlylocal\ndescription: \ntype: user\n---\nfrom local\n",
        encoding="utf-8",
    )

    code = sync_mod.run_sync("/test/proj")
    assert code == 0
    names = {m["name"] for m in fake_api.memories}
    assert names == {"onlyserver", "onlylocal"}
    local_files = {p.name for p in memory_dir.iterdir() if p.suffix == ".md"}
    assert "onlyserver.md" in local_files
    assert "onlylocal.md" in local_files


# --- Prune-on-pull --------------------------------------------------------


def test_pull_prunes_server_deleted_memory(fake_api: FakeAPI, memory_dir: Path):
    """--pull on a synced project deletes local memory files the server no
    longer has, and drops them from MEMORY.md."""
    fake_api.projects = [{"slug": "proj", "path": "/test/proj"}]
    fake_api.memories = [{
        "id": 1, "name": "keep", "description": "d", "type": "user",
        "body": "kept", "project_slug": None,
        "created_at": "t", "updated_at": "t",
    }]
    memory_dir.mkdir(parents=True)
    (memory_dir / "orphan.md").write_text(
        "---\nname: orphan\ndescription: d\ntype: user\n---\ngone from server\n",
        encoding="utf-8",
    )

    code = sync_mod.run_sync("/test/proj", do_pull=True, do_push=False)
    assert code == 0
    names = {p.name for p in memory_dir.iterdir() if p.suffix == ".md"}
    assert "orphan.md" not in names
    assert "keep.md" in names
    assert "orphan" not in (memory_dir / "MEMORY.md").read_text()


def test_pull_does_not_prune_unsynced_cwd(fake_api: FakeAPI, memory_dir: Path):
    """A stoplisted/unregistered cwd resolves to no slug, so --pull must not
    prune its local files (no authoritative server view)."""
    memory_dir.mkdir(parents=True)
    (memory_dir / "orphan.md").write_text(
        "---\nname: orphan\ndescription: d\ntype: user\n---\nlocal only\n",
        encoding="utf-8",
    )
    code = sync_mod.run_sync("/home/me/Downloads", do_pull=True, do_push=False)
    assert code == 0
    names = {p.name for p in memory_dir.iterdir() if p.suffix == ".md"}
    assert "orphan.md" in names


def test_pull_dry_run_does_not_prune(fake_api: FakeAPI, memory_dir: Path):
    """Dry-run --pull reports prunes but deletes nothing."""
    fake_api.projects = [{"slug": "proj", "path": "/test/proj"}]
    memory_dir.mkdir(parents=True)
    (memory_dir / "orphan.md").write_text(
        "---\nname: orphan\ndescription: d\ntype: user\n---\ngone\n",
        encoding="utf-8",
    )
    code = sync_mod.run_sync(
        "/test/proj", do_pull=True, do_push=False, dry_run=True
    )
    assert code == 0
    assert (memory_dir / "orphan.md").exists()


def test_bidirectional_does_not_prune_local_only(fake_api: FakeAPI, memory_dir: Path):
    """Bidirectional sync uploads a local-only memory rather than pruning it."""
    fake_api.projects = [{"slug": "proj", "path": "/test/proj"}]
    memory_dir.mkdir(parents=True)
    (memory_dir / "onlylocal.md").write_text(
        "---\nname: onlylocal\ndescription: d\ntype: user\n---\nkeep me\n",
        encoding="utf-8",
    )
    code = sync_mod.run_sync("/test/proj")  # bidirectional
    assert code == 0
    assert (memory_dir / "onlylocal.md").exists()
    assert ("onlylocal", None) in {
        (m["name"], m.get("project_slug")) for m in fake_api.memories
    }

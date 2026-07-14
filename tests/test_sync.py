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

    Tests inject a list of projects and a list of server-side memories. Mirrors
    the real server's memory semantics: names are GLOBALLY unique, so a POST
    upserts by name and refuses (409) to move an existing name into a different
    scope without rescope=true; a PUT updates by id and 404s once the row is
    gone.
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
        if path.startswith("/api/memory/"):
            mem_id = int(path.rsplit("/", 1)[1])
            for m in self.memories:
                if m["id"] == mem_id:
                    return 200, json.dumps(m)
            return 404, json.dumps({"detail": "Memory not found"})
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
            name = payload["name"]
            for i, existing in enumerate(self.memories):
                if existing["name"] != name:
                    continue
                same_scope = (
                    existing.get("project_slug") == payload.get("project_slug")
                )
                if not same_scope and not payload.get("rescope"):
                    return 409, json.dumps(
                        {"detail": f"Memory '{name}' already exists in another scope;"
                                   " memory names are globally unique."}
                    )
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

    def put_json(self, path: str, payload: dict) -> tuple[int, str]:
        if path.startswith("/api/memory/"):
            mem_id = int(path.rsplit("/", 1)[1])
            for i, existing in enumerate(self.memories):
                if existing["id"] != mem_id:
                    continue
                clash = any(
                    m["name"] == payload.get("name") and m["id"] != mem_id
                    for m in self.memories
                )
                if clash:
                    return 409, json.dumps({"detail": "name already taken"})
                self.memories[i] = {**existing, **payload, "updated_at": "now"}
                return 200, json.dumps(self.memories[i])
            return 404, json.dumps({"detail": "Memory not found"})
        return 404, json.dumps({"detail": f"no fake handler for {path}"})


@pytest.fixture
def fake_api(monkeypatch: pytest.MonkeyPatch) -> FakeAPI:
    fake = FakeAPI()
    monkeypatch.setattr(sync_mod.api, "get", fake.get)
    monkeypatch.setattr(sync_mod.api, "post", fake.post)
    monkeypatch.setattr(sync_mod.api, "put_json", fake.put_json)
    return fake


@pytest.fixture
def memory_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Redirect Claude Code's memory dir for cwd '/test/proj' to tmp_path."""
    target = tmp_path / "memory"
    monkeypatch.setattr(sync_mod, "memory_dir_for_cwd", lambda _cwd: target)
    return target


def _parsed(path: Path) -> dict[str, Any]:
    """parse_memory_file, asserting the file is well-formed."""
    parsed = sync_mod.parse_memory_file(path)
    assert parsed is not None, f"{path.name} did not parse"
    return parsed


# --- Unit: parse / serialize ----------------------------------------------


def test_parse_valid_memory(tmp_path: Path):
    p = tmp_path / "m.md"
    p.write_text(
        "---\nid: 7\nname: foo\ndescription: d\ntype: user\nupdated_at: T1\n"
        "---\nbody text\n",
        encoding="utf-8",
    )
    parsed = sync_mod.parse_memory_file(p)
    assert parsed == {
        "id": 7, "name": "foo", "description": "d", "type": "user",
        "body": "body text", "updated_at": "T1",
    }


def test_parse_missing_id_is_not_a_failure(tmp_path: Path):
    """A file with no id (model-authored, or written before ids existed) parses
    with id=None and re-keys itself by name on the next push."""
    p = tmp_path / "m.md"
    p.write_text(
        "---\nname: foo\ndescription: d\ntype: user\n---\nbody text\n",
        encoding="utf-8",
    )
    parsed = sync_mod.parse_memory_file(p)
    assert parsed is not None
    assert parsed["id"] is None


def test_parse_garbage_id_is_not_a_failure(tmp_path: Path):
    p = tmp_path / "m.md"
    p.write_text(
        "---\nid: nonsense\nname: foo\ntype: user\n---\nbody\n", encoding="utf-8",
    )
    parsed = sync_mod.parse_memory_file(p)
    assert parsed is not None
    assert parsed["id"] is None


def test_canonical_filenames_disambiguates_slug_collision(tmp_path: Path):
    """Two different names can collapse to one base slug. The lower id keeps the
    plain filename; the other gets an id suffix, so neither clobbers the other."""
    mems = [
        {"id": 2, "name": "Keep Hydra deployment-agnostic"},
        {"id": 9, "name": "keep-hydra-deployment-agnostic"},
    ]
    out = sync_mod.canonical_filenames(mems)
    assert out == {
        2: "keep_hydra_deployment_agnostic.md",
        9: "keep_hydra_deployment_agnostic-9.md",
    }


def test_stamp_provenance_preserves_unknown_frontmatter_keys(tmp_path: Path):
    """Claude Code's own memory tool writes keys we don't emit (originSessionId).
    Stamping must not round-trip the file through our serializer."""
    p = tmp_path / "m.md"
    p.write_text(
        "---\nname: foo\ntype: user\noriginSessionId: abc-123\n---\nbody\n",
        encoding="utf-8",
    )
    sync_mod.stamp_provenance(p, {"id": 42, "updated_at": "2026-07-14T10:00:00+00:00"})
    text = p.read_text()
    assert "id: 42" in text
    assert "originSessionId: abc-123" in text
    assert "body" in text
    parsed = _parsed(p)
    assert parsed["id"] == 42
    assert parsed["updated_at"] == "2026-07-14T10:00:00+00:00"


def test_stamp_provenance_overwrites_existing_keys(tmp_path: Path):
    p = tmp_path / "m.md"
    p.write_text(
        "---\nid: 1\nname: foo\ntype: user\nupdated_at: OLD\n---\nbody\n",
        encoding="utf-8",
    )
    sync_mod.stamp_provenance(p, {"id": 1, "updated_at": "NEW"})
    parsed = _parsed(p)
    assert parsed["updated_at"] == "NEW"
    assert p.read_text().count("updated_at:") == 1


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
    """Claude Code encodes every non-alphanumeric char (incl. `:` and `\\`) as
    `-`, so a Windows-style path maps cleanly."""
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    # os.path.abspath is a no-op on absolute Windows-style paths when run on
    # Linux, so it's safe to drive this test cross-platform with a raw string.
    monkeypatch.setattr(sync_mod.os.path, "abspath", lambda p: p)
    d = sync_mod.memory_dir_for_cwd(r"C:\Users\giosu\projects\pcb")
    assert (
        d == tmp_path / ".claude" / "projects" / "C--Users-giosu-projects-pcb" / "memory"
    )


def test_memory_dir_for_cwd_underscore(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    """Claude Code maps `_` to `-` too. A slug that kept the underscore would
    point at a nonexistent dir and sync 0 files silently (the reported bug)."""
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    d = sync_mod.memory_dir_for_cwd("/home/me/foo_bar")
    assert d == tmp_path / ".claude" / "projects" / "-home-me-foo-bar" / "memory"


def test_memory_dir_for_cwd_dotted(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    """Dots (and any other non-alphanumeric) also collapse to `-`."""
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    d = sync_mod.memory_dir_for_cwd("/home/me/my.proj")
    assert d == tmp_path / ".claude" / "projects" / "-home-me-my-proj" / "memory"


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


def test_push_warns_when_memory_dir_missing(
    fake_api: FakeAPI, memory_dir: Path, capsys: pytest.CaptureFixture[str]
):
    """If the computed memory dir doesn't exist, a --push must warn instead of
    silently reporting 0 pushed (the wrong-slug failure mode)."""
    fake_api.projects = [{"slug": "proj", "path": "/test/proj"}]
    # NB: deliberately do NOT mkdir memory_dir - simulate a missing dir.
    code = sync_mod.run_sync("/test/proj", do_pull=False, do_push=True)
    assert code == 0
    err = capsys.readouterr().err
    assert "no memory dir" in err


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
    # Carries an id: it was pulled from the server once, and that row is gone.
    (memory_dir / "orphan.md").write_text(
        "---\nid: 99\nname: orphan\ndescription: d\ntype: user\n---\ngone from server\n",
        encoding="utf-8",
    )

    code = sync_mod.run_sync("/test/proj", do_pull=True, do_push=False)
    assert code == 0
    names = {p.name for p in memory_dir.iterdir() if p.suffix == ".md"}
    assert "orphan.md" not in names
    assert "keep.md" in names
    assert "orphan" not in (memory_dir / "MEMORY.md").read_text()


def test_pull_keeps_a_memory_the_server_refused(
    fake_api: FakeAPI, memory_dir: Path
):
    """A memory the server 409s (its name is held in another scope) is skipped by
    push and stays on disk with no id. The prune must NOT then delete it - the
    mirror is the only copy of content nobody has accepted yet."""
    fake_api.projects = [{"slug": "proj", "path": "/test/proj"}]
    fake_api.memories = [_server_memory(id=1, name="keep")]
    memory_dir.mkdir(parents=True)
    (memory_dir / "refused.md").write_text(
        "---\nname: refused\ndescription: d\ntype: user\n---\nthe only copy\n",
        encoding="utf-8",
    )

    sync_mod.run_sync("/test/proj", do_pull=True, do_push=False)

    assert (memory_dir / "refused.md").exists()
    assert "the only copy" in (memory_dir / "refused.md").read_text()


def test_pull_never_deletes_an_unparseable_file(fake_api: FakeAPI, memory_dir: Path):
    """Prune globs *.md, so it must not touch files it cannot read - hand-written
    notes, or another tool's files. The server has no opinion on them."""
    fake_api.projects = [{"slug": "proj", "path": "/test/proj"}]
    fake_api.memories = [_server_memory(id=1, name="keep")]
    memory_dir.mkdir(parents=True)
    (memory_dir / "notes.md").write_text("# just some notes\n", encoding="utf-8")

    sync_mod.run_sync("/test/proj", do_pull=True, do_push=False)

    assert (memory_dir / "notes.md").exists()


def test_push_tombstone_rebuilds_the_index(fake_api: FakeAPI, memory_dir: Path):
    """A push-only run that tombstones a file must rebuild MEMORY.md, or the
    index goes on advertising a memory whose file it just deleted."""
    fake_api.projects = [{"slug": "proj", "path": "/test/proj"}]
    fake_api.memories = [_server_memory(id=1, name="keep")]
    memory_dir.mkdir(parents=True)
    (memory_dir / "keep.md").write_text(
        "---\nid: 1\nname: keep\ndescription: d\ntype: user\n---\nb\n", encoding="utf-8",
    )
    (memory_dir / "ghost.md").write_text(
        "---\nid: 99\nname: ghost\ndescription: d\ntype: user\n---\nb\n",
        encoding="utf-8",
    )
    (memory_dir / "MEMORY.md").write_text(
        "- [keep](keep.md)\n- [ghost](ghost.md)\n", encoding="utf-8",
    )

    sync_mod.run_sync("/test/proj", do_pull=False, do_push=True)

    index = (memory_dir / "MEMORY.md").read_text()
    assert "ghost" not in index
    assert "keep" in index


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
    """Dry-run --pull reports prunes but deletes nothing. The server must hold at
    least one memory, or the empty-server guard would skip the prune anyway and
    this would pass for the wrong reason."""
    fake_api.projects = [{"slug": "proj", "path": "/test/proj"}]
    fake_api.memories = [{
        "id": 1, "name": "keep", "description": "d", "type": "user",
        "body": "kept", "project_slug": None,
        "created_at": "t", "updated_at": "t",
    }]
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


# --- Identity: the duplicate-memory bug -----------------------------------
#
# Every test below is a regression guard for one way the mirror used to mint a
# duplicate row. See the module docstring.


def _server_memory(**overrides: Any) -> dict[str, Any]:
    base = {
        "id": 1, "name": "m", "description": "d", "type": "user", "body": "b",
        "project_slug": None, "created_at": "t", "updated_at": "t",
    }
    return {**base, **overrides}


def test_pull_stamps_id_into_frontmatter(fake_api: FakeAPI, memory_dir: Path):
    fake_api.projects = [{"slug": "proj", "path": "/test/proj"}]
    fake_api.memories = [_server_memory(id=512, name="m")]

    sync_mod.run_sync("/test/proj", do_pull=True, do_push=False)

    parsed = sync_mod.parse_memory_file(memory_dir / "m.md")
    assert parsed is not None
    assert parsed["id"] == 512


def test_push_does_not_resurrect_a_deleted_memory(
    fake_api: FakeAPI, memory_dir: Path
):
    """THE bug. A memory deleted on the server leaves its mirror file behind;
    the Stop-hook push used to re-INSERT it as a brand-new row. Now the file's
    id 404s and the file is tombstoned instead."""
    fake_api.projects = [{"slug": "proj", "path": "/test/proj"}]
    fake_api.memories = [_server_memory(id=1, name="survivor")]
    memory_dir.mkdir(parents=True)
    (memory_dir / "survivor.md").write_text(
        "---\nid: 1\nname: survivor\ndescription: d\ntype: user\n---\nb\n",
        encoding="utf-8",
    )
    # id 99 was deleted on the server, but its mirror file is still here.
    (memory_dir / "ghost.md").write_text(
        "---\nid: 99\nname: ghost\ndescription: d\ntype: user\n---\nb\n",
        encoding="utf-8",
    )

    code = sync_mod.run_sync("/test/proj", do_pull=False, do_push=True)

    assert code == 0
    assert [m["name"] for m in fake_api.memories] == ["survivor"]  # NOT resurrected
    assert not (memory_dir / "ghost.md").exists()  # tombstoned


def test_push_does_not_fight_a_memory_rescoped_elsewhere(
    fake_api: FakeAPI, memory_dir: Path
):
    """A memory re-scoped to ANOTHER project is absent from this project's view
    but very much alive. Pushing it back would re-hijack it to this project on
    every Stop hook, so the file is dropped and the row left alone."""
    fake_api.projects = [
        {"slug": "proj", "path": "/test/proj"},
        {"slug": "other", "path": "/test/other"},
    ]
    fake_api.memories = [_server_memory(id=7, name="moved", project_slug="other",
                                        type="project")]
    memory_dir.mkdir(parents=True)
    (memory_dir / "moved.md").write_text(
        "---\nid: 7\nname: moved\ndescription: d\ntype: user\n---\nb\n",
        encoding="utf-8",
    )

    sync_mod.run_sync("/test/proj", do_pull=False, do_push=True)

    assert len(fake_api.memories) == 1
    assert fake_api.memories[0]["project_slug"] == "other"  # untouched
    assert fake_api.memories[0]["type"] == "project"
    assert not (memory_dir / "moved.md").exists()


def test_push_renames_in_place_instead_of_creating_a_row(
    fake_api: FakeAPI, memory_dir: Path
):
    """Renaming a memory locally updates the row by id. Before, the new name was
    a new key, so it INSERTed and the old row lived on as a duplicate."""
    fake_api.projects = [{"slug": "proj", "path": "/test/proj"}]
    fake_api.memories = [_server_memory(id=3, name="old-name", updated_at="T1")]
    memory_dir.mkdir(parents=True)
    (memory_dir / "old_name.md").write_text(
        "---\nid: 3\nname: new-name\ndescription: d\ntype: user\nupdated_at: T1\n"
        "---\nb\n",
        encoding="utf-8",
    )

    sync_mod.run_sync("/test/proj", do_pull=False, do_push=True)

    assert len(fake_api.memories) == 1
    assert fake_api.memories[0]["id"] == 3
    assert fake_api.memories[0]["name"] == "new-name"


def test_push_rescopes_in_place(fake_api: FakeAPI, memory_dir: Path):
    """Changing a memory's type from global to project re-scopes the same row."""
    fake_api.projects = [{"slug": "proj", "path": "/test/proj"}]
    fake_api.memories = [_server_memory(id=4, name="m", type="feedback", updated_at="T1")]
    memory_dir.mkdir(parents=True)
    (memory_dir / "m.md").write_text(
        "---\nid: 4\nname: m\ndescription: d\ntype: project\nupdated_at: T1\n---\nb\n",
        encoding="utf-8",
    )

    sync_mod.run_sync("/test/proj", do_pull=False, do_push=True)

    assert len(fake_api.memories) == 1
    assert fake_api.memories[0]["id"] == 4
    assert fake_api.memories[0]["project_slug"] == "proj"


def test_push_of_a_versionless_mirror_cannot_move_a_row(
    fake_api: FakeAPI, memory_dir: Path
):
    """A pre-upgrade mirror file (no id, no updated_at) pairs by name, but it has
    no basis to move the row's IDENTITY - its type/scope may be arbitrarily
    stale. Only its content lands. Otherwise the first Stop hook after the
    migration would push every legacy file's old scope back over the rows the
    migration just re-scoped, undoing it."""
    fake_api.projects = [{"slug": "proj", "path": "/test/proj"}]
    # The server row was re-scoped to a project by the migration.
    fake_api.memories = [_server_memory(
        id=5, name="m", type="project", project_slug="proj", body="OLD",
        updated_at="T2",
    )]
    memory_dir.mkdir(parents=True)
    # The legacy mirror still says global/feedback, and carries no version.
    (memory_dir / "m.md").write_text(
        "---\nname: m\ndescription: d\ntype: feedback\n---\nEDITED\n",
        encoding="utf-8",
    )

    sync_mod.run_sync("/test/proj", do_pull=False, do_push=True)

    row = fake_api.memories[0]
    assert len(fake_api.memories) == 1
    assert row["body"] == "EDITED"          # content still lands
    assert row["project_slug"] == "proj"    # but the scope is NOT reverted
    assert row["type"] == "project"
    assert row["name"] == "m"


def test_push_stamps_id_on_a_new_local_memory(fake_api: FakeAPI, memory_dir: Path):
    fake_api.projects = [{"slug": "proj", "path": "/test/proj"}]
    memory_dir.mkdir(parents=True)
    (memory_dir / "fresh.md").write_text(
        "---\nname: fresh\ndescription: d\ntype: user\n---\nb\n", encoding="utf-8",
    )

    sync_mod.run_sync("/test/proj", do_pull=False, do_push=True)

    assert len(fake_api.memories) == 1
    new_id = fake_api.memories[0]["id"]
    assert _parsed(memory_dir / "fresh.md")["id"] == new_id


def test_push_skips_when_name_is_held_in_another_scope(
    fake_api: FakeAPI, memory_dir: Path, capsys: pytest.CaptureFixture[str]
):
    """An id-less file whose name is already pinned elsewhere gets a 409. It must
    warn and skip, never raise - a hook discards stderr, and an exception would
    strand every file after it."""
    fake_api.projects = [{"slug": "proj", "path": "/test/proj"}]
    fake_api.memories = [
        _server_memory(id=1, name="taken", project_slug="other", type="project"),
    ]
    memory_dir.mkdir(parents=True)
    (memory_dir / "taken.md").write_text(
        "---\nname: taken\ndescription: d\ntype: user\n---\nb\n", encoding="utf-8",
    )
    (memory_dir / "fine.md").write_text(
        "---\nname: fine\ndescription: d\ntype: user\n---\nb\n", encoding="utf-8",
    )

    code = sync_mod.run_sync("/test/proj", do_pull=False, do_push=True)

    assert code == 2  # reported as a conflict
    # The later file still got pushed - one 409 must not abort the pass.
    assert "fine" in {m["name"] for m in fake_api.memories}
    assert len(fake_api.memories) == 2


def test_push_skips_files_claiming_the_same_id(
    fake_api: FakeAPI, memory_dir: Path, capsys: pytest.CaptureFixture[str]
):
    """A memory file copied by hand carries its id. Two files, one id: skip both
    rather than let last-write-wins silently destroy one."""
    fake_api.projects = [{"slug": "proj", "path": "/test/proj"}]
    fake_api.memories = [_server_memory(id=5, name="orig", body="SERVER")]
    memory_dir.mkdir(parents=True)
    (memory_dir / "a.md").write_text(
        "---\nid: 5\nname: orig\ndescription: d\ntype: user\n---\nAAA\n",
        encoding="utf-8",
    )
    (memory_dir / "b.md").write_text(
        "---\nid: 5\nname: copy\ndescription: d\ntype: user\n---\nBBB\n",
        encoding="utf-8",
    )

    sync_mod.run_sync("/test/proj", do_pull=False, do_push=True)

    assert fake_api.memories[0]["body"] == "SERVER"  # neither won
    assert "same id/name" in capsys.readouterr().err


def test_push_does_not_tombstone_against_an_empty_server(
    fake_api: FakeAPI, memory_dir: Path
):
    """A wrong HYDRA_URL or a restored DB looks exactly like 'all deleted'. The
    mirror may be the only copy left, so nothing is removed."""
    fake_api.projects = [{"slug": "proj", "path": "/test/proj"}]
    fake_api.memories = []
    memory_dir.mkdir(parents=True)
    (memory_dir / "m.md").write_text(
        "---\nid: 1\nname: m\ndescription: d\ntype: user\n---\nb\n", encoding="utf-8",
    )

    sync_mod.run_sync("/test/proj", do_pull=False, do_push=True)

    assert (memory_dir / "m.md").exists()
    assert fake_api.memories == []  # and not re-inserted either


def test_pull_does_not_prune_against_an_empty_server(
    fake_api: FakeAPI, memory_dir: Path
):
    fake_api.projects = [{"slug": "proj", "path": "/test/proj"}]
    fake_api.memories = []
    memory_dir.mkdir(parents=True)
    (memory_dir / "m.md").write_text(
        "---\nid: 1\nname: m\ndescription: d\ntype: user\n---\nb\n", encoding="utf-8",
    )

    sync_mod.run_sync("/test/proj", do_pull=True, do_push=False)

    assert (memory_dir / "m.md").exists()


def test_pull_writes_colliding_slugs_to_separate_files(
    fake_api: FakeAPI, memory_dir: Path
):
    """Two names that collapse to one base slug used to clobber one file: the
    higher id won it and the loser became a zombie - listed by the API, absent
    from the mirror, unfixable by editing files."""
    fake_api.projects = [{"slug": "proj", "path": "/test/proj"}]
    fake_api.memories = [
        _server_memory(id=2, name="Keep Hydra deployment-agnostic", body="ONE"),
        _server_memory(id=9, name="keep-hydra-deployment-agnostic", body="TWO"),
    ]

    sync_mod.run_sync("/test/proj", do_pull=True, do_push=False)

    files = sorted(p.name for p in memory_dir.glob("*.md") if p.name != "MEMORY.md")
    assert files == [
        "keep_hydra_deployment_agnostic-9.md",
        "keep_hydra_deployment_agnostic.md",
    ]
    index = (memory_dir / "MEMORY.md").read_text()
    assert "(keep_hydra_deployment_agnostic.md)" in index
    assert "(keep_hydra_deployment_agnostic-9.md)" in index


def test_pull_prunes_an_orphan_left_by_a_rename(fake_api: FakeAPI, memory_dir: Path):
    """The vscode_remote_env_setup.md class: a rename left the old file behind,
    holding the SAME name as the canonical one. The old prune keyed on name, so
    the name was 'on the server' and the orphan survived forever - and MEMORY.md
    listed it twice."""
    fake_api.projects = [{"slug": "proj", "path": "/test/proj"}]
    fake_api.memories = [_server_memory(id=512, name="VSCode Remote env vars")]
    memory_dir.mkdir(parents=True)
    (memory_dir / "vscode_remote_env_setup.md").write_text(
        "---\nname: VSCode Remote env vars\ndescription: d\ntype: user\n"
        "originSessionId: abc\n---\nstale\n",
        encoding="utf-8",
    )

    sync_mod.run_sync("/test/proj", do_pull=True, do_push=False)

    files = sorted(p.name for p in memory_dir.glob("*.md") if p.name != "MEMORY.md")
    assert files == ["vscode_remote_env_vars.md"]
    index = (memory_dir / "MEMORY.md").read_text()
    assert index.count("VSCode Remote env vars") == 1  # was 2 identical lines


def test_push_does_not_revert_a_server_side_rescope(
    fake_api: FakeAPI, memory_dir: Path, capsys: pytest.CaptureFixture[str]
):
    """The /forget workflow: the mirror was pulled at SessionStart, then the
    memory was re-scoped on the server mid-session. The Stop-hook push must not
    write the stale file back over that change - it holds an older updated_at,
    so the server wins and the next pull refreshes the file."""
    fake_api.projects = [{"slug": "proj", "path": "/test/proj"}]
    fake_api.memories = [_server_memory(
        id=8, name="m", type="project", project_slug="proj", updated_at="T2",
    )]
    memory_dir.mkdir(parents=True)
    # Pulled at T1, when the memory was still a global 'feedback' memory.
    (memory_dir / "m.md").write_text(
        "---\nid: 8\nname: m\ndescription: d\ntype: feedback\nupdated_at: T1\n---\nb\n",
        encoding="utf-8",
    )

    sync_mod.run_sync("/test/proj", do_pull=False, do_push=True)

    assert fake_api.memories[0]["project_slug"] == "proj"  # re-scope survived
    assert fake_api.memories[0]["type"] == "project"
    assert "changed on the server" in capsys.readouterr().err


def test_push_restamps_updated_at_so_the_next_push_still_works(
    fake_api: FakeAPI, memory_dir: Path
):
    """After a successful push the file must record the version it just wrote,
    or the precondition above would treat every later local edit as a conflict."""
    fake_api.projects = [{"slug": "proj", "path": "/test/proj"}]
    fake_api.memories = [_server_memory(id=1, name="m", body="OLD", updated_at="T1")]
    memory_dir.mkdir(parents=True)
    (memory_dir / "m.md").write_text(
        "---\nid: 1\nname: m\ndescription: d\ntype: user\nupdated_at: T1\n---\nEDIT1\n",
        encoding="utf-8",
    )

    sync_mod.run_sync("/test/proj", do_pull=False, do_push=True)
    assert fake_api.memories[0]["body"] == "EDIT1"
    assert _parsed(memory_dir / "m.md")["updated_at"] == "now"  # restamped

    # A second local edit still pushes.
    (memory_dir / "m.md").write_text(
        "---\nid: 1\nname: m\ndescription: d\ntype: user\nupdated_at: now\n---\nEDIT2\n",
        encoding="utf-8",
    )
    sync_mod.run_sync("/test/proj", do_pull=False, do_push=True)
    assert fake_api.memories[0]["body"] == "EDIT2"


def test_push_dry_run_writes_nothing(fake_api: FakeAPI, memory_dir: Path):
    fake_api.projects = [{"slug": "proj", "path": "/test/proj"}]
    fake_api.memories = [_server_memory(id=1, name="keep")]
    memory_dir.mkdir(parents=True)
    (memory_dir / "ghost.md").write_text(
        "---\nid: 99\nname: ghost\ndescription: d\ntype: user\n---\nb\n",
        encoding="utf-8",
    )
    (memory_dir / "fresh.md").write_text(
        "---\nname: fresh\ndescription: d\ntype: user\n---\nb\n", encoding="utf-8",
    )

    sync_mod.run_sync("/test/proj", do_pull=False, do_push=True, dry_run=True)

    assert (memory_dir / "ghost.md").exists()  # not tombstoned
    assert len(fake_api.memories) == 1  # nothing pushed
    assert _parsed(memory_dir / "fresh.md")["id"] is None

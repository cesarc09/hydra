"""Tests for the dry-run-first project registry prune command."""

from __future__ import annotations

import json
from typing import Any

import pytest
from hydra_cli import __main__ as main_mod
from hydra_cli import prune as prune_mod


def project(
    slug: str,
    *paths: str,
    confirmed: bool = False,
) -> dict[str, Any]:
    return {
        "slug": slug,
        "auto_registered_at": None if confirmed else "2026-01-01T00:00:00Z",
        "paths": [
            {"instance_id": "local", "path": path}
            for path in paths
        ],
    }


class FakeAPI:
    def __init__(
        self,
        projects: list[dict[str, Any]],
        memory_responses: list[list[dict[str, Any]] | tuple[int, str]],
        delete_responses: dict[str, tuple[int, str]] | None = None,
    ):
        self.projects = projects
        self.memory_responses = memory_responses
        self.delete_responses = delete_responses or {}
        self.gets: list[str] = []
        self.deletes: list[str] = []
        self.posts: list[str] = []

    def get(self, path: str) -> tuple[int, str]:
        self.gets.append(path)
        if path == "/api/projects":
            return 200, json.dumps(self.projects)
        if path == "/api/memory":
            response = self.memory_responses.pop(0)
            if isinstance(response, tuple):
                return response
            return 200, json.dumps(response)
        return 404, json.dumps({"detail": "not found"})

    def delete(self, path: str) -> tuple[int, str]:
        self.deletes.append(path)
        return self.delete_responses.get(path, (204, ""))

    def post(self, path: str, payload: dict[str, Any]) -> tuple[int, str]:
        self.posts.append(path)
        return 500, json.dumps({"detail": "unexpected write"})


def install_api(monkeypatch: pytest.MonkeyPatch, fake: FakeAPI) -> FakeAPI:
    monkeypatch.setattr(prune_mod.api, "get", fake.get)
    monkeypatch.setattr(prune_mod.api, "delete", fake.delete)
    monkeypatch.setattr(prune_mod.api, "post", fake.post)
    return fake


def actions(plan: prune_mod.PrunePlan) -> dict[str, str]:
    return {assessment.slug: assessment.action for assessment in plan.assessments}


def run(*argv: str) -> None:
    args = main_mod.build_parser().parse_args(["project", "prune", *argv])
    main_mod.cmd_project_prune(args)


def test_bucketing_requires_every_path_and_no_memories():
    projects = [
        project("anchor", "/work/anchor", confirmed=True),
        project("descendant", "/work/anchor/src"),
        project("junk", "/tmp/build", "/work/.cache/build"),
        project("mixed", "/tmp/mixed", "/srv/real-project"),
        project("remembered", "/tmp/remembered"),
        project("pathless"),
    ]
    memories = [{"id": 1, "project_slug": "remembered"}]

    plan = prune_mod.build_prune_plan(projects, memories)

    assert actions(plan) == {
        "anchor": "keep",
        "descendant": "delete",
        "junk": "delete",
        "mixed": "keep",
        "pathless": "keep",
        "remembered": "keep",
    }


def test_merge_candidate_is_reported_and_never_executed(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
):
    projects = [
        project("foo-bar", "/work/foo-bar", confirmed=True),
        project("foo_bar", "/work/foo-bar/foo_bar"),
    ]
    fake = install_api(monkeypatch, FakeAPI(projects, [[]]))

    run("--apply")

    out = capsys.readouterr().out
    assert "MERGE CANDIDATE foo_bar -> foo-bar (report only)" in out
    assert fake.deletes == []
    assert fake.posts == []


def test_ambiguous_twins_are_kept_for_manual_handling():
    projects = [
        project("foo-bar", "/tmp/a"),
        project("foo_bar", "/tmp/b"),
        project("f.o.o.b.a.r", "/tmp/c"),
    ]

    plan = prune_mod.build_prune_plan(projects, [])

    assert plan.merge_candidates == ()
    assert plan.ambiguous_twins == (("f.o.o.b.a.r", "foo-bar", "foo_bar"),)
    assert set(actions(plan).values()) == {"keep"}


@pytest.mark.parametrize("flag", [(), ("--dry-run",)])
def test_dry_run_is_default_and_issues_no_writes(
    flag: tuple[str, ...],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
):
    projects = [project("junk", "/tmp/build")]
    fake = install_api(monkeypatch, FakeAPI(projects, [[]]))

    run(*flag)

    out = capsys.readouterr().out
    assert "DELETE junk paths=1 memories=0" in out
    assert "exists=" in out
    assert "Dry run only" in out
    assert fake.deletes == []
    assert fake.posts == []


def test_apply_deletes_only_eligible_and_refreshes_before_each(
    monkeypatch: pytest.MonkeyPatch,
):
    projects = [
        project("one", "/tmp/one"),
        project("two", "/tmp/two"),
        project("real", "/srv/real"),
        project("remembered", "/tmp/remembered"),
    ]
    memory = {"id": 1, "project_slug": "remembered"}
    fake = install_api(monkeypatch, FakeAPI(projects, [[memory], [memory], [memory]]))

    run("--apply")

    assert fake.gets == [
        "/api/projects",
        "/api/memory",
        "/api/memory",
        "/api/memory",
    ]
    assert fake.deletes == ["/api/projects/one", "/api/projects/two"]


def test_apply_skips_project_that_acquired_memory(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
):
    projects = [project("one", "/tmp/one"), project("two", "/tmp/two")]
    acquired = {"id": 2, "project_slug": "one"}
    fake = install_api(monkeypatch, FakeAPI(projects, [[], [acquired], [acquired]]))

    run("--apply")

    assert fake.deletes == ["/api/projects/two"]
    assert "SKIP   one: acquired a pinned memory" in capsys.readouterr().out


def test_apply_aborts_when_refresh_fails_part_way(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
):
    projects = [
        project("one", "/tmp/one"),
        project("two", "/tmp/two"),
        project("three", "/tmp/three"),
    ]
    fake = install_api(
        monkeypatch,
        FakeAPI(projects, [[], [], (503, json.dumps({"detail": "offline"}))]),
    )

    with pytest.raises(SystemExit, match="1"):
        run("--apply")

    assert fake.deletes == ["/api/projects/one"]
    assert "failed to fetch memories before delete: offline" in capsys.readouterr().err


def test_apply_surfaces_409_and_stops_deleting(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
):
    projects = [
        project("one", "/tmp/one"),
        project("two", "/tmp/two"),
        project("three", "/tmp/three"),
    ]
    conflict = (409, json.dumps({"detail": "Project still has pinned memories"}))
    fake = install_api(
        monkeypatch,
        FakeAPI(
            projects,
            [[], [], []],
            delete_responses={"/api/projects/three": conflict},
        ),
    )

    with pytest.raises(SystemExit, match="1"):
        run("--apply")

    assert fake.deletes == ["/api/projects/one", "/api/projects/three"]
    err = capsys.readouterr().err
    assert "failed to delete three (409)" in err
    assert "Project still has pinned memories" in err


def test_initial_fetch_failure_never_writes(
    monkeypatch: pytest.MonkeyPatch,
):
    fake = install_api(
        monkeypatch,
        FakeAPI([], [(500, json.dumps({"detail": "broken"}))]),
    )

    with pytest.raises(SystemExit, match="1"):
        run("--apply")

    assert fake.deletes == []
    assert fake.posts == []


def test_prune_mode_flags_are_mutually_exclusive():
    with pytest.raises(SystemExit):
        main_mod.build_parser().parse_args(
            ["project", "prune", "--dry-run", "--apply"]
        )

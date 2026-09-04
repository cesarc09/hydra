"""Tests for the `hydra doctor` CLI report (stats aggregation + anomaly checks)."""

import argparse
import json
import urllib.error
from pathlib import Path

from hydra_cli import __main__ as cli
from hydra_cli import api


def _fake_get(responses: dict[str, tuple[int, str]]):
    def _get(path: str) -> tuple[int, str]:
        return responses[path]

    return _get


def test_doctor_healthy_with_anomalies(monkeypatch, capsys):
    projects = [
        {"slug": "hydra", "paths": [{"instance_id": "pi", "path": "/p"}],
         "auto_registered_at": None},
        {"slug": "ghost", "paths": [], "auto_registered_at": "2026-01-01T00:00:00+00:00"},
    ]
    memories = [
        {"id": 1, "name": "global-pref", "type": "user", "project_slug": None},
        {"id": 2, "name": "pinned-ok", "type": "project", "project_slug": "hydra"},
        # invariant violation: global type but pinned to a project
        {"id": 3, "name": "bad-scope", "type": "feedback", "project_slug": "hydra"},
        # orphan: pinned to a slug not in the registry
        {"id": 4, "name": "orphan", "type": "project", "project_slug": "gone"},
    ]
    sessions = [
        {"session_id": "a", "status": "active", "instance_id": "pi",
         "remote_control_url": "https://claude.ai/code/session_Abc123"},
        {"session_id": "b", "status": "idle", "instance_id": "pi",
         "remote_control_url": None},
        # ended sessions have their URL cleared by SessionEnd, so they are not
        # a valid denominator and must be excluded
        {"session_id": "c", "status": "ended", "instance_id": "pi",
         "remote_control_url": None},
        # another machine's session must not be counted for this instance
        {"session_id": "d", "status": "active", "instance_id": "laptop",
         "remote_control_url": None},
    ]
    responses = {
        "/api/health": (200, json.dumps({"status": "ok", "db": "ok"})),
        "/api/projects": (200, json.dumps(projects)),
        "/api/memory": (200, json.dumps(memories)),
        "/api/sessions": (200, json.dumps(sessions)),
    }
    monkeypatch.setattr(api, "get", _fake_get(responses))
    monkeypatch.setenv("HYDRA_AUTH_TOKEN", "x")
    monkeypatch.setenv("HYDRA_INSTANCE_ID", "pi")
    # Keep the local half of the Remote Control check off this machine's corpus.
    monkeypatch.setattr(cli, "_newest_transcript", lambda: None)
    monkeypatch.setattr(cli, "_claude_code_version", lambda: "2.1.251")

    cli.cmd_doctor(argparse.Namespace())
    out = capsys.readouterr().out

    assert "server:    UP" in out
    assert "database:  OK" in out
    assert "auth:      OK (token set)" in out
    assert "projects:  2 total (1 pending review, 1 confirmed)" in out
    assert "memories:  4 total (1 global, 3 pinned)" in out
    assert "[WARN] user/feedback memories pinned to a project" in out
    assert "#3 bad-scope" in out
    assert "[WARN] memories pinned to an unregistered slug" in out
    assert "#4 orphan" in out
    assert "[WARN] projects with no registered path: 1 - ghost" in out
    assert "[WARN] projects pending review" in out
    assert "remote control:  (Claude Code 2.1.251)" in out
    assert "server:  1/2 live sessions have a URL (instance pi)" in out
    assert "local:   no transcripts under ~/.claude/projects" in out


def test_doctor_server_down(monkeypatch, capsys):
    def _down(path: str) -> tuple[int, str]:
        raise urllib.error.URLError("Connection refused")

    monkeypatch.setattr(api, "get", _down)
    cli.cmd_doctor(argparse.Namespace())
    out = capsys.readouterr().out
    assert "server:    DOWN" in out
    assert "Cannot reach the server" in out


def test_doctor_auth_failed(monkeypatch, capsys):
    responses = {
        "/api/health": (200, json.dumps({"status": "ok", "db": "ok"})),
        "/api/projects": (401, '{"detail":"Unauthorized"}'),
    }
    monkeypatch.setattr(api, "get", _fake_get(responses))
    cli.cmd_doctor(argparse.Namespace())
    out = capsys.readouterr().out
    assert "auth:      FAILED (401)" in out


def test_doctor_reports_stray_memory_files(monkeypatch, capsys, tmp_path: Path):
    responses = {
        "/api/health": (200, json.dumps({"status": "ok", "db": "ok"})),
        "/api/projects": (200, "[]"),
        "/api/memory": (200, "[]"),
        "/api/sessions": (200, "[]"),
    }
    monkeypatch.setattr(api, "get", _fake_get(responses))
    monkeypatch.setattr(cli, "_newest_transcript", lambda: None)
    project_memory = tmp_path / "project" / "memory"
    project_memory.mkdir(parents=True)
    stray = project_memory / "stray.md"
    stray.write_text(
        "---\nname: stray\ndescription: d\ntype: user\n---\nlocal only\n",
        encoding="utf-8",
    )
    (project_memory / "broken.md").write_text("not frontmatter\n", encoding="utf-8")
    (project_memory / "MEMORY.md").write_text("index\n", encoding="utf-8")

    cli.cmd_doctor(argparse.Namespace(), projects_root=tmp_path)
    out = capsys.readouterr().out

    assert "[WARN] stray local memory files: 2" in out
    assert str(stray) in out
    assert str(project_memory / "broken.md") in out
    assert str(project_memory / "MEMORY.md") not in out

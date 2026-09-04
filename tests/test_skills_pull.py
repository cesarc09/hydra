"""Tests for harness-specific skill installation and managed-file pruning."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from hydra_cli import skills as skills_mod


def row(
    text: str,
    *,
    kind: str = "skill",
    enabled: bool = True,
    implicit: bool = True,
    instances: list[str] | None = None,
) -> dict[str, object]:
    filename = "instructions" if kind == "instructions" else "SKILL.md"
    return {
        "kind": kind,
        "enabled": enabled,
        "implicit_invocation": implicit,
        "instances": instances,
        "files": {filename: text},
    }


class FakePull:
    def __init__(self) -> None:
        self.served: dict[str, dict[str, object]] = {}
        self.status = 200

    def get(self, path: str) -> tuple[int, str]:
        assert path in {
            "/api/config/skills/claude-code",
            "/api/config/skills/codex-cli",
        }
        return self.status, json.dumps(self.served)


@pytest.fixture
def pull_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    claude = tmp_path / "claude"
    codex = tmp_path / "codex"
    agents = tmp_path / "agents" / "skills"
    monkeypatch.setattr(skills_mod, "claude_dir", lambda: claude)
    monkeypatch.setattr(skills_mod, "codex_home", lambda: codex)
    monkeypatch.setattr(skills_mod, "agents_skills_dir", lambda: agents)
    monkeypatch.setenv("HYDRA_INSTANCE_ID", "pi")
    fake = FakePull()
    monkeypatch.setattr(skills_mod.api, "get", fake.get)
    return fake, claude, codex, agents


def managed(claude: Path, harness: str) -> set[str]:
    path = claude / f".hydra-skills-{harness}.json"
    return set(json.loads(path.read_text())["managed"])


def test_claude_instructions_and_invocation_frontmatter(pull_env):
    fake, claude, _codex, _agents = pull_env
    fake.served = {
        "instructions": row("Be useful.\n", kind="instructions"),
        "explicit": row("---\nname: Explicit\n---\nBody\n", implicit=False),
        "implicit": row("---\nname: Implicit\n---\nBody\n"),
        "existing": row(
            "---\nname: Existing\ndisable-model-invocation: false\n---\nBody\n",
            implicit=False,
        ),
        "stale": row("---\nname: Stale\ndisable-model-invocation: true\n---\nBody\n"),
    }

    assert skills_mod.run_pull("claude-code") == 0
    assert (claude / "CLAUDE.md").read_text() == "Be useful.\n"
    explicit = (claude / "skills" / "explicit" / "SKILL.md").read_text()
    assert explicit == "---\nname: Explicit\ndisable-model-invocation: true\n---\nBody\n"
    assert "disable-model-invocation" not in (
        claude / "skills" / "implicit" / "SKILL.md"
    ).read_text()
    # The server's flag wins over whatever value the body arrived with, in both
    # directions - counting the key would pass on a stale value either way.
    assert (claude / "skills" / "existing" / "SKILL.md").read_text() == (
        "---\nname: Existing\ndisable-model-invocation: true\n---\nBody\n"
    )
    assert (claude / "skills" / "stale" / "SKILL.md").read_text() == (
        "---\nname: Stale\n---\nBody\n"
    )


def test_claude_missing_frontmatter_is_written_with_warning(pull_env, capsys):
    fake, claude, _codex, _agents = pull_env
    fake.served = {"plain": row("Body\n", implicit=False)}
    assert skills_mod.run_pull("claude-code") == 0
    assert (claude / "skills" / "plain" / "SKILL.md").read_text() == "Body\n"
    assert "has no YAML frontmatter" in capsys.readouterr().err


def test_codex_writes_verbatim_skill_and_exact_openai_yaml(pull_env):
    fake, _claude, codex, agents = pull_env
    text = '---\nname: "Debug Hydra"\ndescription: "A useful helper"\n---\nBody\n'
    fake.served = {
        "instructions": row("Codex rules\n", kind="instructions"),
        "debug": row(text, implicit=False),
        "fallback": row("---\ndescription: Short\n---\nBody\n"),
    }

    assert skills_mod.run_pull("codex-cli") == 0
    assert (codex / "AGENTS.md").read_text() == "Codex rules\n"
    assert (agents / "debug" / "SKILL.md").read_text() == text
    assert (agents / "debug" / "agents" / "openai.yaml").read_text() == (
        "interface:\n"
        '  display_name: "Debug Hydra"\n'
        '  short_description: "A useful helper"\n'
        "\n"
        "policy:\n"
        "  allow_implicit_invocation: false\n"
    )
    assert 'display_name: "fallback"' in (
        agents / "fallback" / "agents" / "openai.yaml"
    ).read_text()


def test_second_pull_is_unchanged(pull_env, capsys):
    fake, _claude, _codex, _agents = pull_env
    fake.served = {"one": row("---\nname: One\n---\nBody\n")}
    skills_mod.run_pull("codex-cli")
    capsys.readouterr()
    assert skills_mod.run_pull("codex-cli") == 0
    assert "0 written, 2 unchanged, 0 pruned, 0 refused" in capsys.readouterr().out


def test_empty_response_keeps_files_and_state_bytes(pull_env, capsys):
    fake, claude, _codex, _agents = pull_env
    fake.served = {"one": row("---\nname: One\n---\n")}
    skills_mod.run_pull("claude-code")
    state = claude / ".hydra-skills-claude-code.json"
    before = state.read_bytes()
    fake.served = {}
    assert skills_mod.run_pull("claude-code") == 0
    assert state.read_bytes() == before
    assert (claude / "skills" / "one" / "SKILL.md").exists()
    assert "server served 0 skills" in capsys.readouterr().err


def test_disabled_and_instance_filtered_rows_prune_skills(pull_env):
    fake, claude, _codex, _agents = pull_env
    fake.served = {
        "disabled": row("A", enabled=True),
        "elsewhere": row("B", instances=["pi"]),
    }
    skills_mod.run_pull("claude-code")
    fake.served = {
        "disabled": row("A", enabled=False),
        "elsewhere": row("B", instances=["laptop"]),
    }
    assert skills_mod.run_pull("claude-code") == 0
    assert not (claude / "skills" / "disabled").exists()
    assert not (claude / "skills" / "elsewhere").exists()
    assert managed(claude, "claude-code") == set()


def test_unmanaged_refusal_identical_adoption_and_adopt_overwrite(pull_env, capsys):
    fake, claude, _codex, _agents = pull_env
    target = claude / "skills" / "one" / "SKILL.md"
    target.parent.mkdir(parents=True)
    target.write_text("local")
    fake.served = {"one": row("remote")}

    assert skills_mod.run_pull("claude-code") == 1
    assert target.read_text() == "local"
    assert str(target) not in managed(claude, "claude-code")
    assert "unmanaged; rerun with --adopt" in capsys.readouterr().err

    target.write_text("remote")
    assert skills_mod.run_pull("claude-code") == 0
    assert str(target) in managed(claude, "claude-code")

    state = claude / ".hydra-skills-claude-code.json"
    state.unlink()
    target.write_text("local again")
    assert skills_mod.run_pull("claude-code", adopt=True) == 0
    assert target.read_text() == "remote"
    assert str(target) in managed(claude, "claude-code")


def test_symlink_is_refused_even_with_adopt(pull_env, tmp_path: Path):
    fake, claude, _codex, _agents = pull_env
    source = tmp_path / "source"
    source.write_text("local")
    target = claude / "skills" / "one" / "SKILL.md"
    target.parent.mkdir(parents=True)
    target.symlink_to(source)
    fake.served = {"one": row("remote")}
    assert skills_mod.run_pull("claude-code", adopt=True) == 1
    assert source.read_text() == "local"
    assert str(target) not in managed(claude, "claude-code")


def test_previously_managed_symlink_is_refused_not_pruned(pull_env, tmp_path: Path):
    fake, claude, _codex, _agents = pull_env
    target = claude / "skills" / "one" / "SKILL.md"
    fake.served = {"one": row("remote")}
    skills_mod.run_pull("claude-code")
    target.unlink()
    source = tmp_path / "source"
    source.write_text("local")
    target.symlink_to(source)

    assert skills_mod.run_pull("claude-code") == 1
    assert target.is_symlink()
    assert source.read_text() == "local"
    assert str(target) not in managed(claude, "claude-code")


def test_harness_states_do_not_cross_prune(pull_env):
    fake, claude, _codex, agents = pull_env
    fake.served = {"claude": row("C")}
    skills_mod.run_pull("claude-code")
    fake.served = {"codex": row("X")}
    skills_mod.run_pull("codex-cli")
    assert (claude / "skills" / "claude" / "SKILL.md").exists()
    assert (agents / "codex" / "SKILL.md").exists()


@pytest.mark.parametrize("harness", ["claude-code", "codex-cli"])
def test_absent_instructions_stay_managed_while_skills_prune(pull_env, harness: str):
    fake, claude, codex, agents = pull_env
    fake.served = {
        "instructions": row("Rules", kind="instructions"),
        "one": row("Body"),
    }
    skills_mod.run_pull(harness)
    fake.served = {"other": row("ignored", enabled=False)}
    assert skills_mod.run_pull(harness) == 0

    instruction = claude / "CLAUDE.md" if harness == "claude-code" else codex / "AGENTS.md"
    skill = claude / "skills" / "one" if harness == "claude-code" else agents / "one"
    assert instruction.read_text() == "Rules"
    assert str(instruction) in managed(claude, harness)
    assert not skill.exists()


def test_non_200_writes_nothing(pull_env):
    fake, claude, _codex, _agents = pull_env
    fake.status = 500
    fake.served = {"one": row("Body")}
    assert skills_mod.run_pull("claude-code") == 1
    assert not claude.exists()

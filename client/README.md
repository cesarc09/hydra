# claude-config

Shared Claude Code configuration — personal rules and hook settings synced across machines via git.

## What's in here

| File | Purpose |
|------|---------|
| `settings.json` | User-level settings including Hydra hook configuration |
| `setup.sh` | Deploys these files to `~/.claude/` (copy on Windows, symlink on Linux) |

## How Claude Code loads configuration

Claude Code reads settings from two layers:

1. **Project-level** — `CLAUDE.md` and `.claude/settings.json` inside each git repo (shared via git with collaborators)
2. **User-level** — `~/.claude/CLAUDE.md` and `~/.claude/settings.json` (personal, per-machine)

This repo manages **layer 2** — your personal preferences and hook configuration. It is deployed to `~/.claude/` on each machine via `setup.sh`.

## Setup (new machine)

```bash
git clone <this-repo> ~/projects/hydra
cd ~/projects/hydra

# Set Hydra env vars in your shell profile (~/.bashrc or ~/.zshrc):
export HYDRA_INSTANCE_ID="machine-name"    # unique per machine
export HYDRA_AUTH_TOKEN="your-token"        # must match Hydra server

# Deploy
./client/setup.sh          # Windows (Git Bash) — copies files
./client/setup.sh --link   # Linux / macOS / WSL — creates symlinks
```

## Updating rules

1. Edit `settings.json` in this repo
2. Commit and push
3. On each machine: `cd ~/projects/hydra && git pull && ./client/setup.sh`

Or wait — the `SessionStart` hook automatically pulls and deploys on every new Claude Code session.

## Hydra hooks

`settings.json` includes HTTP hooks that report Claude Code session events to the Hydra dashboard server (this repo's `server/`). Events tracked:

- Session start/end
- User prompts
- Tool usage (Write, Edit, Bash)
- Session idle/waiting states
- Subagent lifecycle

Hooks are non-blocking (5s timeout, fail silently). If the Hydra server is unreachable, Claude Code continues normally.

## Auto-sync

On every `SessionStart`, a command hook runs:

```bash
cd ~/projects/hydra && git pull --ff-only && bash client/setup.sh
```

This keeps all machines in sync without manual intervention. If the pull fails (no network, merge conflict), it fails silently.

# claude-config

Shared Claude Code configuration - personal rules and hook settings synced across machines via git.

## What's in here

| File | Purpose |
|------|---------|
| `settings.json` | Hydra hooks template (HTTP hooks + sync commands; `__HYDRA_URL__` placeholder) |
| `settings.user.template.json` | Default user preferences (`effortLevel`, `attribution`, `statusLine`, …) |
| `statusline.sh` | Default Claude Code status-line script (context-window progress bar) |
| `setup.sh` | Pip-installs `hydra_cli` and renders `~/.claude/settings.json` from the layers above |

## How Claude Code loads configuration

Claude Code reads settings from two layers:

1. **Project-level** - `CLAUDE.md` and `.claude/settings.json` inside each git repo (shared via git with collaborators)
2. **User-level** - `~/.claude/CLAUDE.md` and `~/.claude/settings.json` (personal, per-machine)

This repo manages **layer 2** - your personal preferences and hook configuration. It is deployed to `~/.claude/` on each machine via `setup.sh`.

## Setup (new machine)

```bash
git clone <this-repo> ~/projects/hydra
cd ~/projects/hydra

# Set Hydra env vars in your shell profile (~/.bashrc or ~/.zshrc):
export HYDRA_INSTANCE_ID="machine-name"    # unique per machine
export HYDRA_AUTH_TOKEN="your-token"        # must match Hydra server

# Deploy
bash client/setup.sh
```

## How `~/.claude/settings.json` is composed

`setup.sh` runs `python -m hydra_cli apply-settings`, which merges three
layers in priority order:

1. **Hydra hooks template** - `client/settings.json` (HTTP hooks + sync commands).
2. **User-pref defaults** - `client/settings.user.template.json` (`effortLevel`, `attribution`, `statusLine`, …).
3. **Your overrides** - `~/.claude/settings.user.json`. Scaffolded as a *copy* of the template on first run so you see every available knob.

For each event under `hooks`, Hydra's matcher-groups come first and any user
matcher-groups append. For other top-level keys, later layers override earlier
ones (so your overrides beat both templates).

### Customizing prefs

Edit `~/.claude/settings.user.json`:

- **Change a value** - your value wins on the next render.
- **Delete a field** - falls back to the template default. This is how to opt out of a default you don't want without removing it from the shipped template (which would affect everyone else). For example, drop the `statusLine` block to use Claude Code's built-in status line instead of `~/.claude/statusline.sh`.

`~/.claude/settings.user.json` is never overwritten after the initial scaffold -
your edits survive every `setup.sh` re-run.

## Updating shared rules / hooks

1. Edit `client/settings.json` (hooks) or `client/settings.user.template.json` (defaults).
2. Commit and push.
3. On each machine: `cd ~/projects/hydra && git pull && bash client/setup.sh`.

The `SessionStart` hook also pulls and re-renders automatically on every new Claude Code session.

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

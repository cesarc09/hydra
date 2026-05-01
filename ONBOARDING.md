# Onboarding a new machine

Follow this once per machine where you'll run Claude Code as a Hydra client. Skip sections that don't apply.

## Prerequisites

- Git, Python 3.11+, pip
- Hydra server URL + auth token (ask the server admin if that's not you)
- `~/.claude/` — will be created by Claude Code on first run

## 1. Clone the repo

Pick any location — setup.sh auto-detects where it lives. `~/projects/hydra` is just a convention:

```bash
git clone https://github.com/cesarc09/hydra.git ~/projects/hydra    # or wherever you keep code
cd ~/projects/hydra
```

The SessionStart hook will `cd` back to this clone to `git pull` on every session, so pick somewhere stable.

## 2. Set environment variables

Every Claude Code process on this machine needs:

- `HYDRA_URL` — e.g. `http://localhost:8400` or the public server URL
- `HYDRA_AUTH_TOKEN` — shared secret from the server
- `HYDRA_INSTANCE_ID` — unique machine name, usually `$(hostname)`

**Where** to put them depends on how you launch Claude Code. Pick the section(s) that apply.

### Interactive terminal (`claude` from a shell prompt)

Append to `~/.bashrc` (or `~/.zshrc`):

```bash
export HYDRA_URL=https://your-hydra-server
export HYDRA_AUTH_TOKEN=<token>
export HYDRA_INSTANCE_ID="$(hostname)"
```

Reload with `source ~/.bashrc`.

### VSCode Remote (SSH into this machine, use the Claude Code extension)

VSCode's Remote Server is launched non-interactively by sshd, so `~/.bashrc` is never read. Use VSCode's own env-setup hook instead:

```bash
umask 077
cat > ~/.vscode-server/server-env-setup <<'EOF'
export HYDRA_URL=https://your-hydra-server
export HYDRA_AUTH_TOKEN=<token>
export HYDRA_INSTANCE_ID="$(hostname)"
EOF
chmod 600 ~/.vscode-server/server-env-setup
```

Then **fully disconnect and reconnect** VSCode Remote (not just restart the extension) — the file is only sourced at server startup.

### Windows (native, not WSL)

```powershell
[Environment]::SetEnvironmentVariable("HYDRA_URL", "https://your-hydra-server", "User")
[Environment]::SetEnvironmentVariable("HYDRA_AUTH_TOKEN", "<token>", "User")
[Environment]::SetEnvironmentVariable("HYDRA_INSTANCE_ID", $env:COMPUTERNAME, "User")
```

Restart any shell or editor for changes to take effect.

## 3. Run setup.sh

```bash
bash ~/projects/hydra/client/setup.sh
```

This materializes `~/.claude/settings.json` with hooks pointing at `$HYDRA_URL` and installs the `hydra` CLI. If `pip install -e` errors with PEP 668, retry with `pip install --user -e ~/projects/hydra/client`.

> **Run `setup.sh` against the Python interpreter Claude Code will see.** The SessionStart/Stop hooks invoke `python -m hydra_cli ...`, which resolves through whatever `python` is first on `PATH` when Claude Code spawns the hook — usually your *base* user/system Python, not a venv. `setup.sh` calls `pip install -e client/`, so run it from a shell where `pip` and `python` resolve to the same interpreter you want hydra installed into. If you run it from inside a venv, hydra ends up only in that venv and the hooks will silently no-op outside it. Re-run `setup.sh` after switching Python versions.

Verify: `python -m hydra_cli --help` (and optionally `hydra --help` if the console shim is on PATH).

## 4. Register your projects

For each project directory where you use Claude Code:

```bash
hydra project create --slug <short-name> --path "$(pwd)"
```

The slug identifies the project across machines — use the same slug everywhere. If a project with the same slug already exists (from another machine), `hydra sync` auto-attaches this machine's path by directory basename, so you only need to register brand-new projects manually.

## 5. Upload this machine's existing memories

From each project directory:

```bash
hydra sync --push
```

Scope is derived from memory type: `user`/`feedback` → global; `project`/`reference` → pinned to the current project. Re-runs are safe (upsert).

## 6. Verify

```bash
hydra memory list | head
```

Start a fresh Claude Code session inside a registered project. Within a second it should appear on the dashboard, and `~/.claude/projects/<dir>/memory/` should contain pulled memories.

## 7. Enable Remote Control (recommended, for mobile)

Claude Code's **Remote Control** feature lets you reach a running session from the Claude mobile app. Inside any Claude Code session, run `/config` and turn on "Enable Remote Control for all sessions" so it's auto-enabled per session.

The URL the CLI prints (`https://claude.ai/code/session_...`) isn't exposed to external tooling, so Hydra can't capture it automatically. Each session card on the dashboard has a field to paste this URL once per session — after which the card's "Open Remote Control" button deep-links to that specific session from your phone.

## Troubleshooting

**401 on hook events.** Env vars aren't reaching the Claude Code process. Inspect one:

```bash
tr '\0' '\n' < /proc/$(pgrep -f claude | head -1)/environ | grep HYDRA
```

Empty = env-setup file not read. For VSCode Remote, confirm full disconnect + reconnect.

**`hydra: command not found`.** The CLI isn't on PATH. Ensure `~/.local/bin` is in `$PATH`, or run via `python -m hydra_cli`.

**Sync reports "no project registered".** The cwd doesn't match any registered path. Either `hydra project create` for this directory, or ensure its basename matches an existing slug (auto-attach will fire).

## Automating this flow

If you'd rather have Claude Code walk through the steps interactively, paste the prompt from [client/onboard-prompt.md](client/onboard-prompt.md) into a fresh session on the new machine.

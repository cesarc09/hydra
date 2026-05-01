# Hydra — Claude Code Control Plane

One server that holds your memories and CLAUDE.md, and watches every session — across every machine you use Claude Code on.

## Why

Two things about Claude Code hurt once you use it on more than one machine.

**Every session starts stateless.** Context you've established — preferences, project conventions, decisions already made — doesn't carry forward to the next session, let alone the next machine. You re-brief, over and over, and that friction is what keeps agents out of workflows where they'd otherwise fit.

**Every session runs in isolation.** You can't tell at a glance which session on which machine is waiting for input, what just got edited, or whether something is stuck.

Hydra is one server that solves both. A memory store and CLAUDE.md that travel with you across machines — pulled when a session starts, pushed when it ends — and a live dashboard that watches every session in real time. Same server, same bearer token; the two are independent, so observation keeps working if sync fails, and vice versa.

```
┌──────────────────────────────────────────────────────────────┐
│                    Hydra Server (24/7)                        │
│                                                               │
│     Memory store  ·  CLAUDE.md  ·  Project registry           │
│                            │                                  │
│                  Live dashboard (SSE)                         │
└──────────────┬──────────────────────────────┬────────────────┘
               │ SessionStart: pull           │ Stop/SessionEnd: push
               │ (memories, CLAUDE.md)        │ (new memories, events)
               │                              │
       ┌───────┴──────┬───────────┬──────────┴────┐
       │              │           │               │
     Mac            WSL        SSH-1           SSH-2
  Claude Code    Claude Code  Claude Code    Claude Code
```

## How It Works

Two loops run continuously:

**Context loop** — `hydra sync` reconciles each machine's local memory dir (`~/.claude/projects/<dir>/memory/`) with the server. A SessionStart hook runs `hydra sync --pull` before Claude sees the session; a Stop hook runs `hydra sync --push` at turn end. Memories are typed: `user`/`feedback` are global (available everywhere), `project`/`reference` are pinned to the project the cwd maps to.

**Observation loop** — every Claude Code tool call fires an HTTP hook to `/api/hooks/event`. The server tracks session state transitions (active / idle / waiting_input / ended) and broadcasts them over Server-Sent Events to any open dashboard.

Same bearer token, same server, but the two loops don't depend on each other.

## Setup

### Server

Run on any always-on machine: laptop, VPS, home server, Raspberry Pi.

```bash
git clone https://github.com/cesarc09/hydra.git ~/hydra && cd ~/hydra
python3 -m venv venv && source venv/bin/activate
pip install -r requirements.txt

export HYDRA_AUTH_TOKEN=$(openssl rand -hex 32)
export HYDRA_BIND_HOST=127.0.0.1    # loopback-only; reverse-proxy terminates TLS

uvicorn server.app:app --host "$HYDRA_BIND_HOST" --port 8400
```

The server fails closed if `HYDRA_AUTH_TOKEN` is unset. For local dev without a token, set `HYDRA_ALLOW_NO_AUTH=1`. For production, run under a service manager (systemd, launchd, Docker) and put the server behind whatever reverse-proxy or TLS termination you prefer — Hydra doesn't care.

### Client (each machine that runs Claude Code)

```bash
git clone https://github.com/cesarc09/hydra.git ~/projects/hydra
export HYDRA_URL=https://your-hydra-server       # or http://localhost:8400
export HYDRA_AUTH_TOKEN=...                      # must match the server
export HYDRA_INSTANCE_ID="$(hostname)"
bash ~/projects/hydra/client/setup.sh
```

`setup.sh` installs `~/.claude/settings.json` with hooks pointing at `$HYDRA_URL` and installs the `hydra` CLI (`pip install -e`). Put the exports in your shell profile so hooks see them in every session.

Run `setup.sh` from a shell where `python` and `pip` resolve to the interpreter Claude Code will see at hook time — typically your base user/system Python, not a venv. The SessionStart/Stop hooks call `python -m hydra_cli`, so the package must be installed against whatever `python` is first on `PATH` when Claude Code spawns. Installing inside a venv pins hydra to that venv and the hooks silently no-op everywhere else.

For a full walkthrough covering VSCode Remote, Windows, and initial memory sync, see [ONBOARDING.md](ONBOARDING.md).

### Verify

1. Start the server.
2. Open `$HYDRA_URL/` in a browser, paste the token when prompted.
3. Start a Claude Code session on any configured machine.
4. The session appears on the dashboard within a second.

## Reaching the Server from Remote Machines

Pick whichever fits your threat model and infrastructure:

- **LAN only** — direct IP or `.local` hostname. Fine for a home setup where every client is on the same network.
- **Tailscale / WireGuard** — zero-config mesh VPN, every client reaches the server on a private address. Nothing exposed to the internet.
- **Cloudflare Tunnel** — outbound connection from the server to Cloudflare's edge. Works through CGNAT, no port forwarding. HTTPS terminated at the edge.
- **VPS with a reverse proxy** — nginx or Caddy in front of `127.0.0.1:8400`, with Let's Encrypt. Straightforward if the server has a public IP.

Regardless of network path, keep `HYDRA_BIND_HOST=127.0.0.1` and terminate TLS *somewhere* — the bearer token alone isn't a substitute for transport encryption.

## CLI Reference

```
hydra sync [--pull|--push|--dry-run] [--cwd PATH]
                      # Reconcile local memory dir with server. Bidirectional
                      # by default; flags restrict direction. Conflicts are
                      # flagged, not merged.
hydra memory list | get ID | create ... | update ID ... | delete ID
hydra project list | get SLUG | create --slug --path | update SLUG | delete
hydra config get-claude-md | put-claude-md FILE
```

Global auth via `HYDRA_AUTH_TOKEN` and `HYDRA_URL` env vars.

## Dashboard

Two pages, both behind the bearer token:

- **`/` — Sessions.** Live grid of session cards (active / waiting / idle / ended), grouped by status and updated via SSE.
  - **Archive** ended or idle sessions to hide them from the main view (per-card `×` or the bulk "Archive ended/idle" button). Archived sessions stay in the DB under a collapsible **Archive** section and auto-unarchive if they receive a new hook event.
  - **Filter Recent Events** by selected sessions via the chip row next to the Recent Events header. Only active / waiting / idle sessions appear as chips; selection is session-local and resets on reload.
- **`/memory` — Memory dashboard.** Browse the cross-machine memory store.
  - Global memories (`user` / `feedback`) listed separately from project-scoped memories, grouped per project with expandable rows.
  - Click a memory name to expand its body inline.
  - Per memory: **Delete**, **Copy to another project** (with overwrite confirmation), **Move to global** (pick new `user` / `feedback` type). Read-only bodies — edits still go through `hydra sync`.
  - Stats header: project count and memory count, split global vs project-scoped.

## Session State Machine

| Event | Status |
|-------|--------|
| SessionStart / UserPromptSubmit / PostToolUse | `active` |
| Stop | `idle` |
| Notification (idle_prompt) | `waiting_input` |
| SessionEnd | `ended` |

Session cards on the dashboard are grouped by status and updated live via SSE.

## Development

```bash
pip install -r requirements-dev.txt
python -m pytest tests/ -v
ruff check server/ tests/ client/
pyright server/ tests/ client/
```

All three must pass before committing.

## Tech Stack

| Component | Choice |
|-----------|--------|
| Server | Python 3.13, FastAPI, aiosqlite |
| Database | SQLite (WAL mode, partial unique indexes for memory scoping) |
| Live updates | Server-Sent Events |
| Frontend | Vanilla HTML/JS, Pico CSS (no build step) |
| Auth | Bearer token (fail-closed) |
| Client CLI | Python stdlib (`urllib`) |

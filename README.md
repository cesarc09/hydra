# Hydra — Claude Code Control Plane

An observation layer for all your Claude Code instances, across every machine.

## Why

If you use Claude Code on multiple machines — a Windows desktop, WSL, SSH servers — each instance is isolated. You can't see what's running where, which sessions are waiting for input, or what files just changed on another machine.

Hydra gives you a single dashboard that watches all of them in real time. Combined with a shared config repo (for permissions, rules, and hooks), it forms a unified control plane: one place to observe, one place to configure.

```
┌─────────────────────────────────────────────────┐
│              Hydra Server                        │
│  ┌──────────────┐  ┌──────┐  ┌──────────────┐  │
│  │ Hook API     │  │  DB  │  │ Config Sync  │  │
│  └──────┬───────┘  └──────┘  └──────────────┘  │
│         │                                       │
│  ┌──────┴───────┐                               │
│  │ Dashboard    │ ← browser (any device)        │
│  │ (live SSE)   │                               │
│  └──────────────┘                               │
└─────────────────────────────────────────────────┘
          ▲ HTTP hooks
          │
    ┌─────┴──────────────────────────────┐
    │         │           │        │     │
  Win/VS    WSL        SSH-1    SSH-2  SSH-3
```

## How It Works

1. Each Claude Code instance has HTTP hooks in `settings.json` that POST events to the Hydra server
2. The server tracks session state and stores events in SQLite
3. The dashboard shows live session cards via Server-Sent Events
4. A shared config repo keeps rules, permissions, and hooks consistent across machines

### Session States

| Event | Sets state to |
|-------|--------------|
| SessionStart / UserPromptSubmit / PostToolUse | `active` |
| Stop | `idle` |
| Notification (idle_prompt) | `waiting_input` |
| SessionEnd | `ended` |

## Setup

### Server

Run on any always-on machine (Raspberry Pi, home server, VPS):

```bash
git clone <repo-url> ~/hydra && cd ~/hydra
python3 -m venv venv && source venv/bin/activate
pip install -r requirements.txt

cp .env.example .env
# Edit .env: set HYDRA_AUTH_TOKEN to a random string

uvicorn server.app:app --host 0.0.0.0 --port 8400
```

For persistent deployment: `sudo cp deploy/hydra.service /etc/systemd/system/` and enable it.

### Each Claude Code Instance

Set these environment variables:

```bash
export HYDRA_INSTANCE_ID="windows-vscode"  # unique per machine
export HYDRA_AUTH_TOKEN="<same token as server>"
```

Then configure hooks in `~/.claude/settings.json` to POST events to `http://<hydra-server>:8400/api/hooks/event`. See the config repo for a working example.

### Verify

1. Start the Hydra server
2. Open `http://<server>:8400` in a browser
3. Start a Claude Code session on any configured machine
4. The session appears on the dashboard

## Config Sync

Hydra can pull a shared config repo on demand (via the dashboard's "Sync Now" button). Each instance can also pull on session start via a command hook. This keeps `CLAUDE.md`, `settings.json`, and permission rules in sync across machines.

The config repo is independent of Hydra — it works on its own as a git-synced dotfiles approach. Hydra simply adds visibility into sync status.

## Network

All instances must be able to reach the Hydra server. Options:
- **LAN:** Direct IP or hostname (e.g., `pi.local:8400`)
- **Tailscale:** Zero-config mesh VPN across machines
- **Cloudflare Tunnel:** Public HTTPS without port forwarding

Do not expose the server directly to the internet with just a bearer token.

## Development

```bash
pip install -r requirements-dev.txt

# Run tests
python -m pytest tests/ -v

# Lint + type check
ruff check server/ tests/
pyright server/ tests/
```

## Tech Stack

| Component | Choice |
|-----------|--------|
| Server | Python 3.13, FastAPI |
| Database | SQLite (WAL mode) |
| Live updates | Server-Sent Events |
| Frontend | Vanilla HTML/JS, Pico CSS |
| Auth | Bearer token |

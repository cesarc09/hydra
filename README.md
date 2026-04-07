# Hydra — Claude Code Control Plane

A central dashboard that monitors and orchestrates multiple Claude Code instances across machines.

## Architecture

```
                    Raspberry Pi (or any server)
┌─────────────────────────────────────────────────────┐
│  Hydra Server (FastAPI, port 8400)                  │
│  ┌──────────────┐  ┌──────────┐  ┌───────────────┐  │
│  │ Hook API      │  │ SQLite   │  │ Config repo   │  │
│  │ (ingestion)  │  │ (state)  │  │ (git sync)    │  │
│  └──────┬───────┘  └──────────┘  └───────────────┘  │
│         │                                            │
│  ┌──────┴───────┐                                    │
│  │ Dashboard UI │ ← browser (any device)             │
│  │ (SSE live)   │                                    │
│  └──────────────┘                                    │
└─────────────────────────────────────────────────────┘
          ▲ HTTP hooks (POST events)
          │
    ┌─────┴──────────────────────────────────┐
    │          │            │          │      │
  Win/VS    WSL         SSH-1      SSH-2   SSH-3
  Claude    Claude      Claude     Claude  Claude
  Code      Code        Code       Code    Code
```

**How it works:**
1. Each Claude Code instance has HTTP hooks (configured in `~/.claude/settings.json`) that POST events to the Hydra server
2. The server tracks session state (active/idle/waiting for input/ended) and stores events in SQLite
3. The dashboard shows all sessions as cards with live updates via Server-Sent Events (SSE)
4. To interact with a session, click "Open Remote Control" which links to claude.ai/code

## Tech Stack

| Component | Choice |
|-----------|--------|
| Server | FastAPI (Python) |
| Database | SQLite (WAL mode) |
| Live updates | Server-Sent Events |
| Frontend | Vanilla HTML/JS + Pico CSS |
| Auth | Bearer token (shared secret) |
| Process manager | systemd |

## Hook Events Tracked

| Event | What it means |
|-------|---------------|
| `SessionStart` | New session opened (or resumed) |
| `SessionEnd` | Session closed |
| `UserPromptSubmit` | User sent a message |
| `PostToolUse` (Write/Edit/Bash) | Claude used a tool |
| `Stop` | Claude finished responding |
| `Notification` (idle_prompt) | Session waiting for user input |
| `SubagentStart` / `SubagentStop` | Subagent spawned/finished |

## Session State Machine

```
SessionStart / UserPromptSubmit / PostToolUse / SubagentStart
  → active

Stop
  → idle

Notification (idle_prompt)
  → waiting_input

SessionEnd
  → ended
```

## API

### Hook ingestion (Claude Code → Hydra)

```
POST /api/hooks/event
Headers: Authorization: Bearer <token>, X-Instance-Id: <machine-name>
Body: JSON from Claude Code hook system
```

### Dashboard queries (browser → Hydra)

```
GET  /api/sessions                      # All sessions with state
GET  /api/sessions/{session_id}/events  # Event history
GET  /api/events/stream                 # SSE live stream
GET  /api/memory/status                 # Config sync status
POST /api/memory/sync                   # Trigger config sync
```

## Setup

### 1. Server (Raspberry Pi or any Linux machine)

```bash
git clone <repo-url> ~/hydra
cd ~/hydra

python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt

cp .env.example .env
# Edit .env:
#   HYDRA_AUTH_TOKEN=<random-string>
#   HYDRA_CONFIG_REPO=/path/to/claude-config   (optional, for sync)

# Run
uvicorn server.app:app --host 0.0.0.0 --port 8400
```

For persistent deployment, install the systemd service:

```bash
sudo cp deploy/hydra.service /etc/systemd/system/
# Edit the service file paths if needed
sudo systemctl enable hydra
sudo systemctl start hydra
```

### 2. Each Claude Code instance

Set environment variables in your shell profile (`~/.bashrc` or `~/.zshrc`):

```bash
export HYDRA_INSTANCE_ID="windows-vscode"   # unique per machine
export HYDRA_AUTH_TOKEN="<same-token-as-server>"
```

Deploy the hook configuration:

```bash
git clone <claude-config-repo> ~/projects/claude-config
cd ~/projects/claude-config
./setup.sh          # Windows (Git Bash)
./setup.sh --link   # Linux / macOS / WSL
```

The hooks are in `settings.json` and point to `http://localhost:8400`. Change the URL to your server's address (e.g., `http://pi.local:8400` or a Tailscale IP).

### 3. Verify

1. Start the Hydra server
2. Open `http://<server>:8400` in your browser
3. Start a Claude Code session on any configured machine
4. The session should appear on the dashboard

## Config Sync

On each session start, a command hook pulls the latest `claude-config` repo and runs `setup.sh`. This keeps `CLAUDE.md` (personal rules) and `settings.json` (hooks) consistent across machines.

The dashboard also has a "Sync Now" button that pulls the latest config on the server side.

## Project Structure

```
hydra/
├── server/
│   ├── app.py                  # FastAPI entry point
│   ├── config.py               # Settings from env vars
│   ├── db.py                   # SQLite connection
│   ├── models.py               # Pydantic models
│   ├── routers/
│   │   ├── hooks.py            # POST /api/hooks/event
│   │   ├── sessions.py         # GET /api/sessions + SSE
│   │   └── memory.py           # Config sync endpoints
│   └── services/
│       ├── session_manager.py  # State machine + SSE broadcast
│       └── memory_sync.py      # Git-based config sync
├── static/
│   ├── index.html              # Dashboard
│   ├── app.js                  # SSE client + rendering
│   └── style.css               # Dark theme
├── scripts/
│   └── simulate_sessions.sh    # Test simulation
├── deploy/
│   └── hydra.service           # systemd unit
├── schema.sql                  # SQLite schema
└── requirements.txt
```

## Testing

Run the simulation script to populate the dashboard with test data:

```bash
./scripts/simulate_sessions.sh http://localhost:8400
```

## Network Notes

- All machines must be able to reach the Hydra server on port 8400
- For remote access, use Tailscale or a VPN (do not expose directly to the internet with just a bearer token)
- The URL in `settings.json` hooks must be updated to match the server's address

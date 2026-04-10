# Implementation Plan

Planning document for Hydra features. Focus: shared abstractions to avoid bloat.

## Current Architecture (baseline)

```
Hook POST → handle_event() → DB write + SSE broadcast → Dashboard JS
```

Key files:
- `session_manager.py` — monolithic event handler, state machine, SSE broadcast
- `events` table — stores event name, tool name, summary (lossy)
- `sessions` table — computed state per session
- `app.js` — single file, renders everything, one SSE subscription
- Config spread across `.env`, `editors.json`

---

## Shared Abstractions

Six abstractions that multiple features depend on. Build these incrementally — each feature adds what it needs, not upfront.

### 1. Event listeners (server-side)

**Problem:** `handle_event()` currently does everything — state updates, DB writes, SSE broadcast. Every new feature (notifications, cost tracking, task board, pipelines) would add more code to this function.

**Solution:** Split `handle_event()` into a pipeline. The core handler does DB + state. Additional listeners register for specific events.

```python
# session_manager.py
_listeners: list[Callable] = []

def on_event(fn):
    """Register a function to be called after every event is processed."""
    _listeners.append(fn)
    return fn

async def handle_event(event, instance_id):
    # ... existing DB + state logic ...
    # ... SSE broadcast ...
    for listener in _listeners:
        await listener(event, instance_id, context)
```

**Used by:** Notifications, cost tracking, task board, pipeline chains, plugins, auto-cleanup.

### 2. Rich event storage

**Problem:** The `events` table only stores `tool_name` and a truncated `tool_input_summary`. Diff viewer needs `old_string`/`new_string`. Cost tracking needs token data. Task board needs task descriptions.

**Solution:** Add a `payload` JSON column to the `events` table that stores the full (or relevant subset of) event data.

```sql
ALTER TABLE events ADD COLUMN payload TEXT;  -- JSON blob
```

Keep the existing `tool_name`/`tool_input_summary` columns for fast queries. Use `payload` for detailed views that need the full data.

**Used by:** Diff viewer, cost tracking, task board, session history.

### 3. Notification channels

**Problem:** Multiple notification backends (browser push, Discord, Slack, ntfy.sh) with configurable rules per event type.

**Solution:** A `Notifier` base with `send(title, body, url)`. Each backend implements it. Rules are configured in `hydra.toml`.

```python
# server/services/notifier.py
class Notifier:
    async def send(self, title: str, body: str, url: str = ""): ...

class NtfyNotifier(Notifier): ...
class DiscordWebhookNotifier(Notifier): ...
class SlackWebhookNotifier(Notifier): ...
```

Rules are event listener functions that check conditions and call the notifier.

```toml
# hydra.toml
[notifications]
backend = "ntfy"            # or "discord", "slack"
url = "https://ntfy.sh/hydra-alerts"

[notifications.rules]
waiting_input = true
session_end = false
error = true
```

**Used by:** Push notifications, configurable rules, plugin system.

### 4. Unified config file

**Problem:** Config is scattered: `.env` for secrets, `editors.json` for editors. More features mean more config files (notification rules, instance metadata, user prefs).

**Solution:** One `hydra.toml` for all non-secret configuration. `.env` stays for secrets only.

```toml
# hydra.toml

[editors.default]
editor = "vscode"
type = "local"

[editors.instances.windows-vscode]
editor = "vscode"
type = "local"

[editors.instances.wsl-main]
editor = "vscode"
type = "wsl"
distro = "Ubuntu"

[notifications]
backend = "ntfy"
url = "https://ntfy.sh/hydra-alerts"

[cleanup]
max_event_age_days = 7

[server]
host = "0.0.0.0"
port = 8400
```

This replaces `editors.json` and absorbs future config. Loaded once at startup, served to the dashboard via `GET /api/config` (non-secret subset).

**Used by:** Editor links, notifications, auto-cleanup, instance registry, zero-config CLI.

**Migration from editors.json:** The `/api/editors` endpoint reads from hydra.toml instead. Old `editors.json` support can be dropped or kept as fallback temporarily.

### 5. Dashboard panels (frontend)

**Problem:** `app.js` is a single file that will grow with every new UI feature (cost charts, task board, diff viewer, history).

**Solution:** Split into modules. Each panel is a JS file that exports `init()` and `render()`. Main app imports and composes them.

```
static/
├── app.js              # Init, SSE connection, panel orchestration
├── panels/
│   ├── sessions.js     # Session cards (existing code extracted)
│   ├── events.js       # Event log (existing code extracted)
│   ├── sync.js         # Config sync (existing code extracted)
│   ├── costs.js        # Cost tracking (new)
│   ├── tasks.js        # Task board (new)
│   └── diffs.js        # Diff viewer (new)
└── lib/
    ├── sse.js          # SSE connection + typed event dispatch
    ├── editors.js      # Editor URI generation (existing code extracted)
    └── helpers.js      # escHtml, truncate, timeAgo
```

No build step — use ES modules (`<script type="module">`). Works natively in all modern browsers.

**Used by:** Every UI feature.

### 6. Instance registry

**Problem:** Instances are implicitly created when their first event arrives. There's no place to store per-instance metadata (editor config, notification prefs, display name, group).

**Solution:** An `instances` table + config section.

```sql
CREATE TABLE IF NOT EXISTS instances (
    instance_id TEXT PRIMARY KEY,
    display_name TEXT,
    first_seen TEXT NOT NULL,
    last_seen TEXT NOT NULL
);
```

Static config (editor, notifications) lives in `hydra.toml`. Dynamic state (first_seen, last_seen, active session count) lives in the DB. `handle_event()` upserts the instance on every event.

**Used by:** Editor links, notifications, multi-user, orchestration, instance health.

---

## Feature Implementation Plans

### Editor Integration

#### Inline diff viewer
**Depends on:** Rich event storage (abstraction 2), dashboard panels (5)

**Server:**
- Store `old_string` and `new_string` from Edit events in the `payload` column
- New endpoint: `GET /api/sessions/{id}/diffs` — returns all Edit events with their payloads
- Only store diffs for Edit events (Write has no old_string, Bash is not a diff)

**Frontend:**
- New `panels/diffs.js` — renders side-by-side or unified diff
- Use a lightweight JS diff renderer (e.g., inline `<ins>`/`<del>` tags — no library needed for simple diffs)
- Accessible from session card: "View Diffs" link next to files list
- Modal or expandable section, not a separate page

**Hook config change:** Expand `PostToolUse` matcher to capture `Read` if we want before/after context. Actually, Edit already sends `old_string`/`new_string` — no hook change needed, just richer storage.

---

### Notifications

#### Push notifications for waiting_input
**Depends on:** Event listeners (1), notification channels (3), unified config (4)

**Server:**
- Implement `NtfyNotifier` first (simplest — just an HTTP POST to ntfy.sh)
- Register an event listener that checks: if event is `Notification` with `idle_prompt`, call notifier
- Notifier reads config from `hydra.toml`

**Frontend:**
- Optional: Browser Notification API as a second channel (no server needed, just JS)
- `Notification.requestPermission()` on first visit, then show notifications for `waiting_input` SSE events
- This is zero-config — works immediately without ntfy.sh setup

**Recommendation:** Ship browser notifications first (pure JS, no server work). Add ntfy.sh/Discord later for mobile.

#### Configurable notification rules
**Depends on:** Notification channels (3), unified config (4)

**Server:**
- Rules in `hydra.toml` under `[notifications.rules]`
- Each rule maps event_name (+ optional matcher) → notify yes/no
- Event listener checks rules before calling notifier

**Frontend:**
- Settings panel in dashboard to toggle rules (writes to `hydra.toml` via API)
- Or: keep it config-file-only for simplicity

---

### Cost & Usage Tracking

#### Token/cost dashboard
**Depends on:** Rich event storage (2), event listeners (1), dashboard panels (5)

**Hook config change needed:** Add a `Stop` hook that captures cost data. The `Stop` event from Claude Code includes cost metadata but we need to verify exactly what fields arrive. May need a new hook event or richer `Stop` payload.

**Server:**
- New table: `session_costs`
  ```sql
  CREATE TABLE session_costs (
      session_id TEXT PRIMARY KEY,
      total_cost_usd REAL DEFAULT 0,
      input_tokens INTEGER DEFAULT 0,
      output_tokens INTEGER DEFAULT 0,
      updated_at TEXT NOT NULL,
      FOREIGN KEY (session_id) REFERENCES sessions(session_id)
  );
  ```
- Event listener on `Stop`: extract cost fields, upsert into `session_costs`
- New endpoint: `GET /api/costs?period=day|week|all` — aggregated cost data

**Frontend:**
- New `panels/costs.js` — summary cards (total today, total this week, per-instance breakdown)
- For charts: use a tiny library (e.g., `<canvas>` with simple bar chart) or pure CSS bars. No Chart.js — too heavy for a Pi.

#### Rate limit monitoring
**Same infrastructure as cost tracking.** Rate limit data may come in the same payload or via status line polling. If hooks don't include rate limits, this feature requires a periodic reporting mechanism from each instance (e.g., a cron hook that POSTs status line data).

---

### Session Intelligence

#### Task board
**Depends on:** Rich event storage (2), event listeners (1), dashboard panels (5)

**Hook config change:** Add `TaskCreated` and `TaskCompleted` hooks to `settings.json`.

**Server:**
- New table:
  ```sql
  CREATE TABLE tasks (
      task_id TEXT PRIMARY KEY,
      session_id TEXT NOT NULL,
      instance_id TEXT NOT NULL,
      subject TEXT,
      status TEXT DEFAULT 'pending',
      created_at TEXT NOT NULL,
      completed_at TEXT,
      FOREIGN KEY (session_id) REFERENCES sessions(session_id)
  );
  ```
- Event listener on `TaskCreated`/`TaskCompleted`: insert/update tasks
- Endpoint: `GET /api/tasks?status=pending|completed|all`

**Frontend:**
- New `panels/tasks.js` — grouped by session, shows subject + status
- Simple list, not a full kanban (kanban is overkill for monitoring)

#### Transcript viewer
**Depends on:** Dashboard panels (5)

**This is the trickiest feature.** Transcripts are local files on each instance machine (`transcript_path` in hook events). The Hydra server can't read them directly unless it has filesystem access.

**Options:**
1. **Instance pushes transcript chunks** — Add a `PostCompact` or `Stop` hook that reads the transcript and POSTs it to Hydra. Heavy, but works cross-network.
2. **Hydra fetches via SSH** — Server SSHes into instance machines to read the file. Complex, requires SSH keys.
3. **Shared filesystem** — Only works if instance and server share NFS/Samba. Not general.
4. **Instance serves it locally** — Each instance runs a tiny HTTP server (MCP?) that serves its transcript. Hydra dashboard links to it.

**Recommendation:** Option 1, but only on demand. Add a button "Fetch Transcript" that triggers a one-time fetch via a special command hook or channel message. Don't auto-sync all transcripts — too much data.

**Defer this feature** — it requires solving the cross-machine file access problem which is not worth it until the simpler features are solid.

#### Session history
**Depends on:** Dashboard panels (5), auto-cleanup (Polish)

**Server:**
- Already have data — `sessions` table has ended sessions, `events` table has history
- New endpoint: `GET /api/sessions/history?days=7&instance=all` — paginated ended sessions
- Add `ended_at` column to sessions table (currently only have `last_event_at`)

**Frontend:**
- New `panels/history.js` — table/list of ended sessions with search/filter
- Click a session → expands to show its event timeline

---

### Orchestration

#### Dispatch tasks
**Depends on:** Event listeners (1), instance registry (6)

**This requires bidirectional communication** — dashboard → instance. Current architecture is one-way (instance → Hydra).

**Options:**
1. **Claude Code Channels (MCP)** — Each instance runs an MCP channel server that listens for commands from Hydra. Most "correct" but requires MCP setup on every instance.
2. **Polling** — Instances periodically check `GET /api/dispatch/{instance_id}` for pending tasks. Simple but adds latency.
3. **Remote Control** — Dashboard links to claude.ai/code where user manually inputs the task. Already works — this is what "Open Remote Control" does.

**Recommendation:** Start with option 2 (polling). Add a `SessionStart` command hook that checks for pending dispatch:

```bash
curl -s http://pi.local:8400/api/dispatch/$HYDRA_INSTANCE_ID | ...
```

Server stores pending tasks. Instance picks them up on next session start or via a periodic check. Not real-time, but simple and works.

**Defer real-time dispatch** until MCP Channels are more mature.

#### Scheduled sweeps
**Depends on:** Dispatch tasks

Build on dispatch. Hydra server has a cron-like scheduler (use `apscheduler` or a simple `asyncio` loop) that creates dispatch entries on a schedule.

```toml
# hydra.toml
[[schedules]]
cron = "0 9 * * *"          # Every day at 9am
instance = "wsl-main"
task = "cd ~/projects/pcb && git pull && python -m pytest"
```

**Implementation:** Lightweight — just a timer that inserts rows into a `dispatch` table.

#### Pipeline chains
**Depends on:** Event listeners (1), dispatch tasks

Event listener that watches for `SessionEnd` on a specific instance/session, then creates a dispatch entry for the next step.

```toml
# hydra.toml
[[pipelines]]
trigger = { instance = "ssh-devbox", event = "SessionEnd" }
dispatch = { instance = "wsl-main", task = "run integration tests against staging" }
```

**This is just a specialized event listener + dispatch.** No new abstraction needed.

---

### Cross-Host Access

#### Cloudflare Tunnel / Tailscale
**No code changes.** Just deployment documentation.

- Cloudflare: `cloudflared tunnel --url http://localhost:8400`
- Tailscale: `tailscale funnel 8400`

Add to `deploy/` as scripts or README section.

#### PWA support
**Depends on:** Dashboard panels (5)

- Add `manifest.json` (app name, icons, theme color)
- Add service worker for offline shell (serve cached HTML/CSS/JS, fetch API data when online)
- This makes the dashboard installable on phone + enables browser push notifications

**Files:** `static/manifest.json`, `static/sw.js`, icon files. Small lift.

---

### Open Source & Extensibility

#### Zero-config CLI
**Depends on:** Unified config (4)

A `hydra` CLI entry point that handles setup:

```bash
hydra init              # Generate hydra.toml, .env, editors.json
hydra serve             # Start the server
hydra hook-config       # Print settings.json hook block for copy-paste
```

**Implementation:** A `cli.py` using `click` or `argparse`. `hydra init` generates config from templates with interactive prompts. `hydra serve` wraps uvicorn.

Add to `requirements.txt`, add `[project.scripts]` entry in `pyproject.toml`.

#### Plugin system
**Depends on:** Event listeners (1), unified config (4)

Plugins are Python files in a `plugins/` directory. Each exports an `on_event` function. Hydra auto-discovers and registers them as event listeners.

```python
# plugins/my_plugin.py
async def on_event(event, instance_id, context):
    if event.hook_event_name == "PostToolUse" and event.tool_name == "Bash":
        if "rm -rf" in (event.tool_input or {}).get("command", ""):
            await context.notify("Dangerous command detected!")
```

**Discovery:** `importlib` scan of `plugins/*.py` at startup. No package system — just drop a .py file.

#### Multi-user support
**Depends on:** Instance registry (6), unified config (4)

- `users` table with API keys
- Each instance is assigned to a user
- Dashboard login (API key in header or cookie)
- Middleware checks API key on all endpoints

**Defer until open-source launch.** Solo use doesn't need this.

---

### Polish

#### Auto-cleanup
**Depends on:** Unified config (4)

Background task on server startup that periodically deletes old events:

```python
async def cleanup_loop():
    while True:
        await asyncio.sleep(3600)  # Every hour
        max_age = config.cleanup.max_event_age_days
        cutoff = (datetime.now() - timedelta(days=max_age)).isoformat()
        await db.execute("DELETE FROM events WHERE received_at < ?", (cutoff,))
```

Small. Add to `lifespan()` in `app.py`.

#### Event rate limiting / debounce
**Depends on:** Event listeners (1)

In `handle_event()`, skip DB write + broadcast if the same `(session_id, event_name, tool_name)` was seen less than N seconds ago. Simple in-memory dict with timestamps.

```python
_last_seen: dict[tuple, float] = {}
DEBOUNCE_SECONDS = 2

def should_debounce(event) -> bool:
    key = (event.session_id, event.hook_event_name, event.tool_name)
    now = time.time()
    if key in _last_seen and (now - _last_seen[key]) < DEBOUNCE_SECONDS:
        return True
    _last_seen[key] = now
    return False
```

#### Mobile-responsive layout
CSS-only. Adjust grid breakpoints, card sizing, event log layout for narrow screens. Test at 375px width.

#### Favicon with status indicator
Dynamically update `<link rel="icon">` based on session states. If any session is `waiting_input`, show a yellow dot. If all idle, show gray. Pure JS, no server change.

---

## Abstraction → Feature Matrix

| Feature | Event listeners | Rich storage | Notifiers | Config | Panels | Instance registry |
|---------|:-:|:-:|:-:|:-:|:-:|:-:|
| Diff viewer | | x | | | x | |
| Push notifications | x | | x | x | | |
| Notification rules | x | | x | x | | |
| Cost tracking | x | x | | x | x | |
| Rate limits | x | x | | | x | |
| Task board | x | x | | | x | |
| Transcript viewer | | | | | x | |
| Session history | | | | | x | |
| Dispatch tasks | x | | | x | | x |
| Scheduled sweeps | x | | | x | | x |
| Pipeline chains | x | | | x | | |
| PWA | | | | | x | |
| Zero-config CLI | | | | x | | |
| Plugin system | x | | | x | | |
| Multi-user | | | | x | | x |
| Auto-cleanup | | | | x | | |
| Debounce | x | | | | | |
| Favicon status | | | | | x | |

## Suggested build order

Based on dependency chains and value:

1. **Unified config** (`hydra.toml`) — unblocks notifications, cleanup, CLI
2. **Rich event storage** (`payload` column) — unblocks diffs, costs, task board
3. **Event listeners** — unblocks everything server-side
4. **Dashboard panels** (extract existing code into modules) — unblocks all new UI
5. **Browser push notifications** — high value, pure JS, no server deps
6. **Auto-cleanup** — small, prevents DB growth
7. **Cost tracking** — if hook data is available
8. **Task board** — after adding TaskCreated/TaskCompleted hooks
9. **Diff viewer** — after rich storage
10. **Session history** — after panels refactor
11. **PWA** — after notifications
12. **Zero-config CLI** — before open-source launch
13. **Dispatch/scheduling** — after polling mechanism is proven
14. **Plugin system** — after event listeners are stable
15. **Multi-user** — last, only needed for teams

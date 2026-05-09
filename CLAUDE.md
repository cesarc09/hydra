# Hydra — Claude Code Control Plane

One server that holds memories, CLAUDE.md, and the project registry, and watches every live session. Clients sync context at SessionStart/Stop; every tool call is reported over HTTP hooks. Same bearer token, same server, two loops that don't depend on each other.

## Tech Stack

- **Server:** Python 3.13, FastAPI, aiosqlite (SQLite in WAL mode, partial unique indexes)
- **Frontend:** Vanilla HTML/JS, Pico CSS (dark theme), Server-Sent Events
- **Client CLI:** `client/hydra_cli/` — stdlib `urllib`, installed via `pip install -e client/`. Hooks invoke it as `python -m hydra_cli ...` (not the `hydra` console shim) so it works without depending on a venv-bound entry point.
- **Auth:** Bearer token (fail-closed unless `HYDRA_ALLOW_NO_AUTH=1`); `secrets.compare_digest` for compare

## Running Locally

```bash
source venv/bin/activate
pip install -r requirements.txt
export HYDRA_AUTH_TOKEN=$(openssl rand -hex 32)   # or HYDRA_ALLOW_NO_AUTH=1 for dev
uvicorn server.app:app --host 127.0.0.1 --port 8400
```

The server binds to loopback by default (`HYDRA_BIND_HOST=127.0.0.1`). Reverse-proxy or VPN handles external access — see README for network options.

## Running Tests

```bash
pip install -r requirements-dev.txt
python -m pytest tests/ -v
```

## Linting & Type Checking

```bash
ruff check server/ tests/ client/
pyright server/ tests/ client/
```

Both must pass clean. Fix issues before committing.

## Project Layout

```
server/
  app.py              — FastAPI entry, lifespan startup guard, body-size middleware, CORS,
                        /memory route -> static/memory.html
  auth.py             — require_auth + require_auth_sse (accepts ?token= for EventSource)
  config.py           — env vars (AUTH_TOKEN, BIND_HOST, PUBLIC_ORIGIN, ALLOW_NO_AUTH, DB_PATH)
  db.py               — SQLite singleton + idempotent migrations (project_slug, archived_at)
  models.py           — Pydantic models (HookEvent, MemoryCreate/Item, ProjectItem, ...)
  routers/
    hooks.py          — POST /api/hooks/event
    sessions.py       — GET sessions/events, SSE stream (require_auth_sse), editor config,
                        POST /sessions/{id}/archive|unarchive + /sessions/archive-ended
    config.py         — GET/PUT /api/config/claude-md
    memory.py         — CRUD /api/memory with upsert on (name, project_slug) + filtered GET
    projects.py       — CRUD /api/projects + auto-register + confirm endpoints
  services/
    session_manager.py — State machine, bounded SSE broadcast, DB writes, archive ops
    slug.py            — Slug normalization + stoplist for auto-registered projects
client/
  hydra_cli/
    __main__.py            — CLI dispatch (memory, project, config, sync,
                              capture-remote-url, apply-settings)
    api.py                 — Stdlib urllib + bearer token + User-Agent header
    sync.py                — Bidirectional memory sync, frontmatter parse, MEMORY.md regen
    remote.py              — Stop-hook entry: scans transcript for bridge_status, PUTs URL
    apply_settings.py      — 3-way merge for ~/.claude/settings.json
                              (hydra hooks → template defaults → user overrides)
  settings.json            — Hydra hooks template (__HYDRA_URL__ / __HYDRA_REPO_PATH__ placeholders)
  settings.user.template.json — User-pref defaults (effortLevel, attribution, statusLine, …);
                                scaffolded to ~/.claude/settings.user.json on first run
  statusline.sh            — Default status-line script; scaffolded to ~/.claude/ (only if absent)
  setup.sh                 — pip-installs hydra_cli, then runs apply-settings to render
                              ~/.claude/settings.json from the three layers
static/
  index.html, app.js   — Sessions dashboard (/); archive, Recent Events chip filter
  memory.html, memory.js — Memory dashboard (/memory); browse, delete, copy, move,
                            pending-review queue for auto-registered projects
  utils.js             — Shared apiFetch + token handling, escHtml (loaded before page JS)
  style.css            — Shared styles (no build step, bearer-token prompt)
tests/
  conftest.py         — Per-test isolated SQLite + test client; sets ALLOW_NO_AUTH=True
  test_hooks.py       — Hook ingestion + state machine
  test_config.py      — CLAUDE.md endpoint
  test_memory.py      — Memory CRUD + upsert + project scoping + filtered list
  test_projects.py    — Project CRUD
  test_sync.py        — CLI sync (pull, push, bidirectional, conflict detection)
  test_session_archive.py — Archive endpoints + auto-unarchive on new activity
  test_startup.py     — Fail-closed startup guard
schema.sql            — DDL; sessions.archived_at, memories has project_slug FK + partial unique indexes
```

## Key Patterns

- **Session state machine:** SessionStart/UserPromptSubmit/PostToolUse → `active`, Stop → `idle`, Notification(idle_prompt) → `waiting_input`, SessionEnd → `ended`
- **Session archive:** only `ended` / `idle` can be archived. SessionStart / UserPromptSubmit / PostToolUse clear `archived_at` — archived sessions auto-surface when they wake up. `Stop` alone does NOT unarchive.
- **Dashboard pages:** `/` (sessions) and `/memory` (memory) are separate HTML/JS entry points. Shared helpers (`apiFetch`, `ensureToken`, `escHtml`) live in `static/utils.js`; both pages load it before their own script. No bundler.
- **Memory scope:** type=user/feedback → global (project_slug=NULL); type=project/reference → pinned to cwd's registered project. `hydra sync` derives scope from type automatically.
- **Upsert semantics:** `POST /api/memory` upserts on `(name, project_slug)`. Partial unique indexes make NULL (global) names distinct from project-pinned names.
- **SSE broadcast:** `session_manager._subscribers` is a list of `asyncio.Queue(maxsize=1000)`. Slow consumers are dropped on `QueueFull` rather than blocking the broadcast.
- **Auth:** Fail-closed when `HYDRA_AUTH_TOKEN` is empty unless `HYDRA_ALLOW_NO_AUTH=1`. `require_auth` reads the `Authorization` header; `require_auth_sse` also accepts `?token=` (EventSource can't set headers).
- **Body size limits:** Per-path in `server/app.py` (64KB hooks, 1MB CLAUDE.md, 256KB default); 413 on overrun.
- **DB access:** `get_db()` is a module-level singleton. Tests patch `server.db._db` to an isolated connection via the `client` fixture.
- **CLI sync:** `python -m hydra_cli sync --cwd <path>` maps cwd → project slug via `/api/projects` registry, pulls globals + project-pinned memories to `~/.claude/projects/<dir-hash>/memory/`, regenerates `MEMORY.md` as a flat index.
- **Auto-register:** unregistered cwds POST to `/api/projects/auto-register` on SessionStart's pull. Server applies a stoplist (`server/services/slug.py` — `home`, `tmp`, `Downloads`, …) and either creates a new slug, attaches this machine's path to an existing slug, or skips with a reason. Auto-flagged entries (`projects.auto_registered_at`, `project_paths.auto_registered_at`) surface in the `/memory` dashboard's **Pending review** section with Confirm/Delete actions.
- **Editor deep-links:** `editors.json` maps instance_id → editor URI scheme (vscode://, cursor://, wsl, ssh-remote, jetbrains).
- **Remote Control URL capture:** Stop hook runs `python -m hydra_cli capture-remote-url`, which scans `transcript_path` for `{type:"system",subtype:"bridge_status",url:...}` and PUTs the latest URL to `/api/sessions/{id}/remote-control-url`. Empirically only `entrypoint=cli` Claude Code writes this event; `entrypoint=claude-vscode` is silent there, so VS Code users keep using the dashboard's manual-paste input on each session card. Filter on the JSON shape, not substring grep — user/assistant text quoting "bridge_status" can otherwise contaminate the scan.
- **Settings render (3-way merge):** `~/.claude/settings.json` is composed by `python -m hydra_cli apply-settings` (called from `setup.sh`) from three layers, in priority order: (1) `client/settings.json` — Hydra hooks template, (2) `client/settings.user.template.json` — shipped defaults (effortLevel, statusLine, attribution, …), (3) `~/.claude/settings.user.json` — user overrides. Hooks per event concatenate (Hydra first, user appended); other top-level keys: later layers win. The user file is scaffolded as a copy of the template on first run so users see all available knobs. **Deleting** a key in the user file falls back to the template default — that's the sanctioned way to opt out of a default (e.g. `statusLine`) without losing it for everyone else. `client/statusline.sh` is scaffolded to `~/.claude/statusline.sh` (chmod +x) on first run, only if absent.

## Conventions

- Keep it simple. No speculative abstractions — build what the next feature needs.
- No build step for the frontend. ES modules are fine if splitting JS later.
- SQL strings go in Python (no ORM). Wrap long SQL lines at 100 chars.
- Tests use `pytest` + `httpx.AsyncClient` with isolated per-test SQLite databases.
- Run `ruff check server/ tests/ client/` and `pyright server/ tests/ client/` before considering work done.

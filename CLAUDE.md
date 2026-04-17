# Hydra — Claude Code Control Plane

One server that holds memories, CLAUDE.md, and the project registry, and watches every live session. Clients sync context at SessionStart/Stop; every tool call is reported over HTTP hooks. Same bearer token, same server, two loops that don't depend on each other.

## Tech Stack

- **Server:** Python 3.13, FastAPI, aiosqlite (SQLite in WAL mode, partial unique indexes)
- **Frontend:** Vanilla HTML/JS, Pico CSS (dark theme), Server-Sent Events
- **Client CLI:** `client/hydra_cli/` — stdlib `urllib`, installed via `pip install -e client/`
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
  app.py              — FastAPI entry, lifespan startup guard, body-size middleware, CORS
  auth.py             — require_auth + require_auth_sse (accepts ?token= for EventSource)
  config.py           — env vars (AUTH_TOKEN, BIND_HOST, PUBLIC_ORIGIN, ALLOW_NO_AUTH, DB_PATH)
  db.py               — SQLite singleton + idempotent migration (project_slug ALTER)
  models.py           — Pydantic models (HookEvent, MemoryCreate/Item, ProjectItem, ...)
  routers/
    hooks.py          — POST /api/hooks/event
    sessions.py       — GET sessions/events, SSE stream (require_auth_sse), editor config
    config.py         — GET/PUT /api/config/claude-md
    memory.py         — CRUD /api/memory with upsert on (name, project_slug) + filtered GET
    projects.py       — CRUD /api/projects
  services/
    session_manager.py — State machine, bounded SSE broadcast, DB writes
client/
  hydra_cli/
    __main__.py       — CLI dispatch (memory, project, config, sync)
    api.py            — Stdlib urllib + bearer token + User-Agent header
    sync.py           — Bidirectional memory sync, frontmatter parse, MEMORY.md regen
  settings.json       — Claude Code settings template (__HYDRA_URL__ placeholder)
  setup.sh            — Copies settings.json with URL substitution + installs hydra CLI
static/
  index.html, app.js, style.css — Dashboard (no build step, bearer-token prompt)
tests/
  conftest.py         — Per-test isolated SQLite + test client; sets ALLOW_NO_AUTH=True
  test_hooks.py       — Hook ingestion + state machine
  test_config.py      — CLAUDE.md endpoint
  test_memory.py      — Memory CRUD + upsert + project scoping + filtered list
  test_projects.py    — Project CRUD
  test_sync.py        — CLI sync (pull, push, bidirectional, conflict detection)
  test_startup.py     — Fail-closed startup guard
schema.sql            — DDL; memories has project_slug FK + partial unique indexes
```

## Key Patterns

- **Session state machine:** SessionStart/UserPromptSubmit/PostToolUse → `active`, Stop → `idle`, Notification(idle_prompt) → `waiting_input`, SessionEnd → `ended`
- **Memory scope:** type=user/feedback → global (project_slug=NULL); type=project/reference → pinned to cwd's registered project. `hydra sync` derives scope from type automatically.
- **Upsert semantics:** `POST /api/memory` upserts on `(name, project_slug)`. Partial unique indexes make NULL (global) names distinct from project-pinned names.
- **SSE broadcast:** `session_manager._subscribers` is a list of `asyncio.Queue(maxsize=1000)`. Slow consumers are dropped on `QueueFull` rather than blocking the broadcast.
- **Auth:** Fail-closed when `HYDRA_AUTH_TOKEN` is empty unless `HYDRA_ALLOW_NO_AUTH=1`. `require_auth` reads the `Authorization` header; `require_auth_sse` also accepts `?token=` (EventSource can't set headers).
- **Body size limits:** Per-path in `server/app.py` (64KB hooks, 1MB CLAUDE.md, 256KB default); 413 on overrun.
- **DB access:** `get_db()` is a module-level singleton. Tests patch `server.db._db` to an isolated connection via the `client` fixture.
- **CLI sync:** `hydra sync --cwd <path>` maps cwd → project slug via `/api/projects` registry, pulls globals + project-pinned memories to `~/.claude/projects/<dir-hash>/memory/`, regenerates `MEMORY.md` as a flat index.
- **Editor deep-links:** `editors.json` maps instance_id → editor URI scheme (vscode://, cursor://, wsl, ssh-remote, jetbrains).

## Conventions

- Keep it simple. No speculative abstractions — build what the next feature needs.
- No build step for the frontend. ES modules are fine if splitting JS later.
- SQL strings go in Python (no ORM). Wrap long SQL lines at 100 chars.
- Tests use `pytest` + `httpx.AsyncClient` with isolated per-test SQLite databases.
- Run `ruff check server/ tests/ client/` and `pyright server/ tests/ client/` before considering work done.

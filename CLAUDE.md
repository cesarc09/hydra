# Hydra — Claude Code Control Plane

Central dashboard that monitors Claude Code instances across machines via HTTP hooks.

## Tech Stack

- **Server:** Python 3.13, FastAPI, aiosqlite (SQLite in WAL mode)
- **Frontend:** Vanilla HTML/JS, Pico CSS (dark theme), Server-Sent Events
- **Auth:** Bearer token (shared secret via `HYDRA_AUTH_TOKEN` env var)

## Running Locally

```bash
source venv/Scripts/activate   # Windows (Git Bash)
# source venv/bin/activate     # Linux/macOS
pip install -r requirements.txt
uvicorn server.app:app --host 0.0.0.0 --port 8400
```

## Running Tests

```bash
pip install -r requirements-dev.txt
python -m pytest tests/ -v
```

## Linting & Type Checking

```bash
ruff check server/ tests/
pyright server/ tests/
```

Both must pass clean. Fix issues before committing.

## Project Layout

```
server/
  app.py              — FastAPI entry point
  config.py           — env var settings
  db.py               — SQLite connection singleton
  models.py           — Pydantic models
  routers/
    hooks.py          — POST /api/hooks/event (hook ingestion)
    sessions.py       — GET sessions, events, SSE stream, editor config
    memory.py         — Config sync endpoints
  services/
    session_manager.py — State machine, SSE broadcast, DB writes
    memory_sync.py     — Git-based config repo sync
static/
  index.html, app.js, style.css — Dashboard (single-page, no build step)
tests/
  conftest.py         — Fixtures (isolated DB + test client per test)
  test_hooks.py       — Hook ingestion and state machine tests
  test_memory.py      — Config sync endpoint tests
```

## Key Patterns

- **Session state machine:** SessionStart/UserPromptSubmit/PostToolUse → active, Stop → idle, Notification(idle_prompt) → waiting_input, SessionEnd → ended
- **SSE broadcast:** `session_manager._subscribers` list of asyncio.Queue, each SSE client gets a queue
- **DB access:** `get_db()` returns a module-level singleton connection. Tests monkeypatch this in `session_manager` (where it's imported via `from server.db import get_db`)
- **Editor deep-links:** `editors.json` maps instance_id → editor config for vscode:// / cursor:// URIs

## Conventions

- Keep it simple. No speculative abstractions — build what the next feature needs.
- No build step for the frontend. ES modules are fine if splitting JS later.
- SQL strings go in the Python code (no ORM). Wrap long SQL lines at 100 chars.
- Tests use `pytest` + `httpx.AsyncClient` with isolated per-test SQLite databases.
- Run `ruff check` and `pyright` before considering work done.

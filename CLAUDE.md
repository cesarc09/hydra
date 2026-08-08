# Hydra - Claude Code Control Plane

One server that holds memories, CLAUDE.md, and the project registry, and watches every live session. Clients sync context at SessionStart/Stop; every tool call is reported over HTTP hooks. Same bearer token, same server, two loops that don't depend on each other.

## Tech Stack

- **Server:** Python 3.13, FastAPI, aiosqlite (SQLite in WAL mode, `UNIQUE(name)` on memories)
- **Frontend:** Vanilla HTML/JS, Pico CSS (dark theme), Server-Sent Events
- **Client CLI:** `client/hydra_cli/` - stdlib `urllib`, installed via `pip install -e client/`. Hooks invoke it as `python -m hydra_cli ...` (not the `hydra` console shim) so it works without depending on a venv-bound entry point.
- **Auth:** Bearer token (fail-closed unless `HYDRA_ALLOW_NO_AUTH=1`); `secrets.compare_digest` for compare

## Running Locally

```bash
source venv/bin/activate
pip install -r requirements.txt
export HYDRA_AUTH_TOKEN=$(openssl rand -hex 32)   # or HYDRA_ALLOW_NO_AUTH=1 for dev
uvicorn server.app:app --host 127.0.0.1 --port 8400
```

The server binds to loopback by default (`HYDRA_BIND_HOST=127.0.0.1`). Reverse-proxy or VPN handles external access - see README for network options.

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
  app.py              - FastAPI entry, lifespan startup guard, body-size middleware, CORS,
                        /api/health (unauthenticated liveness+DB probe), /memory route
                        -> static/memory.html
  auth.py             - require_auth + require_auth_sse (accepts ?token= for EventSource)
  config.py           - env vars (AUTH_TOKEN, BIND_HOST, PUBLIC_ORIGIN, ALLOW_NO_AUTH, DB_PATH)
  db.py               - SQLite singleton + idempotent migrations (project_slug, archived_at)
  models.py           - Pydantic models (HookEvent, MemoryCreate/Item, ProjectItem, ...)
  routers/
    hooks.py          - POST /api/hooks/event
    sessions.py       - GET sessions/events, SSE stream (require_auth_sse), editor config,
                        POST /sessions/{id}/archive|unarchive + /sessions/archive-ended
    config.py         - GET/PUT /api/config/claude-md (empty-body guard); CRUD
                        /api/config/commands (name-validated slash-command blobs)
    memory.py         - CRUD /api/memory; upsert on name (globally unique; 409 on a
                        cross-scope name without rescope) + filtered GET
    projects.py       - CRUD /api/projects + auto-register + confirm endpoints
  services/
    session_manager.py - State machine, bounded SSE broadcast, DB writes, archive ops
    slug.py            - Slug normalization + stoplist for auto-registered projects
client/
  hydra_cli/
    __main__.py            - CLI dispatch (memory, project, config, commands, hooks, sync,
                              doctor, capture-remote-url, apply-settings)
    api.py                 - Stdlib urllib + bearer token + User-Agent header
    sync.py                - Memory sync, keyed on the server row id (stamped into each
                              mirror file's frontmatter). Frontmatter parse, collision-free
                              filename assignment, tombstones, MEMORY.md regen
    commands.py            - `commands pull`: fetch the server command map, write
                              ~/.claude/commands/<name>.md, state-file-scoped prune
    hooks.py               - `hooks pull`: fetch the server hook map, install
                              ~/.claude/hooks/<name>.<ext>, render the wiring layer
                              ~/.claude/settings.hooks.json, state-file-scoped prune
    remote.py              - Stop-hook entry: scans transcript for bridge_status, PUTs URL
    apply_settings.py      - 4-way merge for ~/.claude/settings.json (hydra hooks →
                              server policy hooks → template defaults → user overrides)
  settings.json            - Hydra hooks template (__HYDRA_URL__ / __HYDRA_REPO_PATH__ placeholders)
  settings.user.template.json - User-pref defaults (effortLevel, attribution, statusLine, …);
                                scaffolded to ~/.claude/settings.user.json on first run
  statusline.sh            - Default status-line script; scaffolded to ~/.claude/ (only if absent)
  commands/                - Authoring home for PUBLIC slash-command sources (*.md):
                              seeded into the server by scripts/publish_commands.sh and
                              pulled to ~/.claude/commands/ via the `commands pull` hook
                              (the server is the single distribution source).
    debug-hydra.md         - /debug-hydra: thin slash - runs `hydra doctor` and has Claude
                              interpret the result (health verdict, anomalies, fixes).
  setup.sh                 - pip-installs hydra_cli, runs `hooks pull` then apply-settings
                              to render ~/.claude/settings.json, scaffolds statusline.sh
scripts/
  publish_commands.sh   - Seed a dir of *.md (default client/commands/) into the server's
                          command store; the source dir is an argument
static/
  index.html, app.js   - Sessions dashboard (/); archive, Recent Events chip filter
  memory.html, memory.js - Memory dashboard (/memory); browse, delete, copy, move,
                            pending-review queue for auto-registered projects
  utils.js             - Shared apiFetch + token handling, escHtml (loaded before page JS)
  style.css            - Shared styles (no build step, bearer-token prompt)
tests/
  conftest.py         - Per-test isolated SQLite + test client; sets ALLOW_NO_AUTH=True
  test_hooks.py       - Hook ingestion + state machine
  test_config.py      - CLAUDE.md endpoint
  test_memory.py      - Memory CRUD + unique-name upsert/409/rescope + unpin + scoping
  test_migrations.py  - Legacy partial-index DB → UNIQUE(name) (twin collapse, rename,
                        idempotency)
  test_projects.py    - Project CRUD
  test_sync.py        - CLI sync (pull, push, bidirectional, conflict detection) + the
                        duplicate-memory regression guards (no resurrection, no revert,
                        tombstones, filename collisions, empty-server safety)
  test_commands.py    - /api/config/commands endpoints (CRUD, name validation, auth)
  test_commands_pull.py - `commands pull` write + state-file-scoped prune
  test_config_hooks.py - /api/config/hooks endpoints (CRUD, name + metadata validation,
                        name ordering, auth). Named to avoid test_hooks.py (ingestion)
  test_hooks_pull.py  - `hooks pull`: install + wire, wire-only-what-is-on-disk, instance
                        and enabled filters, compile-failure retain, empty-server safety
  test_health.py      - /api/health probe (200 + DB ok; reachable without auth)
  test_doctor.py      - `hydra doctor` report (stats aggregation + anomaly checks, mocked api)
  test_session_archive.py - Archive endpoints + auto-unarchive on new activity
  test_startup.py     - Fail-closed startup guard
schema.sql            - DDL; sessions.archived_at, memories project_slug FK + UNIQUE(name)
                        (inline; db._migrate installs it on legacy DBs - see Key Patterns),
                        config_commands (server-distributed slash commands),
                        config_hooks (server-distributed policy hooks: script + wiring)
```

## Key Patterns

- **Session state machine:** SessionStart/UserPromptSubmit/PostToolUse → `active`, Stop → `idle`, Notification(idle_prompt) → `waiting_input`, SessionEnd → `ended`
- **Session archive:** only `ended` / `idle` can be archived. SessionStart / UserPromptSubmit / PostToolUse clear `archived_at` - archived sessions auto-surface when they wake up. `Stop` alone does NOT unarchive.
- **Dashboard pages:** `/` (sessions) and `/memory` (memory) are separate HTML/JS entry points. Shared helpers (`apiFetch`, `ensureToken`, `escHtml`) live in `static/utils.js`; both pages load it before their own script. No bundler.
- **Memory identity = the row id (read this before touching sync).** A memory IS its server row id. Pull stamps `id:` and `updated_at:` into each mirror file's frontmatter; push updates **by id** (`PUT /api/memory/{id}`), so a rename or a re-scope edits the row in place instead of minting a second one. A file whose id 404s is a **tombstone**: delete the mirror file, never re-insert it. Before ids, the mirror *file* was the identity of record and push was upsert-only-by-name, so every server-side delete/rename/re-scope came back as a duplicate row on the next Stop hook (2026-07-11: a `/forget` pass re-scoped 5 memories at 01:01:36; the Stop push re-created 10 deleted globals at 01:02:48). Corollaries that are load-bearing:
  - **Never re-scope by delete + re-create.** A new row means a new id, and every mirror still holding the old id then looks server-deleted. Re-scope with `PUT project_slug` (`hydra memory update <id> --project <slug>` / `--global`, or the dashboard's Move actions).
  - **`updated_at` is a version token, not decoration.** Push skips a file whose recorded `updated_at` no longer matches the server's - the row changed under it (dashboard, `/forget`, another machine) and pushing the stale mirror would silently revert that change. This is why `_now()` in `routers/memory.py` keeps **microseconds**: truncated to whole seconds, a re-scope in the same second as the mirror's recorded version is invisible and gets reverted.
  - **An empty server is never authority to delete.** A wrong `HYDRA_URL`, a fresh DB and a half-restored backup all look exactly like "everything was deleted", and the mirror may be the only copy left. `run_sync` checks the whole corpus (`fetch_whole_corpus()`, fetched once and lazily), not the project's slice - a project with no memories of its own is normal and prunes fine. Nor does the prune ever delete a file the server has **not accepted**: an unparseable `.md`, or a local-only memory whose name the server refused with a 409, is the only copy of its content.
  - **A mirror file with no version token may not move a row's identity.** A pre-upgrade file (no `id`, no `updated_at`) pairs by name, but only its *content* is pushed - never its name, type or scope, which may be arbitrarily stale. Without this, the first Stop hook after the migration would push every legacy file's old scope back over the rows the migration had just re-scoped, quietly undoing it.
- **Memory scope:** type=user/feedback → global (project_slug=NULL); type=project/reference → pinned to cwd's registered project. `hydra sync` derives scope from type automatically.
- **Type↔scope invariant, enforced in BOTH directions** (`_type_for_scope` in `routers/memory.py`, on upsert *and* update). `hydra sync` derives scope from type, so a row whose type and scope disagree is unstable - the next Stop-hook push "corrects" it and the human's intent is lost. Pinned + a global type → coerced to `project` (this is what auto-scopes the dashboard's Move-to-project). Global + a project-scoped type → **422**, because there is no way to guess user vs feedback; the caller has to say. So `hydra memory update <id> --global` requires `--type user|feedback`, and the dashboard's Move-to-Global asks for one. Left uncoerced, that row would be a global memory that sync re-pins to whatever project the next session happens to run in.
- **CLAUDE.md scope:** single-row, global-only (no project_slug column). Editable via the `/memory` dashboard or `python -m hydra_cli config put-claude-md <file>`. SessionStart hook curls the blob to `~/.claude/CLAUDE.md` (user-level), so a save propagates to every machine on next start. PUT rejects empty/whitespace-only bodies to prevent accidental wipe.
- **Slash-command distribution:** the server is the single distribution source for slash commands. `config_commands` is a `name -> content` blob table; `GET /api/config/commands` returns the whole `{name: content}` map in one round trip (no manifest - YAGNI), plus per-name GET/PUT/DELETE. Names are validated server-side to `^[A-Za-z0-9][A-Za-z0-9_-]*$` (no path separators / leading dot). The SessionStart `commands pull` hook writes each into `~/.claude/commands/<name>.md` **verbatim** - deliberately NOT via `sync.py`'s `_base_slug`, which would rename `code-review` → `code_review` and break the command - and prunes via a managed-names state file (`~/.claude/.hydra-commands.json`) so it only ever deletes files it wrote, never hand-authored ones. Public commands are authored in `client/commands/` and seeded with `scripts/publish_commands.sh` (run on deploy); private/per-deployment commands are seeded from their own source, so Hydra's repo carries no deployment-specific command content.
- **Policy-hook distribution (read the exit-2 note before touching `hooks.py`).** `config_hooks` carries a hook's **script body and its settings.json wiring in one row**, and they must never travel separately. `python <missing>.py` exits **2**, and exit 2 on `PreToolUse` is the *blocking* code - so wiring that reaches a machine ahead of its script converts a fail-open guard into a hard deny of every matching tool call. `run_pull` therefore emits wiring **only for a name whose file is on disk after the write phase**; that check is the whole safety story, not the compile check. Corollaries:
  - **Wiring says `python`, never `python3`, and never an absolute path.** Hook commands run in shell form - `sh -c` on macOS/Linux, **Git Bash on Windows**, PowerShell only if Git Bash is absent - and Windows has no `python3` on PATH (the python.org installer ships `python.exe` and `py.exe`; the `python3.exe` that resolves there is the Microsoft Store alias stub). `python3` wiring therefore installed all four policy hooks on Windows and ran none of them, silently, for weeks. Bare `python` is the same interpreter contract `setup.sh` and `client/settings.json` already depend on, and a bare name keeps the layer stable where an absolute `sys.executable` would rewrite `settings.hooks.json` on every venv switch. `run_pull` warns to stderr when a wired runtime's interpreter is not on PATH, because that failure is otherwise invisible: it exits 127, not the blocking 2. `$HOME` in the script path is fine - sh, Git Bash and PowerShell all expand it.
  - **A syntax-broken script keeps the previous file *and* its wiring.** Python content is `compile()`-checked before it is written; on `SyntaxError` the last-good script stays installed and stays wired, because running the previous version beats running nothing for a fail-open hook. A broken script with *no* previous version gets no wiring at all.
  - **Prune is scoped by a state file of FILENAMES** (`~/.claude/.hydra-hooks.json`), not names and never a glob - so hand-authored hooks in `~/.claude/hooks/` survive forever, and a hook whose `runtime` changes has its old suffix pruned correctly. **An empty server is never authority to delete** (same rule as `sync.py`): a wrong `HYDRA_URL` and a fresh DB look exactly like "every hook was deleted", so a 0-hook response prunes nothing. The wiring layer still empties, so the retained scripts are inert.
  - **`setup.sh` runs `hooks pull` immediately *before* `apply-settings`**, so the generated layer is fresh when the single renderer reads it and the puller never has to re-enter the merge. Hooks hot-reload from a watched settings file (verified 2026-07-27: a hook added mid-session fired on the next tool call), so the re-render takes effect in the same session - no one-session lag.
  - **Scope filters are client-side.** `enabled` is the fleet-wide off switch; `instances` (JSON array, NULL = everywhere) is matched against `HYDRA_INSTANCE_ID` by the *client*, so `GET /api/config/hooks` keeps returning the whole fleet's config to any machine. `ORDER BY name` on that query - SQLite promises no order without it, and an unstable one rewrites `settings.json` every pull.
  - **`HYDRA_POLICY_HOOKS_DISABLE` empties the wiring layer and nothing else.** Hydra's telemetry `http` hooks and the `sync` / `commands pull` / `capture-remote-url` lines live in `client/settings.json`, a layer the puller never writes, so observability survives a machine switching its policy hooks off. Claude Code's own `disableAllHooks` is the wider blast radius when nothing may run.
  - **Hook sources live outside this repo**, like private slash commands - Hydra ships the mechanism and no `client/hooks/` content, seeded with `hydra hooks put`.
- **Instance diagnostics (`hydra doctor` + `/debug-hydra`):** the gathering lives in the CLI, not the slash command. `hydra doctor` probes `/api/health` (unauthenticated, catches `URLError` → server DOWN), then an authed call (200/401 → auth state), then aggregates stats and checks corpus invariants (user/feedback memories pinned to a project, memories on an unregistered slug, pathless projects, pending review). It prints a labeled report and **exits 0 with status in the text** so a wrapper never loses output - run it standalone for a zero-token health check. `/debug-hydra` just runs it and spends tokens on interpretation: a slash command earns its round-trip only when the LLM adds judgment, so raw stats belong in the CLI, never a relay-only command. `/api/health` must be deployed (server restart) before doctor reports `server: UP`; a stale server 404s as `DEGRADED`.
- **Upsert semantics:** memory names are **globally unique, scope-independent** - one name = one memory (`UNIQUE(name)`). `POST /api/memory` upserts on name; a POST whose name already exists in a *different* scope is **409** unless it passes `rescope: true`. That guard is what stops a by-name push (any id-less mirror file) from silently unpinning a memory someone deliberately scoped to a project. `PUT /api/memory/{id}` uses `model_dump(exclude_unset=True)`, so `{"project_slug": null}` **unpins** - dropping every None instead would make an unpin unexpressible, which is what forced re-scopes through delete + re-create in the first place.
- **Legacy DBs (`_ensure_unique_memory_names` in `db.py`):** pre-existing DBs used two *partial* unique indexes, so one name could exist twice (once global, once pinned) - the shape the duplicate bug lived in. The migration collapses exact twins (a global row byte-identical to a pinned one is a stale-mirror re-insert; the pinned row wins), **renames** any remaining duplicate rather than deleting it, then swaps in `UNIQUE(name)`. The unique index is inline in `schema.sql` for fresh DBs but installed by `_migrate` for existing ones, because `get_db()` runs `schema.sql` *before* `_migrate` and a bare `CREATE UNIQUE INDEX` would abort startup while duplicates still exist.
- **SSE broadcast:** `session_manager._subscribers` is a list of `asyncio.Queue(maxsize=1000)`. Slow consumers are dropped on `QueueFull` rather than blocking the broadcast.
- **Auth:** Fail-closed when `HYDRA_AUTH_TOKEN` is empty unless `HYDRA_ALLOW_NO_AUTH=1`. `require_auth` reads the `Authorization` header; `require_auth_sse` also accepts `?token=` (EventSource can't set headers).
- **Body size limits:** Per-path in `server/app.py` (64KB hooks, 1MB CLAUDE.md, 256KB default); 413 on overrun.
- **DB access:** `get_db()` is a module-level singleton. Tests patch `server.db._db` to an isolated connection via the `client` fixture.
- **CLI sync:** `python -m hydra_cli sync --cwd <path>` maps cwd → project slug via `/api/projects` registry, pulls globals + project-pinned memories to `~/.claude/projects/<dir-slug>/memory/`, regenerates `MEMORY.md` as a flat index. `memory_dir_for_cwd` derives `<dir-slug>` with Claude Code's exact encoding - every non-alphanumeric char (incl. `_`/`.`) → `-` (`re.sub(r"[^A-Za-z0-9]", "-", abspath)`), NOT just `: \ /`; a slug that keeps `_` points at a nonexistent dir and syncs 0 files silently, so this must track CC's encoder. (CC also truncates+hashes >200-char paths; not replicated - `run_sync` warns when the computed push dir is missing.) **Filenames are assigned over the whole server set at once** (`canonical_filenames`), never per row: the base slug is not injective (it lowercases and collapses every run of non-alphanumerics to `_`, so `Keep Hydra deployment-agnostic` and `keep-hydra-deployment-agnostic` both want one file), and per-row derivation let two rows clobber one file - the loser became a zombie, listed by the API but absent from the mirror and unfixable by editing files. Lowest id keeps `<slug>.md`; later claimants get `<slug>-<id>.md`. `regenerate_index` links to the filename each memory was **actually found at** on disk, never a re-derived one. **Prune-on-pull:** `--pull` (server-wins) deletes every `*.md` that is not at a canonical filename - server-deleted rows, rows re-scoped away, and orphans a rename left behind (an old prune keyed on memory *name*, so a renamed memory's stale file looked alive forever and MEMORY.md listed it twice). Guarded to pull-only mode (bidirectional treats local-only files as uploads), to synced cwds, and to a non-empty server corpus. Caveats: a never-pushed local-only memory can be pruned in `--pull`, consistent with the server-wins contract, and unparseable `.md` files in the memory dir are now swept too.
- **Auto-register:** unregistered cwds POST to `/api/projects/auto-register` on SessionStart's pull. Server applies a stoplist (`server/services/slug.py` - `home`, `tmp`, `Downloads`, …) and either creates a new slug, attaches this machine's path to an existing slug, or skips with a reason. Auto-flagged entries (`projects.auto_registered_at`, `project_paths.auto_registered_at`) surface in the `/memory` dashboard's **Pending review** section with Confirm/Delete actions.
- **Editor deep-links:** `editors.json` maps instance_id → editor URI scheme (vscode://, cursor://, wsl, ssh-remote, jetbrains).
- **Remote Control URL capture:** Stop hook runs `python -m hydra_cli capture-remote-url`, which scans `transcript_path` for `{type:"system",subtype:"bridge_status",url:...}` and PUTs the latest URL to `/api/sessions/{id}/remote-control-url`. Empirically only `entrypoint=cli` Claude Code writes this event; `entrypoint=claude-vscode` is silent there, so VS Code users keep using the dashboard's manual-paste input on each session card. Filter on the JSON shape, not substring grep - user/assistant text quoting "bridge_status" can otherwise contaminate the scan.
- **Settings render (4-way merge):** `~/.claude/settings.json` is composed by `python -m hydra_cli apply-settings` (called from `setup.sh`) from four layers, in priority order: (1) `client/settings.json` - Hydra hooks template, (2) `~/.claude/settings.hooks.json` - the server policy-hook layer generated by `hooks pull` (absent is normal; malformed degrades to "no server hooks" rather than costing the user their settings file), (3) `client/settings.user.template.json` - shipped defaults (effortLevel, statusLine, attribution, …), (4) `~/.claude/settings.user.json` - user overrides. Hooks per event concatenate (Hydra first, then server hooks, user appended); other top-level keys: later layers win. The generated layer carries **only** a `hooks` key for that reason - any other key there would outrank the shipped defaults. A third migration strips user-file wiring for a hook the server now distributes (matched on the full `.claude/hooks/<filename>` path, so hand-authored hooks are untouched): `merge` concatenates rather than dedupes, so a hook left in both layers fires twice. The user file is scaffolded as a copy of the template on first run so users see all available knobs. **Deleting** a key in the user file falls back to the template default - that's the sanctioned way to opt out of a default (e.g. `statusLine`) without losing it for everyone else. **Migration:** because scaffolded user files pin old defaults forever, `apply-settings` migrates old-format user files in place: a stale `effortLevel: "max"` (legacy of the removed CLAUDE_CODE_EFFORT_LEVEL env promotion, which couldn't be overridden in-session) is dropped, and a top-level `defaultMode` moves inside `permissions` where Claude Code expects it. `client/statusline.sh` is scaffolded to `~/.claude/statusline.sh` (chmod +x) on first run, only if absent - so a later edit to the shipped script does NOT refresh machines that already have a copy; re-copy it by hand on each to propagate a fix.

## Conventions

- Keep it simple. No speculative abstractions - build what the next feature needs.
- No build step for the frontend. ES modules are fine if splitting JS later.
- SQL strings go in Python (no ORM). Wrap long SQL lines at 100 chars.
- Tests use `pytest` + `httpx.AsyncClient` with isolated per-test SQLite databases.
- Run `ruff check server/ tests/ client/` and `pyright server/ tests/ client/` before considering work done.

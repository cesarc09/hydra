# Hydra Roadmap

Features organized by theme, roughly prioritized within each section.

## Context System

- **Memory dashboard: search, type filter, inline edit** - follow-ups to the v1 dashboard at `/memory`. Currently read-only with delete / copy / move actions; no search box, no type facet, no body editor. Add once the memory list grows past one screen.
- **Auto-register project from cwd** - during `hydra sync`, if no project matches the cwd, prompt (or auto-register) with a sensible slug from the dirname. Removes a manual `hydra project create` step per machine.
- **Fold CLAUDE.md into `hydra sync`** - currently pulled separately via curl in the SessionStart hook. Would simplify onboarding by removing one hook.
- **Memory history** - keep edit history rather than clobbering on upsert. Enables rollback and answers "what did this memory say last week?"

## Editor Integration

- **Inline diff viewer** - store `old_string`/`new_string` from Edit tool_input and render diffs in the dashboard. Review changes without opening an editor.

## Notifications

- **Push notifications for waiting_input** - Browser Notification API, Discord/Slack webhook, Telegram bot, or ntfy.sh. Alert when a session needs user input.
- **Configurable notification rules** - pick which events trigger notifications (only errors, only waiting, all state changes).

## Cost & Usage Tracking

- **Token/cost dashboard** - total_cost_usd, input/output tokens per session, aggregated per day + instance.
- **Rate limit monitoring** - show 5-hour and 7-day rate limit usage across instances.
- **Usage charts** - time-series visualization of cost and tokens.

## Session Intelligence

- **Task board** - aggregate TaskCreated/TaskCompleted events into a kanban view showing what each agent is working on.
- **Transcript viewer** - fetch and render conversation transcripts from instances on the same network. Read-only session replay.
- **Session history** - searchable archive of ended sessions with timeline view.

## Orchestration

- **Dispatch tasks** - text input on the dashboard to send a task to an idle instance (via MCP or Claude Code Channels).
- **Scheduled sweeps** - cron-style recurring tasks ("run tests on all repos every morning").
- **Pipeline chains** - event-driven workflows where one instance finishing triggers work on another.

## Open Source & Extensibility

- **Zero-config bootstrap** - `hydra init` generates `.env`, systemd unit, and starts the server.
- **Plugin system** - Python functions that react to hook events for custom automation.
- **Multi-user support** - API keys per user, each seeing their own instances.

## Hardening & Performance

- **Event retention** - prune events older than N days; current table grows forever.
- **DB backup** - nightly snapshot of `hydra.db` to a safe location.
- **Pagination** - `/api/memory` and `/api/projects` list endpoints.
- **Per-session SSE subscriptions** - today every event fans out to every connected dashboard regardless of relevance.
- **Index on `sessions.status`** - speeds up dashboard filtering once there are many sessions.
- **Reduce N+1 in session_manager** - `files_changed` fetch per PostToolUse deserializes the full session row; batch or cache.
- **Rate limiting on hook ingestion** - defense against runaway event loops from a misbehaving client.
- **PWA / mobile layout** - installable dashboard, push notifications, phone-friendly card layout.
- **Favicon with status indicator** - glanceable tab status.

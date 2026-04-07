# Hydra Roadmap

Features organized by theme, roughly prioritized within each section.

## Editor Integration

- **Deep-link to changed files** — Click a changed file in the dashboard to open it in VS Code, JetBrains, or Cursor. Map instance_id → editor URI scheme (e.g., `vscode://file/path`, `vscode://vscode-remote/ssh-remote+host/path`).
- **Inline diff viewer** — Store `old_string`/`new_string` from Edit tool_input and render diffs in the dashboard. Review changes without opening an editor.

## Notifications

- **Push notifications for waiting_input** — Browser Notification API, Discord/Slack webhook, Telegram bot, or ntfy.sh. Alert when a session needs user input.
- **Configurable notification rules** — Choose which events trigger notifications (e.g., only errors, only waiting, all state changes).

## Cost & Usage Tracking

- **Token/cost dashboard** — Track total_cost_usd, input/output tokens per session. Aggregate per day, per instance.
- **Rate limit monitoring** — Show 5-hour and 7-day rate limit usage across instances.
- **Usage charts** — Simple time-series visualization of cost and token usage.

## Session Intelligence

- **Task board** — Aggregate TaskCreated/TaskCompleted events into a kanban-style view showing what each agent is working on.
- **Transcript viewer** — Fetch and render conversation transcripts from instances on the same network. Read-only session replay.
- **Session history** — Searchable archive of ended sessions with timeline view.

## Orchestration

- **Dispatch tasks** — Text input on dashboard to send a task to an idle instance via Claude Code Channels/MCP.
- **Scheduled sweeps** — Cron-style recurring tasks (e.g., "run tests on all repos every morning").
- **Pipeline chains** — Event-driven workflows: when one instance finishes, trigger a task on another.

## Cross-Host Access

- **Public access via Cloudflare Tunnel or Tailscale Funnel** — HTTPS without exposing the Pi directly.
- **PWA support** — Installable on phone with offline shell, push notification support.
- **Custom domain** — `hydra.yourdomain.com` accessible from anywhere.

## Open Source & Extensibility

- **Zero-config CLI** — `hydra init` generates hook config, `.env`, and starts the server.
- **Plugin system** — Python functions that react to hook events for custom automation.
- **Multi-user support** — API keys per user, each seeing their own instances.

## Polish

- **Auto-cleanup** — Purge events older than N days.
- **Event rate limiting** — Debounce rapid PostToolUse events.
- **Mobile-responsive layout** — Phone-friendly card layout.
- **Favicon with status indicator** — Glanceable tab status.

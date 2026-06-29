---
description: Diagnose this Hydra instance - server/DB/auth health, stats, and data anomalies, with fixes
---

Diagnose the Hydra instance this session is wired to. A CLI subcommand does the
gathering; your job is to **interpret** the result, not echo it. Read-only -
nothing here writes. No arguments.

Run the check:

```bash
python -m hydra_cli doctor
```

Then, from its output:
- Lead with a one-line health verdict: **healthy / degraded / down**.
- If `server`, `database`, or `auth` is red, name the likely cause and the fix - e.g.
  `DOWN` -> server not running or wrong `HYDRA_URL`; `auth FAILED (401)` ->
  `HYDRA_AUTH_TOKEN` unset or stale; `database ERROR` -> DB unwritable/locked.
- For each `[WARN]` anomaly, say what it means and the concrete remedy. Don't invent
  anomalies it didn't report. Known ones:
  - *user/feedback memories pinned to a project* - violates the type<->scope invariant
    (`_type_for_scope` coerces pinned global types to `project`); these usually predate
    coercion-on-update. Fix by re-saving each via the API, re-scoping it, or running
    `/forget` over that cohort.
  - *memories pinned to an unregistered slug (orphans)* - the project was deleted/renamed;
    re-pin to a live slug or delete the memory.
  - *projects with no registered path* - stale registry rows; confirm or delete in the
    `/memory` dashboard.
  - *projects pending review* - auto-registered, awaiting Confirm/Delete in the dashboard.
- End with the single highest-priority action, or "nothing to do" if everything is green.

If `python -m hydra_cli doctor` itself errors (e.g. the client isn't installed), report
that rather than guessing.

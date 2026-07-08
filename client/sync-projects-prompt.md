# Claude Code project-resync prompt

Paste this into a fresh Claude Code session on a Hydra-onboarded machine to push any local project memories that haven't reached the server yet, and to bulk-trigger auto-registration for projects you haven't yet opened a Claude session in.

> **You probably don't need this prompt.** Since the auto-register endpoint shipped, opening a Claude Code session in any unregistered directory automatically creates a project record (subject to the server's stoplist) on SessionStart, and the Stop hook pushes any new memories. This prompt is for two cases:
> 1. The machine has projects whose local memory dir has files but the project either isn't registered or never had its memories pushed (e.g. machine pre-dates auto-register, or the user added memory files manually).
> 2. You want to pre-register many directories without having to open a session in each.

Companion to [onboard-prompt.md](onboard-prompt.md): that one bootstraps a fresh machine; this one fills in gaps after onboarding.

---

Backfill any project memories on this machine that haven't reached the Hydra server yet. This machine is already onboarded - `HYDRA_URL`, `HYDRA_AUTH_TOKEN`, `HYDRA_INSTANCE_ID` should already be in my environment.

1. **Precondition check.** Confirm `python -m hydra_cli --help` runs and `python -m hydra_cli project list` returns a JSON array. If either fails, stop and tell me - the fix lives in [onboard-prompt.md](onboard-prompt.md), not here.

2. **Discover candidate cwds.** Use your judgment - you know what a project looks like on a dev machine. Useful signals:
   - `~/.claude/projects/*/` - every cwd Claude Code has touched on this machine. The directory name encodes the absolute path with every non-alphanumeric character (including `_` and `.`, not just `: \ /`) replaced by `-`. Decode each one and verify the path exists on disk; skip phantoms.
   - `.git` directories under common dev roots (`~/projects`, `~/code`, `~/work`, `~/dev`).
   - Any other dev-project signals you'd reasonably check.
   
   Drop downloads, dotfiles, system dirs, throwaway sandboxes - the server's stoplist will reject those anyway, but filtering early avoids noise in the report.

3. **Run the sync from each candidate cwd.** For each:

       cd <cwd> && python -m hydra_cli sync --push

   The CLI's pull/push step calls `/api/projects/auto-register` for unregistered cwds, so this both registers the project (if needed, subject to stoplist) and uploads any local memories in one shot. Capture each invocation's stderr/stdout so the summary can show what got auto-created vs. attached vs. skipped vs. just pushed.

4. **Summary.** Tell me, per cwd: status (registered already / auto-created / auto-attached / skipped) and memory counts (pushed / skipped / errors). At the end, point me at the dashboard's **Pending review** section - that's where the freshly auto-registered entries need a quick "Confirm" or "Delete" pass.

5. **(Optional) Hand-curate.** If any cwd's basename produced a slug I'd want to override (e.g. the auto-derived slug clashes with another project, or it's too generic), tell me and I'll run `python -m hydra_cli project create --slug <preferred>` manually.

Fail loudly. Don't swallow errors - especially `No module named hydra_cli` (means `pip install -e client/` ran against a different interpreter than `python` on PATH; re-run setup.sh from a non-venv shell) or 401 from the API (auth env vars not reaching the process).

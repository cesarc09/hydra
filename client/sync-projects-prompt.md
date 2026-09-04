# Claude Code project-resync prompt

Paste this into a fresh Claude Code session on a Hydra-onboarded machine to pull project memories and bulk-trigger auto-registration for projects you haven't yet opened a Claude session in.

> **You probably don't need this prompt.** Since the auto-register endpoint shipped, opening a Claude Code session in any unregistered directory automatically creates a project record (subject to the server's stoplist) and pulls its memories on SessionStart. This prompt is for two cases:
> 1. The machine has projects that are not registered or have not pulled the server mirror yet.
> 2. You want to pre-register many directories without having to open a session in each.

Companion to [onboard-prompt.md](onboard-prompt.md): that one bootstraps a fresh machine; this one fills in gaps after onboarding.

---

Pull server memories for projects on this machine. This machine is already onboarded - `HYDRA_URL`, `HYDRA_AUTH_TOKEN`, `HYDRA_INSTANCE_ID` should already be in my environment.

1. **Precondition check.** Confirm `python -m hydra_cli --help` runs and `python -m hydra_cli project list` returns a JSON array. If either fails, stop and tell me - the fix lives in [onboard-prompt.md](onboard-prompt.md), not here.

2. **Discover candidate cwds.** Use your judgment - you know what a project looks like on a dev machine. Useful signals:
   - `~/.claude/projects/*/` - every cwd Claude Code has touched on this machine. The directory name encodes the absolute path with every non-alphanumeric character (including `_` and `.`, not just `: \ /`) replaced by `-`. Decode each one and verify the path exists on disk; skip phantoms.
   - `.git` directories under common dev roots (`~/projects`, `~/code`, `~/work`, `~/dev`).
   - Any other dev-project signals you'd reasonably check.
   
   Drop downloads, dotfiles, system dirs, throwaway sandboxes - the server's stoplist will reject those anyway, but filtering early avoids noise in the report.

3. **Run the pull from each candidate cwd.** For each:

       cd <cwd> && python -m hydra_cli sync

   Sync calls `/api/projects/auto-register` for unregistered cwds, then pulls server memories into the local mirror. Local files are never uploaded. Capture stderr/stdout so the summary can show what was auto-created, attached, skipped, or pulled.

4. **Summary.** Tell me, per cwd: status (registered already / auto-created / auto-attached / skipped) and memory counts (pulled / pruned / errors). At the end, point me at the dashboard's **Pending review** section - that's where the freshly auto-registered entries need a quick "Confirm" or "Delete" pass.

5. **(Optional) Hand-curate.** If any cwd's basename produced a slug I'd want to override (e.g. the auto-derived slug clashes with another project, or it's too generic), tell me and I'll run `python -m hydra_cli project create --slug <preferred>` manually.

Fail loudly. Don't swallow errors - especially `No module named hydra_cli` (means `pip install -e client/` ran against a different interpreter than `python` on PATH; re-run setup.sh from a non-venv shell) or 401 from the API (auth env vars not reaching the process).

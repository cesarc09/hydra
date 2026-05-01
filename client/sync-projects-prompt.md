# Claude Code project-sync prompt

Paste this into a fresh Claude Code session on a Hydra-onboarded machine to discover any project directories that aren't yet registered with the server, register them, and push their existing local memories. Companion to [onboard-prompt.md](onboard-prompt.md): that one bootstraps a fresh machine; this one resyncs an already-onboarded one when new projects have appeared since the last pass.

---

Sync any new project directories on this machine into the Hydra registry. This machine is already onboarded — `HYDRA_URL`, `HYDRA_AUTH_TOKEN`, `HYDRA_INSTANCE_ID` should already be in my environment.

1. **Precondition check.** Confirm `python -m hydra_cli --help` runs without error and `python -m hydra_cli project list` returns a JSON array (not a 401, not a "No module named" error). If either fails, stop and tell me — don't try to fix it. The fix lives in [onboard-prompt.md](onboard-prompt.md), and patching half-broken state from this prompt risks compounding the problem.

2. **Discover candidate project paths.** Use your judgment — you know what a "project" looks like on a dev machine. Useful signals, in roughly descending order of strength:
   - `~/.claude/projects/*/` — every cwd Claude Code has touched on this machine. The directory name encodes the absolute path with `:`, `\`, `/` replaced by `-`. Decode each one and verify the path actually exists on disk; skip phantoms and decoded paths that look ambiguous.
   - `.git` directories under common dev roots (`~/projects`, `~/code`, `~/work`, `~/dev`, etc.).
   - Whatever else you'd reasonably check on this OS — recently-active cwds, editor MRU lists, shell history.
   
   Filter aggressively: drop downloads, dotfiles, system dirs, throwaway sandboxes, and anything that's clearly not a project I'd want centralized.

3. **Cross-reference with the registry.** Pull `python -m hydra_cli project list`. Each project's `paths` array contains `{instance_id, path}` entries. Drop any candidate whose absolute path already appears for this machine's `HYDRA_INSTANCE_ID`. If a candidate path appears under a *different* `instance_id` for some slug, keep it on the list but flag it as "would attach this machine to existing slug X (registered for path Y on machine Z)."

4. **Propose the unregistered set as a single batched list, then wait.** Show one block with: absolute path, proposed slug (default to `basename`), and any flags from step 3. Let me confirm the whole batch, edit slugs, or remove entries before any write happens. Don't proceed on partial confirmation.

5. **Slug-collision policy.** If a candidate's basename matches an existing slug on the server but the existing project's paths look like a different logical project (very different parent dirs, different git remotes if you can read them), surface it and ask me — don't auto-attach. If the existing slug looks like the same project across machines (matching git remote, same basename, paths of the form `~/projects/<slug>` from other instances), proposing auto-attach is fine, but still show me the existing entry so I can say no.

6. **Register confirmed projects.** For each:

       python -m hydra_cli project create --slug <slug> --path <absolute-path>

   The server treats this as idempotent attach on `(slug, instance_id)`: a new slug is created, an existing slug gets a new path row for this machine. A 4xx is a stop-and-ask, not a retry.

7. **Push existing local memories from each newly-registered cwd.** For each:

       cd <project-path> && python -m hydra_cli sync --push

   Upserts are by `(name, project_slug)`, so re-runs are safe. Capture per-project counts (pushed / skipped / errors) for the summary.

8. **Verify convergence.** From each newly-registered cwd:

       cd <project-path> && python -m hydra_cli sync

   Bidirectional sync should report `0 pushed, 0 pulled, 0 conflicts` if step 7 completed cleanly. Anything else is worth surfacing — flag it but don't try to auto-resolve conflicts.

9. **Summary.** Tell me: candidates discovered, candidates dropped (and why — already-registered, ambiguous, filtered as non-project, etc.), projects registered, total memories pushed, and any per-project conflicts or errors. If anything stalled at step 5 or 6 awaiting my input, list it so I can follow up manually.

Fail loudly. Don't swallow errors — especially `python -m hydra_cli` returning `No module named hydra_cli` (means `pip install -e client/` was run against a different interpreter than `python` on PATH; re-running `setup.sh` from a non-venv shell is the fix) or 401 from the API (auth env vars not reaching the process).

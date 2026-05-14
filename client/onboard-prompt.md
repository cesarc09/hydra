# Claude Code onboarding prompt

Paste this into a fresh Claude Code session on a new client machine. The assistant will walk through the onboarding steps ([../ONBOARDING.md](../ONBOARDING.md)) and ask for the auth token at the right moment.

---

Onboard this machine as a Hydra client. Hydra is a cross-machine control plane for Claude Code, running at <HYDRA_URL>.

1. Ask me where to clone the repo. Suggest `~/projects/hydra` as the default but let me override - setup.sh auto-detects its own location, so any path works. Then:
   - If the target directory doesn't exist: `git clone https://github.com/cesarc09/hydra.git <chosen-path>`
   - Otherwise: `cd <chosen-path> && git pull --ff-only`
   Use that chosen path consistently for the rest of these steps.

2. Persist these env vars (ask me for the auth token - do NOT guess or reuse any token you've seen):
   - `HYDRA_URL=<HYDRA_URL>`
   - `HYDRA_AUTH_TOKEN=<I'll paste>`
   - `HYDRA_INSTANCE_ID=$(hostname)`

   Pick the right file based on how I launch Claude Code:
   - Interactive terminal → append to `~/.bashrc` or `~/.zshrc` (detect from `$SHELL`)
   - VSCode Remote → write to `~/.vscode-server/server-env-setup` with mode 0600 (I'll need to disconnect + reconnect VSCode Remote after)

   Don't duplicate existing lines. Also `export` the vars in the current shell so the next steps can use them.

3. Run `bash ~/projects/hydra/client/setup.sh`. Installs `~/.claude/settings.json` (hooks pointing at `$HYDRA_URL`) and the `hydra_cli` package. **Run setup.sh from a shell where `python` and `pip` resolve to the same interpreter - usually the base user/system Python, not a venv.** The hooks call `python -m hydra_cli`, so the package must live in whichever Python is first on `PATH` at hook spawn time. If `pip install -e` fails with PEP 668, retry with `pip install --user -e ~/projects/hydra/client`. Verify `python -m hydra_cli --help` works.

4. Register every project directory on this machine where I use Claude Code. Ask me for the list if you don't know; at minimum register `~/projects/hydra` if it exists:

       python -m hydra_cli project create --slug <short-name> --path <absolute-project-path>

   Project-scoped memories won't push from cwds that aren't registered - `python -m hydra_cli sync` will log them as skipped. If a project with the same basename already exists on the server, auto-attach will bind this machine's path on the next sync.

5. Sync this machine's existing memories to the server. From EACH registered project directory:

       cd <project-path> && python -m hydra_cli sync --push

   Upserts are by `(name, project_slug)`, so re-runs are safe. Report per-project counts.

6. Verify convergence on one project: `cd <project-path> && python -m hydra_cli sync` (bidirectional) should report `0 pushed, 0 pulled, 0 conflicts` - that's the real idempotency check. (`--pull` is an unconditional overwrite, so it always reports `pulled = <server count>`, which isn't what we want for verification.) Then `python -m hydra_cli memory list` should show the full set.

7. Recommend (don't force) enabling Claude Code's "Enable Remote Control for all sessions" toggle via the `/config` slash command. This auto-enables Remote Control per session so each session is reachable from the Claude mobile app. On terminal (`claude` CLI) sessions Hydra's Stop hook auto-captures the `https://claude.ai/code/session_...` URL from the transcript; in VS Code, the extension doesn't write the URL to the transcript so it has to be pasted once per session into the dashboard's session card field. Either way the card's "Open Remote Control" button deep-links to that specific session from anywhere.

8. Tell me to start a fresh Claude Code session in one of the registered projects. The SessionStart hook should pull the latest memories + CLAUDE.md, and the session should appear on the dashboard within a second or two.

Fail loudly on any step. Don't swallow errors - especially 401 from the CLI (wrong token, or env vars not reaching the process) or `No module named hydra_cli` (setup.sh's `pip install -e client/` ran against a different interpreter than `python` on PATH; re-run setup.sh from a non-venv shell).

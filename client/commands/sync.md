---
description: Wrap up the session - propose doc + memory updates, then write a session summary
argument-hint: "[optional topic slug]"
---

Wrap up the current work session in three steps, in order. Be concise and do not
invent work: if a step has nothing worth recording, say so and move on.
Optional topic hint: $ARGUMENTS

## 1. Propose documentation updates
Scan this session for any non-obvious constraint, decision, or gotcha a future
session would want in a CLAUDE.md - project (`./CLAUDE.md`) or global
(`~/.claude/CLAUDE.md`), per the memory-scope rules.
- Present each as a tight before/after, grouped by file. Write nothing yet.
- Apply only what I approve.

## 2. Propose memory updates
Identify durable, one-fact-per-file items worth persisting, and any existing
memory this session made stale or wrong.
- New/updated: name, type (user/feedback = global; project/reference = pinned),
  one-line description, body.
- Deletions: memory name + why.
- Present them; on my approval, write approved files into the memory dir +
  update MEMORY.md, and delete confirmed ones with
  `python -m hydra_cli memory delete <name>`.

## 3. Write the session summary
Once 1-2 are settled, write the summary file (no approval needed).

First gather identifiers by running:
- `date +%F` -> DATE
- `echo "$CLAUDE_CODE_SESSION_ID"` -> SID (the current session id)
- `find ~/.claude/projects -name "$CLAUDE_CODE_SESSION_ID.jsonl"` -> the transcript
  path. Record it; do NOT rename or move it - Claude Code resolves resume/history
  by this UUID filename, so renaming breaks both.

Choose a <title>: a concise description of the session, max 7 words. <slug> is the
kebab-case of <title>.

- Path: `~/.claude/sessions/<DATE>-<project>-<slug>.md` (project = this repo's dir
  name). Create `~/.claude/sessions/` if missing. If today's file for this project
  exists, add a `-2`, `-3`, etc. suffix rather than overwriting.
- This is the EPISODIC layer. Do NOT restate facts that just became memories/docs -
  reference them. Capture what happened, why, what's next.
- Use exactly this structure:

```markdown
---
date: <DATE>
project: <project>
title: <title, max 7 words>
session_id: <SID>
transcript: <transcript path>
status: done | in-progress | blocked
---

# <title>

## What we did
- <concrete changes/decisions, with file refs>

## Decisions & rationale
- <decision -> why; note any rejected alternative>

## Open threads / next steps
- <what's unfinished; what the next session picks up first>

## Pointers
- Docs updated: <files or "none">
- Memories: <names written/deleted or "none">
- Commits: <hashes or "none">
```

After writing, report three things: the file **path**, the **title** (so I can run
`/rename <title>` to name this session if I want), and a 3-line recap.

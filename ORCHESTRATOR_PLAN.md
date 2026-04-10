# Orchestrator Plan — Agent-Gated Session Control

## Concept

An orchestrator layer inside Hydra that uses specialized review agents to monitor, gate, and respond to Claude Code sessions in real-time. Instead of just observing events, Hydra actively approves/denies/modifies tool calls before they execute.

## Current vs. Proposed Architecture

### Current: Observation-only

```
Claude Code → PostToolUse (async, fire-and-forget) → Hydra → Dashboard
```

Hydra sees what happened **after** it happened. Cannot prevent anything.

### Proposed: Active gating

```
Claude Code → PreToolUse (sync, blocking) → Hydra Orchestrator
                                                  ↓
                                          Route to review agents
                                                  ↓
                                    ┌─────────────┼─────────────┐
                                    ↓             ↓             ↓
                              Security Agent  Perf Agent   Logic Agent
                              (heuristics +   (pattern     (LLM-based
                               rule engine)    matching)    review)
                                    ↓             ↓             ↓
                                    └─────────────┼─────────────┘
                                                  ↓
                                          Aggregate decisions
                                                  ↓
                                    approve / deny / escalate
                                                  ↓
                              ┌────────────────────────────────────┐
                              │ approve → return {} (allow)        │
                              │ deny    → return exit 2 (block)    │
                              │ escalate → hold, notify user,      │
                              │            wait for dashboard input │
                              └────────────────────────────────────┘
```

The key insight: Claude Code's `PreToolUse` hook is **blockable**. If the HTTP hook returns a deny decision, the tool call is prevented. This gives Hydra real control.

## Two-Layer Review System

### Layer 1: Heuristics (fast, deterministic, <100ms)

Pattern-matching rules that run instantly. No LLM calls. These handle the 90% of cases that are clearly safe or clearly dangerous.

```python
# Example heuristic rules for the Security Agent
ALLOW_PATTERNS = [
    r"^ls\b",
    r"^cat\b",
    r"^echo\b",
    r"^git\s+(status|log|diff|branch)\b",
    r"^python\s+-m\s+pytest\b",
    r"^npm\s+(test|run|install)\b",
    r"^pwd$",
    r"^cd\b",
]

DENY_PATTERNS = [
    r"rm\s+-rf\s+/",           # rm -rf /
    r":\(\)\{.*\}",            # fork bomb
    r">\s*/dev/sd",            # write to raw device
    r"mkfs\.",                 # format filesystem
    r"dd\s+if=.*of=/dev/",    # dd to device
    r"curl.*\|\s*bash",       # pipe curl to bash
    r"wget.*\|\s*sh",         # pipe wget to sh
    r"chmod\s+777\s+/",       # chmod 777 root
]

ESCALATE_PATTERNS = [
    r"rm\s+-rf\b",            # rm -rf (not root, but ask)
    r"git\s+push\s+--force",  # force push
    r"DROP\s+TABLE",          # SQL DDL
    r"sudo\b",                # elevated privileges
    r"docker\s+rm",           # container removal
    r"kill\s+-9",             # force kill
]
```

**Decision logic:**
1. Check DENY_PATTERNS first → instant deny
2. Check ALLOW_PATTERNS → instant approve
3. Check ESCALATE_PATTERNS → escalate to user
4. No match → pass to Layer 2 (or escalate if no Layer 2)

### Layer 2: LLM-based review (slower, nuanced, 1-5s)

For commands that heuristics can't classify. Uses the Claude API to evaluate the command in context.

```python
async def llm_review(command: str, cwd: str, context: str) -> str:
    """Returns 'approve', 'deny', or 'escalate' with reasoning."""
    response = await anthropic.messages.create(
        model="claude-haiku-4-5-20251001",  # Fast + cheap
        max_tokens=200,
        messages=[{
            "role": "user",
            "content": f"""Evaluate this shell command for safety.
Working directory: {cwd}
Command: {command}
Context: {context}

Respond with one of: APPROVE, DENY, ESCALATE
Then a one-line reason."""
        }]
    )
    # Parse response...
```

**When to use Layer 2:**
- Command didn't match any heuristic pattern
- Command is ambiguous (e.g., `rm -rf ./build/` — safe in context, dangerous in general)
- Review agent is configured for LLM mode

**Cost control:** Haiku is ~$0.001 per evaluation. At 100 tool calls per session, that's $0.10 per session. Acceptable.

**Latency budget:** PreToolUse hook timeout is configurable. Set to 5-10s. Heuristics use <100ms, LLM uses 1-3s. Both fit within budget.

## Review Agent Types

### Security Agent (priority 1)
**Monitors:** `PreToolUse` for `Bash` tool
**Heuristics:** Allow/deny/escalate patterns for shell commands
**LLM layer:** Evaluate ambiguous commands in context
**Config:**
```toml
[orchestrator.agents.security]
enabled = true
tools = ["Bash"]
mode = "heuristic+llm"     # or "heuristic-only", "llm-only"
llm_model = "claude-haiku-4-5-20251001"
on_no_match = "escalate"    # what to do when no rule matches
```

### File Safety Agent (priority 2)
**Monitors:** `PreToolUse` for `Write`, `Edit`
**Heuristics:** Block writes to sensitive paths (`.env`, `/etc/`, `~/.ssh/`, credentials files)
**LLM layer:** Review if a code change introduces obvious vulnerabilities (optional, expensive)
**Config:**
```toml
[orchestrator.agents.file_safety]
enabled = true
tools = ["Write", "Edit"]
mode = "heuristic-only"
deny_paths = ["**/.env", "**/*.pem", "**/*.key", "**/credentials*"]
```

### Code Review Agent (priority 3, future)
**Monitors:** `PostToolUse` for `Write`, `Edit` (non-blocking, advisory)
**LLM layer:** Reviews code changes for quality, performance, logic errors
**Output:** Doesn't block — adds review comments to the dashboard
**Note:** This runs on PostToolUse (after the fact) because blocking every edit for LLM review would be too slow.

### Custom Agents (plugin-based, future)
Users define their own agents via Python files in `plugins/agents/`:
```python
# plugins/agents/no_console_log.py
TOOLS = ["Write", "Edit"]

async def review(event, context):
    content = event.tool_input.get("new_string", "")
    if "console.log" in content:
        return "escalate", "New code contains console.log"
    return "approve", None
```

## Orchestrator Server Components

### New files

```
server/
├── orchestrator/
│   ├── __init__.py
│   ├── router.py           # Replaces hooks.py for PreToolUse events
│   ├── engine.py           # Routes events to agents, aggregates decisions
│   ├── heuristics.py       # Pattern matching engine
│   ├── llm_reviewer.py     # Claude API integration for Layer 2
│   └── agents/
│       ├── __init__.py
│       ├── security.py     # Bash command security agent
│       └── file_safety.py  # File write path safety agent
```

### Decision aggregation

When multiple agents review the same event:
- Any **deny** → deny (most restrictive wins)
- Any **escalate** + no deny → escalate
- All **approve** → approve

Same precedence as Claude Code's own hook system.

### Escalation flow

When a review agent returns "escalate":

1. Hydra holds the PreToolUse response (within timeout)
2. Sends a notification to the dashboard + push notification
3. Dashboard shows a modal: "Security Agent flagged: `rm -rf ./build/` — Approve / Deny?"
4. User clicks approve or deny
5. Hydra returns the decision to Claude Code

**Timeout handling:** If the user doesn't respond within the hook timeout (e.g., 30s), default to deny (safe fallback). The timeout needs to be long enough for user response — configure `PreToolUse` hook timeout to 60s for orchestrated instances.

**Implementation:** Use an `asyncio.Event` or `asyncio.Queue` per pending escalation. The dashboard polls or uses WebSocket for the approval flow.

## Hook Configuration Changes

The current hooks are all `async`-style observation hooks. For gating, we need a **synchronous PreToolUse hook** that waits for the orchestrator's response.

```json
{
  "PreToolUse": [
    {
      "matcher": "Bash|Write|Edit",
      "hooks": [
        {
          "type": "http",
          "url": "http://pi.local:8400/api/orchestrator/review",
          "timeout": 60,
          "headers": {
            "Authorization": "Bearer $HYDRA_AUTH_TOKEN",
            "X-Instance-Id": "$HYDRA_INSTANCE_ID"
          },
          "allowedEnvVars": ["HYDRA_AUTH_TOKEN", "HYDRA_INSTANCE_ID"]
        }
      ]
    }
  ]
}
```

**Key difference from existing hooks:**
- This is `PreToolUse` not `PostToolUse`
- No `async` — it blocks until Hydra responds
- Timeout is 60s (not 5s) to allow for escalation
- The response body determines whether the tool call proceeds

### Response format

```json
// Approve
{}

// Deny
{
  "hookSpecificOutput": {
    "hookEventName": "PreToolUse",
    "permissionDecision": "deny",
    "permissionDecisionReason": "Security agent blocked: rm -rf with recursive flag"
  }
}

// Approve with context injection
{
  "hookSpecificOutput": {
    "hookEventName": "PreToolUse",
    "permissionDecision": "allow",
    "additionalContext": "Note: Security agent approved this command."
  }
}
```

## Dashboard UI for Orchestrator

### Review Queue

A new panel showing pending escalations:

```
┌──────────────────────────────────────────────────────┐
│  Review Queue (1 pending)                             │
│                                                       │
│  ┌──────────────────────────────────────────────────┐ │
│  │ windows-vscode — Security Agent                   │ │
│  │ Bash: rm -rf ./build/dist/                       │ │
│  │ Reason: Recursive delete flagged for review       │ │
│  │ CWD: /c/Users/giosu/projects/hydra               │ │
│  │                                                   │ │
│  │  [Approve]  [Deny]  [Approve + Always Allow]     │ │
│  └──────────────────────────────────────────────────┘ │
└──────────────────────────────────────────────────────┘
```

"Approve + Always Allow" adds the pattern to the allow list, so it doesn't escalate again.

### Audit Log

All orchestrator decisions (approve/deny/escalate) are logged in a new table:

```sql
CREATE TABLE reviews (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL,
    instance_id TEXT NOT NULL,
    agent_name TEXT NOT NULL,
    tool_name TEXT NOT NULL,
    tool_input_summary TEXT,
    decision TEXT NOT NULL,          -- approve, deny, escalate
    reason TEXT,
    decided_by TEXT,                 -- "heuristic", "llm", "user"
    decided_at TEXT NOT NULL
);
```

## Opt-in Design

The orchestrator is **opt-in per instance**. Instances without `PreToolUse` hooks configured continue to work in observation-only mode. This means:

- Existing `PostToolUse` hooks (async observation) remain unchanged
- `PreToolUse` hooks (sync gating) are added only to instances that want orchestration
- An instance can have both: PreToolUse for gating + PostToolUse for dashboard monitoring

Configuration in `hydra.toml`:

```toml
[orchestrator]
enabled = true
default_timeout = 60

# Which instances are orchestrated (others remain observation-only)
instances = ["windows-vscode", "wsl-main"]
```

## Scope

**Immediate focus: Security Agent (heuristic-only).** LLM-based review agents (code review, performance, logic) are a future extension. The architecture supports them, but we build and ship the heuristic security agent first.

## Implementation Phases

### Phase O1: Heuristic security agent
- `PreToolUse` hook for Bash
- Heuristic engine with allow/deny/escalate patterns
- Auto-approve safe commands, auto-deny dangerous ones
- Escalations → hold and wait for user input (same as a normal waiting session)
- Audit log table

### Phase O2: Dashboard review queue
- Escalation hold + notification
- Dashboard panel with approve/deny/always-allow buttons
- WebSocket or long-poll for real-time approval flow
- "Always Allow" adds pattern to allow list for future auto-approval

### Phase O3: File safety agent
- PreToolUse for Write/Edit
- Path-based deny rules
- Sensitive file detection (`.env`, keys, credentials)

### Phase O4: LLM review layer (future)
- Claude API integration (Haiku for speed)
- Configurable per-agent: heuristic-only, llm-only, heuristic+llm
- Cost tracking for LLM reviews

### Phase O5: Code review agent (future, advisory)
- PostToolUse for Write/Edit (non-blocking)
- LLM reviews code changes
- Review comments shown in dashboard

### Phase O6: Custom agents (future, plugin)
- Python plugin interface for user-defined agents
- Auto-discovery from plugins/agents/ directory

## Resolved Design Decisions

1. **Escalation timeout** — No timeout. Escalated commands hold indefinitely, same as any session waiting for input. The session appears as "waiting_input" on the dashboard. User responds when they're ready.

2. **Learning from approvals** — Explicit opt-in only. Two buttons: "Approve" (one-time) and "Always Allow" (adds pattern to allow list). No implicit learning — security rules don't weaken silently.

3. **LLM review** — Deferred. Security agent ships with heuristics only. The agent interface is designed to support LLM review later, but it's not part of the initial build.

## Future: LLM Review Agents

When we're ready to add LLM-based agents, the open questions to revisit:
- Performance impact of synchronous LLM calls (1-3s per gated tool call)
- Model selection per agent (Haiku for speed vs Sonnet for depth)
- Context window scope (command-only vs recent conversation)
- Cost tracking and budgeting for LLM reviews

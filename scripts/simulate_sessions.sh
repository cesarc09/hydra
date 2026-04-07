#!/bin/bash
# Simulates multiple Claude Code instances sending hook events to Hydra.
# Usage: ./scripts/simulate_sessions.sh [server_url]

SERVER="${1:-http://localhost:8400}"
TOKEN="change-me-to-a-random-string"

post() {
    local instance="$1"
    local payload="$2"
    curl -s -X POST "$SERVER/api/hooks/event" \
        -H "Content-Type: application/json" \
        -H "Authorization: Bearer $TOKEN" \
        -H "X-Instance-Id: $instance" \
        -d "$payload" > /dev/null
}

echo "Simulating sessions against $SERVER"
echo ""

# === Instance 1: windows-vscode working on a web app ===
echo "[windows-vscode] SessionStart"
post "windows-vscode" '{"session_id":"sess-win-001","hook_event_name":"SessionStart","cwd":"/c/Users/giosu/projects/hydra","source":"startup","model":"claude-opus-4-6"}'
sleep 0.5

echo "[windows-vscode] UserPromptSubmit"
post "windows-vscode" '{"session_id":"sess-win-001","hook_event_name":"UserPromptSubmit","cwd":"/c/Users/giosu/projects/hydra","prompt":"Add dark mode toggle to dashboard"}'
sleep 0.3

echo "[windows-vscode] PostToolUse Edit style.css"
post "windows-vscode" '{"session_id":"sess-win-001","hook_event_name":"PostToolUse","cwd":"/c/Users/giosu/projects/hydra","tool_name":"Edit","tool_input":{"file_path":"/c/Users/giosu/projects/hydra/static/style.css"}}'
sleep 0.5

# === Instance 2: wsl-main running tests ===
echo "[wsl-main] SessionStart"
post "wsl-main" '{"session_id":"sess-wsl-001","hook_event_name":"SessionStart","cwd":"/home/giosu/projects/pcb","source":"startup","model":"claude-sonnet-4-6"}'
sleep 0.3

echo "[wsl-main] UserPromptSubmit"
post "wsl-main" '{"session_id":"sess-wsl-001","hook_event_name":"UserPromptSubmit","cwd":"/home/giosu/projects/pcb","prompt":"Run the test suite and fix failures"}'
sleep 0.3

echo "[wsl-main] PostToolUse Bash(pytest)"
post "wsl-main" '{"session_id":"sess-wsl-001","hook_event_name":"PostToolUse","cwd":"/home/giosu/projects/pcb","tool_name":"Bash","tool_input":{"command":"python -m pytest tests/ -v"}}'
sleep 0.5

# === Instance 1 continues ===
echo "[windows-vscode] PostToolUse Edit app.js"
post "windows-vscode" '{"session_id":"sess-win-001","hook_event_name":"PostToolUse","cwd":"/c/Users/giosu/projects/hydra","tool_name":"Edit","tool_input":{"file_path":"/c/Users/giosu/projects/hydra/static/app.js"}}'
sleep 0.3

echo "[windows-vscode] PostToolUse Write index.html"
post "windows-vscode" '{"session_id":"sess-win-001","hook_event_name":"PostToolUse","cwd":"/c/Users/giosu/projects/hydra","tool_name":"Write","tool_input":{"file_path":"/c/Users/giosu/projects/hydra/static/index.html"}}'
sleep 0.3

echo "[windows-vscode] Stop"
post "windows-vscode" '{"session_id":"sess-win-001","hook_event_name":"Stop","cwd":"/c/Users/giosu/projects/hydra"}'
sleep 0.5

# === Instance 3: ssh-devbox doing a deployment ===
echo "[ssh-devbox] SessionStart"
post "ssh-devbox" '{"session_id":"sess-ssh-001","hook_event_name":"SessionStart","cwd":"/home/giosu/deploy/production","source":"startup","model":"claude-opus-4-6"}'
sleep 0.3

echo "[ssh-devbox] PostToolUse Bash(make deploy)"
post "ssh-devbox" '{"session_id":"sess-ssh-001","hook_event_name":"PostToolUse","cwd":"/home/giosu/deploy/production","tool_name":"Bash","tool_input":{"command":"make deploy ENVIRONMENT=staging"}}'
sleep 0.5

# === Instance 2 finishes, waiting for input ===
echo "[wsl-main] PostToolUse Edit test_model.py"
post "wsl-main" '{"session_id":"sess-wsl-001","hook_event_name":"PostToolUse","cwd":"/home/giosu/projects/pcb","tool_name":"Edit","tool_input":{"file_path":"/home/giosu/projects/pcb/tests/test_model.py"}}'
sleep 0.3

echo "[wsl-main] Stop"
post "wsl-main" '{"session_id":"sess-wsl-001","hook_event_name":"Stop","cwd":"/home/giosu/projects/pcb"}'
sleep 0.5

echo "[wsl-main] Notification(idle_prompt)"
post "wsl-main" '{"session_id":"sess-wsl-001","hook_event_name":"Notification","cwd":"/home/giosu/projects/pcb","notification_type":"idle_prompt","message":"Claude Code needs your attention"}'
sleep 0.3

# === Instance 1 gets new prompt ===
echo "[windows-vscode] Notification(idle_prompt)"
post "windows-vscode" '{"session_id":"sess-win-001","hook_event_name":"Notification","cwd":"/c/Users/giosu/projects/hydra","notification_type":"idle_prompt","message":"Claude Code needs your attention"}'

echo ""
echo "=== Simulation complete ==="
echo "Open $SERVER in your browser to see the dashboard."
echo ""
echo "Expected state:"
echo "  windows-vscode  -> Waiting for Input (3 files changed)"
echo "  wsl-main        -> Waiting for Input (1 file changed)"
echo "  ssh-devbox      -> Active (0 files changed)"

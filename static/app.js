const API = window.location.origin + "/api";
const MAX_EVENTS = 100;

let sessions = {};
let eventLog = [];
let editorConfig = { default: { editor: "vscode", type: "local" }, instances: {} };

// --- Fetch initial state ---

async function fetchSessions() {
    try {
        const res = await fetch(`${API}/sessions`);
        const data = await res.json();
        sessions = {};
        for (const s of data) {
            sessions[s.session_id] = s;
        }
        renderSessions();
    } catch (e) {
        console.error("Failed to fetch sessions:", e);
    }
}

// --- SSE connection ---

function connectSSE() {
    const statusEl = document.getElementById("connection-status");
    const source = new EventSource(`${API}/events/stream`);

    source.onopen = () => {
        statusEl.textContent = "Connected";
        statusEl.className = "badge badge-green";
    };

    source.addEventListener("hook_event", (e) => {
        const data = JSON.parse(e.data);
        handleEvent(data);
    });

    source.onerror = () => {
        statusEl.textContent = "Disconnected";
        statusEl.className = "badge badge-red";
        source.close();
        // Reconnect after 3 seconds
        setTimeout(connectSSE, 3000);
    };
}

function handleEvent(data) {
    // Update session state optimistically
    const sid = data.session_id;
    if (sessions[sid]) {
        const s = sessions[sid];
        s.last_event_at = data.received_at;
        if (data.tool_name) s.last_tool = data.tool_name;
        if (data.tool_input_summary) s.last_tool_input_summary = data.tool_input_summary;

        if (data.cwd) s.cwd = data.cwd;

        switch (data.event_name) {
            case "SessionStart":
            case "UserPromptSubmit":
            case "PostToolUse":
            case "SubagentStart":
                s.status = "active";
                break;
            case "SubagentStop":
                break;
            case "SessionEnd":
                s.status = "ended";
                break;
            case "Stop":
                s.status = "idle";
                break;
            case "Notification":
                s.status = "waiting_input";
                break;
        }
    } else if (data.event_name === "SessionStart") {
        // New session
        sessions[sid] = {
            session_id: sid,
            instance_id: data.instance_id,
            status: "active",
            cwd: data.cwd || "",
            last_event_at: data.received_at,
            last_tool: null,
            last_tool_input_summary: null,
            files_changed: [],
            started_at: data.received_at,
        };
    } else {
        // Unknown session — refetch all
        fetchSessions();
        return;
    }

    // Add to event log
    eventLog.unshift(data);
    if (eventLog.length > MAX_EVENTS) eventLog.pop();

    renderSessions();
    renderEventLog();
}

// --- Rendering ---

function renderSessions() {
    const grid = document.getElementById("sessions-grid");
    const list = Object.values(sessions);

    if (list.length === 0) {
        grid.innerHTML = '<p class="empty-state">No sessions yet. Start a Claude Code instance with hooks configured.</p>';
        updateCounts(list);
        return;
    }

    // Sort: active/waiting first, then idle, then ended
    const order = { active: 0, waiting_input: 1, idle: 2, ended: 3 };
    list.sort((a, b) => (order[a.status] ?? 9) - (order[b.status] ?? 9));

    grid.innerHTML = list.map(renderCard).join("");
    updateCounts(list);
}

function renderCard(s) {
    const shortCwd = s.cwd ? s.cwd.split("/").slice(-2).join("/") : "—";
    const statusLabel = {
        active: "Working",
        waiting_input: "Waiting for Input",
        idle: "Idle",
        ended: "Ended",
    }[s.status] || s.status;
    const statusBadge = {
        active: "badge-green",
        waiting_input: "badge-yellow",
        idle: "badge-gray",
        ended: "badge-red",
    }[s.status] || "badge-gray";

    const lastActivity = s.last_tool
        ? `<span class="tool-name">${escHtml(s.last_tool)}</span>${s.last_tool_input_summary ? " <code>" + escHtml(truncate(s.last_tool_input_summary, 60)) + "</code>" : ""}`
        : "—";

    const ago = timeAgo(s.last_event_at);
    const files = Array.isArray(s.files_changed) ? s.files_changed : [];
    const filesCount = files.length;

    const filesList = files.map((f) => {
        const uri = editorUri(s.instance_id, f);
        const name = fileName(f);
        if (uri) {
            return `<a class="file-link" href="${escHtml(uri)}" title="${escHtml(f)}">${escHtml(name)}</a>`;
        }
        return `<span class="file-link" title="${escHtml(f)}">${escHtml(name)}</span>`;
    }).join("");

    const filesToggle = filesCount > 0
        ? `<span class="files-count clickable" onclick="toggleFiles('${s.session_id}')">${filesCount} file${filesCount !== 1 ? "s" : ""} changed</span>`
        : `<span class="files-count">0 files changed</span>`;

    return `
        <article class="session-card status-${s.status}">
            <div class="card-header">
                <span class="instance-name">${escHtml(s.instance_id)}</span>
                <span class="badge ${statusBadge}">${statusLabel}</span>
            </div>
            <div class="cwd" title="${escHtml(s.cwd)}">${escHtml(shortCwd)}</div>
            <div class="last-activity"><span>${lastActivity}</span><span class="time-ago">${ago}</span></div>
            ${filesCount > 0 ? `<div id="files-${s.session_id}" class="files-list hidden">${filesList}</div>` : ""}
            <div class="card-footer">
                ${filesToggle}
                <a class="remote-link" href="https://claude.ai/code" target="_blank" rel="noopener">Open Remote Control</a>
            </div>
        </article>
    `;
}

function renderEventLog() {
    const log = document.getElementById("event-log");
    if (eventLog.length === 0) {
        log.innerHTML = '<p class="empty-state">Waiting for events...</p>';
        return;
    }
    log.innerHTML = eventLog.map((e) => {
        const time = new Date(e.received_at).toLocaleTimeString();
        const detail = e.tool_input_summary ? escHtml(truncate(e.tool_input_summary, 80)) : "";
        const shortSession = e.session_id ? e.session_id.slice(0, 8) : "—";
        return `
            <div class="event-row">
                <span class="event-time">${time}</span>
                <span class="event-session">${shortSession}</span>
                <span class="event-instance">${escHtml(e.instance_id || "—")}</span>
                <span class="event-name">${escHtml(e.event_name)}</span>
                <span class="event-detail">${e.tool_name ? escHtml(e.tool_name) + " " : ""}${detail}</span>
            </div>
        `;
    }).join("");
}

function updateCounts(list) {
    const counts = { active: 0, waiting_input: 0, idle: 0, ended: 0 };
    for (const s of list) counts[s.status] = (counts[s.status] || 0) + 1;
    document.getElementById("count-active").textContent = `${counts.active} Active`;
    document.getElementById("count-waiting").textContent = `${counts.waiting_input} Waiting`;
    document.getElementById("count-idle").textContent = `${counts.idle} Idle`;
    document.getElementById("count-ended").textContent = `${counts.ended} Ended`;
}

// --- Helpers ---

function escHtml(str) {
    const d = document.createElement("div");
    d.textContent = str;
    return d.innerHTML;
}

function truncate(str, len) {
    return str.length > len ? str.slice(0, len) + "..." : str;
}

function timeAgo(iso) {
    if (!iso) return "";
    const diff = (Date.now() - new Date(iso).getTime()) / 1000;
    if (diff < 60) return "just now";
    if (diff < 3600) return `${Math.floor(diff / 60)}m ago`;
    if (diff < 86400) return `${Math.floor(diff / 3600)}h ago`;
    return `${Math.floor(diff / 86400)}d ago`;
}

// --- Editor deep-links ---

async function fetchEditorConfig() {
    try {
        const res = await fetch(`${API}/editors`);
        editorConfig = await res.json();
    } catch (e) {
        console.error("Failed to fetch editor config:", e);
    }
}

// Convert Git Bash path (/c/Users/...) to Windows path (C:/Users/...)
function toWindowsPath(p) {
    const m = p.match(/^\/([a-zA-Z])\/(.*)/);
    return m ? `${m[1].toUpperCase()}:/${m[2]}` : p;
}

function editorUri(instanceId, filePath) {
    const cfg = editorConfig.instances[instanceId] || editorConfig.default || {};
    const editor = cfg.editor || "vscode";
    const type = cfg.type || "local";
    const scheme = editor === "cursor" ? "cursor" : "vscode";

    if (type === "wsl" && cfg.distro) {
        return `${scheme}://vscode-remote/wsl+${cfg.distro}${filePath}`;
    }

    if (type === "ssh-remote" && cfg.host) {
        return `${scheme}://vscode-remote/ssh-remote+${cfg.host}${filePath}`;
    }

    if (type === "local") {
        return `${scheme}://file/${toWindowsPath(filePath)}`;
    }

    if (editor === "jetbrains") {
        return `jetbrains://open?file=${encodeURIComponent(filePath)}`;
    }

    return null;
}

function fileName(path) {
    return path.split("/").pop() || path;
}

function toggleFiles(sessionId) {
    const el = document.getElementById(`files-${sessionId}`);
    if (el) el.classList.toggle("hidden");
}

// --- Config sync ---

async function fetchSyncStatus() {
    try {
        const res = await fetch(`${API}/memory/status`);
        const data = await res.json();
        const el = document.getElementById("sync-status");
        if (data.status === "not_configured") {
            el.textContent = "Not configured — set HYDRA_CONFIG_REPO in .env";
        } else if (data.last_sync) {
            const suffix = data.last_error ? ` (error: ${data.last_error})` : "";
            el.textContent = `Last sync: ${timeAgo(data.last_sync)} — ${data.repo}${suffix}`;
        } else {
            el.textContent = `Repo: ${data.repo} — not synced yet`;
        }
    } catch (e) {
        console.error("Failed to fetch sync status:", e);
    }
}

async function triggerSync() {
    const btn = document.getElementById("sync-btn");
    const el = document.getElementById("sync-status");
    btn.disabled = true;
    btn.textContent = "Syncing...";
    try {
        const res = await fetch(`${API}/memory/sync`, { method: "POST" });
        const data = await res.json();
        el.textContent = data.message || data.status;
        fetchSyncStatus();
    } catch (e) {
        el.textContent = "Sync failed: " + e.message;
    } finally {
        btn.disabled = false;
        btn.textContent = "Sync Now";
    }
}

// --- Init ---

fetchEditorConfig();
fetchSessions();
connectSSE();
fetchSyncStatus();

setInterval(fetchSessions, 30000);
setInterval(fetchSyncStatus, 60000);

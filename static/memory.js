let projects = [];
let memories = [];
let expandedMemoryIds = new Set();
let expandedProjectSlugs = new Set();
let openAction = null;  // { memoryId, kind: "copy" | "move" } — at most one inline form open

// --- Fetch ---

async function fetchProjects() {
    const res = await apiFetch(`${API}/projects`);
    if (!res.ok) return;
    projects = await res.json();
}

async function fetchMemories() {
    const res = await apiFetch(`${API}/memory`);
    if (!res.ok) return;
    memories = await res.json();
}

async function refresh() {
    await Promise.all([fetchProjects(), fetchMemories()]);
    render();
}

// --- Render ---

function render() {
    renderStats();
    renderGlobals();
    renderProjects();
}

function renderStats() {
    const el = document.getElementById("memory-stats");
    const globalCount = memories.filter((m) => m.project_slug == null).length;
    const projectCount = memories.length - globalCount;
    el.textContent = `${projects.length} project${projects.length !== 1 ? "s" : ""} · ${memories.length} memor${memories.length !== 1 ? "ies" : "y"} (${globalCount} global · ${projectCount} project-scoped)`;
    document.getElementById("global-count").textContent = globalCount ? `(${globalCount})` : "";
    document.getElementById("project-count").textContent = projects.length ? `(${projects.length})` : "";
}

function renderGlobals() {
    const el = document.getElementById("global-memories");
    const globals = memories.filter((m) => m.project_slug == null);
    globals.sort((a, b) => a.name.localeCompare(b.name));
    if (globals.length === 0) {
        el.innerHTML = '<p class="empty-state">No global memories.</p>';
        return;
    }
    el.innerHTML = globals.map((m) => renderMemoryRow(m, /*isGlobal=*/true)).join("");
}

function renderProjects() {
    const el = document.getElementById("project-list");
    if (projects.length === 0) {
        el.innerHTML = '<p class="empty-state">No projects registered.</p>';
        return;
    }
    const sorted = [...projects].sort((a, b) => a.slug.localeCompare(b.slug));
    el.innerHTML = sorted.map(renderProjectRow).join("");
}

function renderProjectRow(p) {
    const scoped = memories.filter((m) => m.project_slug === p.slug);
    const open = expandedProjectSlugs.has(p.slug);
    const caret = open ? "▾" : "▸";
    const rows = open && scoped.length > 0
        ? scoped.slice().sort((a, b) => a.name.localeCompare(b.name))
            .map((m) => renderMemoryRow(m, /*isGlobal=*/false)).join("")
        : "";
    const emptyNote = open && scoped.length === 0
        ? '<p class="empty-state">No memories in this project.</p>'
        : "";
    const desc = p.description ? ` <span class="project-desc">${escHtml(p.description)}</span>` : "";
    return `
        <div class="project-row">
            <div class="project-header" onclick="toggleProject('${escAttr(p.slug)}')">
                <span class="archive-caret">${caret}</span>
                <span class="project-slug">${escHtml(p.slug)}</span>${desc}
                <span class="project-memory-count">${scoped.length} memor${scoped.length !== 1 ? "ies" : "y"}</span>
            </div>
            ${open ? `<div class="project-memories">${rows}${emptyNote}</div>` : ""}
        </div>
    `;
}

function renderMemoryRow(m, isGlobal) {
    const expanded = expandedMemoryIds.has(m.id);
    const caret = expanded ? "▾" : "▸";
    const typeClass = memoryTypeBadge(m.type);
    const actions = [];
    if (!isGlobal) {
        actions.push(`<span class="memory-action" onclick="startCopy(${m.id})">Copy</span>`);
        actions.push(`<span class="memory-action" onclick="startMoveToGlobal(${m.id})">Move to Global</span>`);
    }
    actions.push(`<span class="memory-action memory-action-danger" onclick="deleteMemory(${m.id})">Delete</span>`);
    const form = renderInlineForm(m);
    return `
        <div class="memory-row">
            <div class="memory-head">
                <span class="memory-toggle" onclick="toggleMemory(${m.id})">
                    <span class="archive-caret">${caret}</span>
                    <span class="memory-name">${escHtml(m.name)}</span>
                </span>
                <span class="badge ${typeClass}">${escHtml(m.type)}</span>
                <span class="memory-actions">${actions.join("")}</span>
            </div>
            ${m.description ? `<div class="memory-description">${escHtml(m.description)}</div>` : ""}
            ${form}
            ${expanded ? `<pre class="memory-body">${escHtml(m.body || "")}</pre>` : ""}
        </div>
    `;
}

function memoryTypeBadge(type) {
    switch (type) {
        case "user": return "badge-green";
        case "feedback": return "badge-yellow";
        case "project": return "badge-gray";
        case "reference": return "badge-red";
        default: return "badge-gray";
    }
}

function renderInlineForm(m) {
    if (!openAction || openAction.memoryId !== m.id) return "";
    if (openAction.kind === "copy") {
        const others = projects.filter((p) => p.slug !== m.project_slug);
        if (others.length === 0) {
            return `<div class="memory-inline-form">No other projects to copy to. <span class="memory-action" onclick="cancelAction()">Cancel</span></div>`;
        }
        const opts = others.map((p) => `<option value="${escAttr(p.slug)}">${escHtml(p.slug)}</option>`).join("");
        return `
            <div class="memory-inline-form">
                <label>Copy to:
                    <select id="copy-target-${m.id}">${opts}</select>
                </label>
                <span class="memory-action" onclick="confirmCopy(${m.id})">Confirm</span>
                <span class="memory-action" onclick="cancelAction()">Cancel</span>
            </div>
        `;
    }
    if (openAction.kind === "move") {
        return `
            <div class="memory-inline-form">
                <label>Move to Global as:
                    <select id="move-type-${m.id}">
                        <option value="user">user</option>
                        <option value="feedback">feedback</option>
                    </select>
                </label>
                <span class="memory-action" onclick="confirmMove(${m.id})">Confirm</span>
                <span class="memory-action" onclick="cancelAction()">Cancel</span>
            </div>
        `;
    }
    return "";
}

// --- Toggles ---

function toggleMemory(id) {
    if (expandedMemoryIds.has(id)) expandedMemoryIds.delete(id);
    else expandedMemoryIds.add(id);
    render();
}

function toggleProject(slug) {
    if (expandedProjectSlugs.has(slug)) expandedProjectSlugs.delete(slug);
    else expandedProjectSlugs.add(slug);
    render();
}

// --- Actions ---

function startCopy(id) {
    openAction = { memoryId: id, kind: "copy" };
    render();
}

function startMoveToGlobal(id) {
    openAction = { memoryId: id, kind: "move" };
    render();
}

function cancelAction() {
    openAction = null;
    render();
}

async function deleteMemory(id) {
    const m = memories.find((x) => x.id === id);
    if (!m) return;
    const scope = m.project_slug ? `project '${m.project_slug}'` : "global";
    if (!confirm(`Delete memory '${m.name}' (${scope})?`)) return;
    const res = await apiFetch(`${API}/memory/${id}`, { method: "DELETE" });
    if (res.status !== 204) {
        alert(`Delete failed: HTTP ${res.status}`);
        return;
    }
    memories = memories.filter((x) => x.id !== id);
    expandedMemoryIds.delete(id);
    render();
}

async function confirmCopy(id) {
    const m = memories.find((x) => x.id === id);
    if (!m) return;
    const sel = document.getElementById(`copy-target-${id}`);
    const target = sel && sel.value;
    if (!target) return;
    const existing = memories.find((x) => x.name === m.name && x.project_slug === target);
    if (existing && !confirm(`Overwrite existing memory '${m.name}' in project '${target}'?`)) return;
    const payload = {
        name: m.name,
        description: m.description || "",
        type: m.type,
        body: m.body || "",
        project_slug: target,
    };
    const res = await apiFetch(`${API}/memory`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
    });
    if (!res.ok) {
        alert(`Copy failed: HTTP ${res.status}`);
        return;
    }
    const saved = await res.json();
    // Replace existing or append
    const idx = memories.findIndex((x) => x.id === saved.id);
    if (idx >= 0) memories[idx] = saved; else memories.push(saved);
    openAction = null;
    render();
}

async function confirmMove(id) {
    const m = memories.find((x) => x.id === id);
    if (!m) return;
    const sel = document.getElementById(`move-type-${id}`);
    const newType = (sel && sel.value) || "user";
    const existing = memories.find((x) => x.name === m.name && x.project_slug == null);
    if (existing && !confirm(`Overwrite existing global memory '${m.name}'?`)) return;
    const payload = {
        name: m.name,
        description: m.description || "",
        type: newType,
        body: m.body || "",
        project_slug: null,
    };
    const createRes = await apiFetch(`${API}/memory`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
    });
    if (!createRes.ok) {
        alert(`Move failed (create step): HTTP ${createRes.status}`);
        return;
    }
    const saved = await createRes.json();

    // Delete original only if the upsert didn't already replace it (can happen
    // if somehow the IDs matched, defensive).
    if (saved.id !== id) {
        const delRes = await apiFetch(`${API}/memory/${id}`, { method: "DELETE" });
        if (delRes.status !== 204) {
            alert(`Move partially failed: created global copy, but DELETE of original returned HTTP ${delRes.status}. Resolve manually.`);
            await refresh();
            return;
        }
    }

    memories = memories.filter((x) => x.id !== id);
    const idx = memories.findIndex((x) => x.id === saved.id);
    if (idx >= 0) memories[idx] = saved; else memories.push(saved);
    openAction = null;
    expandedMemoryIds.delete(id);
    render();
}

// --- Helpers ---

function escAttr(s) {
    return String(s).replace(/"/g, "&quot;").replace(/'/g, "&#39;");
}

// --- Init ---

ensureToken();
refresh();

let projects = [];
let memories = [];
let claudeMd = "";  // current server content
let claudeMdExpanded = false;
let claudeMdDraft = null;  // null when not editing; string when textarea has been touched
let claudeMdStatus = "";  // inline saved/error message
let expandedMemoryIds = new Set();
let expandedProjectSlugs = new Set();
let openAction = null;  // { memoryId, kind: "copy" | "move" | "distribute" } - at most one inline form open

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

async function fetchClaudeMd() {
    const res = await apiFetch(`${API}/config/claude-md`);
    if (!res.ok) return;
    claudeMd = await res.text();
}

async function refresh() {
    await Promise.all([fetchProjects(), fetchMemories(), fetchClaudeMd()]);
    render();
}

// --- Render ---

function render() {
    renderStats();
    renderPendingReview();
    renderClaudeMd();
    renderGlobals();
    renderProjects();
}

function pendingReviewItems() {
    // One row per (project, instance_id, path) that needs review. A project
    // with project-level auto_registered_at gets at least one row regardless
    // of path flags; per-path flags surface separately.
    const items = [];
    for (const p of projects) {
        const projectFlagged = !!p.auto_registered_at;
        for (const path of (p.paths || [])) {
            if (projectFlagged || path.auto_registered_at) {
                items.push({
                    slug: p.slug,
                    instance_id: path.instance_id,
                    path: path.path,
                    auto_registered_at: path.auto_registered_at || p.auto_registered_at,
                    projectFlagged,
                });
            }
        }
        if (projectFlagged && (p.paths || []).length === 0) {
            // Edge case: project flagged but no paths recorded
            items.push({
                slug: p.slug,
                instance_id: null,
                path: null,
                auto_registered_at: p.auto_registered_at,
                projectFlagged: true,
            });
        }
    }
    items.sort((a, b) => (a.auto_registered_at || "").localeCompare(b.auto_registered_at || ""));
    return items;
}

function renderPendingReview() {
    const section = document.getElementById("pending-review-section");
    const list = document.getElementById("pending-review-list");
    const count = document.getElementById("pending-count");
    const items = pendingReviewItems();
    if (items.length === 0) {
        section.hidden = true;
        return;
    }
    section.hidden = false;
    count.textContent = `(${items.length})`;
    list.innerHTML = items.map(renderPendingRow).join("");
}

function renderPendingRow(it) {
    const flagBadge = it.projectFlagged
        ? `<span class="badge badge-yellow">new project</span>`
        : `<span class="badge badge-gray">new path</span>`;
    const pathLabel = it.path
        ? `<span class="memory-description">${escHtml(it.instance_id)} · <code>${escHtml(it.path)}</code></span>`
        : "";
    return `
        <div class="memory-row">
            <div class="memory-head">
                <span class="memory-name">${escHtml(it.slug)}</span>
                ${flagBadge}
                <span class="memory-actions">
                    <span class="memory-action" onclick="confirmAutoRegistered('${escAttr(it.slug)}', ${it.instance_id ? `'${escAttr(it.instance_id)}'` : "null"}, ${it.projectFlagged})">Confirm</span>
                    <span class="memory-action memory-action-danger" onclick="deletePendingEntry('${escAttr(it.slug)}', ${it.instance_id ? `'${escAttr(it.instance_id)}'` : "null"}, ${it.projectFlagged})">Delete</span>
                </span>
            </div>
            ${pathLabel}
        </div>
    `;
}

async function confirmAutoRegistered(slug, instanceId, projectFlagged) {
    // Always clear the path-level flag if we have one. If the project itself
    // is flagged, also clear the project-level flag.
    if (instanceId) {
        const r = await apiFetch(`${API}/projects/${encodeURIComponent(slug)}/paths/${encodeURIComponent(instanceId)}/confirm`, { method: "POST" });
        if (!r.ok && r.status !== 404) {
            alert(`Confirm failed: HTTP ${r.status}`);
            return;
        }
    }
    if (projectFlagged) {
        const r = await apiFetch(`${API}/projects/${encodeURIComponent(slug)}/confirm`, { method: "POST" });
        if (!r.ok) {
            alert(`Confirm (project) failed: HTTP ${r.status}`);
            return;
        }
    }
    await refresh();
}

async function deletePendingEntry(slug, instanceId, projectFlagged) {
    // If only this machine's path is flagged on an otherwise-confirmed project,
    // delete just the path. Otherwise nuke the whole project.
    if (!projectFlagged && instanceId) {
        if (!confirm(`Detach ${instanceId}'s path from project '${slug}'?`)) return;
        const r = await apiFetch(`${API}/projects/${encodeURIComponent(slug)}/paths/${encodeURIComponent(instanceId)}`, { method: "DELETE" });
        if (r.status !== 204) {
            alert(`Delete path failed: HTTP ${r.status}`);
            return;
        }
    } else {
        if (!confirm(`Delete project '${slug}' and all its paths?`)) return;
        const r = await apiFetch(`${API}/projects/${encodeURIComponent(slug)}`, { method: "DELETE" });
        if (r.status !== 204) {
            alert(`Delete project failed: HTTP ${r.status}`);
            return;
        }
    }
    await refresh();
}

function renderClaudeMd() {
    const el = document.getElementById("claude-md-section");
    const caret = claudeMdExpanded ? "▾" : "▸";
    const bytes = claudeMd.length;
    const status = claudeMdStatus
        ? `<span class="claude-md-status">${escHtml(claudeMdStatus)}</span>`
        : "";
    const header = `
        <div class="claude-md-header" onclick="toggleClaudeMd()">
            <span class="archive-caret">${caret}</span>
            <span class="claude-md-label">CLAUDE.md</span>
            <span class="claude-md-meta">${bytes} char${bytes !== 1 ? "s" : ""}</span>
            ${status}
        </div>
    `;
    if (!claudeMdExpanded) {
        el.innerHTML = header;
        return;
    }
    const current = claudeMdDraft != null ? claudeMdDraft : claudeMd;
    const dirty = claudeMdDraft != null && claudeMdDraft !== claudeMd;
    const saveClass = dirty ? "memory-action" : "memory-action memory-action-disabled";
    const revertClass = dirty ? "memory-action" : "memory-action memory-action-disabled";
    el.innerHTML = `
        ${header}
        <div class="claude-md-body">
            <textarea id="claude-md-textarea" class="claude-md-textarea" oninput="onClaudeMdInput(this.value)" spellcheck="false">${escHtml(current)}</textarea>
            <div class="claude-md-actions">
                <span class="${saveClass}" onclick="saveClaudeMd()">Save</span>
                <span class="${revertClass}" onclick="revertClaudeMd()">Revert</span>
            </div>
        </div>
    `;
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
    } else {
        actions.push(`<span class="memory-action" onclick="startDistribute(${m.id})">Move to projects</span>`);
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
    if (openAction.kind === "distribute") {
        if (projects.length === 0) {
            return `<div class="memory-inline-form">No projects to move to. <span class="memory-action" onclick="cancelAction()">Cancel</span></div>`;
        }
        const sorted = [...projects].sort((a, b) => a.slug.localeCompare(b.slug));
        const boxes = sorted.map((p) => `
            <label><input type="checkbox" name="distribute-${m.id}" value="${escAttr(p.slug)}"> ${escHtml(p.slug)}</label>
        `).join("");
        return `
            <div class="memory-inline-form memory-inline-form-distribute">
                <div class="distribute-label">Move to projects:</div>
                <div class="distribute-checkboxes">${boxes}</div>
                <div class="distribute-actions">
                    <span class="memory-action" onclick="confirmDistribute(${m.id})">Confirm</span>
                    <span class="memory-action" onclick="cancelAction()">Cancel</span>
                </div>
            </div>
        `;
    }
    return "";
}

// --- CLAUDE.md actions ---

function toggleClaudeMd() {
    claudeMdExpanded = !claudeMdExpanded;
    if (!claudeMdExpanded) {
        claudeMdDraft = null;
        claudeMdStatus = "";
    }
    render();
}

function onClaudeMdInput(value) {
    claudeMdDraft = value;
    claudeMdStatus = "";
    // Re-render only the action buttons' dirty state without rebuilding the
    // textarea (which would lose caret position). The simplest path: toggle
    // a CSS class on the buttons via direct DOM rather than full render().
    const actions = document.querySelectorAll(".claude-md-actions .memory-action");
    const dirty = claudeMdDraft !== claudeMd;
    for (const a of actions) {
        a.classList.toggle("memory-action-disabled", !dirty);
    }
}

async function saveClaudeMd() {
    if (claudeMdDraft == null || claudeMdDraft === claudeMd) return;
    if (!claudeMdDraft.trim()) {
        alert("CLAUDE.md cannot be empty.");
        return;
    }
    const res = await apiFetch(`${API}/config/claude-md`, {
        method: "PUT",
        headers: { "Content-Type": "text/plain" },
        body: claudeMdDraft,
    });
    if (!res.ok) {
        let detail = `HTTP ${res.status}`;
        try {
            const data = await res.json();
            if (data.detail) detail = data.detail;
        } catch (_) { /* ignore */ }
        claudeMdStatus = `Save failed: ${detail}`;
        render();
        return;
    }
    const data = await res.json();
    claudeMd = claudeMdDraft;
    claudeMdDraft = null;
    claudeMdStatus = `Saved ${data.updated_at}`;
    render();
}

function revertClaudeMd() {
    if (claudeMdDraft == null || claudeMdDraft === claudeMd) return;
    claudeMdDraft = null;
    claudeMdStatus = "";
    render();
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

function startDistribute(id) {
    openAction = { memoryId: id, kind: "distribute" };
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

async function confirmDistribute(id) {
    const m = memories.find((x) => x.id === id);
    if (!m) return;
    const checked = document.querySelectorAll(`input[name="distribute-${id}"]:checked`);
    const targets = [...checked].map((el) => el.value);
    if (targets.length === 0) {
        alert("Pick at least one project.");
        return;
    }
    const collisions = targets.filter(
        (t) => memories.some((x) => x.name === m.name && x.project_slug === t),
    );
    if (collisions.length > 0) {
        const plural = collisions.length > 1 ? "s" : "";
        const list = collisions.map((s) => `'${s}'`).join(", ");
        if (!confirm(`Overwrite existing memory '${m.name}' in project${plural} ${list}?`)) return;
    }
    const payloadBase = {
        name: m.name,
        description: m.description || "",
        type: m.type,
        body: m.body || "",
    };
    const results = await Promise.all(targets.map(async (t) => {
        const res = await apiFetch(`${API}/memory`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ ...payloadBase, project_slug: t }),
        });
        return {
            target: t,
            ok: res.ok,
            status: res.status,
            saved: res.ok ? await res.json() : null,
        };
    }));
    const failed = results.filter((r) => !r.ok);
    if (failed.length > 0) {
        const msg = failed.map((f) => `${f.target} (HTTP ${f.status})`).join(", ");
        alert(`Move to projects partially failed: ${msg}. Global memory NOT deleted. Successful copies are saved; you may retry.`);
        await refresh();
        return;
    }
    const delRes = await apiFetch(`${API}/memory/${id}`, { method: "DELETE" });
    if (delRes.status !== 204) {
        alert(`Move to projects partially failed: copies created, but DELETE of original returned HTTP ${delRes.status}. Resolve manually.`);
        await refresh();
        return;
    }
    memories = memories.filter((x) => x.id !== id);
    for (const r of results) {
        const idx = memories.findIndex((x) => x.id === r.saved.id);
        if (idx >= 0) memories[idx] = r.saved; else memories.push(r.saved);
    }
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

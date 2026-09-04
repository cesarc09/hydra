let projects = [];
let memories = [];
let claudeMd = "";  // current server content
let claudeMdExpanded = false;
let claudeMdDraft = null;  // null when not editing; string when textarea has been touched
let claudeMdStatus = "";  // inline saved/error message
let expandedMemoryIds = new Set();
let expandedProjectSlugs = new Set();
let openAction = null;  // { memoryId, kind: "reproject" | "move" | "distribute" } - at most one inline form open

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
        actions.push(`<span class="memory-action" onclick="startMoveToProject(${m.id})">Move to project</span>`);
        actions.push(`<span class="memory-action" onclick="startMoveToGlobal(${m.id})">Move to Global</span>`);
    } else {
        actions.push(`<span class="memory-action" onclick="startDistribute(${m.id})">Move to projects</span>`);
    }
    actions.push(`<span class="memory-action memory-action-danger" onclick="deleteMemory(${m.id})">Delete</span>`);
    const form = renderInlineForm(m);
    const authorParts = [m.author_harness, m.author_model, m.author_session_id?.slice(0, 8)]
        .filter(Boolean).map(escHtml);
    const author = m.author_harness
        ? `<div class="memory-description">by ${authorParts.join(" · ")}</div>`
        : "";
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
            ${author}
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
    if (openAction.kind === "reproject") {
        const others = projects.filter((p) => p.slug !== m.project_slug);
        if (others.length === 0) {
            return `<div class="memory-inline-form">No other projects to move to. <span class="memory-action" onclick="cancelAction()">Cancel</span></div>`;
        }
        const opts = others.map((p) => `<option value="${escAttr(p.slug)}">${escHtml(p.slug)}</option>`).join("");
        return `
            <div class="memory-inline-form">
                <label>Move to project:
                    <select id="reproject-target-${m.id}">${opts}</select>
                </label>
                <span class="memory-action" onclick="confirmMoveToProject(${m.id})">Confirm</span>
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
                <div class="distribute-hint">One project moves it in place. Several splits it into per-project memories named <code>${escHtml(m.name)}-&lt;slug&gt;</code> - names are globally unique.</div>
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

function startMoveToProject(id) {
    openAction = { memoryId: id, kind: "reproject" };
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

// Re-scoping is a PUT, never create-then-delete. A new row means a new id, and
// every mirror file still carrying the OLD id then looks server-deleted - which
// is exactly how a "move" used to resurrect itself as a duplicate.
async function rescopeMemory(id, projectSlug, extra = {}) {
    const res = await apiFetch(`${API}/memory/${id}`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ project_slug: projectSlug, ...extra }),
    });
    if (!res.ok) {
        alert(`Move failed: ${await errorDetail(res)}`);
        return null;
    }
    const saved = await res.json();
    const idx = memories.findIndex((x) => x.id === saved.id);
    if (idx >= 0) memories[idx] = saved; else memories.push(saved);
    openAction = null;
    render();
    return saved;
}

async function confirmMoveToProject(id) {
    const sel = document.getElementById(`reproject-target-${id}`);
    const target = sel && sel.value;
    if (!target) return;
    await rescopeMemory(id, target);
}

async function confirmMove(id) {
    const sel = document.getElementById(`move-type-${id}`);
    const newType = (sel && sel.value) || "user";
    await rescopeMemory(id, null, { type: newType });
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
    // A single target is a plain re-scope: same row, same id, same name.
    if (targets.length === 1) {
        await rescopeMemory(id, targets[0]);
        expandedMemoryIds.delete(id);
        return;
    }

    // Several targets can't all keep one name (names are globally unique), so
    // each lands as '<name>-<slug>' and the global original is deleted.
    const named = targets.map((t) => ({ target: t, name: `${m.name}-${t}` }));
    const clashes = named.filter((n) => memories.some(
        (x) => x.name === n.name && x.project_slug !== n.target,
    ));
    if (clashes.length > 0) {
        // Deliberately NOT sent with rescope:true - that would drag the existing
        // memory out of the project it is pinned to. Let the server 409 instead.
        alert(`Cannot split: ${clashes.map((n) => `'${n.name}'`).join(", ")} already exists elsewhere. Rename that memory first.`);
        return;
    }
    if (!confirm(`Split '${m.name}' into ${named.map((n) => `'${n.name}'`).join(", ")} and delete the global original?`)) return;
    const payloadBase = {
        description: m.description || "",
        type: m.type,
        body: m.body || "",
    };
    const results = await Promise.all(named.map(async ({ target, name }) => {
        const res = await apiFetch(`${API}/memory`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ ...payloadBase, name, project_slug: target }),
        });
        return {
            target,
            ok: res.ok,
            saved: res.ok ? await res.json() : null,
            detail: res.ok ? null : await errorDetail(res),
        };
    }));
    const failed = results.filter((r) => !r.ok);
    if (failed.length > 0) {
        const msg = failed.map((f) => `${f.target}: ${f.detail}`).join(", ");
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

async function errorDetail(res) {
    // Surface the server's message (the 409 explains WHY a name is refused);
    // a bare status code leaves the user with nothing to act on.
    try {
        const body = await res.json();
        if (body && body.detail) return `${body.detail} (HTTP ${res.status})`;
    } catch { /* not JSON */ }
    return `HTTP ${res.status}`;
}

function escAttr(s) {
    return String(s).replace(/"/g, "&quot;").replace(/'/g, "&#39;");
}

// --- Init ---

ensureToken();
refresh();

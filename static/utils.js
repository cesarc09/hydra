const API = window.location.origin + "/api";

let authToken = localStorage.getItem("hydraToken") || "";

function ensureToken() {
    if (!authToken) {
        const entered = (window.prompt("Enter Hydra auth token:") || "").trim();
        if (entered) {
            authToken = entered;
            localStorage.setItem("hydraToken", authToken);
        }
    }
    return authToken;
}

function clearToken() {
    authToken = "";
    localStorage.removeItem("hydraToken");
}

async function apiFetch(path, opts = {}) {
    ensureToken();
    const tokenUsed = authToken;
    const headers = { ...(opts.headers || {}), "X-Hydra-Flow": "dashboard" };
    if (tokenUsed) headers["Authorization"] = `Bearer ${tokenUsed}`;
    let res = await fetch(path, { ...opts, headers });
    if (res.status === 401) {
        // Only re-prompt if no concurrent request already replaced the token.
        // Without this check, N parallel 401s cause N prompts - even after the
        // first prompt got the correct token.
        if (authToken === tokenUsed) {
            clearToken();
            ensureToken();
        }
        if (authToken && authToken !== tokenUsed) {
            const retryHeaders = { ...headers, Authorization: `Bearer ${authToken}` };
            res = await fetch(path, { ...opts, headers: retryHeaders });
        }
    }
    return res;
}

function escHtml(str) {
    const d = document.createElement("div");
    d.textContent = str == null ? "" : String(str);
    return d.innerHTML;
}

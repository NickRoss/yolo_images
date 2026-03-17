const grid = document.getElementById("grid");
const latInput = document.getElementById("lat");
const lonInput = document.getElementById("lon");
const searchInput = document.getElementById("location-search");
const searchResults = document.getElementById("search-results");
const savedSelect = document.getElementById("saved-locations");
const btnApply = document.getElementById("btn-apply");
const btnDelete = document.getElementById("btn-delete");
const btnSelectAll = document.getElementById("btn-select-all");
const btnDeselectAll = document.getElementById("btn-deselect-all");
const btnSaveLocation = document.getElementById("btn-save-location");
const btnDeleteSaved = document.getElementById("btn-delete-saved");
const saveNameInput = document.getElementById("save-name");
const statusText = document.getElementById("status-text");
const pageInfo = document.getElementById("page-info");
const btnPrev = document.getElementById("btn-prev");
const btnNext = document.getElementById("btn-next");
const toastEl = document.getElementById("toast");
const progressOverlay = document.getElementById("progress-overlay");
const progressText = document.getElementById("progress-text");
const progressBar = document.getElementById("progress-bar");

let currentPage = 1;
let assets = [];
let selected = new Set();
let lastClickedIndex = null;
let hasNextPage = false;
let currentDirectory = null; // set when entering from batch tag

// ── Auth check ───────────────────────────────────────────────────────────────

async function authFetch(url, opts = {}) {
    const resp = await fetch(url, opts);
    if (resp.status === 401) {
        window.location.href = "/login";
        throw new Error("Not authenticated");
    }
    return resp;
}

document.getElementById("btn-logout").addEventListener("click", async () => {
    await fetch("/auth/logout", { method: "POST" });
    window.location.href = "/login";
});

// ── Tabs ─────────────────────────────────────────────────────────────────────

document.querySelectorAll(".tab").forEach((tab) => {
    tab.addEventListener("click", () => {
        document.querySelectorAll(".tab").forEach((t) => t.classList.remove("active"));
        document.querySelectorAll(".tab-content").forEach((c) => c.classList.remove("active"));
        tab.classList.add("active");
        document.getElementById(`tab-${tab.dataset.tab}`).classList.add("active");
        if (tab.dataset.tab === "audit") loadAuditLog(1);
        if (tab.dataset.tab === "batch") loadBatchDirectories();
    });
});

// ── Toast ─────────────────────────────────────────────────────────────────

function toast(msg, isError = false) {
    toastEl.textContent = msg;
    toastEl.className = "toast" + (isError ? " error" : "");
    setTimeout(() => toastEl.classList.add("hidden"), 3000);
}

// ── Progress ──────────────────────────────────────────────────────────────

function showProgress(text, current, total) {
    progressOverlay.classList.remove("hidden");
    progressText.textContent = `${text} (${current}/${total})`;
    progressBar.style.width = `${(current / total) * 100}%`;
}

function hideProgress() {
    progressOverlay.classList.add("hidden");
    progressBar.style.width = "0%";
}

// ── Assets ────────────────────────────────────────────────────────────────

async function loadAssets(page = 1) {
    currentPage = page;
    selected.clear();
    updateSelectedCount();
    grid.innerHTML = '<div class="loading">Loading...</div>';

    try {
        let url = `/api/assets?page=${page}&size=50`;
        if (currentDirectory) {
            url += `&directory=${encodeURIComponent(currentDirectory)}`;
        }
        const resp = await authFetch(url);
        if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
        const data = await resp.json();
        assets = data.items;
        hasNextPage = data.hasNextPage;
        renderGrid();
        updatePagination();

        statusText.textContent = `${assets.length} untagged asset(s) on this page`;
        updateFilterBanner();
        checkFileTypes();
    } catch (e) {
        grid.innerHTML = `<div class="loading">Error loading assets: ${e.message}</div>`;
    }
}

function updateFilterBanner() {
    const banner = document.getElementById("dir-filter-banner");
    const nameEl = document.getElementById("dir-filter-name");
    if (currentDirectory) {
        nameEl.textContent = currentDirectory;
        banner.classList.remove("hidden");
    } else {
        banner.classList.add("hidden");
    }
}

document.getElementById("btn-clear-filter").addEventListener("click", () => {
    currentDirectory = null;
    latInput.value = "";
    lonInput.value = "";
    searchInput.value = "";
    saveNameInput.value = "";
    updateFilterBanner();
    updateSelectedCount();
    loadAssets(1);
});

function enterDirectoryMode(dirName, location, lat, lon) {
    currentDirectory = dirName;
    // Switch to tagger tab
    document.querySelectorAll(".tab").forEach((t) => t.classList.remove("active"));
    document.querySelectorAll(".tab-content").forEach((c) => c.classList.remove("active"));
    document.querySelector('[data-tab="tagger"]').classList.add("active");
    document.getElementById("tab-tagger").classList.add("active");
    // Pre-fill location
    if (lat != null && lon != null) {
        latInput.value = lat;
        lonInput.value = lon;
    }
    if (location) {
        searchInput.value = location;
        saveNameInput.value = location;
    }
    updateSelectedCount();
    loadAssets(1);
}

async function checkFileTypes() {
    const paths = assets.map((a) => a.originalPath).filter(Boolean);
    if (paths.length === 0) return;
    try {
        const resp = await authFetch("/api/check-types", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ originalPaths: paths }),
        });
        if (!resp.ok) return;
        const data = await resp.json();
        // Build set of mismatched file paths (by basename for matching)
        const mismatchInfo = {};
        for (const r of data.results || []) {
            if (r.mismatch) {
                const basename = r.file_path.split("/").pop();
                mismatchInfo[basename] = `${r.extension} is actually ${r.actual_type}`;
            }
        }
        if (Object.keys(mismatchInfo).length === 0) return;
        document.querySelectorAll(".thumb").forEach((el) => {
            const idx = parseInt(el.dataset.index);
            const asset = assets[idx];
            if (!asset) return;
            const info = mismatchInfo[asset.originalFileName];
            if (info) {
                el.classList.add("type-mismatch");
                const badge = document.createElement("div");
                badge.className = "mismatch-badge";
                badge.dataset.tooltip = info;
                badge.textContent = "!";
                el.appendChild(badge);
            }
        });
    } catch (e) {
        console.error("File type check failed:", e);
    }
}

function renderGrid() {
    grid.innerHTML = "";
    if (assets.length === 0) {
        grid.innerHTML = '<div class="loading">No untagged images found.</div>';
        return;
    }
    assets.forEach((asset, idx) => {
        const div = document.createElement("div");
        div.className = "thumb" + (selected.has(asset.id) ? " selected" : "");
        div.dataset.index = idx;

        const img = document.createElement("img");
        img.src = `/api/thumbnail/${asset.id}`;
        img.alt = asset.originalFileName;
        img.loading = "lazy";

        const info = document.createElement("div");
        info.className = "thumb-info";
        const date = asset.fileCreatedAt ? new Date(asset.fileCreatedAt).toLocaleDateString() : "";
        info.textContent = `${asset.originalFileName}${date ? " · " + date : ""}`;

        const zoom = document.createElement("button");
        zoom.className = "zoom-btn";
        zoom.textContent = "\u{1F50D}";
        zoom.title = "View full size";
        zoom.addEventListener("click", (e) => {
            e.stopPropagation();
            openLightbox(asset.id, asset.originalFileName);
        });

        div.appendChild(img);
        div.appendChild(zoom);
        div.appendChild(info);
        div.addEventListener("click", (e) => handleThumbClick(e, idx));
        grid.appendChild(div);
    });
}

// ── Lightbox ──────────────────────────────────────────────────────────────

function openLightbox(assetId, filename) {
    const overlay = document.createElement("div");
    overlay.className = "lightbox";
    overlay.innerHTML = `
        <div class="lightbox-content">
            <div class="lightbox-header">
                <span class="lightbox-filename">${filename}</span>
                <button class="lightbox-close">&times;</button>
            </div>
            <div class="lightbox-loading">Loading...</div>
            <img class="lightbox-img hidden" src="/api/fullsize/${assetId}" alt="${filename}">
        </div>
    `;
    const img = overlay.querySelector(".lightbox-img");
    const loading = overlay.querySelector(".lightbox-loading");
    img.addEventListener("load", () => {
        loading.classList.add("hidden");
        img.classList.remove("hidden");
    });
    img.addEventListener("error", () => {
        loading.textContent = "Failed to load image";
    });
    overlay.querySelector(".lightbox-close").addEventListener("click", () => overlay.remove());
    overlay.addEventListener("click", (e) => {
        if (e.target === overlay) overlay.remove();
    });
    document.addEventListener("keydown", function handler(e) {
        if (e.key === "Escape") { overlay.remove(); document.removeEventListener("keydown", handler); }
    });
    document.body.appendChild(overlay);
}

function handleThumbClick(e, idx) {
    const id = assets[idx].id;

    if (e.shiftKey && lastClickedIndex !== null) {
        const start = Math.min(lastClickedIndex, idx);
        const end = Math.max(lastClickedIndex, idx);
        for (let i = start; i <= end; i++) {
            selected.add(assets[i].id);
        }
    } else if (e.ctrlKey || e.metaKey) {
        if (selected.has(id)) selected.delete(id);
        else selected.add(id);
    } else {
        if (selected.has(id) && selected.size === 1) {
            selected.delete(id);
        } else {
            selected.clear();
            selected.add(id);
        }
    }

    lastClickedIndex = idx;
    updateSelection();
}

function updateSelection() {
    document.querySelectorAll(".thumb").forEach((el) => {
        const idx = parseInt(el.dataset.index);
        el.classList.toggle("selected", selected.has(assets[idx].id));
    });
    updateSelectedCount();
}

function updateSelectedCount() {
    btnApply.textContent = `Apply to ${selected.size} selected`;
    btnApply.disabled = selected.size === 0 || (!latInput.value && !lonInput.value);
    btnDelete.textContent = `Delete ${selected.size} selected`;
    btnDelete.disabled = selected.size === 0;
}

btnSelectAll.addEventListener("click", () => {
    assets.forEach((a) => selected.add(a.id));
    updateSelection();
});

btnDeselectAll.addEventListener("click", () => {
    selected.clear();
    updateSelection();
});

// ── Pagination ────────────────────────────────────────────────────────────

function updatePagination() {
    pageInfo.textContent = `Page ${currentPage}`;
    btnPrev.disabled = currentPage <= 1;
    btnNext.disabled = !hasNextPage;
}

btnPrev.addEventListener("click", () => { if (currentPage > 1) loadAssets(currentPage - 1); });
btnNext.addEventListener("click", () => { if (hasNextPage) loadAssets(currentPage + 1); });

// ── Location search ───────────────────────────────────────────────────────

let searchTimeout = null;

searchInput.addEventListener("input", () => {
    clearTimeout(searchTimeout);
    const q = searchInput.value.trim();
    if (q.length < 2) {
        searchResults.classList.add("hidden");
        return;
    }
    searchTimeout = setTimeout(() => searchLocation(q), 300);
});

searchInput.addEventListener("keydown", (e) => {
    const items = searchResults.querySelectorAll(".dropdown-item");
    const active = searchResults.querySelector(".active");
    let idx = Array.from(items).indexOf(active);

    if (e.key === "ArrowDown") {
        e.preventDefault();
        if (idx < items.length - 1) idx++;
        items.forEach((el, i) => el.classList.toggle("active", i === idx));
    } else if (e.key === "ArrowUp") {
        e.preventDefault();
        if (idx > 0) idx--;
        items.forEach((el, i) => el.classList.toggle("active", i === idx));
    } else if (e.key === "Enter" && active) {
        e.preventDefault();
        active.click();
    } else if (e.key === "Escape") {
        searchResults.classList.add("hidden");
    }
});

async function searchLocation(q) {
    try {
        const resp = await authFetch(`/api/search-location?q=${encodeURIComponent(q)}`);
        const results = await resp.json();
        searchResults.innerHTML = "";
        if (results.length === 0) {
            searchResults.classList.add("hidden");
            return;
        }
        results.forEach((r) => {
            const div = document.createElement("div");
            div.className = "dropdown-item";
            div.textContent = r.display;
            div.addEventListener("click", () => {
                latInput.value = r.latitude;
                lonInput.value = r.longitude;
                searchInput.value = r.display;
                saveNameInput.value = r.display;
                searchResults.classList.add("hidden");
                updateSelectedCount();
            });
            searchResults.appendChild(div);
        });
        searchResults.classList.remove("hidden");
    } catch (e) {
        console.error("Search failed:", e);
    }
}

document.addEventListener("click", (e) => {
    if (!searchInput.contains(e.target) && !searchResults.contains(e.target)) {
        searchResults.classList.add("hidden");
    }
});

// ── Saved locations ───────────────────────────────────────────────────────

async function loadSavedLocations() {
    try {
        const resp = await authFetch("/api/saved-locations");
        const locations = await resp.json();
        savedSelect.innerHTML = '<option value="">-- select --</option>';
        locations.forEach((loc) => {
            const opt = document.createElement("option");
            opt.value = JSON.stringify(loc);
            opt.textContent = `${loc.name} (${loc.latitude.toFixed(4)}, ${loc.longitude.toFixed(4)})`;
            savedSelect.appendChild(opt);
        });
    } catch (e) {
        console.error("Failed to load saved locations:", e);
    }
}

savedSelect.addEventListener("change", () => {
    if (!savedSelect.value) return;
    const loc = JSON.parse(savedSelect.value);
    latInput.value = loc.latitude;
    lonInput.value = loc.longitude;
    searchInput.value = loc.name;
    saveNameInput.value = loc.name;
    updateSelectedCount();
});

btnSaveLocation.addEventListener("click", async () => {
    const name = saveNameInput.value.trim();
    const lat = parseFloat(latInput.value);
    const lon = parseFloat(lonInput.value);
    if (!name || isNaN(lat) || isNaN(lon)) {
        toast("Enter a name and valid coordinates first", true);
        return;
    }
    try {
        await authFetch("/api/saved-locations", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ name, latitude: lat, longitude: lon }),
        });
        await loadSavedLocations();
        toast(`Saved "${name}"`);
    } catch (e) {
        toast("Failed to save location", true);
    }
});

btnDeleteSaved.addEventListener("click", async () => {
    if (!savedSelect.value) return;
    const loc = JSON.parse(savedSelect.value);
    try {
        await authFetch(`/api/saved-locations/${encodeURIComponent(loc.name)}`, { method: "DELETE" });
        await loadSavedLocations();
        toast(`Removed "${loc.name}"`);
    } catch (e) {
        toast("Failed to delete location", true);
    }
});

// ── Apply ─────────────────────────────────────────────────────────────────

latInput.addEventListener("input", updateSelectedCount);
lonInput.addEventListener("input", updateSelectedCount);

btnApply.addEventListener("click", async () => {
    const lat = parseFloat(latInput.value);
    const lon = parseFloat(lonInput.value);
    if (isNaN(lat) || isNaN(lon)) {
        toast("Enter valid coordinates", true);
        return;
    }
    if (selected.size === 0) {
        toast("Select at least one image", true);
        return;
    }

    const ids = Array.from(selected);
    btnApply.disabled = true;
    showProgress("Applying GPS", 0, ids.length);

    try {
        const resp = await authFetch("/api/apply-location", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ assetIds: ids, latitude: lat, longitude: lon }),
        });

        if (resp.headers.get("content-type")?.includes("application/x-ndjson")) {
            const reader = resp.body.getReader();
            const decoder = new TextDecoder();
            let buffer = "";
            let lastResult = null;
            while (true) {
                const { done, value } = await reader.read();
                if (done) break;
                buffer += decoder.decode(value, { stream: true });
                const lines = buffer.split("\n");
                buffer = lines.pop();
                for (const line of lines) {
                    if (!line.trim()) continue;
                    const msg = JSON.parse(line);
                    if (msg.type === "progress") {
                        showProgress("Applying GPS", msg.current, msg.total);
                    } else if (msg.type === "done") {
                        lastResult = msg;
                    } else if (msg.type === "error") {
                        throw new Error(msg.detail);
                    }
                }
            }
            if (lastResult) {
                toast(`Updated ${lastResult.updated} image(s)`);
            }
        } else {
            if (!resp.ok) {
                const err = await resp.json();
                throw new Error(err.detail || `HTTP ${resp.status}`);
            }
            const data = await resp.json();
            toast(`Updated ${data.updated} image(s)`);
        }


        assets = assets.filter((a) => !selected.has(a.id));
        selected.clear();
        renderGrid();
        updateSelectedCount();
        statusText.textContent = `${assets.length} untagged image(s) remaining`;
    } catch (e) {
        toast(`Failed: ${e.message}`, true);
    } finally {
        hideProgress();
        updateSelectedCount();
        btnApply.disabled = selected.size === 0;
    }
});

// ── Delete ────────────────────────────────────────────────────────────────

btnDelete.addEventListener("click", async () => {
    if (selected.size === 0) return;
    if (!confirm(`Delete ${selected.size} image(s)? Files will be moved to trash.`)) return;

    const ids = Array.from(selected);
    btnDelete.disabled = true;
    showProgress("Deleting", 0, ids.length);

    try {
        const resp = await authFetch("/api/delete-assets", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ assetIds: ids }),
        });

        if (resp.headers.get("content-type")?.includes("application/x-ndjson")) {
            const reader = resp.body.getReader();
            const decoder = new TextDecoder();
            let buffer = "";
            let lastResult = null;
            while (true) {
                const { done, value } = await reader.read();
                if (done) break;
                buffer += decoder.decode(value, { stream: true });
                const lines = buffer.split("\n");
                buffer = lines.pop();
                for (const line of lines) {
                    if (!line.trim()) continue;
                    const msg = JSON.parse(line);
                    if (msg.type === "progress") {
                        showProgress("Deleting", msg.current, msg.total);
                    } else if (msg.type === "done") {
                        lastResult = msg;
                    } else if (msg.type === "error") {
                        throw new Error(msg.detail);
                    }
                }
            }
            if (lastResult) {
                toast(`Deleted ${lastResult.deleted} image(s)`);
            }
        } else {
            if (!resp.ok) {
                const err = await resp.json();
                throw new Error(err.detail || `HTTP ${resp.status}`);
            }
            const data = await resp.json();
            toast(`Deleted ${data.deleted} image(s)`);
        }

        assets = assets.filter((a) => !selected.has(a.id));
        selected.clear();
        renderGrid();
        updateSelectedCount();
        statusText.textContent = `${assets.length} image(s) remaining`;
    } catch (e) {
        toast(`Failed: ${e.message}`, true);
    } finally {
        hideProgress();
        btnDelete.disabled = selected.size === 0;
    }
});

// ── Audit Log ─────────────────────────────────────────────────────────────

let auditPage = 1;
let auditHasNext = false;

async function loadAuditLog(page = 1) {
    auditPage = page;
    const logEl = document.getElementById("audit-log");
    logEl.innerHTML = '<div class="loading">Loading audit log...</div>';

    try {
        const resp = await authFetch(`/api/audit-log?page=${page}&size=50`);
        const data = await resp.json();
        auditHasNext = data.hasNextPage;

        if (data.items.length === 0) {
            logEl.innerHTML = '<div class="loading">No audit entries yet.</div>';
        } else {
            logEl.innerHTML = `<table class="audit-table">
                <thead><tr>
                    <th>Time</th><th>Action</th><th>File</th><th>Details</th><th></th>
                </tr></thead>
                <tbody>${data.items.map(renderAuditRow).join("")}</tbody>
            </table>`;

            logEl.querySelectorAll(".btn-undo").forEach((btn) => {
                btn.addEventListener("click", () => undoAction(parseInt(btn.dataset.id)));
            });
        }

        document.getElementById("audit-page-info").textContent = `Page ${auditPage}`;
        document.getElementById("audit-prev").disabled = auditPage <= 1;
        document.getElementById("audit-next").disabled = !auditHasNext;
    } catch (e) {
        logEl.innerHTML = `<div class="loading">Error: ${e.message}</div>`;
    }
}

function renderAuditRow(entry) {
    const time = new Date(entry.timestamp).toLocaleString();
    const action = entry.action === "write-gps" ? "GPS Write" : entry.action === "trash" ? "Trash" : entry.action;
    const file = entry.file_path ? entry.file_path.split("/").pop() : "-";
    let details = "";
    if (entry.action === "write-gps") {
        const oldGps = entry.old_latitude != null ? `${entry.old_latitude.toFixed(4)}, ${entry.old_longitude.toFixed(4)}` : "none";
        details = `${oldGps} -> ${entry.new_latitude.toFixed(4)}, ${entry.new_longitude.toFixed(4)}`;
    } else if (entry.action === "trash") {
        details = "Moved to trash";
    }
    const undone = entry.undone ? '<span class="undone-badge">undone</span>' : "";
    const undoBtn = entry.undone ? "" : `<button class="btn-undo" data-id="${entry.id}">Undo</button>`;

    return `<tr class="${entry.undone ? "row-undone" : ""}">
        <td>${time}</td><td>${action} ${undone}</td><td title="${entry.file_path || ""}">${file}</td><td>${details}</td><td>${undoBtn}</td>
    </tr>`;
}

async function undoAction(entryId) {
    if (!confirm("Undo this action?")) return;
    try {
        const resp = await authFetch(`/api/audit-log/${entryId}/undo`, { method: "POST" });
        if (!resp.ok) {
            const err = await resp.json();
            throw new Error(err.detail || `HTTP ${resp.status}`);
        }
        toast("Action undone");
        loadAuditLog(auditPage);
    } catch (e) {
        toast(`Undo failed: ${e.message}`, true);
    }
}

document.getElementById("audit-prev").addEventListener("click", () => { if (auditPage > 1) loadAuditLog(auditPage - 1); });
document.getElementById("audit-next").addEventListener("click", () => { if (auditHasNext) loadAuditLog(auditPage + 1); });

// ── Health ─────────────────────────────────────────────────────────────────

async function checkHealth() {
    try {
        const resp = await authFetch("/api/health");
        const status = await resp.json();
        for (const [service, state] of Object.entries(status)) {
            const el = document.getElementById(`health-${service}`);
            if (el) {
                el.className = "health-dot " + (state === "ok" ? "ok" : "error");
            }
        }
    } catch {
        document.getElementById("health-immich").className = "health-dot error";
        document.getElementById("health-exiftool").className = "health-dot error";
    }
}

checkHealth();
setInterval(checkHealth, 30000);

// ── Batch Tag ─────────────────────────────────────────────────────────────

let batchDirs = null;
let batchPage = 1;
const BATCH_PAGE_SIZE = 20;

async function loadBatchDirectories() {
    const listEl = document.getElementById("batch-list");
    listEl.innerHTML = '<div class="loading">Loading directories...</div>';
    try {
        const resp = await authFetch("/api/batch-directories");
        batchDirs = await resp.json();
        batchPage = 1;
        renderBatchList();
    } catch (e) {
        listEl.innerHTML = `<div class="loading">Error: ${e.message}</div>`;
    }
}

function getFilteredBatchDirs() {
    if (!batchDirs) return [];
    const filter = document.getElementById("batch-filter").value;
    let filtered = batchDirs.filter((d) => d.file_count > 0);
    if (filter === "with-location") filtered = filtered.filter((d) => d.lat != null);
    else if (filter === "no-location") filtered = filtered.filter((d) => d.lat == null);
    // Sort: untagged first (by % complete ascending), fully tagged last
    filtered.sort((a, b) => {
        const pctA = a.file_count > 0 ? a.tagged_count / a.file_count : 0;
        const pctB = b.file_count > 0 ? b.tagged_count / b.file_count : 0;
        return pctA - pctB;
    });
    return filtered;
}

function renderBatchList() {
    const listEl = document.getElementById("batch-list");
    const filtered = getFilteredBatchDirs();

    if (filtered.length === 0) {
        listEl.innerHTML = '<div class="loading">No directories match filter.</div>';
        return;
    }

    const totalPages = Math.ceil(filtered.length / BATCH_PAGE_SIZE);
    const start = (batchPage - 1) * BATCH_PAGE_SIZE;
    const page = filtered.slice(start, start + BATCH_PAGE_SIZE);

    let html = page.map((d) => {
        const hasLoc = d.lat != null;
        const locText = d.location || "No location";
        const dateText = d.date || "No date";
        const coordsText = hasLoc ? `${d.lat.toFixed(4)}, ${d.lon.toFixed(4)}` : "\u2014";
        const encodedDir = encodeURIComponent(d.directory);

        const previews = (d.preview_files || []).map((f) =>
            `<img class="batch-thumb" src="/api/batch-preview/${encodedDir}/${encodeURIComponent(f)}" alt="${f}" loading="lazy">`
        ).join("");

        const pct = d.file_count > 0 ? Math.round((d.tagged_count / d.file_count) * 100) : 0;
        const isDone = d.tagged_count >= d.file_count && d.file_count > 0;
        const barColor = isDone ? "#27ae60" : pct > 0 ? "#e67e22" : "#7f8c8d";

        return `<div class="batch-row ${isDone ? "batch-row-done" : ""}" data-dir="${encodedDir}">
            <div class="batch-previews">${previews}</div>
            <div class="batch-info">
                <div class="batch-dirname">${d.directory}</div>
                <div class="batch-meta">
                    <span class="batch-files ${isDone ? 'batch-done' : ''}">${d.tagged_count}/${d.file_count} tagged</span>${d.disk_count !== d.file_count ? `<span class="batch-mismatch" title="DB: ${d.file_count}, Disk: ${d.disk_count}">sync</span>` : ""}
                    <span class="batch-date">${dateText}</span>
                    <span class="batch-location">${locText}</span>
                    <span class="batch-coords">${coordsText}</span>
                </div>
                <div class="batch-progress-track"><div class="batch-progress-fill" style="width:${pct}%;background:${barColor}"></div></div>
            </div>
            <div class="batch-actions">
                <button class="btn-batch-tag" data-dir="${encodedDir}" data-location="${d.location || ""}" data-lat="${d.lat ?? ""}" data-lon="${d.lon ?? ""}">Tag</button>
            </div>
        </div>`;
    }).join("");

    html += `<div class="pagination">
        <button id="batch-prev" ${batchPage <= 1 ? "disabled" : ""}>&larr; Previous</button>
        <span>Page ${batchPage} of ${totalPages} (${filtered.length} directories)</span>
        <button id="batch-next" ${batchPage >= totalPages ? "disabled" : ""}>Next &rarr;</button>
    </div>`;

    listEl.innerHTML = html;

    listEl.querySelectorAll(".btn-batch-tag").forEach((btn) => {
        btn.addEventListener("click", () => {
            const dir = decodeURIComponent(btn.dataset.dir);
            const loc = btn.dataset.location || null;
            const lat = btn.dataset.lat ? parseFloat(btn.dataset.lat) : null;
            const lon = btn.dataset.lon ? parseFloat(btn.dataset.lon) : null;
            enterDirectoryMode(dir, loc, lat, lon);
        });
    });

    const prevBtn = document.getElementById("batch-prev");
    const nextBtn = document.getElementById("batch-next");
    if (prevBtn) prevBtn.addEventListener("click", () => { batchPage--; renderBatchList(); });
    if (nextBtn) nextBtn.addEventListener("click", () => { batchPage++; renderBatchList(); });
}

async function applyBatchGPS(dirName) {
    const dir = batchDirs.find((d) => d.directory === dirName);
    if (!dir || dir.lat == null) return;

    if (!confirm(`Apply GPS (${dir.lat.toFixed(4)}, ${dir.lon.toFixed(4)}) to all ${dir.file_count} files in "${dirName}"?`)) return;

    showProgress("Applying GPS to " + dirName, 0, dir.file_count);

    try {
        const resp = await authFetch("/api/batch-apply", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ directory: dirName, latitude: dir.lat, longitude: dir.lon }),
        });

        const reader = resp.body.getReader();
        const decoder = new TextDecoder();
        let buffer = "";
        let lastResult = null;
        while (true) {
            const { done, value } = await reader.read();
            if (done) break;
            buffer += decoder.decode(value, { stream: true });
            const lines = buffer.split("\n");
            buffer = lines.pop();
            for (const line of lines) {
                if (!line.trim()) continue;
                const msg = JSON.parse(line);
                if (msg.type === "progress") {
                    showProgress("Applying GPS to " + dirName, msg.current, msg.total);
                } else if (msg.type === "done") {
                    lastResult = msg;
                }
            }
        }

        if (lastResult) {
            const errText = lastResult.errors > 0 ? ` (${lastResult.errors} errors)` : "";
            toast(`Applied GPS to ${lastResult.updated} files in "${dirName}"${errText}`);
        }

        loadBatchDirectories();
    } catch (e) {
        toast(`Failed: ${e.message}`, true);
    } finally {
        hideProgress();
    }
}

document.getElementById("batch-filter").addEventListener("change", () => { batchPage = 1; renderBatchList(); });

// ── Init ──────────────────────────────────────────────────────────────────

loadAssets(1);
loadSavedLocations();

const grid = document.getElementById("grid");
const latInput = document.getElementById("lat");
const lonInput = document.getElementById("lon");
const searchInput = document.getElementById("location-search");
const searchResults = document.getElementById("search-results");
const savedSelect = document.getElementById("saved-locations");
const btnApply = document.getElementById("btn-apply");
const btnSelectAll = document.getElementById("btn-select-all");
const btnDeselectAll = document.getElementById("btn-deselect-all");
const btnSaveLocation = document.getElementById("btn-save-location");
const btnDeleteSaved = document.getElementById("btn-delete-saved");
const saveNameInput = document.getElementById("save-name");
const selectedCountEl = document.getElementById("selected-count");
const statusText = document.getElementById("status-text");
const pageInfo = document.getElementById("page-info");
const btnPrev = document.getElementById("btn-prev");
const btnNext = document.getElementById("btn-next");
const toastEl = document.getElementById("toast");

let currentPage = 1;
let assets = [];
let selected = new Set();
let lastClickedIndex = null;
let hasNextPage = false;

// ── Toast ─────────────────────────────────────────────────────────────────

function toast(msg, isError = false) {
    toastEl.textContent = msg;
    toastEl.className = "toast" + (isError ? " error" : "");
    setTimeout(() => toastEl.classList.add("hidden"), 3000);
}

// ── Assets ────────────────────────────────────────────────────────────────

async function loadAssets(page = 1) {
    currentPage = page;
    selected.clear();
    updateSelectedCount();
    grid.innerHTML = '<div class="loading">Loading...</div>';

    try {
        const resp = await fetch(`/api/assets?page=${page}&size=50`);
        if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
        const data = await resp.json();
        assets = data.items;
        hasNextPage = data.hasNextPage;
        renderGrid();
        updatePagination();
        statusText.textContent = `${assets.length} untagged image(s) on this page`;
    } catch (e) {
        grid.innerHTML = `<div class="loading">Error loading assets: ${e.message}</div>`;
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

        div.appendChild(img);
        div.appendChild(info);
        div.addEventListener("click", (e) => handleThumbClick(e, idx));
        grid.appendChild(div);
    });
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
    selectedCountEl.textContent = selected.size;
    btnApply.disabled = selected.size === 0 || (!latInput.value && !lonInput.value);
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
        const resp = await fetch(`/api/search-location?q=${encodeURIComponent(q)}`);
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
        const resp = await fetch("/api/saved-locations");
        const locations = await resp.json();
        savedSelect.innerHTML = '<option value="">— select —</option>';
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
        await fetch("/api/saved-locations", {
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
        await fetch(`/api/saved-locations/${encodeURIComponent(loc.name)}`, { method: "DELETE" });
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

    btnApply.disabled = true;
    btnApply.textContent = "Applying...";

    try {
        const resp = await fetch("/api/apply-location", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
                assetIds: Array.from(selected),
                latitude: lat,
                longitude: lon,
            }),
        });
        if (!resp.ok) {
            const err = await resp.json();
            throw new Error(err.detail || `HTTP ${resp.status}`);
        }
        const data = await resp.json();
        toast(`Updated ${data.updated} image(s)`);

        // Auto-save location if name is provided
        const name = saveNameInput.value.trim();
        if (name) {
            await fetch("/api/saved-locations", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ name, latitude: lat, longitude: lon }),
            });
            await loadSavedLocations();
        }

        // Remove tagged images from the grid
        assets = assets.filter((a) => !selected.has(a.id));
        selected.clear();
        renderGrid();
        updateSelectedCount();
        statusText.textContent = `${assets.length} untagged image(s) remaining`;
    } catch (e) {
        toast(`Failed: ${e.message}`, true);
    } finally {
        btnApply.textContent = `Apply to ${selected.size} selected`;
        btnApply.disabled = selected.size === 0;
    }
});

// ── Health ─────────────────────────────────────────────────────────────────

async function checkHealth() {
    try {
        const resp = await fetch("/api/health");
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

// ── Init ──────────────────────────────────────────────────────────────────

loadAssets(1);
loadSavedLocations();

import hashlib
import json
import logging
import os
import secrets
import sqlite3
import time
from pathlib import Path

import httpx
from dotenv import load_dotenv
from fastapi import Cookie, FastAPI, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

load_dotenv()

IMMICH_URL = os.environ["IMMICH_URL"].rstrip("/")
IMMICH_API_KEY = os.environ["IMMICH_API_KEY"]
EXIFTOOL_URL = os.environ["EXIFTOOL_URL"].rstrip("/")
EXIFTOOL_API_KEY = os.environ["EXIFTOOL_API_KEY"]
IMMICH_PHOTOS_PREFIX = os.environ.get("IMMICH_PHOTOS_PREFIX", "")
EXIFTOOL_PHOTOS_PREFIX = os.environ.get("EXIFTOOL_PHOTOS_PREFIX", "")
LOGIN_USER = os.environ.get("LOGIN_USER", "admin")
LOGIN_PASS = os.environ.get("LOGIN_PASS", "changeme")
SAVED_LOCATIONS_FILE = Path("saved_locations.json")
AUDIT_DB_PATH = os.environ.get("AUDIT_DB_PATH", "/data/audit.db")

app = FastAPI()

immich_headers = {"x-api-key": IMMICH_API_KEY}

# ── Session store ─────────────────────────────────────────────────────────────

_sessions: dict[str, float] = {}
SESSION_TTL = 86400 * 7  # 7 days


def _check_session(session_id: str | None):
    if not session_id or session_id not in _sessions:
        raise HTTPException(status_code=401, detail="Not authenticated")
    if time.time() - _sessions[session_id] > SESSION_TTL:
        _sessions.pop(session_id, None)
        raise HTTPException(status_code=401, detail="Session expired")


# ── Audit DB ──────────────────────────────────────────────────────────────────


def _get_audit_db() -> sqlite3.Connection:
    db = sqlite3.connect(AUDIT_DB_PATH)
    db.row_factory = sqlite3.Row
    db.execute("""
        CREATE TABLE IF NOT EXISTS audit_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT NOT NULL,
            action TEXT NOT NULL,
            asset_id TEXT,
            file_path TEXT,
            old_latitude REAL,
            old_longitude REAL,
            new_latitude REAL,
            new_longitude REAL,
            undone INTEGER DEFAULT 0,
            details TEXT
        )
    """)
    db.commit()
    return db


def _log_action(action: str, asset_id: str, file_path: str,
                old_lat=None, old_lon=None, new_lat=None, new_lon=None, details=None):
    from datetime import datetime, timezone
    db = _get_audit_db()
    db.execute(
        "INSERT INTO audit_log (timestamp, action, asset_id, file_path, old_latitude, old_longitude, new_latitude, new_longitude, details) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (datetime.now(timezone.utc).isoformat(), action, asset_id, file_path,
         old_lat, old_lon, new_lat, new_lon, details),
    )
    db.commit()
    db.close()


# ── Helpers ───────────────────────────────────────────────────────────────────


def immich_client() -> httpx.AsyncClient:
    return httpx.AsyncClient(
        base_url=IMMICH_URL, headers=immich_headers, timeout=30.0
    )


def _remap_path(original_path: str) -> str:
    if IMMICH_PHOTOS_PREFIX and EXIFTOOL_PHOTOS_PREFIX:
        return original_path.replace(IMMICH_PHOTOS_PREFIX, EXIFTOOL_PHOTOS_PREFIX, 1)
    return original_path


# ── Auth ──────────────────────────────────────────────────────────────────────

LOGIN_PAGE = """<!DOCTYPE html>
<html><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Login - EXIF Location Tagger</title>
<style>
body { font-family: system-ui; background: #1a1a2e; color: #e0e0e0; display: flex; align-items: center; justify-content: center; min-height: 100vh; margin: 0; }
.login-box { background: #16213e; padding: 2rem; border-radius: 8px; width: 320px; }
.login-box h1 { font-size: 1.2rem; margin: 0 0 1.5rem; text-align: center; }
.login-box input { width: 100%; padding: 0.5rem; margin: 0.3rem 0 0.8rem; border: 1px solid #333; border-radius: 4px; background: #0f3460; color: #e0e0e0; box-sizing: border-box; }
.login-box button { width: 100%; padding: 0.6rem; background: #e94560; border: none; border-radius: 4px; color: white; cursor: pointer; font-size: 1rem; }
.login-box button:hover { background: #c73650; }
.error { color: #e94560; font-size: 0.85rem; text-align: center; margin-top: 0.5rem; display: none; }
</style></head><body>
<div class="login-box">
<h1>EXIF Location Tagger</h1>
<form id="login-form">
<input type="text" name="username" placeholder="Username" required autofocus>
<input type="password" name="password" placeholder="Password" required>
<button type="submit">Log in</button>
<div class="error" id="error">Invalid username or password</div>
</form>
</div>
<script>
document.getElementById("login-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    const form = new FormData(e.target);
    const resp = await fetch("/auth/login", {
        method: "POST",
        headers: {"Content-Type": "application/json"},
        body: JSON.stringify({username: form.get("username"), password: form.get("password")}),
    });
    if (resp.ok) { window.location.href = "/"; }
    else { document.getElementById("error").style.display = "block"; }
});
</script></body></html>"""


class LoginRequest(BaseModel):
    username: str
    password: str


@app.get("/login", response_class=HTMLResponse)
async def login_page():
    return LOGIN_PAGE


@app.post("/auth/login")
async def login(req: LoginRequest):
    if req.username == LOGIN_USER and req.password == LOGIN_PASS:
        session_id = secrets.token_urlsafe(32)
        _sessions[session_id] = time.time()
        resp = JSONResponse({"status": "ok"})
        resp.set_cookie("session", session_id, httponly=True, max_age=SESSION_TTL, samesite="lax")
        return resp
    raise HTTPException(status_code=401, detail="Invalid credentials")


@app.post("/auth/logout")
async def logout(session: str | None = Cookie(None)):
    _sessions.pop(session, None)
    resp = JSONResponse({"status": "ok"})
    resp.delete_cookie("session")
    return resp


# ── Auth middleware ────────────────────────────────────────────────────────────


@app.middleware("http")
async def auth_middleware(request: Request, call_next):
    path = request.url.path
    if path in ("/login", "/auth/login") or path.startswith("/auth/"):
        return await call_next(request)
    session_id = request.cookies.get("session")
    if not session_id or session_id not in _sessions:
        if path.startswith("/api/"):
            return JSONResponse({"detail": "Not authenticated"}, status_code=401)
        return HTMLResponse(LOGIN_PAGE, status_code=200)
    if time.time() - _sessions[session_id] > SESSION_TTL:
        _sessions.pop(session_id, None)
        if path.startswith("/api/"):
            return JSONResponse({"detail": "Session expired"}, status_code=401)
        return HTMLResponse(LOGIN_PAGE, status_code=200)
    return await call_next(request)


# ── Health ────────────────────────────────────────────────────────────────────


@app.get("/api/health")
async def health():
    status = {"immich": "unknown", "exiftool": "unknown"}
    async with immich_client() as client:
        try:
            r = await client.get("/api/server/version")
            status["immich"] = "ok" if r.status_code == 200 else f"error ({r.status_code})"
        except Exception as e:
            status["immich"] = f"error ({e})"
    async with httpx.AsyncClient(timeout=5.0) as client:
        try:
            r = await client.get(f"{EXIFTOOL_URL}/health")
            status["exiftool"] = "ok" if r.status_code == 200 else f"error ({r.status_code})"
        except Exception as e:
            status["exiftool"] = f"error ({e})"
    return status


# ── Assets ───────────────────────────────────────────────────────────────────


@app.get("/api/assets")
async def get_untagged_assets(
    page: int = Query(1, ge=1),
    size: int = Query(50, ge=1, le=200),
    directory: str = Query(None),
):
    untagged: list[dict] = []
    batch_size = size * 3
    max_immich_pages = 20

    # Build directory prefix filter for Immich paths
    dir_prefix = None
    if directory:
        dir_prefix = f"/mnt/external-photos/Import-Staging/{directory}/"

    seen_ids: set[str] = set()

    async with immich_client() as client:
        skip_count = (page - 1) * size
        skipped = 0
        immich_pg = 1

        while len(untagged) < size and immich_pg <= max_immich_pages:
            search_body: dict = {
                "page": immich_pg,
                "size": batch_size,
                "withExif": True,
            }
            if dir_prefix:
                search_body["originalPath"] = dir_prefix

            resp = await client.post(
                "/api/search/metadata",
                json=search_body,
            )
            resp.raise_for_status()
            data = resp.json()

            assets_data = data.get("assets", data)
            if isinstance(assets_data, dict):
                assets = assets_data.get("items", [])
            elif isinstance(assets_data, list):
                assets = assets_data
            else:
                assets = []

            if not assets:
                break

            for a in assets:
                aid = a["id"]
                if aid in seen_ids:
                    continue
                seen_ids.add(aid)

                orig_path = a.get("originalPath", "")
                if dir_prefix and not orig_path.startswith(dir_prefix):
                    continue

                exif = a.get("exifInfo") or {}
                lat = exif.get("latitude")
                lon = exif.get("longitude")
                if lat is None or lon is None or (lat == 0 and lon == 0):
                    if skipped < skip_count:
                        skipped += 1
                        continue
                    untagged.append(
                        {
                            "id": aid,
                            "originalFileName": a.get("originalFileName", ""),
                            "originalPath": orig_path,
                            "fileCreatedAt": a.get("fileCreatedAt", ""),
                        }
                    )
                    if len(untagged) >= size:
                        break

            if len(assets) < batch_size:
                break
            immich_pg += 1

    has_next = len(untagged) == size
    return {"items": untagged, "page": page, "hasNextPage": has_next}


# ── Thumbnail proxy ──────────────────────────────────────────────────────────


@app.get("/api/thumbnail/{asset_id}")
async def proxy_thumbnail(asset_id: str):
    async with immich_client() as client:
        resp = await client.get(f"/api/assets/{asset_id}/thumbnail")
        if resp.status_code != 200:
            raise HTTPException(status_code=resp.status_code, detail="Thumbnail fetch failed")
        return Response(
            content=resp.content,
            media_type=resp.headers.get("content-type", "image/jpeg"),
            headers={"Cache-Control": "public, max-age=86400"},
        )


@app.get("/api/fullsize/{asset_id}")
async def proxy_fullsize(asset_id: str):
    async with immich_client() as client:
        resp = await client.get(f"/api/assets/{asset_id}/original")
        if resp.status_code != 200:
            raise HTTPException(status_code=resp.status_code, detail="Original fetch failed")
        return Response(
            content=resp.content,
            media_type=resp.headers.get("content-type", "image/jpeg"),
            headers={"Cache-Control": "public, max-age=86400"},
        )


# ── Location search (Nominatim) ────────────────────────────────────────────


@app.get("/api/search-location")
async def search_location(q: str = Query(..., min_length=2)):
    async with httpx.AsyncClient(timeout=10.0, headers={"User-Agent": "yolo-images/1.0"}) as client:
        resp = await client.get(
            "https://nominatim.openstreetmap.org/search",
            params={"q": q, "format": "json", "limit": 7, "addressdetails": 1},
        )
        resp.raise_for_status()
        data = resp.json()

    results = []
    for place in data:
        lat = place.get("lat")
        lon = place.get("lon")
        if lat is None or lon is None:
            continue
        results.append(
            {
                "display": place.get("display_name", ""),
                "latitude": float(lat),
                "longitude": float(lon),
            }
        )
    return results


# ── Saved locations ──────────────────────────────────────────────────────────


def _load_saved() -> list[dict]:
    if SAVED_LOCATIONS_FILE.exists():
        return json.loads(SAVED_LOCATIONS_FILE.read_text())
    return []


def _save_saved(locations: list[dict]):
    SAVED_LOCATIONS_FILE.write_text(json.dumps(locations, indent=2))


@app.get("/api/saved-locations")
async def get_saved_locations():
    return _load_saved()


class SavedLocation(BaseModel):
    name: str
    latitude: float
    longitude: float


@app.post("/api/saved-locations")
async def add_saved_location(loc: SavedLocation):
    locations = _load_saved()
    if not any(l["name"] == loc.name for l in locations):
        locations.append(loc.model_dump())
        _save_saved(locations)
    return locations


@app.delete("/api/saved-locations/{name}")
async def delete_saved_location(name: str):
    locations = _load_saved()
    locations = [l for l in locations if l["name"] != name]
    _save_saved(locations)
    return locations


# ── Apply location ───────────────────────────────────────────────────────────


class ApplyRequest(BaseModel):
    assetIds: list[str]
    latitude: float
    longitude: float


@app.post("/api/apply-location")
async def apply_location(req: ApplyRequest):
    async def stream():
        errors = []
        total = len(req.assetIds)
        async with immich_client() as immich, httpx.AsyncClient(timeout=30.0) as exif_client:
            for i, asset_id in enumerate(req.assetIds):
                yield json.dumps({"type": "progress", "current": i + 1, "total": total}) + "\n"

                resp = await immich.get(f"/api/assets/{asset_id}")
                if resp.status_code != 200:
                    errors.append(f"{asset_id}: couldn't fetch asset info")
                    continue
                asset_data = resp.json()
                original_path = asset_data.get("originalPath", "")
                if not original_path:
                    errors.append(f"{asset_id}: no originalPath")
                    continue

                # Read current GPS before writing
                exif = asset_data.get("exifInfo") or {}
                old_lat = exif.get("latitude")
                old_lon = exif.get("longitude")

                file_path = _remap_path(original_path)

                exif_resp = await exif_client.post(
                    f"{EXIFTOOL_URL}/write-gps",
                    json={
                        "api_key": EXIFTOOL_API_KEY,
                        "file_path": file_path,
                        "latitude": req.latitude,
                        "longitude": req.longitude,
                    },
                )
                if exif_resp.status_code != 200:
                    errors.append(f"{asset_id}: exiftool error {exif_resp.text}")
                else:
                    logger.info("Wrote GPS to %s: %s", original_path, exif_resp.json())
                    _log_action("write-gps", asset_id, original_path,
                                old_lat, old_lon, req.latitude, req.longitude)

        if errors:
            logger.error("Apply errors: %s", errors)
            yield json.dumps({"type": "error", "detail": f"Failed: {'; '.join(errors)}"}) + "\n"
            return

        async with immich_client() as immich:
            libs = await immich.get("/api/libraries")
            for lib in libs.json():
                await immich.post(f"/api/libraries/{lib['id']}/scan")
                logger.info("Triggered scan for library %s", lib["id"])

        yield json.dumps({"type": "done", "updated": total}) + "\n"

    return StreamingResponse(stream(), media_type="application/x-ndjson")


# ── Check file types ──────────────────────────────────────────────────────────


class CheckTypesRequest(BaseModel):
    originalPaths: list[str]


@app.post("/api/check-types")
async def check_types(req: CheckTypesRequest):
    file_paths = [_remap_path(p) for p in req.originalPaths]
    async with httpx.AsyncClient(timeout=60.0) as client:
        resp = await client.post(
            f"{EXIFTOOL_URL}/check-types",
            json={"api_key": EXIFTOOL_API_KEY, "file_paths": file_paths},
        )
        if resp.status_code != 200:
            raise HTTPException(status_code=500, detail=resp.text)
        return resp.json()


# ── Delete assets ────────────────────────────────────────────────────────────


class DeleteRequest(BaseModel):
    assetIds: list[str]


@app.post("/api/delete-assets")
async def delete_assets(req: DeleteRequest):
    async def stream():
        errors = []
        total = len(req.assetIds)
        async with immich_client() as immich, httpx.AsyncClient(timeout=30.0) as exif_client:
            for i, asset_id in enumerate(req.assetIds):
                yield json.dumps({"type": "progress", "current": i + 1, "total": total}) + "\n"

                resp = await immich.get(f"/api/assets/{asset_id}")
                if resp.status_code != 200:
                    errors.append(f"{asset_id}: couldn't fetch asset info")
                    continue
                original_path = resp.json().get("originalPath", "")
                if not original_path:
                    errors.append(f"{asset_id}: no originalPath")
                    continue

                file_path = _remap_path(original_path)

                trash_resp = await exif_client.post(
                    f"{EXIFTOOL_URL}/trash-file",
                    json={
                        "api_key": EXIFTOOL_API_KEY,
                        "file_path": file_path,
                    },
                )
                if trash_resp.status_code != 200:
                    errors.append(f"{asset_id}: trash error {trash_resp.text}")
                else:
                    trash_data = trash_resp.json()
                    logger.info("Trashed %s", original_path)
                    _log_action("trash", asset_id, original_path,
                                details=json.dumps({"trash_path": trash_data.get("trash_path")}))

        if errors:
            logger.error("Delete errors: %s", errors)
            yield json.dumps({"type": "error", "detail": f"Failed: {'; '.join(errors)}"}) + "\n"
            return

        async with immich_client() as immich:
            libs = await immich.get("/api/libraries")
            for lib in libs.json():
                await immich.post(f"/api/libraries/{lib['id']}/scan")
                logger.info("Triggered scan for library %s", lib["id"])

        yield json.dumps({"type": "done", "deleted": total}) + "\n"

    return StreamingResponse(stream(), media_type="application/x-ndjson")


# ── Audit log ─────────────────────────────────────────────────────────────────


@app.get("/api/audit-log")
async def get_audit_log(page: int = Query(1, ge=1), size: int = Query(50, ge=1, le=200)):
    db = _get_audit_db()
    offset = (page - 1) * size
    rows = db.execute(
        "SELECT * FROM audit_log ORDER BY timestamp DESC LIMIT ? OFFSET ?",
        (size + 1, offset),
    ).fetchall()
    db.close()
    has_next = len(rows) > size
    items = [dict(r) for r in rows[:size]]
    return {"items": items, "page": page, "hasNextPage": has_next}


@app.post("/api/audit-log/{entry_id}/undo")
async def undo_action(entry_id: int):
    db = _get_audit_db()
    row = db.execute("SELECT * FROM audit_log WHERE id = ?", (entry_id,)).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Audit entry not found")
    if row["undone"]:
        raise HTTPException(status_code=400, detail="Already undone")

    entry = dict(row)
    action = entry["action"]

    if action == "write-gps":
        file_path = _remap_path(entry["file_path"])
        async with httpx.AsyncClient(timeout=30.0) as exif_client:
            if entry["old_latitude"] is not None and entry["old_longitude"] is not None:
                # Restore old GPS
                resp = await exif_client.post(
                    f"{EXIFTOOL_URL}/write-gps",
                    json={
                        "api_key": EXIFTOOL_API_KEY,
                        "file_path": file_path,
                        "latitude": entry["old_latitude"],
                        "longitude": entry["old_longitude"],
                    },
                )
            else:
                # Remove GPS entirely
                resp = await exif_client.post(
                    f"{EXIFTOOL_URL}/remove-gps",
                    json={
                        "api_key": EXIFTOOL_API_KEY,
                        "file_path": file_path,
                    },
                )
            if resp.status_code != 200:
                raise HTTPException(status_code=500, detail=f"Exiftool error: {resp.text}")

        db.execute("UPDATE audit_log SET undone = 1 WHERE id = ?", (entry_id,))
        db.commit()
        db.close()

        # Trigger library scan
        async with immich_client() as immich:
            libs = await immich.get("/api/libraries")
            for lib in libs.json():
                await immich.post(f"/api/libraries/{lib['id']}/scan")

        return {"status": "ok", "action": "restored_gps"}

    elif action == "trash":
        details = json.loads(entry["details"]) if entry["details"] else {}
        trash_path = details.get("trash_path")
        if not trash_path:
            raise HTTPException(status_code=400, detail="No trash path recorded")

        async with httpx.AsyncClient(timeout=30.0) as exif_client:
            resp = await exif_client.post(
                f"{EXIFTOOL_URL}/restore-file",
                json={
                    "api_key": EXIFTOOL_API_KEY,
                    "trash_path": trash_path,
                    "original_path": _remap_path(entry["file_path"]),
                },
            )
            if resp.status_code != 200:
                raise HTTPException(status_code=500, detail=f"Restore error: {resp.text}")

        db.execute("UPDATE audit_log SET undone = 1 WHERE id = ?", (entry_id,))
        db.commit()
        db.close()

        async with immich_client() as immich:
            libs = await immich.get("/api/libraries")
            for lib in libs.json():
                await immich.post(f"/api/libraries/{lib['id']}/scan")

        return {"status": "ok", "action": "restored_file"}

    db.close()
    raise HTTPException(status_code=400, detail=f"Cannot undo action type: {action}")


# ── Batch tag directories ─────────────────────────────────────────────────────

DIRECTORY_METADATA_FILE = Path("directory_metadata.json")


def _load_dir_metadata() -> list[dict]:
    if DIRECTORY_METADATA_FILE.exists():
        return json.loads(DIRECTORY_METADATA_FILE.read_text())
    return []


def _save_dir_metadata(data: list[dict]):
    DIRECTORY_METADATA_FILE.write_text(json.dumps(data, indent=2))


COPY_PROGRESS_DB = os.environ.get(
    "COPY_PROGRESS_DB", "/data/copy_progress.db"
)


@app.get("/api/batch-directories")
async def get_batch_directories():
    dirs = _load_dir_metadata()

    # Query DB for per-directory counts (source of truth)
    dir_stats: dict[str, dict] = {}
    try:
        db = sqlite3.connect(COPY_PROGRESS_DB)
        rows = db.execute(
            "SELECT dest_path, has_gps_data FROM file_copies WHERE status = 'completed' AND dest_path IS NOT NULL"
        ).fetchall()
        db.close()
        prefix = EXIFTOOL_PHOTOS_PREFIX + "/Import-Staging/"
        for dest_path, has_gps in rows:
            if not dest_path.startswith(prefix):
                continue
            rel = dest_path[len(prefix):]
            dirname = rel.split("/")[0]
            if dirname not in dir_stats:
                dir_stats[dirname] = {"total": 0, "tagged": 0}
            dir_stats[dirname]["total"] += 1
            if has_gps:
                dir_stats[dirname]["tagged"] += 1
    except Exception:
        pass

    for d in dirs:
        dir_path = os.path.join(
            EXIFTOOL_PHOTOS_PREFIX, "Import-Staging", d["directory"]
        )
        stats = dir_stats.get(d["directory"], {"total": 0, "tagged": 0})
        d["file_count"] = stats["total"]
        d["tagged_count"] = stats["tagged"]

        # Disk file count as checksum
        try:
            disk_count = sum(1 for _, _, files in os.walk(dir_path) for _ in files)
            d["disk_count"] = disk_count
        except FileNotFoundError:
            d["disk_count"] = 0

        # Preview thumbnails from top-level files
        try:
            top_files = sorted([
                f for f in os.listdir(dir_path)
                if os.path.isfile(os.path.join(dir_path, f))
            ])
            d["preview_files"] = top_files[:6]
        except FileNotFoundError:
            d["preview_files"] = []
    return dirs


@app.get("/api/batch-preview/{directory}/{filename}")
async def batch_preview(directory: str, filename: str):
    """Serve a file from Import-Staging as a preview image."""
    dir_path = os.path.join(
        EXIFTOOL_PHOTOS_PREFIX, "Import-Staging", directory
    )
    file_path = os.path.join(dir_path, filename)
    resolved = os.path.realpath(file_path)
    base = os.path.realpath(os.path.join(EXIFTOOL_PHOTOS_PREFIX, "Import-Staging"))
    if not resolved.startswith(base):
        raise HTTPException(status_code=403, detail="Path outside allowed base")
    if not os.path.isfile(resolved):
        raise HTTPException(status_code=404, detail="File not found")

    ext = os.path.splitext(filename)[1].lower()
    content_types = {
        ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
        ".png": "image/png", ".heic": "image/heic",
        ".gif": "image/gif", ".webp": "image/webp",
    }
    ct = content_types.get(ext, "image/jpeg")

    with open(resolved, "rb") as f:
        content = f.read()
    return Response(
        content=content,
        media_type=ct,
        headers={"Cache-Control": "public, max-age=86400"},
    )


class BatchApplyRequest(BaseModel):
    directory: str
    latitude: float | None = None
    longitude: float | None = None


@app.post("/api/batch-apply")
async def batch_apply(req: BatchApplyRequest):
    if req.latitude is None or req.longitude is None:
        raise HTTPException(status_code=400, detail="Latitude and longitude required")

    # Get file list from DB (source of truth)
    prefix = EXIFTOOL_PHOTOS_PREFIX + "/Import-Staging/" + req.directory + "/"
    db = sqlite3.connect(COPY_PROGRESS_DB)
    rows = db.execute(
        "SELECT dest_path FROM file_copies WHERE status = 'completed' AND dest_path LIKE ? AND (has_gps_data = 0 OR has_gps_data IS NULL)",
        (prefix + "%",),
    ).fetchall()
    db.close()

    file_paths = [r[0] for r in rows]
    if not file_paths:
        raise HTTPException(status_code=404, detail="No untagged files found in directory")

    async def stream():
        total = len(file_paths)
        errors = []
        updated = 0

        async with httpx.AsyncClient(timeout=30.0) as exif_client:
            for i, file_path in enumerate(sorted(file_paths)):
                yield json.dumps({"type": "progress", "current": i + 1, "total": total}) + "\n"

                # Verify file exists on disk
                if not os.path.isfile(file_path):
                    errors.append(f"{os.path.basename(file_path)}: file missing from disk")
                    continue

                resp = await exif_client.post(
                    f"{EXIFTOOL_URL}/write-gps",
                    json={
                        "api_key": EXIFTOOL_API_KEY,
                        "file_path": file_path,
                        "latitude": req.latitude,
                        "longitude": req.longitude,
                    },
                )
                if resp.status_code != 200:
                    errors.append(f"{os.path.basename(file_path)}: {resp.text}")
                else:
                    updated += 1
                    _log_action(
                        "batch-write-gps", req.directory, file_path,
                        None, None, req.latitude, req.longitude,
                        details=json.dumps({"directory": req.directory}),
                    )

        if errors:
            logger.error("Batch apply errors for %s: %s", req.directory, errors)

        # Trigger Immich library scan
        async with immich_client() as immich:
            libs = await immich.get("/api/libraries")
            for lib in libs.json():
                await immich.post(f"/api/libraries/{lib['id']}/scan")

        yield json.dumps({
            "type": "done",
            "updated": updated,
            "errors": len(errors),
            "error_details": errors[:5],
        }) + "\n"

    return StreamingResponse(stream(), media_type="application/x-ndjson")


class BatchUpdateMetadataRequest(BaseModel):
    directory: str
    location: str | None = None
    lat: float | None = None
    lon: float | None = None


@app.post("/api/batch-directories/update")
async def update_batch_directory(req: BatchUpdateMetadataRequest):
    dirs = _load_dir_metadata()
    for d in dirs:
        if d["directory"] == req.directory:
            if req.location is not None:
                d["location"] = req.location
            if req.lat is not None:
                d["lat"] = req.lat
            if req.lon is not None:
                d["lon"] = req.lon
            break
    _save_dir_metadata(dirs)
    return {"status": "ok"}


# ── Static files (must be last) ──────────────────────────────────────────────

app.mount("/", StaticFiles(directory="static", html=True), name="static")

if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8050)

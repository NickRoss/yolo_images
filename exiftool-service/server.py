import json
import os
import shutil
import sqlite3
import subprocess
from datetime import datetime, timezone
from pathlib import Path

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

app = FastAPI()

API_KEY = os.environ["EXIFTOOL_API_KEY"]
ALLOWED_BASE = os.environ.get("ALLOWED_BASE", "/photos")
DB_PATH = os.environ.get("DB_PATH", "/data/copy_progress.db")
TRASH_DIR = os.environ.get("TRASH_DIR", os.path.join(ALLOWED_BASE, "_trash"))


def _get_db() -> sqlite3.Connection:
    db = sqlite3.connect(DB_PATH)
    db.row_factory = sqlite3.Row
    return db


def _check_key(key: str):
    if key != API_KEY:
        raise HTTPException(status_code=401, detail="Invalid API key")


def _check_path(path: str):
    resolved = os.path.realpath(path)
    if not resolved.startswith(os.path.realpath(ALLOWED_BASE)):
        raise HTTPException(status_code=403, detail="Path outside allowed base")
    if not os.path.isfile(resolved):
        raise HTTPException(status_code=404, detail="File not found")
    return resolved


def _fix_extension_mismatch(resolved: str) -> str:
    """Detect and fix files with wrong extensions (e.g. .heic that is really JPEG).
    Renames the file and updates dest_path in copy_progress.db."""
    result = subprocess.run(
        ["exiftool", "-json", "-FileType", "-FileTypeExtension", resolved],
        capture_output=True, text=True, timeout=10,
    )
    if result.returncode != 0 or not result.stdout:
        return resolved

    data = json.loads(result.stdout)[0]
    actual_ext = data.get("FileTypeExtension", "").lower()
    current_ext = os.path.splitext(resolved)[1].lstrip(".").lower()

    if not actual_ext or actual_ext == current_ext:
        return resolved

    # Rename file to correct extension
    new_path = os.path.splitext(resolved)[0] + "." + actual_ext
    os.rename(resolved, new_path)

    # Update migration database
    db = _get_db()
    db.execute(
        "UPDATE file_copies SET dest_path = ? WHERE dest_path = ?",
        (new_path, resolved),
    )
    db.commit()
    db.close()

    return new_path


class WriteGPSRequest(BaseModel):
    api_key: str
    file_path: str
    latitude: float
    longitude: float


@app.post("/write-gps")
async def write_gps(req: WriteGPSRequest):
    _check_key(req.api_key)
    resolved = _check_path(req.file_path)

    # Check for extension mismatch and fix before writing
    resolved = _fix_extension_mismatch(resolved)

    # AVI files can't be written directly — use XMP sidecar
    ext = os.path.splitext(resolved)[1].lower()
    use_sidecar = ext == ".avi"
    target = resolved + ".xmp" if use_sidecar else resolved

    cmd = [
        "exiftool",
        "-overwrite_original",
        f"-GPSLatitude={abs(req.latitude)}",
        f"-GPSLatitudeRef={'N' if req.latitude >= 0 else 'S'}",
        f"-GPSLongitude={abs(req.longitude)}",
        f"-GPSLongitudeRef={'E' if req.longitude >= 0 else 'W'}",
        target,
    ]

    result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)

    if result.returncode != 0:
        raise HTTPException(status_code=500, detail=f"exiftool error: {result.stderr}")

    # Update migration database
    db = _get_db()
    db.execute(
        "UPDATE file_copies SET has_gps_data = 1, exif_gps_lat = ?, exif_gps_lon = ? WHERE dest_path = ?",
        (req.latitude, req.longitude, resolved),
    )
    db.commit()
    db.close()

    return {"status": "ok", "output": result.stdout.strip(), "file_path": resolved, "sidecar": use_sidecar}


class ReadGPSRequest(BaseModel):
    api_key: str
    file_path: str


@app.post("/read-gps")
async def read_gps(req: ReadGPSRequest):
    _check_key(req.api_key)
    resolved = _check_path(req.file_path)

    result = subprocess.run(
        ["exiftool", "-json", "-GPSLatitude", "-GPSLongitude", "-n", resolved],
        capture_output=True,
        text=True,
        timeout=30,
    )

    if result.returncode != 0:
        raise HTTPException(status_code=500, detail=f"exiftool error: {result.stderr}")

    data = json.loads(result.stdout)[0]
    return {
        "latitude": data.get("GPSLatitude"),
        "longitude": data.get("GPSLongitude"),
    }


class TrashFileRequest(BaseModel):
    api_key: str
    file_path: str


@app.post("/trash-file")
async def trash_file(req: TrashFileRequest):
    _check_key(req.api_key)
    resolved = _check_path(req.file_path)

    # Get file info before moving
    file_size = os.path.getsize(resolved)
    original_filename = os.path.basename(resolved)

    # Create trash directory preserving subdirectory structure
    rel_path = os.path.relpath(resolved, os.path.realpath(ALLOWED_BASE))
    trash_path = os.path.join(TRASH_DIR, rel_path)
    os.makedirs(os.path.dirname(trash_path), exist_ok=True)

    # Move file to trash
    shutil.move(resolved, trash_path)

    # Update migration tracking database
    now = datetime.now(timezone.utc).isoformat()
    db = _get_db()
    row = db.execute(
        "SELECT id FROM file_copies WHERE dest_path = ?", (resolved,)
    ).fetchone()
    if row:
        db.execute(
            "UPDATE file_copies SET status = 'deleted', dedup_notes = ? WHERE id = ?",
            (f"Deleted via exiftool-service at {now}. Moved to {trash_path}", row["id"]),
        )
    db.commit()
    db.close()

    return {
        "status": "ok",
        "original_path": resolved,
        "trash_path": trash_path,
        "db_updated": row is not None,
    }


@app.get("/deleted")
async def list_deleted():
    db = _get_db()
    rows = db.execute(
        "SELECT id, dest_path, dedup_notes, completed_date, size_bytes FROM file_copies WHERE status = 'deleted' ORDER BY completed_date DESC LIMIT 100"
    ).fetchall()
    db.close()
    return [dict(r) for r in rows]


class RemoveGPSRequest(BaseModel):
    api_key: str
    file_path: str


@app.post("/remove-gps")
async def remove_gps(req: RemoveGPSRequest):
    _check_key(req.api_key)
    resolved = _check_path(req.file_path)

    result = subprocess.run(
        ["exiftool", "-overwrite_original", "-gps:all=", resolved],
        capture_output=True,
        text=True,
        timeout=30,
    )

    if result.returncode != 0:
        raise HTTPException(status_code=500, detail=f"exiftool error: {result.stderr}")

    return {"status": "ok", "output": result.stdout.strip()}


class RestoreFileRequest(BaseModel):
    api_key: str
    trash_path: str
    original_path: str


@app.post("/restore-file")
async def restore_file(req: RestoreFileRequest):
    _check_key(req.api_key)

    trash_resolved = os.path.realpath(req.trash_path)
    if not os.path.isfile(trash_resolved):
        raise HTTPException(status_code=404, detail="Trash file not found")

    original_resolved = os.path.realpath(req.original_path)
    if not original_resolved.startswith(os.path.realpath(ALLOWED_BASE)):
        raise HTTPException(status_code=403, detail="Restore path outside allowed base")

    os.makedirs(os.path.dirname(original_resolved), exist_ok=True)
    shutil.move(trash_resolved, original_resolved)

    # Update migration tracking database
    now = datetime.now(timezone.utc).isoformat()
    db = _get_db()
    row = db.execute(
        "SELECT id FROM file_copies WHERE dest_path = ?", (original_resolved,)
    ).fetchone()
    if row:
        db.execute(
            "UPDATE file_copies SET status = 'completed', dedup_notes = ? WHERE id = ?",
            (f"Restored via exiftool-service at {now}", row["id"]),
        )
    db.commit()
    db.close()

    return {"status": "ok", "restored_path": original_resolved}


class CheckTypesRequest(BaseModel):
    api_key: str
    file_paths: list[str]


@app.post("/check-types")
async def check_types(req: CheckTypesRequest):
    _check_key(req.api_key)

    # Validate all paths
    resolved_paths = []
    for fp in req.file_paths:
        resolved = os.path.realpath(fp)
        if not resolved.startswith(os.path.realpath(ALLOWED_BASE)):
            continue
        if not os.path.isfile(resolved):
            continue
        resolved_paths.append(resolved)

    if not resolved_paths:
        return {"results": []}

    # Run exiftool once for all files
    result = subprocess.run(
        ["exiftool", "-json", "-FileType", "-FileTypeExtension"] + resolved_paths,
        capture_output=True,
        text=True,
        timeout=60,
    )

    if result.returncode != 0 and not result.stdout:
        raise HTTPException(status_code=500, detail=f"exiftool error: {result.stderr}")

    data = json.loads(result.stdout)
    results = []
    for entry in data:
        source_file = entry.get("SourceFile", "")
        actual_type = entry.get("FileType", "").lower()
        expected_ext = os.path.splitext(source_file)[1].lstrip(".").lower()
        actual_ext = entry.get("FileTypeExtension", "").lower()
        mismatch = expected_ext != actual_ext and expected_ext != actual_type
        results.append({
            "file_path": source_file,
            "extension": expected_ext,
            "actual_type": entry.get("FileType", ""),
            "actual_ext": actual_ext,
            "mismatch": mismatch,
        })

    return {"results": results}


@app.get("/health")
async def health():
    return {"status": "ok"}

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
DB_PATH = os.environ.get("DB_PATH", "/data/exiftool-service.db")
TRASH_DIR = os.environ.get("TRASH_DIR", os.path.join(ALLOWED_BASE, "_trash"))


def _get_db() -> sqlite3.Connection:
    db = sqlite3.connect(DB_PATH)
    db.execute("""
        CREATE TABLE IF NOT EXISTS deleted_files (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            original_path TEXT NOT NULL,
            trash_path TEXT,
            deleted_at TEXT NOT NULL,
            original_filename TEXT,
            file_size INTEGER
        )
    """)
    db.commit()
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


class WriteGPSRequest(BaseModel):
    api_key: str
    file_path: str
    latitude: float
    longitude: float


@app.post("/write-gps")
async def write_gps(req: WriteGPSRequest):
    _check_key(req.api_key)
    resolved = _check_path(req.file_path)

    result = subprocess.run(
        [
            "exiftool",
            "-overwrite_original",
            f"-GPSLatitude={abs(req.latitude)}",
            f"-GPSLatitudeRef={'N' if req.latitude >= 0 else 'S'}",
            f"-GPSLongitude={abs(req.longitude)}",
            f"-GPSLongitudeRef={'E' if req.longitude >= 0 else 'W'}",
            resolved,
        ],
        capture_output=True,
        text=True,
        timeout=30,
    )

    if result.returncode != 0:
        raise HTTPException(status_code=500, detail=f"exiftool error: {result.stderr}")

    return {"status": "ok", "output": result.stdout.strip()}


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

    # Record in database
    db = _get_db()
    db.execute(
        "INSERT INTO deleted_files (original_path, trash_path, deleted_at, original_filename, file_size) VALUES (?, ?, ?, ?, ?)",
        (resolved, trash_path, datetime.now(timezone.utc).isoformat(), original_filename, file_size),
    )
    db.commit()
    db.close()

    return {"status": "ok", "original_path": resolved, "trash_path": trash_path}


@app.get("/deleted")
async def list_deleted():
    db = _get_db()
    rows = db.execute(
        "SELECT id, original_path, trash_path, deleted_at, original_filename, file_size FROM deleted_files ORDER BY deleted_at DESC LIMIT 100"
    ).fetchall()
    db.close()
    return [
        {
            "id": r[0],
            "original_path": r[1],
            "trash_path": r[2],
            "deleted_at": r[3],
            "original_filename": r[4],
            "file_size": r[5],
        }
        for r in rows
    ]


@app.get("/health")
async def health():
    return {"status": "ok"}

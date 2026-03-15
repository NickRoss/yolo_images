import os
import subprocess

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

app = FastAPI()

API_KEY = os.environ["EXIFTOOL_API_KEY"]
ALLOWED_BASE = os.environ.get("ALLOWED_BASE", "/photos")


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

    import json
    data = json.loads(result.stdout)[0]
    return {
        "latitude": data.get("GPSLatitude"),
        "longitude": data.get("GPSLongitude"),
    }


@app.get("/health")
async def health():
    return {"status": "ok"}

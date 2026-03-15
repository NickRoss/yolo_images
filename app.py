import json
import logging
import os
from pathlib import Path

import httpx
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import Response
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
SAVED_LOCATIONS_FILE = Path("saved_locations.json")

app = FastAPI()

immich_headers = {"x-api-key": IMMICH_API_KEY}


def immich_client() -> httpx.AsyncClient:
    return httpx.AsyncClient(
        base_url=IMMICH_URL, headers=immich_headers, timeout=30.0
    )


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
async def get_untagged_assets(page: int = Query(1, ge=1), size: int = Query(50, ge=1, le=200)):
    untagged: list[dict] = []
    immich_page = (page - 1) * size + 1  # rough starting offset
    # We track how many Immich pages we've skipped through to build our logical page
    batch_size = size * 3  # over-fetch to account for tagged images
    max_immich_pages = 20  # safety limit

    async with immich_client() as client:
        # First, skip past items for previous pages
        skip_count = (page - 1) * size
        skipped = 0
        immich_pg = 1

        # Scan through to find enough untagged items
        while len(untagged) < size and immich_pg <= max_immich_pages:
            resp = await client.post(
                "/api/search/metadata",
                json={
                    "page": immich_pg,
                    "size": batch_size,
                    "type": "IMAGE",
                    "withExif": True,
                },
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
                exif = a.get("exifInfo") or {}
                lat = exif.get("latitude")
                lon = exif.get("longitude")
                if lat is None or lon is None or (lat == 0 and lon == 0):
                    if skipped < skip_count:
                        skipped += 1
                        continue
                    untagged.append(
                        {
                            "id": a["id"],
                            "originalFileName": a.get("originalFileName", ""),
                            "fileCreatedAt": a.get("fileCreatedAt", ""),
                        }
                    )
                    if len(untagged) >= size:
                        break

            if len(assets) < batch_size:
                break
            immich_pg += 1

    # There's a next page if we filled the requested size and didn't exhaust Immich
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


# ── Location search (Photon) ────────────────────────────────────────────────


@app.get("/api/search-location")
async def search_location(q: str = Query(..., min_length=2)):
    async with httpx.AsyncClient(timeout=10.0, headers={"User-Agent": "yolo-images/1.0"}) as client:
        resp = await client.get(
            "https://photon.komoot.io/api/",
            params={"q": q, "limit": 7},
        )
        resp.raise_for_status()
        data = resp.json()

    results = []
    for feature in data.get("features", []):
        props = feature.get("properties", {})
        coords = feature.get("geometry", {}).get("coordinates", [])
        if len(coords) < 2:
            continue
        parts = [
            props.get("name", ""),
            props.get("city", ""),
            props.get("state", ""),
            props.get("country", ""),
        ]
        display = ", ".join(p for p in parts if p)
        results.append(
            {
                "display": display,
                "latitude": coords[1],
                "longitude": coords[0],
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
    # Avoid duplicates by name
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
    errors = []
    async with immich_client() as immich, httpx.AsyncClient(timeout=30.0) as exif_client:
        for asset_id in req.assetIds:
            # Get the original file path from Immich
            resp = await immich.get(f"/api/assets/{asset_id}")
            if resp.status_code != 200:
                errors.append(f"{asset_id}: couldn't fetch asset info")
                continue
            original_path = resp.json().get("originalPath", "")
            if not original_path:
                errors.append(f"{asset_id}: no originalPath")
                continue

            # Remap path if prefixes are configured
            file_path = original_path
            if IMMICH_PHOTOS_PREFIX and EXIFTOOL_PHOTOS_PREFIX:
                file_path = original_path.replace(IMMICH_PHOTOS_PREFIX, EXIFTOOL_PHOTOS_PREFIX, 1)

            # Write GPS via exiftool service
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

    if errors:
        logger.error("Apply errors: %s", errors)
        raise HTTPException(status_code=500, detail=f"Failed: {'; '.join(errors)}")

    # Trigger Immich library scan so it picks up the new EXIF data
    async with immich_client() as immich:
        libs = await immich.get("/api/libraries")
        for lib in libs.json():
            await immich.post(f"/api/libraries/{lib['id']}/scan")
            logger.info("Triggered scan for library %s", lib["id"])

    return {"updated": len(req.assetIds)}


# ── Static files (must be last) ──────────────────────────────────────────────

app.mount("/", StaticFiles(directory="static", html=True), name="static")

if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8000)

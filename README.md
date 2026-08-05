# FiveAtlas

A local, Xenium-Explorer-style web app for viewing high-resolution Xenium
morphology imagery and editing the GeoJSON that segments brain regions --
including shared-border snapping, merge, and unmerge -- reusing this repo's
existing Shapely topology engine (`snap_borders/`).

## Architecture

```
Browser (React + deck.gl [+ Viv later])
   |  tiles (PNG)            regions (GeoJSON), snap/merge ops
   v                          v
FastAPI backend  ->  tiles.py (decode JPEG2000 OME-TIFF pyramid -> PNG tiles)
                 ->  geo.py   (wraps snap_borders/fix_geojson_borders_v2.py)
```

Why server-side tiles: the morphology OME-TIFF is JPEG2000-compressed, which
geotiff.js (Viv's in-browser reader) can't decode. Python decodes it and serves
plain tiles to deck.gl. Viv joins later for the multichannel gene view via
OME-Zarr.

## Backend

Uses the repo's `.venv` (already has tifffile / shapely / rasterio / zarr).

```bash
# one-time: add the three missing deps
.venv/Scripts/python.exe -m pip install fastapi "uvicorn[standard]" imagecodecs python-multipart

# smoke test (no browser needed)
.venv/Scripts/python.exe atlas_editor/backend/smoke_test.py

# run the API
.venv/Scripts/python.exe -m uvicorn app:app --app-dir atlas_editor/backend --port 8000 --reload
```

Edits are saved to `backend/workdir/<dataset>/regions_edited.geojson`. The
original files on disk are never overwritten.

## Frontend

Requires Node.js (not yet installed). Scaffolded after Node is available.

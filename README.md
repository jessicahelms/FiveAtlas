# FiveAtlas

## [Download FiveAtlas for Windows or Mac](https://github.com/jessicahelms/FiveAtlas/releases/latest)

One page with both downloads, always the newest version, plus the instructions
PDF. **Already have FiveAtlas? Close the old one before starting the new one**,
or the new one just opens the old one again (details in [INSTALL.md](INSTALL.md#updating-to-a-new-version)).

# What's new in v1.3.0

## Stains load all the way, and faster
A stain square that failed to load no longer stays blank: it is asked for again
automatically. Squares load more than twice as fast from a network drive, and a
brightness slider re-colours what is loaded instead of reading it all again.

## Save is one button, and it tells you where your work went
Save, Reset, Undo and Redo sit at the top right. The first Save asks where to put
the file; every Save after that updates it. A panel under Save shows exactly
where it went. Original region files are never overwritten.

## Delete points with a right-click
In Edit points, right-click a point to delete it. The border re-spaces itself,
and a point shared with a neighbour goes from both, so borders never open.

## Resample points now works in Edit points
Re-space a single region's outline with the fine-to-coarse slider; its shared
borders stay joined.

## Load regions file asks: add, or replace?
Add the file's regions beside what is on the map, or replace the map. A file
with hemi asks whether to load its damage too.

## Drawing over a region: Replace, Overlap, or Fit around
Choose how a new region sits on the ones it lands on. Right-click any region for
Bring to front / Send to back, and move a whole region with Move region.

Full details: [CHANGELOG.md](CHANGELOG.md) · How to use every feature:
[docs/FiveAtlas-instructions.pdf](docs/FiveAtlas-instructions.pdf) · Install and
update help: [INSTALL.md](INSTALL.md)

---

## For developers

A local, Xenium-Explorer-style web app for viewing high-resolution Xenium
morphology imagery and editing the GeoJSON that segments brain regions --
including shared-border snapping, merge, and unmerge -- reusing this repo's
existing Shapely topology engine (`snap_borders/`).

### Architecture

```
Browser (React + deck.gl)
   |  tiles (PNG)            regions (GeoJSON), snap/merge ops
   v                          v
FastAPI backend  ->  tiles.py  (decode JPEG2000 OME-TIFF pyramid -> PNG tiles)
                 ->  stains.py (morphology_focus channels -> blended PNG tiles)
                 ->  geo.py / topology.py (region files, shared borders, snapping)
```

Why server-side tiles: the morphology OME-TIFF is JPEG2000-compressed, which
geotiff.js (Viv's in-browser reader) can't decode. Python decodes it and serves
plain tiles to deck.gl.

### Backend

Uses the repo's `.venv` (already has tifffile / shapely / zarr).

```bash
# one-time: add the missing deps
.venv/Scripts/python.exe -m pip install fastapi "uvicorn[standard]" imagecodecs python-multipart

# smoke test (no browser needed)
.venv/Scripts/python.exe atlas_editor/backend/smoke_test.py

# run the API
.venv/Scripts/python.exe -m uvicorn app:app --app-dir atlas_editor/backend --port 8050
```

From source, edits are saved to `backend/workdir/<dataset>/` (packaged:
`%LOCALAPPDATA%\FiveAtlas` or `~/Library/Application Support/FiveAtlas`), and
Save writes the regions file wherever you chose. The original files on disk are
never overwritten.

### Frontend

```bash
cd atlas_editor/frontend && npm install
ATLAS_API_TARGET=http://127.0.0.1:8050 npm run dev     # http://localhost:5173
```

### Releasing

Add a `## vX.Y.Z` section to `CHANGELOG.md` (each `### ` heading in it becomes a
big headline on the release page), then push a `vX.Y.Z` tag: GitHub builds the
Mac and Windows downloads and publishes them on one release page.

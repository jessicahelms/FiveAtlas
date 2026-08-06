"""FiveAtlas -- FastAPI backend.

Endpoints (thin end-to-end slice):
  GET  /api/health
  GET  /api/datasets
  GET  /api/datasets/{id}/info
  GET  /api/datasets/{id}/tiles/{z}/{x}/{y}.png      deck.gl TileLayer source
  GET  /api/datasets/{id}/overview.png               quick sanity image
  GET  /api/datasets/{id}/regions                    editable-or-original FC
  PUT  /api/datasets/{id}/regions                    save edited FC (working copy)
  POST /api/datasets/{id}/snap                        shared-border snap
"""
from __future__ import annotations

import base64
import io
import json
import os
import re
import subprocess
import sys
import zipfile
from typing import Optional

from pathlib import Path

from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, FileResponse
from fastapi.staticfiles import StaticFiles

import config
import datasets as ds
import geo
import topology
from transcripts import GeneDensity
from stains import StainStack
from tiles import Pyramid

DEFAULT_GENES = ["Slc17a7", "Calb2", "Pvalb"]

app = FastAPI(title="FiveAtlas")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],          # dev: Vite serves on a different port
    allow_methods=["*"],
    allow_headers=["*"],
)

_PYR: dict[str, Pyramid] = {}
_GENES: dict[str, GeneDensity] = {}


class NoImagery(LookupError):
    """The dataset folder has no morphology OME-TIFF.

    A legitimate state, not an error: regions are editable on a blank canvas.
    Deliberately NOT a KeyError, so callers can tell it apart from an unknown
    dataset id."""


def pyramid(ds_id: str) -> Pyramid:
    if ds_id not in _PYR:
        d = ds.get_dataset(ds_id)
        img = ds.image_path(d)
        if img is None:
            # Pyramid(None) used to defer the failure and surface it later as
            # FileNotFoundError on the literal path "None" -- a 500 on every tile
            # request for any folder without imagery. Say so up front instead.
            raise NoImagery(ds_id)
        _PYR[ds_id] = Pyramid(img)
    return _PYR[ds_id]


def genedensity(ds_id: str) -> GeneDensity:
    if ds_id not in _GENES:
        d = ds.get_dataset(ds_id)
        tz = d["sources"].get("transcripts")
        if not tz:
            raise KeyError("no transcripts.zarr")
        _GENES[ds_id] = GeneDensity(tz["path"], d.get("pixel_size_um"))
    return _GENES[ds_id]


_STAINS: dict[str, StainStack] = {}


def stainstack(ds_id: str) -> StainStack:
    if ds_id not in _STAINS:
        d = ds.get_dataset(ds_id)
        mf = d["sources"].get("morphology_focus")
        if not mf:
            raise KeyError("no morphology_focus")
        _STAINS[ds_id] = StainStack(mf, ds.workdir_for(ds_id))
    return _STAINS[ds_id]


@app.get("/api/health")
def health():
    return {"ok": True}


@app.get("/api/datasets")
def list_datasets():
    return ds.all_datasets()


@app.delete("/api/datasets/{ds_id}")
def close_dataset(ds_id: str):
    for cache in (_PYR, _GENES, _STAINS):
        cache.pop(ds_id, None)
    ds.forget(ds_id)
    return {"closed": ds_id, "datasets": ds.all_datasets()}


_DIALOG_SCRIPT = str(Path(__file__).with_name("nativedialog.py"))


def _last_folder():
    """Where a picker should open: the most recently opened dataset's folder.
    Starting there is most of the speed win -- the dialog never has to enumerate
    This PC, which is what stalls when a mapped network drive is slow or offline."""
    try:
        for d in reversed(list(ds._REGISTRY.values())):
            root = d.get("root")
            if root and Path(root).exists():
                return str(Path(root).parent)
    except Exception:
        pass
    return ""


def _picker_cmd(kind: str):
    """(argv, env) for spawning the picker.

    Frozen, sys.executable IS this app and nativedialog.py is not a file on disk,
    so we re-launch ourselves with --pick instead of running the script. That
    makes the arguments load-bearing: a child that does not see `--pick` falls
    through to main() and starts a SECOND SERVER with its own browser tab, held
    open for the subprocess timeout. A windowed macOS .app bootloader is the
    least trustworthy place to bet on argv surviving, so the same instruction
    goes through the environment as well, and ATLAS_CHILD marks the process as a
    helper that must never serve no matter what it does or does not parse.
    """
    initial = _last_folder()
    env = dict(os.environ)
    env["ATLAS_CHILD"] = "1"
    env["ATLAS_PICK_KIND"] = kind
    env["ATLAS_PICK_INITIAL"] = initial
    if config.FROZEN:
        return [sys.executable, "--pick", kind, initial], env
    return [sys.executable, _DIALOG_SCRIPT, kind, initial], env


def _run_picker(kind: str) -> str:
    """Spawn the picker in its own process so a modal dialog never blocks uvicorn."""
    if kind not in ("folder", "file"):
        raise HTTPException(400, f"bad picker kind {kind!r}")
    argv, env = _picker_cmd(kind)
    try:
        res = subprocess.run(argv, capture_output=True, text=True, timeout=600, env=env)
    except Exception as e:
        raise HTTPException(500, f"browse failed: {e}")
    lines = [ln for ln in (res.stdout or "").splitlines() if ln.strip()]
    return lines[-1].strip() if lines else ""


@app.post("/api/browse")
def browse():
    """Pop a native folder picker on the (local) machine and return the path."""
    return {"path": _run_picker("folder")}


@app.post("/api/datasets/open")
async def open_dataset(request: Request):
    body = await request.json()
    path = (body or {}).get("path")
    if not path:
        raise HTTPException(400, "need 'path'")
    try:
        desc = ds.open_path(path)
    except Exception as e:
        raise HTTPException(400, f"scan failed: {e}")
    # a re-scan can change file paths -> drop cached readers for this dataset
    for cache in (_PYR, _GENES, _STAINS):
        cache.pop(desc["id"], None)
    return {"id": desc["id"], "label": desc["label"],
            "sources": desc["sources"], "pixelSizeUm": desc.get("pixel_size_um")}


@app.get("/api/datasets/{ds_id}/sources")
def dataset_sources(ds_id: str):
    try:
        d = ds.get_dataset(ds_id)
    except KeyError:
        raise HTTPException(404, "unknown dataset")
    return {"id": ds_id, "label": d["label"], "root": d["root"],
            "sources": d["sources"], "pixelSizeUm": d.get("pixel_size_um")}


def _regions_extent(fc):
    """Bounding canvas (w, h) from region coordinates, for when there's no imagery."""
    mx = my = 0.0
    def walk(c):
        nonlocal mx, my
        if c and isinstance(c[0], (int, float)):
            mx = max(mx, c[0]); my = max(my, c[1]); return
        for x in (c or []):
            walk(x)
    for f in fc.get("features", []):
        walk((f.get("geometry") or {}).get("coordinates", []))
    return max(int(mx) + 100, 512), max(int(my) + 100, 512)


@app.get("/api/datasets/{ds_id}/info")
def info(ds_id: str):
    try:
        d = ds.get_dataset(ds_id)
    except KeyError:
        raise HTTPException(404, "unknown dataset")
    try:
        p = pyramid(ds_id)
        return {"id": ds_id, "label": d["label"], "idProp": d["id_prop"],
                "pixelSizeUm": d.get("pixel_size_um"), **p.info()}
    except Exception:
        # no imagery (offline drive / stub dataset): size the canvas to the regions
        fc, _ = geo.load_regions(ds_id)
        w, h = _regions_extent(fc)
        return {"id": ds_id, "label": d["label"], "idProp": d["id_prop"],
                "pixelSizeUm": d.get("pixel_size_um"), "width": w, "height": h,
                "levels": 1, "tileSize": 512, "nZ": 1, "offline": True}


@app.get("/api/datasets/{ds_id}/tiles/{z}/{x}/{y}.png")
def tile(ds_id: str, z: int, x: int, y: int, plane: Optional[int] = None):
    try:
        p = pyramid(ds_id)
    except NoImagery:
        return Response(status_code=204)      # blank canvas, same as an empty tile
    except KeyError:
        raise HTTPException(404, "unknown dataset")
    level = (p.nlevels - 1) - z
    png = p.tile_png(level, x, y, plane)
    if png is None:
        return Response(status_code=204)
    return Response(content=png, media_type="image/png",
                    headers={"Cache-Control": "max-age=3600"})


@app.get("/api/datasets/{ds_id}/overview.png")
def overview(ds_id: str):
    try:
        p = pyramid(ds_id)
    except NoImagery:
        raise HTTPException(404, "this dataset folder has no morphology image")
    except KeyError:
        raise HTTPException(404, "unknown dataset")
    return Response(content=p.overview_png(), media_type="image/png")


@app.get("/api/datasets/{ds_id}/regions")
def get_regions(ds_id: str):
    try:
        fc, _ = geo.load_regions(ds_id)
    except KeyError:
        raise HTTPException(404, "unknown dataset")
    return JSONResponse(fc)


@app.put("/api/datasets/{ds_id}/regions")
async def put_regions(ds_id: str, request: Request):
    try:
        ds.get_dataset(ds_id)
    except KeyError:
        raise HTTPException(404, "unknown dataset")
    fc = await request.json()
    res = geo.save_edited(ds_id, fc)
    return {"saved": res["saved"], "version": res["version"],
            "versions": res["versions"], "features": len(fc.get("features", []))}


@app.post("/api/datasets/{ds_id}/regions/restore-original")
def regions_restore_original(ds_id: str):
    """Discard the working copy and reload the dataset's own GeoJSON. The working
    copy is snapshotted into versions/ first, so nothing is lost."""
    try:
        ds.get_dataset(ds_id)
    except KeyError:
        raise HTTPException(404, "unknown dataset")
    try:
        res = geo.restore_original(ds_id)
    except FileNotFoundError as e:
        raise HTTPException(422, str(e))
    except Exception as e:
        raise HTTPException(500, f"restore failed: {e}")
    return {"type": "FeatureCollection", "features": res["fc"].get("features", []),
            "source": res["source"], "backup": res["backup"], "count": res["features"]}


@app.get("/api/datasets/{ds_id}/regions/versions")
def regions_versions(ds_id: str):
    """Every snapshot Save has written, newest first."""
    try:
        ds.get_dataset(ds_id)
    except KeyError:
        raise HTTPException(404, "unknown dataset")
    return {"versions": geo.list_versions(ds_id)}


@app.post("/api/datasets/{ds_id}/snap")
async def snap(ds_id: str, request: Request):
    try:
        ds.get_dataset(ds_id)
    except KeyError:
        raise HTTPException(404, "unknown dataset")
    body = await request.json()
    before = body.get("before")
    after = body.get("after")
    moved = body.get("moved", [])
    tol = float(body.get("tol", 40.0))
    if not before or not after:
        raise HTTPException(400, "need 'before' and 'after' FeatureCollections")
    try:
        result = geo.run_snap(ds_id, before, after, moved=moved, tol=tol)
    except Exception as e:  # surface engine errors to the client
        raise HTTPException(500, f"snap failed: {e}")
    return JSONResponse(result)


@app.post("/api/datasets/{ds_id}/regions/shared-border")
async def shared_border(ds_id: str, request: Request):
    """The shared arc(s) between two explicitly named regions. The client sends
    the two regions the user picked (no fragile auto neighbour detection); we
    return the arc(s) so they can be dragged."""
    try:
        d = ds.get_dataset(ds_id)
    except KeyError:
        raise HTTPException(404, "unknown dataset")
    body = await request.json()
    a, b = body.get("regionA"), body.get("regionB")
    if not a or not b:
        raise HTTPException(400, "need 'regionA' and 'regionB'")
    fc = body.get("fc") or geo.load_regions(ds_id)[0]
    grid = float(body.get("grid", 4.0))
    touch_tol = float(body.get("touchTol", 6.0))
    try:
        gap_px = topology.region_gap(fc["features"], d["id_prop"], str(a), str(b))
    except ValueError as e:
        raise HTTPException(422, str(e))
    pair_meta = {
        "regionA": a, "regionB": b,
        "gapPx": round(gap_px, 2),
        "touching": gap_px <= touch_tol,
        "touchTol": touch_tol,
    }
    if not body.get("bridge", True):
        # exact coincident border only (no geometry change)
        arcs = topology.border_between(fc["features"], d["id_prop"], str(a), str(b), grid)
        return {**pair_meta, "arcs": arcs, "bridged": False}
    # bridge=True: the COMPLETE shared border via nearest-region partition. This
    # captures the WHOLE adjacency -- including stretches where the two outlines
    # "fall off" by a few px, which an exact boundary-intersection misses. The
    # returned `fc` is the pair bridged to share that full border (applied only
    # when the user actually edits, so picking never silently changes geometry).
    tol = float(body.get("tol", 40.0))
    try:
        res = topology.partition_regions(fc["features"], d["id_prop"], [str(a), str(b)], tol=tol)
    except ValueError:
        arcs = topology.border_between(fc["features"], d["id_prop"], str(a), str(b), grid)
        return {**pair_meta, "arcs": arcs, "bridged": False}
    arcs = [bd["points"] for bd in res["borders"]]
    return {**pair_meta, "arcs": arcs, "bridged": bool(arcs), "tol": tol,
            "fc": {"type": "FeatureCollection", "features": res["features"]}}


@app.post("/api/datasets/{ds_id}/regions/move-border")
async def move_border(ds_id: str, request: Request):
    """Rebuild BOTH regions around a dragged shared border. Body:
    {fc, regionA, regionB, points:[[x,y]...]}. Returns the updated FC."""
    try:
        d = ds.get_dataset(ds_id)
    except KeyError:
        raise HTTPException(404, "unknown dataset")
    body = await request.json()
    fc = body.get("fc")
    a, b, pts = body.get("regionA"), body.get("regionB"), body.get("points")
    drag_start = body.get("dragStart")
    if drag_start is None:
        drag_start = body.get("orig")     # legacy name; still means this drag's start arc
    if not fc or not a or not b or not pts:
        raise HTTPException(400, "need 'fc', 'regionA', 'regionB', 'points'")
    try:
        feats = topology.move_border(fc["features"], d["id_prop"], str(a), str(b), pts, drag_start=drag_start)
    except ValueError as e:  # geometry couldn't be divided -> client-fixable
        raise HTTPException(422, str(e))
    except Exception as e:
        raise HTTPException(500, f"move-border failed: {e}")
    return {"type": "FeatureCollection", "features": feats}


@app.post("/api/datasets/{ds_id}/regions/partition")
async def regions_partition(ds_id: str, request: Request):
    """Make a set of explicitly-picked regions tile cleanly -- fill the small
    gaps between them, remove small overlaps, and give every adjacent pair one
    shared border. Only the picked regions change. Body:
    {regions:[names], tol?, fc?}. Returns the updated FC + the shared borders."""
    try:
        d = ds.get_dataset(ds_id)
    except KeyError:
        raise HTTPException(404, "unknown dataset")
    body = await request.json()
    names = body.get("regions") or body.get("names")
    if not names or len(names) < 2:
        raise HTTPException(400, "need 'regions': at least two region names")
    fc = body.get("fc") or geo.load_regions(ds_id)[0]
    tol = float(body.get("tol", 40.0))
    try:
        res = topology.partition_regions(fc["features"], d["id_prop"], names, tol=tol)
    except ValueError as e:
        raise HTTPException(422, str(e))
    except Exception as e:
        raise HTTPException(500, f"partition failed: {e}")
    return {"type": "FeatureCollection", "features": res["features"],
            "borders": res["borders"]}


@app.post("/api/datasets/{ds_id}/regions/resample")
async def regions_resample(ds_id: str, request: Request):
    """Thin the outlines of the picked regions without breaking their shared
    borders. Body: {regions:[names], fc?, tol?}. Returns the updated FC plus the
    before/after vertex counts, so the client can show the effect before keeping it."""
    try:
        d = ds.get_dataset(ds_id)
    except KeyError:
        raise HTTPException(404, "unknown dataset")
    body = await request.json()
    names = body.get("regions") or body.get("names")
    if not names or len(names) < 2:
        raise HTTPException(400, "need 'regions': at least two region names")
    fc = body.get("fc") or geo.load_regions(ds_id)[0]
    try:
        res = topology.resample_regions(fc["features"], d["id_prop"], names,
                                        tol=float(body.get("tol", 20.0)))
    except ValueError as e:
        raise HTTPException(422, str(e))
    except Exception as e:
        raise HTTPException(500, f"resample failed: {e}")
    return {"type": "FeatureCollection", "features": res["features"],
            "counts": res["counts"], "tol": res["tol"],
            "handles": res.get("handles"), "arcPoints": res.get("arcPoints")}


@app.post("/api/datasets/{ds_id}/regions/merge")
async def regions_merge(ds_id: str, request: Request):
    """Combine several regions into ONE feature. Body: {regions:[names], fc?, name?}."""
    try:
        d = ds.get_dataset(ds_id)
    except KeyError:
        raise HTTPException(404, "unknown dataset")
    body = await request.json()
    names = body.get("regions") or body.get("names")
    if not names or len(names) < 2:
        raise HTTPException(400, "need 'regions': at least two region names")
    fc = body.get("fc") or geo.load_regions(ds_id)[0]
    try:
        res = topology.merge_regions(fc["features"], d["id_prop"], names, body.get("name"))
    except ValueError as e:
        raise HTTPException(422, str(e))
    except Exception as e:
        raise HTTPException(500, f"merge failed: {e}")
    return {"type": "FeatureCollection", "features": res["features"], "name": res["name"]}


@app.post("/api/datasets/{ds_id}/regions/split")
async def regions_split(ds_id: str, request: Request):
    """Cut one region into two along a drawn line. Body: {region, points, fc?, name?}."""
    try:
        d = ds.get_dataset(ds_id)
    except KeyError:
        raise HTTPException(404, "unknown dataset")
    body = await request.json()
    region, points = body.get("region"), body.get("points")
    if not region or not points:
        raise HTTPException(400, "need 'region' and 'points'")
    fc = body.get("fc") or geo.load_regions(ds_id)[0]
    try:
        res = topology.split_region(fc["features"], d["id_prop"], region, points, body.get("name"))
    except ValueError as e:
        raise HTTPException(422, str(e))
    except Exception as e:
        raise HTTPException(500, f"split failed: {e}")
    return {"type": "FeatureCollection", "features": res["features"], "names": res["names"],
            "areas": res.get("areas"), "parts": res.get("parts")}


@app.post("/api/datasets/{ds_id}/regions/dissolve-gap")
async def regions_dissolve_gap(ds_id: str, request: Request):
    """Delete the leftover void under a clicked point -- the region(s) around it
    grow to fill it. Body: {point:[x,y], fc?, tol?}. Returns the gap outline for
    preview PLUS the already-computed updated FC, so the client can show what it
    found and commit without a second round trip."""
    try:
        d = ds.get_dataset(ds_id)
    except KeyError:
        raise HTTPException(404, "unknown dataset")
    body = await request.json()
    point = body.get("point")
    if not point or len(point) < 2:
        raise HTTPException(400, "need 'point': [x, y]")
    fc = body.get("fc") or geo.load_regions(ds_id)[0]
    tol = float(body.get("tol", 40.0))
    try:
        res = topology.dissolve_gap(fc["features"], d["id_prop"], point, tol=tol)
    except ValueError as e:      # no gap there / click was inside a region
        raise HTTPException(422, str(e))
    except Exception as e:
        raise HTTPException(500, f"dissolve-gap failed: {e}")
    return {"type": "FeatureCollection", "features": res["features"],
            "gap": res["gap"], "area": res["area"], "kind": res["kind"],
            "regions": res["regions"]}


@app.post("/api/datasets/{ds_id}/regions/load")
async def regions_load(ds_id: str, request: Request):
    """Open an arbitrary GeoJSON file (by path) as this dataset's editable
    working copy -- so any region set can be loaded, not just the folder's."""
    try:
        ds.get_dataset(ds_id)
    except KeyError:
        raise HTTPException(404, "unknown dataset")
    body = await request.json()
    path = (body or {}).get("path")
    if not path:
        raise HTTPException(400, "need 'path'")
    try:
        fc = geo.load_regions_file(ds_id, path)
    except Exception as e:
        raise HTTPException(400, f"load failed: {e}")
    return JSONResponse(fc)


@app.post("/api/datasets/{ds_id}/regions/add")
async def regions_add(ds_id: str, request: Request):
    """Add a new region from a drawn outline. Body: {points:[[x,y]...], fc?, name?,
    carve?}. carve (default true) makes overlapped regions cede the ground, so the
    file stays a clean partition."""
    try:
        d = ds.get_dataset(ds_id)
    except KeyError:
        raise HTTPException(404, "unknown dataset")
    body = await request.json()
    points = body.get("points")
    if not points or len(points) < 3:
        raise HTTPException(400, "need 'points': at least three [x, y] pairs")
    fc = body.get("fc") or geo.load_regions(ds_id)[0]
    try:
        res = topology.add_region(fc["features"], d["id_prop"], points,
                                  name=body.get("name"),
                                  carve=bool(body.get("carve", True)))
    except ValueError as e:
        raise HTTPException(422, str(e))
    except Exception as e:
        raise HTTPException(500, f"add-region failed: {e}")
    return {"type": "FeatureCollection", "features": res["features"],
            "name": res["name"], "area": res["area"], "ceded": res["ceded"]}


@app.post("/api/datasets/{ds_id}/regions/fill-gap")
async def regions_fill_gap(ds_id: str, request: Request):
    """Turn the gap under a clicked point into a NEW region (the alternative to
    dissolve-gap, which hands it to the neighbours). Body: {point:[x,y], fc?,
    name?, tol?}."""
    try:
        d = ds.get_dataset(ds_id)
    except KeyError:
        raise HTTPException(404, "unknown dataset")
    body = await request.json()
    point = body.get("point")
    if not point or len(point) < 2:
        raise HTTPException(400, "need 'point': [x, y]")
    fc = body.get("fc") or geo.load_regions(ds_id)[0]
    try:
        res = topology.fill_gap_with_region(fc["features"], d["id_prop"], point,
                                            name=body.get("name"),
                                            tol=float(body.get("tol", 40.0)))
    except ValueError as e:
        raise HTTPException(422, str(e))
    except Exception as e:
        raise HTTPException(500, f"fill-gap failed: {e}")
    return {"type": "FeatureCollection", "features": res["features"],
            "name": res["name"], "area": res["area"], "kind": res["kind"],
            "gap": res["gap"]}


@app.post("/api/datasets/{ds_id}/regions/validate")
async def regions_validate(ds_id: str, request: Request):
    """Check the regions for self-intersections, empties, duplicate names and the
    like. Body: {fc?}. Returns {problems, counts, ok} -- nothing is modified."""
    try:
        d = ds.get_dataset(ds_id)
    except KeyError:
        raise HTTPException(404, "unknown dataset")
    body = await request.json()
    fc = (body or {}).get("fc") or geo.load_regions(ds_id)[0]
    try:
        return JSONResponse(topology.validate_features(fc.get("features", []), d["id_prop"]))
    except Exception as e:
        raise HTTPException(500, f"validate failed: {e}")


@app.post("/api/datasets/{ds_id}/regions/repair")
async def regions_repair(ds_id: str, request: Request):
    """Heal invalid/empty polygons (buffer(0)). Body: {fc?}. Returns the fixed FC
    plus the names that changed. Names and properties are never touched."""
    try:
        d = ds.get_dataset(ds_id)
    except KeyError:
        raise HTTPException(404, "unknown dataset")
    body = await request.json()
    fc = (body or {}).get("fc") or geo.load_regions(ds_id)[0]
    try:
        res = topology.repair_features(fc.get("features", []), d["id_prop"])
    except Exception as e:
        raise HTTPException(500, f"repair failed: {e}")
    return {"type": "FeatureCollection", "features": res["features"], "fixed": res["fixed"]}


@app.post("/api/datasets/{ds_id}/regions/export")
async def regions_export(ds_id: str, request: Request):
    """Export the current regions either as ONE merged .geojson (all features in a
    single FeatureCollection) or as SEPARATE per-region files bundled in a .zip.

    Refuses to write broken geometry: self-intersections, empty or non-polygon
    features, missing names. Send force=true to export anyway. (A repeated name is
    only a warning -- it means one region in several parts.)"""
    try:
        d = ds.get_dataset(ds_id)
    except KeyError:
        raise HTTPException(404, "unknown dataset")
    body = await request.json()
    mode = (body or {}).get("mode", "merged")
    fc = body.get("fc") or geo.load_regions(ds_id)[0]
    feats = fc.get("features", [])

    if not body.get("force"):
        try:
            report = topology.validate_features(feats, d["id_prop"])
        except Exception:
            report = None
        if report and not report["ok"]:
            # 422 body carries the problem list so the client can show it and
            # offer Repair or Export anyway
            raise HTTPException(422, json.dumps({
                "message": "the regions have geometry errors",
                "problems": report["problems"],
                "counts": report["counts"],
            }))

    if mode == "separate":
        buf = io.BytesIO()
        used: dict[str, int] = {}
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
            for f in feats:
                nm = str((f.get("properties") or {}).get(d["id_prop"]) or "region")
                safe = re.sub(r"[^A-Za-z0-9_.-]+", "_", nm).strip("_") or "region"
                k = used.get(safe, 0)
                used[safe] = k + 1
                fname = f"{safe}.geojson" if k == 0 else f"{safe}_{k}.geojson"
                z.writestr(fname, json.dumps(
                    {"type": "FeatureCollection", "features": [f]}, indent=1))
        buf.seek(0)
        return Response(buf.read(), media_type="application/zip", headers={
            "Content-Disposition": 'attachment; filename="regions_separate.zip"'})

    return Response(json.dumps(fc, indent=1), media_type="application/geo+json",
                    headers={"Content-Disposition": 'attachment; filename="regions_merged.geojson"'})


@app.post("/api/browse-file")
def browse_file():
    """Native file picker for a GeoJSON region file."""
    return {"path": _run_picker("file")}


@app.get("/api/datasets/{ds_id}/genes")
def genes_info(ds_id: str):
    try:
        gd = genedensity(ds_id)
    except KeyError:
        raise HTTPException(404, "no transcripts")
    defaults = [g for g in DEFAULT_GENES if g in gd.gene_names]
    if not defaults:
        defaults = gd.gene_list()[:3]
    return gd.info(defaults)


@app.get("/api/datasets/{ds_id}/genes/contrast")
def genes_contrast(ds_id: str, gene: str):
    try:
        gd = genedensity(ds_id)
    except KeyError:
        raise HTTPException(404, "no transcripts")
    c = gd.contrast(gene)
    if not c:
        raise HTTPException(404, "unknown gene")
    return c


@app.post("/api/datasets/{ds_id}/genes/composite.png")
async def genes_composite(ds_id: str, request: Request):
    try:
        gd = genedensity(ds_id)
    except KeyError:
        raise HTTPException(404, "no transcripts")
    spec = await request.json()
    if not isinstance(spec, list):
        raise HTTPException(400, "body must be a list of channel specs")
    return Response(content=gd.composite_png(spec), media_type="image/png")


@app.get("/api/datasets/{ds_id}/stains")
def stains_info(ds_id: str):
    try:
        ss = stainstack(ds_id)
    except KeyError:
        raise HTTPException(404, "no morphology_focus stains")
    return ss.info()


@app.get("/api/datasets/{ds_id}/stains/contrast")
def stains_contrast(ds_id: str, idx: int):
    try:
        ss = stainstack(ds_id)
    except KeyError:
        raise HTTPException(404, "no morphology_focus stains")
    return ss.channel_contrast(idx)


@app.get("/api/datasets/{ds_id}/stains/tiles/{z}/{x}/{y}.png")
def stains_tile(ds_id: str, z: int, x: int, y: int, s: str = ""):
    """Composite tile: additive blend of the visible stain channels. `s` is a
    base64(JSON) channel spec [{index,color,min,max,visible}]."""
    try:
        ss = stainstack(ds_id)
    except KeyError:
        raise HTTPException(404, "no morphology_focus stains")
    level = (ss.nlevels - 1) - z
    if level < 0 or level >= ss.nlevels:
        raise HTTPException(404, "bad z")
    try:
        spec = json.loads(base64.b64decode(s).decode()) if s else []
    except Exception:
        spec = []
    png = ss.composite_tile(spec, level, x, y)
    if png is None:
        return Response(status_code=204)
    return Response(content=png, media_type="image/png",
                    headers={"Cache-Control": "max-age=3600"})


# ---- serve the built frontend (packaged / single-server mode) ----
# In dev, Vite serves the UI on :5173 and proxies /api here. For the packaged app
# we `npm run build` the frontend and this server hosts it too, so colleagues just
# run ONE process and open the one URL. config resolves the bundled copy when frozen.
_DIST = config.FRONTEND_DIST
if _DIST.exists():
    if (_DIST / "assets").exists():
        app.mount("/assets", StaticFiles(directory=str(_DIST / "assets")), name="assets")

    @app.get("/")
    def _spa_index():
        return FileResponse(str(_DIST / "index.html"))

    @app.get("/{path:path}")
    def _spa_fallback(path: str):
        if path.startswith("api/"):
            raise HTTPException(404, "not found")
        f = _DIST / path
        if f.is_file():
            return FileResponse(str(f))
        return FileResponse(str(_DIST / "index.html"))   # SPA fallback

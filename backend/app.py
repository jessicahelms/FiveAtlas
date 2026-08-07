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
from PIL import Image as PILImage

import orientation as ORI
import provenance
import topology
from transcripts import GeneDensity
from stains import StainStack
from tiles import Pyramid

DEFAULT_GENES = ["Slc17a7", "Calb2", "Pvalb"]

app = FastAPI(title="FiveAtlas")
app.add_middleware(
    CORSMiddleware,
    # Loopback only. This was "*", which on an unauthenticated local server means
    # ANY page open in the user's browser could call this API while FiveAtlas is
    # running -- read the dataset list, overwrite a working copy, or pop a native
    # file dialog on their desktop. The regex still covers dev, where Vite serves
    # the UI on :5173 and proxies /api here, and the packaged app, which picks a
    # free port at startup.
    allow_origin_regex=r"^http://(127\.0\.0\.1|localhost|\[::1\])(:\d+)?$",
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


def _evict(ds_id: str):
    """Drop the cached readers for a dataset AND close their file handles.

    Popping alone is not enough: tifffile's TiffFile, zipfile and zarr's ZipStore
    have no __del__ and sit in reference cycles, so the OS handle survived until
    some later GC. On Windows that leaves the dataset folder locked -- close a
    dataset, try to move or delete the folder, and Explorer says it is open in
    another program, with no way to release it short of quitting FiveAtlas.
    """
    for cache in (_PYR, _GENES, _STAINS):
        obj = cache.pop(ds_id, None)
        closer = getattr(obj, "close", None)
        if callable(closer):
            try:
                closer()
            except Exception as e:
                print(f"[evict] {ds_id}: {type(e).__name__}: {e}", file=sys.stderr)


@app.delete("/api/datasets/{ds_id}")
def close_dataset(ds_id: str):
    _evict(ds_id)
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
    # a re-scan can change file paths -> drop cached readers for this dataset.
    # Close them, don't just drop them: re-opening a dataset whose files moved
    # would otherwise leave the OLD paths locked for the rest of the session.
    _evict(desc["id"])
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


def _frame_tag(frame: str, o) -> str:
    """Filename suffix saying which frame an export is in.

    Empty when nothing was rotated, so an ordinary export keeps the name it has
    always had; otherwise the name itself distinguishes the two files, since the
    same regions can be exported twice in different frames.
    """
    if ORI.is_identity(o):
        return ""
    if frame == "original":
        return "_original"
    o = ORI.normalise(o)
    return (f"_rot{o['rot']}" if o["rot"] else "") \
        + ("_flipH" if o["flipH"] else "") + ("_flipV" if o["flipV"] else "")


@app.get("/api/datasets/{ds_id}/info")
def info(ds_id: str):
    try:
        d = ds.get_dataset(ds_id)
    except KeyError:
        raise HTTPException(404, "unknown dataset")
    o = ORI.get(ds_id)
    try:
        p = pyramid(ds_id)
        nfo = p.info()
        # The canvas is what the user SEES, so a quarter turn swaps the extent.
        # Everything downstream (deck's view, the tile grid, region coords) works
        # in that displayed frame.
        w, h = ORI.out_size(nfo["width"], nfo["height"], o)
        nfo["width"], nfo["height"] = int(w), int(h)
        return {"id": ds_id, "label": d["label"], "idProp": d["id_prop"],
                "pixelSizeUm": d.get("pixel_size_um"), "orientation": o, **nfo}
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
    png = p.tile_png(level, x, y, plane, orient=ORI.get(ds_id))
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
    body = await request.json()
    # Body is the FeatureCollection itself (original contract), or {fc, actions}
    # where `actions` are the edits the client made without a round trip --
    # renames, colour changes, deletions, dragged points. They have no endpoint
    # to stamp them, so they are recorded here, at the save that commits them.
    if isinstance(body, dict) and body.get("type") == "FeatureCollection":
        fc, actions = body, []
    else:
        fc = (body or {}).get("fc") or {}
        actions = (body or {}).get("actions") or []
    # Refuse to save something that is not a FeatureCollection. Without this, a
    # body of null/{}/[] resolved to fc={} and was written straight over the
    # working copy: the frontend sets fc=null while a dataset loads and the Save
    # button is live during that window, so switching datasets and clicking Save
    # too early silently replaced that dataset's edits with an empty file.
    if not isinstance(fc, dict) or not isinstance(fc.get("features"), list):
        raise HTTPException(
            400, "expected a FeatureCollection (or {fc, actions}) with a "
                 "'features' list; refusing to overwrite the working copy")
    for a in actions if isinstance(actions, list) else []:
        if isinstance(a, dict) and a.get("action"):
            provenance.stamp(fc, a["action"], a.get("detail"), a.get("regions"))
    provenance.stamp(fc, "save", f"{len(fc.get('features', []))} regions")
    res = geo.save_edited(ds_id, fc)
    return {"saved": res["saved"], "version": res["version"],
            "versions": res["versions"], "features": len(fc.get("features", [])),
            "provenance": provenance.trail(fc)}


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
    return {**provenance.stamped(res["fc"].get("features", []), res["fc"],
                                 "restore-original", Path(res["source"]).name),
            "source": res["source"], "backup": res["backup"], "count": res["features"]}


@app.get("/api/datasets/{ds_id}/orientation")
def get_orientation(ds_id: str):
    try:
        ds.get_dataset(ds_id)
    except KeyError:
        raise HTTPException(404, "unknown dataset")
    return ORI.get(ds_id)


@app.put("/api/datasets/{ds_id}/orientation")
async def put_orientation(ds_id: str, request: Request):
    """Rotate/flip the whole dataset -- image AND regions -- because the slide was
    imaged the wrong way up. Body: {rot, flipH, flipV, fc?}.

    Regions are stored in the DISPLAYED frame, with `_orientation` recording which
    frame that is. Changing orientation therefore takes them back to the original
    frame and forward into the new one, rather than transforming twice.
    """
    try:
        ds.get_dataset(ds_id)
    except KeyError:
        raise HTTPException(404, "unknown dataset")
    body = await request.json()
    want = ORI.normalise(body)
    have = ORI.get(ds_id)

    fc = (body or {}).get("fc") or geo.load_regions(ds_id)[0]
    have = ORI.normalise(fc.get("_orientation") or have)

    try:
        p = pyramid(ds_id)
        W0, H0 = p.info()["width"], p.info()["height"]
    except Exception:                       # no imagery: size from the regions
        w, h = _regions_extent(fc)
        W0, H0 = ORI.out_size(w, h, ORI.invert(have))

    feats = fc.get("features", [])
    if not ORI.is_identity(have):           # back to the file's original frame
        dw, dh = ORI.out_size(W0, H0, have)
        feats = ORI.transform_features(feats, ORI.invert(have), dw, dh)
    if not ORI.is_identity(want):           # forward into the new one
        feats = ORI.transform_features(feats, want, W0, H0)

    out = provenance.stamped(feats, fc, "orientation",
                             f"rot {want['rot']}°"
                             + (" flipH" if want["flipH"] else "")
                             + (" flipV" if want["flipV"] else ""))
    out["_orientation"] = want
    ORI.put(ds_id, want)
    geo.save_edited(ds_id, out)
    w, h = ORI.out_size(W0, H0, want)
    return {**out, "orientation": want, "width": int(w), "height": int(h)}


@app.get("/api/identity")
def get_identity():
    """Who edits are attributed to. The account is read-only -- a display name on
    its own could be anyone's, and a chain of custody has to say who really made
    the change."""
    return {"name": provenance.display_name(), "account": provenance.account_name()}


@app.post("/api/identity")
async def set_identity(request: Request):
    body = await request.json()
    name = (body or {}).get("name")
    if name is None:
        raise HTTPException(400, "need 'name'")
    return {"name": provenance.set_display_name(name),
            "account": provenance.account_name()}


@app.post("/api/datasets/{ds_id}/regions/history")
async def regions_history(ds_id: str, request: Request):
    """The edit trail carried by the regions themselves. Body: {fc?}."""
    try:
        ds.get_dataset(ds_id)
    except KeyError:
        raise HTTPException(404, "unknown dataset")
    body = await request.json()
    fc = (body or {}).get("fc") or geo.load_regions(ds_id)[0]
    return {**provenance.summary(fc), "entries": provenance.trail(fc)}


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
    provenance.carry(result, after)
    provenance.stamp(result, "snap-neighbours",
                     ", ".join(str(m) for m in moved) or None,
                     moved)
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
    # Picking is not an edit, so this reports rather than refuses -- the user finds
    # out the moment they pick the pair, instead of after the geometry is wrecked.
    # The endpoints that actually change geometry refuse outright.
    contained = topology.containment(fc["features"], d["id_prop"], [str(a), str(b)])
    pair_meta = {
        "regionA": a, "regionB": b,
        "gapPx": round(gap_px, 2),
        "touching": gap_px <= touch_tol,
        "touchTol": touch_tol,
    }
    if contained:
        return {**pair_meta, "arcs": [], "bridged": False,
                "contained": contained,
                "message": topology.containment_message(contained)}
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
    contained = topology.containment(fc["features"], d["id_prop"], [str(a), str(b)])
    if contained:
        raise HTTPException(422, topology.containment_message(contained))
    try:
        feats = topology.move_border(fc["features"], d["id_prop"], str(a), str(b), pts, drag_start=drag_start)
    except ValueError as e:  # geometry couldn't be divided -> client-fixable
        raise HTTPException(422, str(e))
    except Exception as e:
        raise HTTPException(500, f"move-border failed: {e}")
    return provenance.stamped(feats, fc, "move-border",
                              f"{a} / {b}", [str(a), str(b)])


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
    # Refuse a container/content pick rather than shredding the overlap into
    # slivers. This is the mutating call, so it changes nothing and says why.
    contained = topology.containment(fc["features"], d["id_prop"], names)
    if contained:
        raise HTTPException(422, topology.containment_message(contained))
    try:
        res = topology.partition_regions(fc["features"], d["id_prop"], names, tol=tol)
    except ValueError as e:
        raise HTTPException(422, str(e))
    except Exception as e:
        raise HTTPException(500, f"partition failed: {e}")
    return {**provenance.stamped(res["features"], fc, "share-borders",
                                 " + ".join(str(n) for n in names), names),
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
    return {**provenance.stamped(res["features"], fc, "resample",
                                 f"{' + '.join(str(n) for n in names)} at {res['tol']:g} px",
                                 names),
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
    return {**provenance.stamped(res["features"], fc, "merge",
                                 f"{' + '.join(str(n) for n in names)} -> {res['name']}",
                                 names),
            "name": res["name"]}


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
    return {**provenance.stamped(res["features"], fc, "split",
                                 f"{region} -> {' + '.join(str(n) for n in res['names'])}",
                                 res["names"]),
            "names": res["names"],
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
    return {**provenance.stamped(res["features"], fc, "dissolve-gap",
                                 f"{res['area']:,.0f} px² into "
                                 f"{' + '.join(str(n) for n in res['regions'])}",
                                 res["regions"]),
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
    # Stamped AFTER the load so the entry joins whatever trail the incoming file
    # already carried -- that is the hand-off being recorded.
    provenance.stamp(fc, "load-regions", Path(str(path)).name)
    geo.save_edited(ds_id, fc)
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
    return {**provenance.stamped(res["features"], fc, "add-region",
                                 f"{res['name']}, {res['area']:,.0f} px²"
                                 + (f", taken from {', '.join(res['ceded'])}" if res["ceded"] else ""),
                                 [res["name"]]),
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
    return {**provenance.stamped(res["features"], fc, "fill-gap",
                                 f"{res['name']}, {res['area']:,.0f} px²", [res["name"]]),
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
    if not res["fixed"]:
        return {**provenance.carry({"type": "FeatureCollection", "features": res["features"]}, fc),
                "fixed": res["fixed"]}
    return {**provenance.stamped(res["features"], fc, "repair",
                                 f"{len(res['fixed'])} fixed: {', '.join(res['fixed'][:6])}",
                                 res["fixed"]),
            "fixed": res["fixed"]}


@app.post("/api/datasets/{ds_id}/regions/export")
async def regions_export(ds_id: str, request: Request):
    """Export the current regions either as ONE merged .geojson (all features in a
    single FeatureCollection) or as SEPARATE per-region files bundled in a .zip.

    `frame` picks which coordinates get written when the view has been rotated or
    flipped: "displayed" (default) writes what is on screen, "original" rotates and
    flips every edit back into the frame the dataset's own file used. Either way the
    file says which frame it is in -- "displayed" keeps `_orientation`, "original"
    drops it, because the original frame is by definition unrotated.

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

    # If the view has been rotated/flipped, the caller chooses which frame to
    # write: what they see, or the coordinates the original file used. Default is
    # what they see -- that is what they have been editing against.
    o = ORI.normalise(fc.get("_orientation") or ORI.get(ds_id))
    frame = "original" if str((body or {}).get("frame") or "").lower() == "original" \
            else "displayed"
    if frame == "original":
        if not ORI.is_identity(o):
            try:
                p = pyramid(ds_id)
                W0, H0 = p.info()["width"], p.info()["height"]
            except Exception:
                w, h = _regions_extent(fc)
                W0, H0 = ORI.out_size(w, h, ORI.invert(o))
            dw, dh = ORI.out_size(W0, H0, o)
            feats = ORI.transform_features(feats, ORI.invert(o), dw, dh)
        fc = {**fc, "features": feats}
        fc.pop("_orientation", None)
    else:
        # A client can post an fc that never carried the marker -- the frame is
        # still rotated, so say so, or the recipient has no way to know.
        fc = {**fc, "features": feats}
        if ORI.is_identity(o):
            fc.pop("_orientation", None)
        else:
            fc["_orientation"] = o

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

    # Two exports of the same regions in different frames must not arrive as two
    # files with the same name -- the second would land as "(1)" and there would be
    # nothing to say which was which.
    tag = _frame_tag(frame, o)

    if mode == "separate":
        buf = io.BytesIO()
        used: dict[str, int] = {}
        # The file-level members belong to the FILE, not to the merged collection:
        # a per-region file out of a rotated export has to carry the frame marker
        # and the trail too, or it is an unlabelled bag of coordinates.
        shell = {k: v for k, v in fc.items() if k not in ("type", "features")}
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
            for f in feats:
                nm = str((f.get("properties") or {}).get(d["id_prop"]) or "region")
                safe = re.sub(r"[^A-Za-z0-9_.-]+", "_", nm).strip("_") or "region"
                k = used.get(safe, 0)
                used[safe] = k + 1
                fname = f"{safe}.geojson" if k == 0 else f"{safe}_{k}.geojson"
                z.writestr(fname, json.dumps(
                    {**shell, "type": "FeatureCollection", "features": [f]}, indent=1))
        buf.seek(0)
        return Response(buf.read(), media_type="application/zip", headers={
            "Content-Disposition": f'attachment; filename="regions_separate{tag}.zip"'})

    return Response(json.dumps(fc, indent=1), media_type="application/geo+json",
                    headers={"Content-Disposition":
                             f'attachment; filename="regions_merged{tag}.geojson"'})


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
    nfo = gd.info(defaults)
    # The gene layer is one bitmap placed by world bounds. Left un-rotated it
    # stays put while the morphology turns underneath it -- two pictures of the
    # same section on screen at once.
    o = ORI.get(ds_id)
    if not ORI.is_identity(o):
        left, bottom, right, top = nfo["bounds"]
        w, h = ORI.out_size(abs(right - left), abs(bottom - top), o)
        nfo["bounds"] = [0, h, w, 0]
        if ORI.normalise(o)["rot"] in (90, 270):
            nfo["grid"] = [nfo["grid"][1], nfo["grid"][0]]
    nfo["orientation"] = o
    return nfo


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
    o = ORI.get(ds_id)
    if ORI.is_identity(o):
        return Response(content=gd.composite_png(spec), media_type="image/png")
    # Turn the composite itself, so it lands on the rotated bounds /genes reports.
    rgba = ORI.transform_image(gd.composite(spec), o)
    buf = io.BytesIO()
    PILImage.fromarray(rgba, mode="RGBA").save(buf, format="PNG")
    return Response(content=buf.getvalue(), media_type="image/png")


@app.get("/api/datasets/{ds_id}/stains")
def stains_info(ds_id: str):
    try:
        ss = stainstack(ds_id)
    except KeyError:
        raise HTTPException(404, "no morphology_focus stains")
    nfo = ss.info()
    # Same displayed frame as the morphology, or the stain layer would be laid out
    # on the un-rotated extent and slide off the image.
    o = ORI.get(ds_id)
    w, h = ORI.out_size(nfo["width"], nfo["height"], o)
    nfo["width"], nfo["height"] = int(w), int(h)
    nfo["orientation"] = o
    return nfo


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
    # A mangled spec must degrade to "nothing to draw", not a 500. Decoding can
    # succeed and still hand back a dict or a list of strings -- '+' and '/' in
    # base64 are re-read as spaces in a query string -- and blending would then
    # blow up per tile, which reads as the whole stain layer being broken.
    if not isinstance(spec, list):
        spec = []
    spec = [c for c in spec if isinstance(c, dict)]
    png = ss.composite_tile(spec, level, x, y, orient=ORI.get(ds_id))
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
        # `_DIST / path` is NOT safe on its own: pathlib discards the left side
        # when the right is absolute, so a request for "/C:/Windows/win.ini" (or
        # "//etc/passwd" on macOS) resolved to that file and served it. Neither
        # uvicorn nor Starlette collapses ".." for us either. Resolve, then
        # require the result to be inside _DIST before serving it.
        try:
            f = (_DIST / path).resolve()
            f.relative_to(_DIST.resolve())
        except (ValueError, OSError):
            return FileResponse(str(_DIST / "index.html"))
        if f.is_file():
            return FileResponse(str(f))
        return FileResponse(str(_DIST / "index.html"))   # SPA fallback

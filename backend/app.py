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
from starlette.concurrency import run_in_threadpool

import appversion
import cachefs
import config
import datasets as ds
import geo
from PIL import Image as PILImage

import orientation as ORI
import scan
import tilecache
import damage as DMG
import notes
import provenance
import topology
from transcripts import GeneDensity
from stains import StainStack
from tiles import Pyramid

DEFAULT_GENES = ["Slc17a7", "Calb2", "Pvalb"]

app = FastAPI(title="FiveAtlas", version=config.VERSION)
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
        ss = StainStack(mf, ds.workdir_for(ds_id))
        # scan() registers the source whenever the folder or zip is THERE, so a
        # morphology_focus that is empty, still copying, or named outside the two
        # ch0000_*/morphology_focus_0000 conventions builds a stack with no members.
        # _prime() then no-ops and the stack has no levels and no size. Treat that as
        # "no stains" here, once, so every stain route 404s alike -- reading it as a
        # live stack got as far as int(None) in stains_info and returned a 500.
        if ss.W0 is None or ss.H0 is None:
            ss.close()
            raise KeyError("morphology_focus has no readable stain channels")
        _STAINS[ds_id] = ss
    return _STAINS[ds_id]


@app.get("/api/health")
def health():
    return {"ok": True}


@app.get("/api/update-check")
def update_check():
    """Is a newer release on GitHub? Cached for hours; offline returns quietly.
    When there is one and this is the installed app, it starts downloading in
    the background (selfupdate.py); nothing is installed until the person
    restarts, or next opens the app."""
    import updatecheck
    import selfupdate
    res = updatecheck.check(config.VERSION)
    try:
        selfupdate.prepare(res)
    except Exception as e:
        print(f"[update] {type(e).__name__}: {e}", file=sys.stderr)
    return {k: v for k, v in res.items() if k != "assets"}


@app.get("/api/update/status")
def update_status():
    """Where the background update is: unsupported | idle | downloading |
    ready | failed, with progress and the version it is for."""
    import selfupdate
    return selfupdate.status()


@app.post("/api/update/apply")
def update_apply():
    """Switch to the downloaded version: a helper waits for this process to
    exit, swaps the installed copy and starts the new one, which opens its own
    tab. The page asks about unsaved edits before calling this."""
    import signal
    import threading
    import selfupdate
    try:
        version = selfupdate.apply()
    except RuntimeError as e:
        raise HTTPException(409, str(e))

    def _stop():
        try:
            os.kill(os.getpid(), signal.SIGTERM)
        except Exception:
            os._exit(0)
    threading.Timer(0.4, _stop).start()
    return {"ok": True, "version": version}


@app.get("/api/selftest")
def selftest():
    """Exercise the native stack (JPEG2000, blosc/zarr, GEOS, YAML, Pillow, Tk)
    on embedded data and report per feature. CI refuses to publish a build where
    any row fails; a colleague can open it to see what their copy can do."""
    import selftest as _st
    return _st.run()


# -- lifecycle -----------------------------------------------------------------
# The packaged app on macOS has no window and no console, and the process has no
# Cocoa event loop, so the Dock cannot quit it. The UI is the only handle the
# user has on it. `ping` is the UI's heartbeat; `quit` is the UI's Quit button.
LAST_PING = None          # monotonic time of the last heartbeat, None until the
                          # first UI ever connects -- the launcher's watchdog
                          # only starts counting after that


@app.post("/api/ping")
def ping():
    import time
    global LAST_PING
    LAST_PING = time.monotonic()
    return {"ok": True}


@app.post("/api/quit")
def quit_app():
    """Stop the server -- after this response has gone out, so the UI can show
    "stopped" rather than a connection error. SIGTERM, because that is what
    uvicorn already handles for a graceful exit (and on Windows os.kill with
    SIGTERM ends the process outright, which is also what the user asked for)."""
    import signal
    import threading

    def _stop():
        try:
            os.kill(os.getpid(), signal.SIGTERM)
        except Exception:
            os._exit(0)
    threading.Timer(0.4, _stop).start()
    return {"ok": True, "stopping": True}


@app.get("/api/version")
def version_info():
    """The version running, and -- on a Mac -- the version now installed in its
    place. They differ when FiveAtlas.app was replaced while this one kept
    running; the page then offers Restart (see /api/restart)."""
    return {"version": config.VERSION,
            "installed": appversion.installed_version(),
            "canRestart": appversion.bundle_dir() is not None}


@app.post("/api/restart")
def restart_app():
    """Switch to the FiveAtlas installed over this one (Mac): start the app bundle
    again a moment after this process has exited, so the new copy -- not this
    one -- takes the port and opens its own tab. macOS alone would only wake this
    old process when the app is opened again."""
    import signal
    import threading

    app_dir = appversion.bundle_dir()
    if app_dir is None:
        raise HTTPException(409, "restarting by itself only works in the Mac app "
                                 "-- close FiveAtlas and open it again")
    subprocess.Popen(["/bin/sh", "-c", 'sleep 2; /usr/bin/open -n "$0"', str(app_dir)],
                     stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                     stderr=subprocess.DEVNULL, start_new_session=True)

    def _stop():
        try:
            os.kill(os.getpid(), signal.SIGTERM)
        except Exception:
            os._exit(0)
    threading.Timer(0.4, _stop).start()
    return {"ok": True, "restarting": True}


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
    # AFTER the readers are gone: a tile route reads the generation before it
    # gets a reader, so any request that could still see an old reader holds the
    # old generation, and its tile is never served again.
    tilecache.drop_dataset(ds_id)


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


def _picker_cmd(kind: str, initial: str = "", name: str = ""):
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
    initial = initial or _last_folder()
    env = dict(os.environ)
    env["ATLAS_CHILD"] = "1"
    env["ATLAS_PICK_KIND"] = kind
    env["ATLAS_PICK_INITIAL"] = initial
    env["ATLAS_PICK_NAME"] = name or ""
    if config.FROZEN:
        return [sys.executable, "--pick", kind, initial, name or ""], env
    return [sys.executable, _DIALOG_SCRIPT, kind, initial, name or ""], env


def _run_picker(kind: str, initial: str = "", name: str = "") -> str:
    """Spawn the picker in its own process so a modal dialog never blocks uvicorn."""
    if kind not in ("folder", "file", "save"):
        raise HTTPException(400, f"bad picker kind {kind!r}")
    argv, env = _picker_cmd(kind, initial, name)
    try:
        res = subprocess.run(argv, capture_output=True, text=True, timeout=600, env=env)
    except Exception as e:
        raise HTTPException(500, f"browse failed: {e}")
    lines = [ln for ln in (res.stdout or "").splitlines() if ln.strip()]
    return lines[-1].strip() if lines else ""


def _same_file(a, b) -> bool:
    """Whether two paths name the same file.

    String comparison is not enough here, and getting it wrong would overwrite
    a shared original: a mapped drive and its UNC form (S:\\... vs
    \\\\server\\share\\...) spell one file two ways, and so do case and
    8.3 short names on Windows. When both exist the filesystem answers
    (samefile compares volume and file id); otherwise normalised strings.
    """
    try:
        if os.path.exists(a) and os.path.exists(b):
            return os.path.samefile(a, b)
    except OSError:
        pass
    def keys(p):
        out = {os.path.normcase(os.path.abspath(str(p)))}
        try:
            out.add(os.path.normcase(str(Path(p).resolve())))
        except Exception:
            pass
        return out
    return bool(keys(a) & keys(b))


def _why_not_written(path, e) -> str:
    """What to tell the annotator when a Save-as file could not be written --
    in words, and about THEIR file, not the temp file the write goes through."""
    if isinstance(e, FileNotFoundError):
        why = "that folder can't be found (is the drive connected?)"
    elif isinstance(e, PermissionError):
        why = "there is no permission to write there"
    elif isinstance(e, OSError) and e.strerror:
        why = e.strerror
    else:
        why = f"{type(e).__name__}: {e}"
    return f"Could not write {path}: {why}."


def _original_region_paths(ds_id: str):
    """Every region file this dataset was scanned FROM -- or None when that
    cannot be established right now.

    None is the case that matters. A dataset registered while its folder was
    unreachable is a stub with `sources: {}` (datasets._stub), and the guard used
    to ask `any(... for r in sources["regions"] or [])` -- over an EMPTY list,
    which answers "not an original" for every path on earth. Save as then wrote
    straight over a shared, hand-corrected prediction file and recorded it in the
    `written` whitelist; that list is never pruned, so from then on every
    ordinary Save rewrote that file too. Measured end to end: 36 MB of edits
    replaced a fixture's merged.regions.geojson and came back as a normal
    successful save with exportError: None.

    So: a scan we have is used; no scan means try again now (the drive may be
    back); still nothing means list the folder ourselves; and if even that fails
    we return None and the caller refuses. An unreachable original cannot be
    written anyway, so refusing costs nothing.
    """
    try:
        d = ds.get_dataset(ds_id)
    except KeyError:
        return None
    srcs = (d.get("sources") or {}).get("regions")
    if srcs:
        return [str(r["path"]) for r in srcs if r.get("path")]
    if isinstance(srcs, list):
        return []                       # scanned, and it really has no region file
    root = d.get("root")
    if not root:
        return None
    try:                                # the folder may be reachable again
        fresh = scan.scan_folder(root)
        got = ((fresh.get("sources") or {}).get("regions")) or []
        return [str(r["path"]) for r in got if r.get("path")]
    except Exception:
        pass
    try:                                # last resort: anything that looks like regions
        return [str(p) for p in Path(root).glob("*.geojson")]
    except Exception:
        return None


def _is_original(ds_id: str, path) -> bool:
    """A region file a dataset was scanned from -- a shared original that Save as
    must never overwrite -- unless THIS dataset wrote it itself."""
    own = _original_region_paths(ds_id)
    if own is None:
        return True                     # cannot verify -> fail closed
    written = geo.get_save_target(ds_id)["written"]
    if not any(_same_file(path, w) for w in written):
        if any(_same_file(path, r) for r in own):
            return True
    # Another dataset's original is protected whatever this dataset's whitelist
    # says. The whitelist exists so an annotator's own file, saved beside the
    # originals and picked up by the next scan, is not then refused to them --
    # that argument does not reach into somebody else's dataset. Without this,
    # posting dataset B's original as dataset A's save-as target wrote it (3
    # features -> 1) while the same path posted to B was correctly refused.
    for row in ds.all_datasets():
        oid = row.get("id")
        if not oid or oid == ds_id:
            continue
        try:
            other = ds.get_dataset(oid)
        except KeyError:
            continue
        for r in ((other.get("sources") or {}).get("regions") or []):
            if r.get("path") and _same_file(path, r["path"]):
                return True
    return False


def _suggest_new_name(ds_id: str, path) -> str:
    """A name beside `path` that is neither an original nor someone else's file:
    merged.regions.geojson -> merged.regions_edited.geojson (then _edited_2...).
    It is written without a dialog, so it must not already exist unless the app
    wrote it."""
    p = Path(path)
    stem = p.name[:-len(".geojson")] if p.name.lower().endswith(".geojson") else p.stem
    written = geo.get_save_target(ds_id)["written"]
    for n in range(1, 1000):
        cand = p.with_name(f"{stem}_edited{'' if n == 1 else '_' + str(n)}.geojson")
        mine = any(_same_file(cand, w) for w in written)
        if not _is_original(ds_id, cand) and (not cand.exists() or mine):
            return str(cand)
    raise HTTPException(500, "could not find a free file name")


def _in_original_frame(ds_id: str, fc: dict) -> dict:
    """The regions in the coordinates the dataset's own files use.

    The working copy is kept as displayed, rotation and flips included. A file
    that sits next to the prediction GeoJSONs, or is opened in another tool,
    has to line up with them and with the image, so it is turned back into
    the original frame; the original frame is unrotated by definition, so the
    `_orientation` marker is dropped.
    """
    feats = fc.get("features", [])
    o = ORI.normalise(fc.get("_orientation") or ORI.get(ds_id))
    if not ORI.is_identity(o):
        try:
            p = pyramid(ds_id)
            W0, H0 = p.info()["width"], p.info()["height"]
        except Exception:
            W0, H0 = _original_canvas(ds_id, fc, o)
        dw, dh = ORI.out_size(W0, H0, o)
        feats = ORI.transform_features(feats, ORI.invert(o), dw, dh)
    out = {**fc, "features": feats}
    out.pop("_orientation", None)
    return out


def _regions_from_body(body):
    """(fc, actions) from a save body: the FeatureCollection itself (the original
    contract) or {fc, actions}, where `actions` are edits the client made without
    a round trip -- renames, colour changes, deletions, dragged points. They have
    no endpoint to stamp them, so they are recorded at the save that commits them.

    Refuses anything that is not a FeatureCollection. Without this, a body of
    null/{}/[] resolved to fc={} and was written straight over the working copy:
    the frontend sets fc=null while a dataset loads and the Save button is live
    during that window, so switching datasets and clicking Save too early
    silently replaced that dataset's edits with an empty file."""
    if isinstance(body, dict) and body.get("type") == "FeatureCollection":
        fc, actions = body, []
    else:
        fc = (body or {}).get("fc") or {}
        actions = (body or {}).get("actions") or []
    if not isinstance(fc, dict) or not isinstance(fc.get("features"), list):
        raise HTTPException(
            400, "expected a FeatureCollection (or {fc, actions}) with a "
                 "'features' list; refusing to overwrite the working copy")
    return fc, actions


def _commit_save(ds_id: str, fc: dict, actions) -> dict:
    """Stamp and write the working copy (plus its snapshot), then -- if the
    annotator chose a file with Save as -- write that too.

    The working copy always comes first and always stands: a Save-as file that
    cannot be written (drive offline, no permission) is reported back, never
    allowed to lose the edits."""
    for a in actions if isinstance(actions, list) else []:
        if isinstance(a, dict) and a.get("action"):
            provenance.stamp(fc, a["action"], a.get("detail"), a.get("regions"))
    provenance.stamp(fc, "save", f"{len(fc.get('features', []))} regions")
    res = geo.save_edited(ds_id, fc)
    out = {"saved": res["saved"], "version": res["version"],
           "versions": res["versions"], "features": len(fc.get("features", [])),
           "provenance": provenance.trail(fc),
           "exported": None, "exportError": None}
    target = geo.get_save_target(ds_id)["path"]
    if target:
        if _is_original(ds_id, target):
            out["exportError"] = (f"{target} is one of the dataset's original region "
                                  "files, which are never overwritten -- use Save as "
                                  "to pick another file")
        else:
            try:
                geo.write_geojson_atomic(target, _in_original_frame(ds_id, fc))
                out["exported"] = target
            except Exception as e:
                out["exportError"] = (_why_not_written(target, e)
                                      + " Your work IS saved in the app's own folder.")
    out["saveTarget"] = target
    return out


@app.post("/api/datasets/{ds_id}/regions/save-as")
async def regions_save_as(ds_id: str, request: Request):
    """Save, choosing the file. Body: {fc, actions?, path?}.

    With no `path` the native Save dialog opens, starting beside the file chosen
    last time (else in the dataset's folder). The chosen file is remembered, so
    later clicks on Save update it too; the app's own working copy and snapshots
    are still written every time, as the backup.

    The dataset's original region files are never overwritten -- they are the
    shared predictions everyone else uses. Choosing one returns
    {refused, message, suggested}: a free name beside it, and nothing written.
    """
    try:
        d = ds.get_dataset(ds_id)
    except KeyError:
        raise HTTPException(404, "unknown dataset")
    body = await request.json()
    fc, actions = _regions_from_body(body)

    path = (body or {}).get("path")
    if not path:
        current = geo.get_save_target(ds_id)["path"]
        if current:
            start, name = str(Path(current).parent), Path(current).name
        else:
            primary = scan.primary_regions_path(d)
            start = str(Path(d["root"])) if d.get("root") else ""
            name = (Path(primary).name[:-len(".geojson")] + "_edited.geojson"
                    if primary and str(primary).lower().endswith(".geojson")
                    else f"{ds_id}_regions.geojson")
        # a modal dialog can sit open for minutes: keep it off the event loop, or
        # every tile request queues behind it
        path = await run_in_threadpool(_run_picker, "save", start, name)
        if not path:
            return {"cancelled": True}
    path = str(path)
    if not path.lower().endswith(".geojson"):
        path += ".geojson"
    if not os.path.isabs(path):
        raise HTTPException(400, "need an absolute path")

    if _is_original(ds_id, path):
        return {"refused": True, "chosen": path,
                "suggested": _suggest_new_name(ds_id, path),
                "message": (f"{Path(path).name} is one of this dataset's original "
                            "region files. It is never overwritten, so everyone keeps "
                            "the original prediction.")}

    # Remember the file only once it has actually been written: a location that
    # cannot be written would otherwise turn every later Save into an error.
    previous = geo.get_save_target(ds_id)["path"]
    geo.clear_save_target(ds_id)          # this save writes only the new choice
    out = _commit_save(ds_id, fc, actions)
    try:
        geo.write_geojson_atomic(path, _in_original_frame(ds_id, fc))
    except Exception as e:
        if previous:                       # a bad new choice must not lose the old one
            geo.set_save_target(ds_id, previous)
        out["exportError"] = (_why_not_written(path, e)
                              + " Your work IS saved in the app's own folder.")
        out["exported"] = None
        out["saveTarget"] = previous
        return out
    geo.set_save_target(ds_id, path)
    out["exported"] = path
    out["saveTarget"] = path
    o = ORI.normalise(fc.get("_orientation") or ORI.get(ds_id))
    out["originalFrame"] = not ORI.is_identity(o)
    return out


@app.get("/api/datasets/{ds_id}/regions/save-target")
def get_save_target(ds_id: str):
    try:
        ds.get_dataset(ds_id)
    except KeyError:
        raise HTTPException(404, "unknown dataset")
    return {"path": geo.get_save_target(ds_id)["path"]}


@app.delete("/api/datasets/{ds_id}/regions/save-target")
def forget_save_target(ds_id: str):
    """Stop writing to the Save-as file. The file itself is left alone."""
    try:
        ds.get_dataset(ds_id)
    except KeyError:
        raise HTTPException(404, "unknown dataset")
    geo.clear_save_target(ds_id)
    return {"path": None}


def _revealable(p: Path) -> bool:
    """Inside the app's working folder, or a file this app itself wrote with
    Save as. Never an arbitrary location."""
    try:
        p.relative_to(Path(config.WORKDIR).resolve())
        return True
    except ValueError:
        pass
    for did in list(ds._REGISTRY):
        if any(_same_file(p, w) for w in geo.get_save_target(did)["written"]):
            return True
    return False


@app.post("/api/reveal")
async def reveal(request: Request):
    """Show a saved file in Explorer / Finder, already selected.

    This is the "where did my save go?" button. Saves land in the app's working
    folder, which sits under AppData on Windows and ~/Library on a Mac -- both
    hidden by default, so pointing at them in words was not enough.

    Only paths inside that working folder, or files this app wrote with Save
    as, are accepted: the page asks to show its own save, never an arbitrary
    location on the machine.
    """
    body = await request.json()
    raw = (body or {}).get("path")
    if not raw:
        raise HTTPException(400, "need 'path'")
    try:
        p = Path(str(raw)).resolve()
    except Exception:
        raise HTTPException(400, "not a usable path")
    if not _revealable(p):
        raise HTTPException(403, "only files FiveAtlas saved can be shown")
    if not p.exists():
        raise HTTPException(404, f"not found: {p}")
    try:
        if sys.platform == "win32":
            # /select opens the folder with the file highlighted
            if p.is_file():
                subprocess.Popen(["explorer", f"/select,{p}"])
            else:
                os.startfile(str(p))
        elif sys.platform == "darwin":
            subprocess.Popen(["open", "-R", str(p)] if p.is_file() else ["open", str(p)])
        else:
            subprocess.Popen(["xdg-open", str(p if p.is_dir() else p.parent)])
    except Exception as e:
        raise HTTPException(500, f"could not open a file window: {e}")
    return {"shown": str(p)}


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
    prev = next((dict(d) for d in ds._REGISTRY.values()
                 if str(d.get("root")) == str(path)), None)
    try:
        desc = ds.open_path(path)
    except Exception as e:
        raise HTTPException(400, f"scan failed: {e}")
    # a re-scan can change file paths -> drop cached readers for this dataset.
    # Close them, don't just drop them: re-opening a dataset whose files moved
    # would otherwise leave the OLD paths locked for the rest of the session.
    # But ONLY when something actually changed: re-opening the folder that is
    # already being viewed used to close the readers mid-request, and every
    # tile/contrast call in flight died on a closed handle -- which the viewer
    # showed as stain channels going permanently dark until a reload.
    if prev is None or prev.get("sources") != desc.get("sources"):
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
            "sources": d["sources"], "pixelSizeUm": d.get("pixel_size_um"),
            "primaryRegions": scan.primary_regions_path(d),
            "hasEdited": ds.edited_regions_path(ds_id).exists()}


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


def _original_canvas(ds_id: str, fc: dict, o) -> tuple:
    """The dataset's ORIGINAL-frame canvas (W0, H0), for a dataset whose imagery
    cannot be read -- an offline drive, a stub, or imagery simply unticked.

    With an image this is just its width and height, fixed for ever. Without
    one it used to be derived from the regions' own bounding box on every call:

        w, h = _regions_extent(fc); W0, H0 = ORI.out_size(w, h, ORI.invert(o))

    The bounding box MOVES when the regions move. So the forward transform and
    the inverse transform were run against different canvases and stopped being
    inverses of each other. Measured on a regions-only folder (regions spanning
    x 3000-9000, y 5000-11000): rotating to 90 and back to 0 shifted every
    vertex by 2,900 px -- 0.6 mm at 0.2125 um/px -- and 12 of the 16 rot/flip
    combinations failed to round-trip, by 2,900-4,900 px. Only rot0,
    rot90+flipV, rot180+flipH+flipV and rot270+flipH happened to survive. The
    displacement was written straight to the working copy and rode out through
    Save as, so it outlived the drive coming back.

    The canvas is therefore established ONCE and reused: whatever the image's
    width and height would have been, had it been readable. It is stored beside
    the working copy, not in orientation.json, because ORI.normalise() keeps
    only rot/flipH/flipV and would drop it.
    """
    rec_path = ds.workdir_for(ds_id) / "canvas.json"
    try:
        rec = json.load(open(rec_path, encoding="utf-8"))
        W0, H0 = float(rec["w"]), float(rec["h"])
        if W0 > 0 and H0 > 0:
            return W0, H0
    except Exception:
        pass
    w, h = _regions_extent(fc)
    W0, H0 = ORI.out_size(w, h, ORI.invert(o))
    try:
        rec_path.parent.mkdir(parents=True, exist_ok=True)
        with open(rec_path, "w", encoding="utf-8") as f:
            json.dump({"w": float(W0), "h": float(H0)}, f)
    except Exception:
        pass                              # unwritable workdir: still consistent
                                          # within this call, as before
    return W0, H0


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


def _cache_ready(ds_id: str, remapped: dict):
    """Every mirrored file is local and verified: point the registry at the
    mirror and drop the network-backed readers so the next request opens the
    local copies. Runs on the copy thread."""
    ds._REGISTRY[ds_id] = remapped
    _evict(ds_id)
    print(f"[cache] {ds_id}: reads switched to local mirror")


@app.get("/api/datasets/{ds_id}/cache")
def dataset_cache(ds_id: str):
    try:
        ds.get_dataset(ds_id)
    except KeyError:
        raise HTTPException(404, "unknown dataset")
    return cachefs.status(ds_id)


@app.get("/api/cache")
def cache_usage():
    return cachefs.usage()


@app.post("/api/cache/clear")
def cache_clear():
    for did in list(ds._REGISTRY):
        _evict(did)
    try:
        return cachefs.clear()
    except RuntimeError as e:
        raise HTTPException(409, str(e))


@app.get("/api/datasets/{ds_id}/info")
def info(ds_id: str, noimg: int = 0):
    """`noimg=1`: the viewer chose not to load imagery, so do not OPEN it --
    the canvas is sized from the regions instead, and not a single imagery
    byte is read."""
    try:
        d = ds.get_dataset(ds_id)
    except KeyError:
        raise HTTPException(404, "unknown dataset")
    # Selecting a dataset is the moment to start mirroring a network-hosted
    # one to local disk (idempotent; no-op for local folders and once ready).
    if not noimg:
        try:
            cachefs.start(ds_id, d, _cache_ready)
        except Exception as e:
            print(f"[cache] start failed for {ds_id}: {e}", file=sys.stderr)
    o = ORI.get(ds_id)
    try:
        if noimg:
            raise NoImagery(ds_id)
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
        # The DISPLAYED canvas, from the same fixed original canvas every
        # transform uses -- not the regions' current bounding box, which moves
        # with them and made this answer disagree with the transforms by 60 px
        # on a 600x500 fixture.
        w, h = ORI.out_size(*_original_canvas(ds_id, fc, o), o)
        w, h = int(w), int(h)
        # `orientation` belongs in THIS answer too. Without it the UI cannot tell
        # the view is turned, so the Export frame picker never appears and the
        # frame is chosen silently -- the worst version of the rotated-coordinates
        # mix-up, and it reaches anyone who simply unticked the imagery.
        return {"id": ds_id, "label": d["label"], "idProp": d["id_prop"],
                "pixelSizeUm": d.get("pixel_size_um"), "width": w, "height": h,
                "levels": 1, "tileSize": 512, "nZ": 1, "orientation": o,
                "offline": True}


def _okey(o) -> tuple:
    """Orientation as a hashable tile-cache key part."""
    o = ORI.normalise(o)
    return (o["rot"], bool(o["flipH"]), bool(o["flipV"]))


@app.get("/api/datasets/{ds_id}/tiles/{z}/{x}/{y}.png")
def tile(ds_id: str, z: int, x: int, y: int, plane: Optional[int] = None):
    gen = tilecache.generation(ds_id)      # before the reader -- see _evict
    try:
        p = pyramid(ds_id)
    except NoImagery:
        return Response(status_code=204)      # blank canvas, same as an empty tile
    except KeyError:
        raise HTTPException(404, "unknown dataset")
    level = (p.nlevels - 1) - z
    o = ORI.get(ds_id)
    key = (ds_id, gen, "morph", level, x, y, plane, _okey(o))
    png = tilecache.get(key)
    if png is None:
        try:
            png = p.tile_png(level, x, y, plane, orient=o)
        except KeyError:
            raise HTTPException(404, "dataset closed while reading")
        if png is None:
            return Response(status_code=204)
        tilecache.put(key, png)
    return Response(content=png, media_type="image/png",
                    headers={"Cache-Control": "max-age=3600"})


@app.get("/api/datasets/{ds_id}/tiles/raw/{z}/{x}/{y}")
def tile_raw(ds_id: str, z: int, x: int, y: int, plane: Optional[int] = None):
    """The morphology tile as raw little-endian uint16 (tile x tile), for the
    browser to window (brightness/contrast) on the GPU. No contrast in the URL,
    so a slider move fetches nothing -- the 16-bit counterpart of the .png route
    above, mirroring /stains/raw. 503 (not 500) on a transient read error so the
    viewer retries the tile instead of leaving a permanent gap.

    Cached in tilecache exactly like the .png route (generation + orientation in
    the key): unlike StainStack, Pyramid keeps no decoded tiles of its own, so
    without this every browser-cache miss re-decoded JPEG2000 -- 0.1-0.5 s a tile
    over a network drive."""
    gen = tilecache.generation(ds_id)      # before the reader -- see _evict
    try:
        p = pyramid(ds_id)
    except NoImagery:
        return Response(status_code=204)
    except KeyError:
        raise HTTPException(404, "unknown dataset")
    level = (p.nlevels - 1) - z
    if level < 0 or level >= p.nlevels:
        return Response(status_code=204)
    o = ORI.get(ds_id)
    key = (ds_id, gen, "morphraw", level, x, y, plane, _okey(o))
    data = tilecache.get(key)
    if data is None:
        try:
            data = p.tile_raw_bytes(level, x, y, plane, orient=o)
        except KeyError:
            raise HTTPException(404, "dataset closed while reading")
        except Exception as e:
            print(f"[tiles] {ds_id} raw z={z} x={x} y={y}: {type(e).__name__}: {e}",
                  file=sys.stderr)
            raise HTTPException(503, "reading the tile failed -- try again")
        if data is None:
            return Response(status_code=204)
        tilecache.put(key, data)
    return Response(content=data, media_type="application/octet-stream",
                    headers={"Cache-Control": "max-age=3600",
                             "X-Tile-Size": str(p.tile)})


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
    fc, actions = _regions_from_body(body)
    return _commit_save(ds_id, fc, actions)


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
        # the dataset's own file is as imaged: bring it into the current rotation
        # (and say so) before anyone sees it -- the working copy too
        res["fc"] = _to_current_frame(ds_id, res["fc"])
        geo.save_edited(ds_id, res["fc"])
    except FileNotFoundError as e:
        raise HTTPException(422, str(e))
    except Exception as e:
        raise HTTPException(500, f"restore failed: {e}")
    return {**provenance.stamped(res["fc"].get("features", []), res["fc"],
                                 "restore-original", Path(res["source"]).name),
            "source": res["source"], "backup": res["backup"], "count": res["features"]}


def _to_current_frame(ds_id: str, fc: dict) -> dict:
    """`fc` moved from ITS frame into the dataset's current orientation, and
    tagged with it. A region file says which frame it is in with `_orientation`;
    one with no tag is as imaged (identity). Files read from disk, and the
    dataset's own GeoJSON on Restore original, used to come in untransformed
    while the image was rotated -- 24,745 px off on the real set -- and with no
    tag, so every later rotate or export took them for already rotated."""
    want = ORI.get(ds_id)
    have = ORI.normalise(fc.get("_orientation") or {"rot": 0, "flipH": False, "flipV": False})
    if have != want:
        try:
            p = pyramid(ds_id)
            W0, H0 = p.info()["width"], p.info()["height"]
        except Exception:
            W0, H0 = _original_canvas(ds_id, fc, have)
        feats = fc.get("features", [])
        if not ORI.is_identity(have):
            dw, dh = ORI.out_size(W0, H0, have)
            feats = ORI.transform_features(feats, ORI.invert(have), dw, dh)
        if not ORI.is_identity(want):
            feats = ORI.transform_features(feats, want, W0, H0)
        fc = {**fc, "features": feats}
    fc["_orientation"] = want
    return fc


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
    if not isinstance(body, dict):
        raise HTTPException(400, "expected an object with {rot, flipH, flipV, fc?}")
    # ORI.normalise swallows a bad rot and reports 0, so a mistyped request read
    # back as a successful one.
    if "rot" in body:
        try:
            float(body["rot"])
        except (TypeError, ValueError):
            raise HTTPException(
                400, f"rot must be a number of degrees, not {body['rot']!r}")
    want = ORI.normalise(body)
    have = ORI.get(ds_id)

    # This route WRITES the working copy, so the body gets the same check a save
    # gets. It used to take whatever was there: an fc with no features, an empty
    # features list, and an fc with no 'features' key each returned 200 and left
    # the working copy holding 0 features -- three shapes PUT /regions refuses
    # with 400 for exactly this reason.
    if (body.get("fc") or None) is not None:
        fc, _ = _regions_from_body({"fc": body["fc"]})
        if not fc.get("features"):
            raise HTTPException(400, "that body has no regions in it; refusing "
                                     "to overwrite the working copy")
    else:
        fc = geo.load_regions(ds_id)[0]
    have = ORI.normalise(fc.get("_orientation") or have)

    try:
        p = pyramid(ds_id)
        W0, H0 = p.info()["width"], p.info()["height"]
    except Exception:                       # no imagery: one fixed canvas
        W0, H0 = _original_canvas(ds_id, fc, have)

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
    # Regions first, the dataset's orientation only once they are safely
    # written: the other way round, a failed save left orientation.json at the
    # new rotation with the regions still in the old frame -- 24,745 px off, and
    # the natural retry then made it permanent.
    geo.save_edited(ds_id, out)
    ORI.put(ds_id, want)
    w, h = ORI.out_size(W0, H0, want)
    return {**out, "orientation": want, "width": int(w), "height": int(h)}


@app.post("/api/datasets/{ds_id}/regions/smartsheet.tsv")
async def smartsheet_tsv(ds_id: str, request: Request):
    """One TSV row per region, ready to paste into the tracking sheet.

    Body: {fc?, damageFc?, notes?, extraDamage?, minOverlap?, mode?}. Returns the
    text AND the assignment behind it, so the UI can show a review table rather than
    handing over a block nobody can check. Read-only: nothing is written.

    `mode` is "dominant" (default) or "all" -- see damage.assign. Shapes the
    annotator has already answered for carry their answer in their own properties,
    so the choice is read back out of the file, not held in the session.
    """
    try:
        d = ds.get_dataset(ds_id)
    except KeyError:
        raise HTTPException(404, "unknown dataset")
    body = await request.json() or {}
    fc = body.get("fc") or geo.load_regions(ds_id)[0]
    regions, inline = DMG.split_features(fc.get("features", []), d["id_prop"])
    # damage.geojson when the client has one, else any damage shapes drawn straight
    # into the region file
    dmg = ((body.get("damageFc") or {}).get("features") or []) or inline
    try:
        res = DMG.assign(regions, dmg, d["id_prop"],
                         float(body.get("minOverlap", 0.01)),
                         mode=str(body.get("mode") or "dominant"),
                         choices=DMG.choices_from(dmg, d["id_prop"]))
    except Exception as e:
        raise HTTPException(500, f"damage assignment failed: {e}")
    # Hand-ticked designations live on the region features, so the TSV, the cells
    # and the YAML all say the same thing without the client passing them around.
    extra = dict(DMG.extras_from(regions, d["id_prop"]))
    for k, v in (body.get("extraDamage") or {}).items():
        extra[k] = sorted(set(extra.get(k, [])) | set(v or []))
    text = DMG.tsv(res, body.get("notes"), extra_damage=extra)
    return {"tsv": text, "columns": DMG.TSV_COLUMNS, "shapes": res["shapes"],
            "unassigned": res["unassigned"], "regions": res["regions"],
            "needsChoice": res["needsChoice"], "containers": res["containers"],
            "containment": res["containment"], "mode": res["mode"],
            "designations": {k: {"label": v[0], "drawn": v[1]}
                             for k, v in DMG.DESIGNATIONS.items()}}


@app.post("/api/datasets/{ds_id}/damage/cells")
async def damage_cells(ds_id: str, request: Request):
    """One SmartSheet cell per region: the damage chips for its dropdown.

    The sheet's Damage column is a multi-select, so the thing worth copying is a
    cell, not a row -- `Done`, then the designations by their display names.
    Body: {fc?, sep?, includeEmpty?, mode?, minOverlap?}. Read-only.
    """
    try:
        d = ds.get_dataset(ds_id)
    except KeyError:
        raise HTTPException(404, "unknown dataset")
    body = await request.json() or {}
    id_prop = d["id_prop"]
    fc = body.get("fc") or geo.load_regions(ds_id)[0]
    regions, dmg = DMG.split_features(fc.get("features") or [], id_prop)
    res = DMG.assign(regions, dmg, id_prop, float(body.get("minOverlap", 0.01)),
                     mode=str(body.get("mode") or "dominant"),
                     choices=DMG.choices_from(dmg, id_prop))
    sep = str(body.get("sep") or DMG.DEFAULT_SEPARATOR)
    rows = DMG.cells(res, DMG.extras_from(regions, id_prop),
                     DMG.done_from(regions, id_prop), sep,
                     include_empty=bool(body.get("includeEmpty")))
    named = {r["region"] for r in rows}
    return {"cells": rows, "sep": sep, "separators": sorted(DMG.SEPARATORS),
            "doneLabel": DMG.DONE_LABEL,
            # the shapes still waiting on an answer, and the ones that reached no
            # region at all -- this panel is where they get a second look
            "needsChoice": res["needsChoice"], "unassigned": res["unassigned"],
            "shapes": res["shapes"], "containment": res["containment"],
            # every region, so a region with nothing yet can still be ticked
            "regions": [r for r in sorted(res["regions"]) if r not in named],
            "designations": [{"tag": t, "label": lab, "drawn": drawn}
                             for t, (lab, drawn) in DMG.DESIGNATIONS.items()]}


@app.post("/api/datasets/{ds_id}/damage/canonicalise")
async def damage_canonicalise(ds_id: str, request: Request):
    """Tidy damage shape names to `<designation>.<n>`. Body: {damageFc}.

    Returns the renamed features AND the rename map, because `voids` in the YAML
    lists shapes by name — the caller must rewrite both together or the two files
    stop agreeing. Read-only: nothing is written."""
    try:
        d = ds.get_dataset(ds_id)
    except KeyError:
        raise HTTPException(404, "unknown dataset")
    body = await request.json() or {}
    feats = ((body.get("damageFc") or {}).get("features") or [])
    res = DMG.canonicalise(feats, d["id_prop"])
    return {"type": "FeatureCollection", "features": res["features"],
            "renames": res["renames"], "collisions": res["collisions"]}


def _hold_out(features, id_prop, names):
    """Split off the regions an operation should ignore.

    `hemi` is the outline of the whole hemisphere: it covers every other region,
    so nothing is ever "outside a region" and **no gap can ever be found**. The
    same goes for any container. Rather than special-casing hemi, the client says
    which regions to set aside, and they are put back untouched afterwards.

    Returns (kept, held) where held is [(original index, feature)].
    """
    skip = {str(n) for n in (names or [])}
    if not skip:
        return list(features or []), []
    kept, held = [], []
    for i, f in enumerate(features or []):
        nm = str((f.get("properties") or {}).get(id_prop))
        (held.append((i, f)) if nm in skip else kept.append(f))
    return kept, held


def _gap_exclude(features, id_prop, names):
    """What a gap route may hold out: the switched-off regions EXCEPT the section
    outline (hemi). find_gap already sets the container aside and uses it as the
    bound of a gap; holding it out as well removed that bound, and one click by
    the tissue edge with hemi switched off handed 18.5 M px2 of background
    outside the section to seven regions."""
    skip = {str(n) for n in (names or [])}
    if not skip:
        return []
    try:
        wrap = topology.section_containers(features, id_prop)
    except Exception:
        wrap = set()
    return sorted(skip - set(wrap))


def _put_back(features, held):
    """Re-insert held-out regions at (about) the index they came from, so draw
    order survives an operation that ignored them."""
    out = list(features or [])
    for i, f in sorted(held, key=lambda t: t[0]):
        out.insert(min(i, len(out)), f)
    return out


def _notes_worked_on(fc, id_prop, assignment, account=None, extras=None):
    """The regions THIS annotator worked on, from the trail the file carries.

    Only these are written. Everyone else's entries in a shared notes file must
    pass through untouched, and `_provenance` already knows whose edits are whose.

    A damage shape is recorded under its own name in the trail, not its host's,
    so the regions a shape was assigned to count as worked on too -- otherwise
    drawing damage would never update any region's notes.
    """
    acct = account or provenance.account_name()
    mine, shapes, claimed = set(), set(), set()
    for e in provenance.trail(fc):
        ours = str(e.get("account") or "") == str(acct)
        for r in e.get("regions") or []:
            r = str(r)
            if not ours:
                claimed.add(r)
                continue
            (shapes if DMG.is_damage(r) else mine).add(r)

    # Damage is decided by NAME, not by how the shape got there: drawn with the
    # damage tool, turned into a region from a gap, or an existing region renamed
    # to `bubble.1` -- all the same thing by the time it reaches the notes. So a
    # shape nobody else's trail claims counts as this annotator's; otherwise
    # damage that arrived by a route with no entry would reach nobody's notes.
    for s in (assignment or {}).get("shapes", []):
        nm = s.get("name")
        if nm in shapes or (nm not in claimed):
            mine.update(s.get("regions") or [])

    # A designation nobody can draw -- cutoff, missing, low transcripts -- is
    # ticked ON the region: no shape for the loop above to find, and no trail
    # entry of its own for the one before it. Same rule as a shape, then, or
    # ticking Cutoff on a region you had not otherwise touched saved as nothing
    # at all -- the preview showing no change, and no reason why.
    for nm, tags in (extras or {}).items():
        if tags and str(nm) not in claimed:
            mine.add(str(nm))
    return mine


def _notes_context(ds_id, body):
    """Everything both preview and save need: the assignment, the loaded YAMLs,
    and the edits applied to them. Shared so the two cannot drift apart -- a
    preview that does not match what gets written is worse than no preview."""
    try:
        d = ds.get_dataset(ds_id)
    except KeyError:
        raise HTTPException(404, "unknown dataset")
    body = body or {}
    id_prop = d["id_prop"]
    fc = body.get("fc") or geo.load_regions(ds_id)[0]
    feats = fc.get("features") or []
    regions, dmg = DMG.split_features(feats, id_prop)
    res = DMG.assign(regions, dmg, id_prop, float(body.get("minOverlap", 0.01)),
                     mode=str(body.get("mode") or "dominant"),
                     choices=DMG.choices_from(dmg, id_prop))

    # Undrawn designations (missing, cutoff, transcripts...) have no shape to
    # find, so they can only come from the person; they are stored on the region
    # itself and merge with the drawn ones below. Worked out BEFORE scope,
    # because a tick is one of the things that puts a region in scope.
    extra = dict(DMG.extras_from(regions, id_prop))
    for k, v in (body.get("extraDamage") or {}).items():
        extra[k] = sorted(set(extra.get(k, [])) | set(v or []))

    scope = str(body.get("scope") or "mine")
    worked = _notes_worked_on(fc, id_prop, res, extras=extra)
    only = None if scope == "all" else worked

    # A name is written into files other people read, so it must be a name
    # somebody chose -- never the Windows account by default. Blank means "leave
    # the annotator fields alone", which apply_regions and add_annotator honour.
    annotator = body.get("annotator")
    if annotator is None:
        annotator = provenance.display_name() if provenance.has_display_name() else ""

    updates = {}
    for nm, v in res["regions"].items():
        tags = sorted(set(v["damage"]) | {t for t in extra.get(nm, [])
                                          if t in DMG.DESIGNATIONS})
        updates[nm] = {"damage": tags, "voids": v["voids"]}

    # What this working copy can SPEAK FOR, per region and per key. Everything
    # else the shared YAML holds belongs to another annotator, another machine or
    # another working copy, and notes.apply_regions leaves it alone -- it used to
    # rewrite a whole key from a partial view, which deleted the rest.
    #
    #   voids: a shape THIS copy actually holds. Not "assigned to this region" --
    #     held at all -- so moving a shape from TH to ISO still takes it off TH,
    #     while a void recorded from a shape that is not here survives.
    #   damage: the designation of a shape this copy holds, plus -- only for the
    #     regions whose ticks the client actually sent -- the designations that
    #     are never drawn as a shape (Missing, Cutoff, Low/no transcripts...).
    #     Those live on the region rather than on a shape, so absence is a real
    #     untick, but ONLY when the client was showing that region's ticks;
    #     otherwise absence just means this copy never asked.
    held_voids = {s["name"] for s in res["shapes"]}
    held_tags = {s["designation"] for s in res["shapes"]}
    undrawn = {t for t, (_lbl, drawn) in DMG.DESIGNATIONS.items() if drawn is False}
    asserted = set(body.get("extraDamage") or {})
    known = {}
    for nm in updates:
        tags = set(held_tags)
        if nm in asserted:
            tags |= undrawn
        known[nm] = {"voids": held_voids, "damage": tags}

    found = notes.find(d["root"])
    out = {"dataset": ds_id, "root": d["root"], "assignment": res,
           "workedOn": sorted(worked), "scope": scope, "annotator": annotator,
           "files": {}}

    doc = notes.read(found["notes"]["path"]) if found["notes"]["found"] else None
    if doc is None:
        out["files"]["notes"] = {"found": False, "path": found["notes"]["path"]}
    elif not hasattr(doc["data"], "get"):
        # Empty, truncated or not a mapping. It happens on a share, and it must
        # not read as "no changes" -- that would quietly skip the save forever.
        out["files"]["notes"] = {"found": True, "path": doc["path"],
                                 "unreadable": True,
                                 "fingerprint": doc["fingerprint"]}
    else:
        applied = notes.apply_regions(doc["data"], updates, annotator=annotator,
                                      only=only, known=known)
        changes = applied["changes"]
        after = doc["render"](doc["data"])
        out["files"]["notes"] = {
            "found": True, "path": doc["path"], "changes": changes,
            # damage the YAML records that this working copy has no shape for:
            # left alone, and shown, so the difference is not invisible
            "kept": applied["kept"],
            "diff": notes.diff(doc["text"], after, doc["path"]),
            "unexpected": notes.unexpected_lines(doc["text"], after, changes),
            "missingRegions": notes.missing_regions(doc["data"],
                                                    [r for r in res["regions"]]),
            "fingerprint": doc["fingerprint"], "text": after,
            "unchanged": after == doc["text"],
        }

    meta = notes.read(found["metadata"]["path"]) if found["metadata"]["found"] else None
    if meta is None:
        out["files"]["metadata"] = {"found": False, "path": found["metadata"]["path"]}
    elif not hasattr(meta["data"], "get"):
        out["files"]["metadata"] = {"found": True, "path": meta["path"],
                                    "unreadable": True,
                                    "fingerprint": meta["fingerprint"]}
    else:
        change = notes.add_annotator(meta["data"], annotator)
        after = meta["render"](meta["data"])
        changes = [change] if change else []
        out["files"]["metadata"] = {
            "found": True, "path": meta["path"], "changes": changes,
            "diff": notes.diff(meta["text"], after, meta["path"]),
            "unexpected": notes.unexpected_lines(meta["text"], after, changes),
            "fingerprint": meta["fingerprint"], "text": after,
            "unchanged": after == meta["text"],
        }
    return out


@app.get("/api/datasets/{ds_id}/notes")
def notes_state(ds_id: str):
    """Which YAMLs the dataset folder has, and their exact paths.

    Cheap and read-only, so the panel can name the file it will write the moment
    a dataset opens — including when there is nothing there to write to.
    """
    try:
        d = ds.get_dataset(ds_id)
    except KeyError:
        raise HTTPException(404, "unknown dataset")
    found = notes.find(d["root"])
    out = {"root": d["root"], "files": {}}
    for key, info in found.items():
        doc = notes.read(info["path"]) if info["found"] else None
        entry = {"found": info["found"], "path": info["path"]}
        if doc is not None:
            data = doc["data"]
            keys = list(data.keys()) if hasattr(data, "keys") else []
            entry["fingerprint"] = doc["fingerprint"]
            entry["keys"] = keys
            if key == "notes":
                entry["regions"] = [k for k in keys
                                    if hasattr(data.get(k), "get")]
            else:
                entry["annotators"] = [str(x) for x in (data.get("annotators") or [])
                                       if not notes.is_unset(x)]
        out["files"][key] = entry
    return out


@app.post("/api/datasets/{ds_id}/notes/preview")
async def notes_preview(ds_id: str, request: Request):
    """What would be written to the two YAMLs, as a DIFF against what is on disk.

    Body: {fc?, mode?, extraDamage?, annotator?, scope?, minOverlap?}. Read-only.
    `scope` defaults to "mine": only the regions this annotator's provenance
    entries name. "all" writes every region the geometry knows about.
    """
    ctx = _notes_context(ds_id, await request.json())
    for f in ctx["files"].values():
        f.pop("text", None)              # the diff is the preview; not the dump
    return ctx


@app.post("/api/datasets/{ds_id}/notes/save")
async def notes_save(ds_id: str, request: Request):
    """Write the YAMLs in the dataset folder. Body: as preview, plus
    {which?: ["notes","metadata"], fingerprints?: {...}, force?: bool}.

    The document is rebuilt here rather than trusting anything the client held:
    between a preview and a save, a colleague can have written to the same file.
    """
    body = await request.json() or {}
    ctx = _notes_context(ds_id, body)
    which = body.get("which") or ["notes", "metadata"]
    force = bool(body.get("force"))
    sent = body.get("fingerprints") or {}
    written, skipped = [], []

    for key in which:
        f = ctx["files"].get(key) or {}
        if not f.get("found"):
            skipped.append({"file": key, "reason": "missing",
                            "path": f.get("path")})
            continue
        if f.get("unreadable"):
            skipped.append({"file": key, "reason": "unreadable",
                            "path": f.get("path")})
            continue
        if f.get("unchanged"):
            skipped.append({"file": key, "reason": "nothing-to-write",
                            "path": f["path"]})
            continue
        if f.get("unexpected") and not force:
            # A line no edit accounts for means the round-trip is rewriting the
            # file. Refuse: reformatting a shared, hand-maintained record is the
            # damage this whole module exists to avoid.
            skipped.append({"file": key, "reason": "unexpected-changes",
                            "path": f["path"], "lines": f["unexpected"]})
            continue
        res = notes.save(f["path"], f["text"], expect=sent.get(key) or f["fingerprint"])
        if not res.get("ok"):
            skipped.append({"file": key, "reason": res.get("reason"),
                            "path": f["path"], "expected": res.get("expected"),
                            "actual": res.get("actual")})
            continue
        # The report is built BEFORE the write, so its fingerprint is the hash we
        # just superseded. Refresh it: the client keeps this report and sends the
        # fingerprint back on the next save, and a stale one makes our own write
        # look like a colleague's -- refusing the next batch and blaming someone.
        f["fingerprint"] = res["fingerprint"]
        f["unchanged"] = True
        written.append({"file": key, "path": f["path"],
                        "changes": f.get("changes") or [],
                        "fingerprint": res["fingerprint"]})

    for f in ctx["files"].values():
        f.pop("text", None)
    return {**ctx, "written": written, "skipped": skipped}


@app.post("/api/datasets/{ds_id}/notes/create")
async def notes_create(ds_id: str, request: Request):
    """Create `annotation.notes.yaml` in the dataset folder, on purpose.

    Separate from save, and never reached by accident: a second competing copy of
    a shared record is worse than not having one. Body: {fc?, path?}.
    """
    try:
        d = ds.get_dataset(ds_id)
    except KeyError:
        raise HTTPException(404, "unknown dataset")
    body = await request.json() or {}
    found = notes.find(d["root"])
    path = Path(str(body.get("path") or found["notes"]["path"]))
    if path.exists():
        raise HTTPException(409, f"{path.name} already exists — it is edited, never replaced")
    fc = body.get("fc") or geo.load_regions(ds_id)[0]
    regions, _ = DMG.split_features(fc.get("features") or [], d["id_prop"])
    names = []
    for f in regions:
        nm = (f.get("properties") or {}).get(d["id_prop"])
        if nm is not None and str(nm) not in names:
            names.append(str(nm))
    text = notes.new_notes_text(names, experiment_id=d.get("label"),
                                by=provenance.display_name())
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="") as fh:
        fh.write(text)
    return {"path": str(path), "regions": names,
            "fingerprint": notes.fingerprint(path)}


@app.get("/api/damage/designations")
def damage_designations():
    """The damage vocabulary, for the "what am I drawing" dropdown.

    `drawn` is the SOP's "should this be annotated": true always, false never
    (nothing to draw -- it can only be a tag on the region), null the annotator's
    call. Served rather than hard-coded in the client so there is one list.

    `aliases` is the fold -> tag map behind parse_name(), so the client can label
    a name in the list the same way the backend would instead of approximating
    it. Fold a name the same way: strip spaces, underscores and hyphens, lower."""
    return {"designations": [{"tag": t, "label": label, "drawn": drawn}
                             for t, (label, drawn) in DMG.DESIGNATIONS.items()],
            "aliases": DMG.alias_map(),
            "enclave": DMG.ENCLAVE}


@app.get("/api/identity")
def get_identity():
    """Who edits are attributed to. The account is read-only -- a display name on
    its own could be anyone's, and a chain of custody has to say who really made
    the change.

    `set` says whether a real name was ever entered, as opposed to `name` falling
    back to the Windows account. The trail can live with "FIVE"; the lab's shared
    annotator roster cannot, so the caller has to be able to tell the difference.
    """
    return {"name": provenance.display_name(), "account": provenance.account_name(),
            "set": provenance.has_display_name()}


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
        d = ds.get_dataset(ds_id)
    except KeyError:
        raise HTTPException(404, "unknown dataset")
    body = await request.json()
    before = body.get("before")
    after = body.get("after")
    moved = body.get("moved", [])
    tol = float(body.get("tol", 40.0))
    if not before or not after:
        raise HTTPException(400, "need 'before' and 'after' FeatureCollections")
    # The whole-section outline is NEVER a peer: snapped as a neighbour, the
    # engine carves the moved region's shape straight through it -- a hole in
    # hemi and a trail of hairline slivers along the border. It sits the snap
    # out (as does anything the client switched off) and is put back untouched.
    # Detected on the before state, so it is held out even when it is not
    # switched off in the sidebar; a name in `moved` is the subject, never held.
    id_prop = d["id_prop"]
    auto = topology.blankets(before.get("features") or [], id_prop, keep=moved)
    holds = (set(body.get("exclude") or []) | set(auto)) - {str(m) for m in moved}
    kept_b, _ = _hold_out(before.get("features") or [], id_prop, holds)
    kept_a, held_a = _hold_out(after.get("features") or [], id_prop, holds)
    try:
        result = geo.run_snap(ds_id, {**before, "features": kept_b},
                              {**after, "features": kept_a}, moved=moved, tol=tol)
    except Exception as e:  # surface engine errors to the client
        raise HTTPException(500, f"snap failed: {e}")
    result["features"] = _put_back(result.get("features") or [], held_a)
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
        # A nested pair HAS a border: the inner's own outline. Hand it over as
        # the draggable arc(s); dragging reshapes the inner and the container
        # keeps covering it (move_border's nested branch).
        arcs = topology.border_between(fc["features"], d["id_prop"], str(a), str(b), grid)
        return {**pair_meta, "arcs": arcs, "bridged": False,
                "contained": contained,
                "message": (f'"{contained["inner"]}" sits inside '
                            f'"{contained["outer"]}" -- the outline of '
                            f'"{contained["inner"]}" is their shared border. '
                            f'Drag it; "{contained["outer"]}" always keeps '
                            "wrapping it.")}
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


@app.post("/api/datasets/{ds_id}/regions/gap")
async def regions_gap(ds_id: str, request: Request):
    """Where the unclaimed gap between the picked regions is, and how wide it is
    at its widest point -- so the Snap reach circle can sit on the gap the user
    is actually filling, not on a border they already share. Body:
    {regions:[names], fc?, search?}. Returns {kind, centre, width, area, pieces}
    (kind "gap" or "apart"), or {kind: null} when they touch with nothing
    between. Reports only; changes no geometry.

    Not to be confused with topology.region_gap (used by shared-border above),
    which measures the distance between two named regions."""
    try:
        d = ds.get_dataset(ds_id)
    except KeyError:
        raise HTTPException(404, "unknown dataset")
    body = await request.json()
    names = body.get("regions") or body.get("names")
    if not names or len(names) < 2:
        raise HTTPException(400, "need 'regions': at least two region names")
    # Take the FC from the body like the other geometry routes: the client's
    # copy may hold unsaved edits, and the gap has to reflect what is on screen.
    fc = body.get("fc") or geo.load_regions(ds_id)[0]
    try:
        search = float(body.get("search", topology.GAP_SEARCH))
    except (TypeError, ValueError):
        raise HTTPException(400, "'search' must be a number")
    try:
        res = topology.gap_between(fc["features"], d["id_prop"], names, search=search)
    except Exception as e:
        raise HTTPException(500, f"gap failed: {e}")
    out = dict(res) if res is not None else {"kind": None}
    # A nested pair (one inside the other) is shared by snap_to_container, which
    # has no "Snap to X" or "Fill all": say so, so the client can hide them
    # instead of offering buttons that do nothing.
    try:
        c = topology.containment(fc["features"], d["id_prop"], [str(n) for n in names][:2]) \
            if len(names) == 2 else None
    except Exception:
        c = None
    if c:
        out["nested"] = {"inner": c.get("inner"), "outer": c.get("outer")}
    return JSONResponse(out)


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
    # Nested pairs are welcome here: move_border reshapes the inner along its
    # dragged outline and the container keeps covering. (Share borders/tiling
    # still refuses nested picks -- there is nothing for a partition to divide.)
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
    # Where the seam goes: None splits the contested ground down the middle, a
    # name puts it on that region's own edge and leaves that region untouched.
    snap_to = (body or {}).get("snapTo")
    if snap_to is not None:
        snap_to = str(snap_to)
        if snap_to not in [str(n) for n in names]:
            raise HTTPException(
                400, f"snapTo must be one of the picked regions, not {snap_to!r}")
    # A nested PAIR gets the nested meaning of "share borders": the hairline
    # band between the inner's edge and the container's outline joins the
    # inner, so they genuinely share the outline along their neighbouring
    # stretch. Three or more picks with a container among them still refuse --
    # a partition would shred the overlap into slivers.
    contained = topology.containment(fc["features"], d["id_prop"], names)
    if contained and len(names) == 2:
        try:
            res = topology.snap_to_container(fc["features"], d["id_prop"],
                                             names, tol=tol)
        except ValueError as e:
            raise HTTPException(422, str(e))
        detail = (f"{res['inner']} joined to {res['outer']}'s outline"
                  f" ({res['sealed']:,.0f} px\u00b2 along {res['stretches']}"
                  " stretch(es)"
                  + (f"; outline grew {res['covered']:,.0f} px\u00b2 to cover"
                     if res.get("covered") else "") + ")")
        return {**provenance.stamped(res["features"], fc, "share-borders",
                                     detail, names),
                "borders": res["borders"],
                "nested": {"outer": res["outer"], "inner": res["inner"],
                           "sealed": res["sealed"], "gapPx": res.get("gapPx"),
                           "covered": res.get("covered", 0.0),
                           "stretches": res["stretches"]}}
    if contained:
        raise HTTPException(422, topology.containment_message(contained))
    try:
        res = topology.partition_regions(fc["features"], d["id_prop"], names,
                                         tol=tol, snap_to=snap_to,
                                         fill_all=bool(body.get("fillAll")))
    except ValueError as e:
        raise HTTPException(422, str(e))
    except Exception as e:
        raise HTTPException(500, f"partition failed: {e}")
    detail = (" + ".join(str(n) for n in names)
              + (f" (seam on {snap_to}'s edge)" if snap_to
                 else " (seam down the middle)"))
    return {**provenance.stamped(res["features"], fc, "share-borders",
                                 detail, names),
            "borders": res["borders"], "snappedTo": snap_to}


@app.post("/api/datasets/{ds_id}/regions/resample")
async def regions_resample(ds_id: str, request: Request):
    """Even out the points of the picked regions without breaking their borders.
    Body: {regions:[names], fc?, tol?}. Returns the updated FC plus the
    before/after point counts, so the client can show the effect before keeping it.

    Two or more regions: the shared border between them (Edit shared borders).
    ONE region: its whole outline (Edit points), re-pointing every neighbour's
    side of each border it shares so none of them opens."""
    try:
        d = ds.get_dataset(ds_id)
    except KeyError:
        raise HTTPException(404, "unknown dataset")
    body = await request.json()
    names = body.get("regions") or body.get("names")
    if not names:
        raise HTTPException(400, "need 'regions': one region, or two whose border to resample")
    fc = body.get("fc") or geo.load_regions(ds_id)[0]
    # "even" spacing (the default), or "shape": the fewest points within tol px
    method = str(body.get("method") or "even")
    # deform: the person said yes to "the shape will deform" -- a region's area
    # may then change by up to 25% instead of 5%
    drift = topology.FEWEST_DEFORM_DRIFT if body.get("deform") else 0.05
    try:
        if len(names) == 1 and method == "shape":
            res = topology.resample_region_fewest(fc["features"], d["id_prop"], str(names[0]),
                                                  tol=float(body.get("tol", 20.0)),
                                                  max_drift=drift)
        elif len(names) == 1:
            res = topology.resample_region(fc["features"], d["id_prop"], str(names[0]),
                                           tol=float(body.get("tol", 20.0)), method=method)
        else:
            res = topology.resample_regions(fc["features"], d["id_prop"], names,
                                            tol=float(body.get("tol", 20.0)), method=method)
    except ValueError as e:
        raise HTTPException(422, str(e))
    except Exception as e:
        raise HTTPException(500, f"resample failed: {e}")
    return {**provenance.stamped(res["features"], fc, "resample",
                                 f"{' + '.join(str(n) for n in names)} at {res['tol']:g} px",
                                 names),
            "counts": res["counts"], "tol": res["tol"],
            "handles": res.get("handles"), "arcPoints": res.get("arcPoints"),
            "kept": res.get("kept", []), "used": res.get("used")}


@app.post("/api/datasets/{ds_id}/regions/delete-point")
async def regions_delete_point(ds_id: str, request: Request):
    """Delete one point of a region's outline without opening any border, and
    re-space the rest of that stretch to close the gap. Body: {fc, region,
    point:[x, y], respace?}. A point on a border shared with a neighbour goes
    from both; a point where regions meet, or the end of a shared border, is
    refused with a sentence saying why (422)."""
    try:
        d = ds.get_dataset(ds_id)
    except KeyError:
        raise HTTPException(404, "unknown dataset")
    body = await request.json()
    fc, region, point = body.get("fc"), body.get("region"), body.get("point")
    if not fc or not region or not point or len(point) != 2:
        raise HTTPException(400, "need 'fc', 'region' and 'point': [x, y]")
    try:
        res = topology.delete_point(fc["features"], d["id_prop"], str(region), point,
                                    respace=bool(body.get("respace", True)))
    except ValueError as e:
        raise HTTPException(422, str(e))
    except Exception as e:
        raise HTTPException(500, f"delete-point failed: {e}")
    who = " and ".join(res["removedFrom"])
    return {**provenance.stamped(res["features"], fc, "delete-point",
                                 f"{who}, {res['stretch']}"
                                 + (", re-spaced" if res["respaced"] else ""),
                                 res["removedFrom"]),
            "removedFrom": res["removedFrom"], "respaced": res["respaced"],
            "stretch": res["stretch"]}


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
    # Merging with the section outline (hemi) is a CHOICE, never a plain merge:
    # a plain merge lets the first pick win, so [TH, hemi] made TH the whole
    # hemisphere and deleted hemi. Without `intoContainer` the client is asked;
    # with it, the picks are folded into the outline and the outline is kept.
    conts = topology.section_containers(fc["features"], d["id_prop"]) & {str(n) for n in names}
    if conts:
        cname = sorted(conts)[0]
        rest = [str(n) for n in names if str(n) != cname]
        if not body.get("intoContainer"):
            return {"needsContainerChoice": True, "container": cname, "regions": rest}
        try:
            res = topology.fold_into_container(fc["features"], d["id_prop"], names)
        except ValueError as e:
            raise HTTPException(422, str(e))
        detail = f"{' + '.join(res['folded'])} folded into {res['name']}"
        return {**provenance.stamped(res["features"], fc, "merge", detail, names),
                "name": res["name"], "folded": res["folded"], "parts": 1, "sealed": 0.0}
    try:
        res = topology.merge_regions(fc["features"], d["id_prop"], names, body.get("name"),
                                     seam_tol=float(body.get("tol", 40.0)),
                                     fill_all=bool(body.get("fillAll")))
    except ValueError as e:
        raise HTTPException(422, str(e))
    except Exception as e:
        raise HTTPException(500, f"merge failed: {e}")
    detail = (f"{' + '.join(str(n) for n in names)} -> {res['name']}"
              + (f", seam sealed ({res['sealed']:,.0f} px²)" if res.get("sealed") else "")
              + (f" -- still {res['parts']} separate pieces" if res.get("parts", 1) > 1 else ""))
    return {**provenance.stamped(res["features"], fc, "merge", detail, names),
            "name": res["name"], "parts": res.get("parts"),
            "sealed": res.get("sealed")}


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


@app.post("/api/datasets/{ds_id}/regions/split-candidates")
async def regions_split_candidates(ds_id: str, request: Request):
    """Which region a Split line is meant for when none is picked, most likely
    first. Read-only. Body: {points, fc?, exclude?} -> {names, reason}."""
    try:
        d = ds.get_dataset(ds_id)
    except KeyError:
        raise HTTPException(404, "unknown dataset")
    body = await request.json()
    points = body.get("points")
    if not points:
        raise HTTPException(400, "need 'points'")
    fc = body.get("fc") or geo.load_regions(ds_id)[0]
    try:
        return topology.split_candidates(fc["features"], d["id_prop"], points,
                                         body.get("exclude") or [])
    except Exception as e:
        raise HTTPException(500, f"finding the region to split failed: {e}")


@app.post("/api/datasets/{ds_id}/regions/delete-part")
async def regions_delete_part(ds_id: str, request: Request):
    """Delete the one separate piece of a region under a right-click, keeping the
    rest of the region. Body: {region, point:[x, y], fc?}."""
    try:
        d = ds.get_dataset(ds_id)
    except KeyError:
        raise HTTPException(404, "unknown dataset")
    body = await request.json()
    region, point = body.get("region"), body.get("point")
    if not region or not point or len(point) < 2:
        raise HTTPException(400, "need 'region' and 'point'")
    fc = body.get("fc") or geo.load_regions(ds_id)[0]
    try:
        res = topology.delete_part(fc["features"], d["id_prop"], region, point)
    except ValueError as e:
        raise HTTPException(422, str(e))
    except Exception as e:
        raise HTTPException(500, f"delete piece failed: {e}")
    return {**provenance.stamped(res["features"], fc, "delete piece",
                                 f"{region}: removed a {res['removed_area']:,.0f} px² piece",
                                 [region]),
            "removedArea": res["removed_area"], "partsLeft": res["parts_left"]}


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
    # Regions switched off in the sidebar -- usually the hemisphere outline, which
    # would otherwise mean there is no such thing as a gap.
    kept, held = _hold_out(fc["features"], d["id_prop"],
                           _gap_exclude(fc["features"], d["id_prop"], body.get("exclude")))
    try:
        res = topology.dissolve_gap(kept, d["id_prop"], point, tol=tol)
    except ValueError as e:      # no gap there / click was inside a region
        raise HTTPException(422, str(e))
    except Exception as e:
        raise HTTPException(500, f"dissolve-gap failed: {e}")
    res["features"] = _put_back(res["features"], held)
    return {**provenance.stamped(res["features"], fc, "dissolve-gap",
                                 f"{res['area']:,.0f} px² into "
                                 f"{' + '.join(str(n) for n in res['regions'])}",
                                 res["regions"]),
            "gap": res["gap"], "area": res["area"], "kind": res["kind"],
            "regions": res["regions"]}


@app.post("/api/datasets/{ds_id}/regions/clean-lines")
async def regions_clean_lines(ds_id: str, request: Request):
    """Remove the stray hairlines inside a traced loop -- sliver parts of
    regions and sliver voids between them -- and hand the ground to the healthy
    neighbours. Body: {points, fc?, width?, exclude?}. Returns the updated FC
    plus the report, so the client can preview and commit without a second
    round trip."""
    try:
        d = ds.get_dataset(ds_id)
    except KeyError:
        raise HTTPException(404, "unknown dataset")
    body = await request.json()
    points = body.get("points")
    if not points or len(points) < 3:
        raise HTTPException(400, "need 'points': the traced loop")
    fc = body.get("fc") or geo.load_regions(ds_id)[0]
    width = float(body.get("width", 12.0))
    # Switched-off regions sit out, same as gap-finding -- and clean_lines
    # additionally holds anything that blankets the loop out of its void
    # union, so hemi does not need to be off for this to work.
    kept, held = _hold_out(fc["features"], d["id_prop"], body.get("exclude"))
    try:
        res = topology.clean_lines(kept, d["id_prop"], points, width=width)
    except ValueError as e:
        raise HTTPException(422, str(e))
    except Exception as e:
        raise HTTPException(500, f"clean-lines failed: {e}")
    res["features"] = _put_back(res["features"], held)
    touched = sorted({r["region"] for r in res["removed"]}
                     | {r["region"] for r in res["despiked"]}
                     | set(res["filled"]) | set(res["deleted"]))
    spikes = sum(r["vertices"] for r in res["despiked"])
    detail = (f"{res['area']:,.0f} px² of stray lines"
              + (f", {spikes} needle point(s) off "
                 f"{', '.join(r['region'] for r in res['despiked'])}"
                 if res["despiked"] else "")
              + (f", {len(res['deleted'])} sliver region(s) deleted" if res["deleted"] else "")
              + (f" -> {' + '.join(res['filled'])}" if res["filled"] else ""))
    return {**provenance.stamped(res["features"], fc, "clean-lines",
                                 detail, touched),
            "removed": res["removed"], "despiked": res["despiked"],
            "deleted": res["deleted"],
            "filled": res["filled"], "covered": res["covered"],
            "freed": res["freed"], "area": res["area"]}


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
    # READ first, write after. load_regions_file() saves as it reads, so a file
    # that cannot be turned into a response had already replaced the working
    # copy: a GeoJSON holding a NaN coordinate was written, the response then
    # failed (NaN is not JSON), and the annotator saw an error and believed
    # nothing had happened -- while every later read of that dataset raised.
    # Reading on a worker thread as well, so a slow network read cannot hold the
    # event loop and stop every other route answering.
    try:
        fc = await run_in_threadpool(geo.read_regions_file, ds_id, str(path))
        fc = _to_current_frame(ds_id, fc)
    except Exception as e:
        raise HTTPException(400, f"load failed: {e}")
    # Stamped AFTER the load so the entry joins whatever trail the incoming file
    # already carried -- that is the hand-off being recorded.
    provenance.stamp(fc, "load-regions", Path(str(path)).name)
    geo.save_edited(ds_id, fc)
    return JSONResponse(fc)


@app.post("/api/datasets/{ds_id}/regions/read-file")
async def regions_read_file(ds_id: str, request: Request):
    """Read a region file and hand it back, changing NOTHING. Body: {path}.

    Load regions file used to replace the map, the undo history and the working
    copy the moment a file was picked. Now the client reads first, asks whether
    to add the file's regions to the map or replace the map with them, and does
    that as an ordinary, undoable edit that Save then writes.
    """
    try:
        d = ds.get_dataset(ds_id)
    except KeyError:
        raise HTTPException(404, "unknown dataset")
    body = await request.json()
    path = (body or {}).get("path")
    if not path:
        raise HTTPException(400, "need 'path'")
    # On a worker thread. This route is async and the read is blocking, so a
    # read that never returns held the EVENT LOOP and every other route stopped
    # answering -- health, Save, everything -- leaving an annotator with unsaved
    # edits no way out but to kill the app. `path: "CON"` did exactly that
    # (Windows opens it as a device); geo now refuses those names outright, and
    # this keeps any other slow read off the loop.
    try:
        fc = await run_in_threadpool(geo.read_regions_file, ds_id, str(path))
        fc = _to_current_frame(ds_id, fc)
    except Exception as e:
        raise HTTPException(400, f"could not read {Path(str(path)).name}: {e}")

    def names_of(features):
        return [str((f.get("properties") or {}).get(d["id_prop"]) or "") for f in features]

    # A file with hemi in it: the annotator is asked whether to bring the damage
    # too. Damage can be in this file, or beside it in files that hold nothing
    # but damage (the one-file-per-region export writes bubble.1.geojson and so
    # on). Other region files in the folder -- other versions of the regions --
    # are never gathered: only a file whose every region is damage counts.
    has_hemi = any(n.strip().lower() == "hemi" for n in names_of(fc.get("features") or []))
    beside = {"files": [], "features": []}
    if has_hemi:
        here = Path(str(path))
        for q in sorted(here.parent.glob("*.geojson")):
            if _same_file(q, here):
                continue
            try:
                sfc = _to_current_frame(ds_id, geo.read_regions_file(ds_id, str(q)))
            except Exception:
                continue
            sf = sfc.get("features") or []
            if sf and all(n and DMG.parse_name(n)[0] for n in names_of(sf)):
                beside["files"].append(q.name)
                beside["features"].extend(sf)
    return JSONResponse({**fc, "_readFrom": str(path), "_hasHemi": has_hemi,
                         "_damageBeside": beside})


@app.post("/api/datasets/{ds_id}/regions/load-multi")
async def regions_load_multi(ds_id: str, request: Request):
    """Load ONE OR SEVERAL region files as the working copy -- the pre-load
    checklist's region rows. Each file goes through the same reader as a
    single load (space transform, healing).

    Names are kept exactly as the files have them; each feature is tagged with
    the file it came from so the sidebar can list the files apart. A name used
    in more than one file is therefore one multi-part region here (edited
    together) and is reported in `shared`. Body: {paths:[...]}."""
    try:
        d = ds.get_dataset(ds_id)
    except KeyError:
        raise HTTPException(404, "unknown dataset")
    body = await request.json()
    paths = (body or {}).get("paths") or []
    if not paths:
        raise HTTPException(400, "need 'paths': at least one region file")
    # ALL of them, or none. This used to call load_regions_file per path, which
    # SAVES as it reads, so a bad file after a good one left the working copy
    # holding just the partial load -- measured: 4 features before, 1 after, one
    # snapshot written, and an error message that named only the missing file.
    # Nothing is written now until every file has been read.
    groups, names, first = [], [], None
    for path in paths:
        try:
            fc = await run_in_threadpool(geo.read_regions_file, ds_id, str(path))
            fc = _to_current_frame(ds_id, fc)
        except Exception as e:
            raise HTTPException(400, f"load failed for {Path(str(path)).name}: {e}"
                                     " -- nothing was changed")
        if first is None:
            first = fc
        groups.append((Path(str(path)).stem, fc.get("features") or []))
        names.append(Path(str(path)).name)
    res = topology.concat_region_files(groups, d["id_prop"])
    # Carry the first file's file-level members -- its provenance trail and its
    # `_orientation`. Building `out` from scratch dropped both, so loading a
    # file through the multi path lost the chain of custody that the single-file
    # path keeps, and a rotated file stopped saying it was rotated.
    out = provenance.foreign(first or {})
    out.update({"type": "FeatureCollection", "features": res["features"]})
    detail = " + ".join(names)
    if res["shared"]:
        # Said out loud because it cannot be fixed by listing them apart: one
        # name over two files is one multi-part region, and an edit acts on all
        # of it.
        detail += (f" ({len(res['shared'])} name(s) in more than one file, so "
                   "each is ONE region in several parts: "
                   + ", ".join(res["shared"][:6])
                   + ("…" if len(res["shared"]) > 6 else "") + ")")
    provenance.stamp(out, "load-regions", detail)
    geo.save_edited(ds_id, out)
    return JSONResponse(out)


@app.post("/api/datasets/{ds_id}/regions/add")
async def regions_add(ds_id: str, request: Request):
    """Add a new region from a drawn outline. Body: {points:[[x,y]...], fc?, name?,
    carve?, damage?}. carve (default true) makes overlapped regions cede the
    ground, so the file stays a clean partition.

    `damage` is a designation tag (see /api/damage/designations). Damage is an
    ordinary region in every respect but two: the designation names it, numbered
    globally across the file the way the lab numbers it; and it must NOT carve,
    because it sits INSIDE its host and the host stays whole."""
    try:
        d = ds.get_dataset(ds_id)
    except KeyError:
        raise HTTPException(404, "unknown dataset")
    body = await request.json()
    points = body.get("points")
    if not points or len(points) < 3:
        raise HTTPException(400, "need 'points': at least three [x, y] pairs")
    fc = body.get("fc") or geo.load_regions(ds_id)[0]
    feats = fc.get("features") or []

    name, carve = body.get("name"), bool(body.get("carve", True))
    overlap = body.get("overlap")          # "top" | "bottom" | "both" | None
    asked = body.get("damage")
    # parse_name() resolves a bare tag as well as a numbered name, and tolerates
    # the display spelling ("Small void") the dropdown sends back.
    tag = DMG.parse_name(asked)[0] if asked else None
    if asked and not tag:
        raise HTTPException(422, f"unknown damage designation: {asked!r}")
    if tag:
        # Every name in the file, not one region's -- numbering is global per
        # designation (separation.4/.5/.6 in one region, .7 in another).
        name = DMG.format_name(
            tag, DMG.next_number([(f.get("properties") or {}).get(d["id_prop"])
                                  for f in feats], tag))
        carve = False

    # Switched-off regions are left untouched, as Fix a gap leaves them: they
    # cede no ground and are not asked about. The section outline stays in --
    # it is never carved, and it is what a draw inside the section sits in. The
    # name is still made unique against every region, held out or not.
    kept, held = _hold_out(feats, d["id_prop"],
                           _gap_exclude(feats, d["id_prop"], body.get("exclude")))
    # Which region is the section outline is decided from the WHOLE file: with
    # all but hemi and one region switched off ("Show only this"), what is left
    # no longer shows hemi wrapping two others, and the draw carved it.
    wrap = topology.section_containers(feats, d["id_prop"])
    if held:
        name = topology._unique_name(
            {str((f.get("properties") or {}).get(d["id_prop"])) for f in feats}, name)

    # The stacking question, asked BEFORE anything is committed: a client that
    # sends ask=true and has not yet chosen gets back what the outline would
    # land on, and nothing is changed. It then re-posts with `overlap`. Damage
    # never asks -- it sits inside its host by definition.
    if body.get("ask") and not overlap and not tag:
        try:
            rep = topology.overlap_report(kept, d["id_prop"], points, containers=wrap)
        except ValueError as e:
            raise HTTPException(422, str(e))
        hits = rep["overlaps"]
        if hits:
            nested = any(h["relationship"] in ("envelops", "inside") for h in hits)
            return {"needsOverlapChoice": True, "overlaps": hits, "area": rep["area"],
                    # a region drawn around (or inside) another is the cover
                    # convention these files already use -- do not carve it;
                    # a slight overlap between neighbours should stay a clean
                    # partition, so there the new shape takes the ground.
                    "suggest": "both" if nested else "top"}

    try:
        res = topology.add_region(kept, d["id_prop"], points,
                                  name=name, carve=carve, overlap=overlap,
                                  containers=wrap)
    except ValueError as e:
        raise HTTPException(422, str(e))
    except Exception as e:
        raise HTTPException(500, f"add-region failed: {e}")
    res["features"] = _put_back(res["features"], held)

    # Where the shape landed, by the same rule the SmartSheet export uses -- so
    # what the sidebar says now is what the export says later. `candidates` is
    # what to offer if it straddles two regions: the annotator is asked at the
    # moment of drawing, when they can still see what they drew.
    inside, candidates, dominant, needs = [], [], None, False
    if tag:
        anat, _ = DMG.split_features(res["features"], d["id_prop"])
        drawn = [f for f in res["features"]
                 if (f.get("properties") or {}).get(d["id_prop"]) == res["name"]]
        try:
            hit = DMG.assign(anat, drawn, d["id_prop"])["shapes"]
            if hit:
                inside = hit[0]["regions"]
                candidates = hit[0]["candidates"]
                dominant = hit[0]["dominant"]
                needs = hit[0]["needsChoice"]
        except Exception:
            pass                             # a label, never a reason to fail

    detail = f"{res['name']}, {res['area']:,.0f} px²"
    if tag:
        detail += f" ({DMG.DESIGNATIONS[tag][0]} damage"
        detail += f" in {', '.join(inside)})" if inside else ", over no region)"
    elif res["ceded"]:
        detail += f", taken from {', '.join(res['ceded'])}"
    elif overlap == "bottom":
        detail += ", placed underneath (trimmed to the free ground)"
    elif overlap == "both":
        under = res.get("placedUnder") or []
        detail += (f", overlapping and drawn beneath {', '.join(under)}"
                   if under else ", overlapping, nothing trimmed")
    return {**provenance.stamped(res["features"], fc, "add-region", detail,
                                 [res["name"]]),
            "name": res["name"], "area": res["area"], "ceded": res["ceded"],
            "damage": tag, "inside": inside, "candidates": candidates,
            "dominant": dominant, "needsChoice": needs}


@app.post("/api/datasets/{ds_id}/regions/outline")
async def regions_outline(ds_id: str, request: Request):
    """Build the hemisection outline from the regions themselves.

    Body: {fc?, name?, method?: "bubble"|"hull", radius?, exclude?, apply?}.
    With apply=false (the default) it returns the geometry to preview; with
    apply=true it adds or REPLACES the named region and returns the new FC.
    """
    try:
        d = ds.get_dataset(ds_id)
    except KeyError:
        raise HTTPException(404, "unknown dataset")
    body = await request.json() or {}
    fc = body.get("fc") or geo.load_regions(ds_id)[0]
    name = str(body.get("name") or "hemi")
    try:
        res = topology.section_outline(
            fc.get("features") or [], d["id_prop"], name=name,
            method=str(body.get("method") or "bubble"),
            radius=float(body.get("radius", 200.0)),
            exclude=body.get("exclude"))
    except ValueError as e:
        raise HTTPException(422, str(e))
    except Exception as e:
        raise HTTPException(500, f"outline failed: {e}")
    if not body.get("apply"):
        return {"geometry": res["geometry"], "area": res["area"],
                "method": res["method"], "sources": res["sources"],
                "parts": res["parts"],
                "exists": any(str((f.get("properties") or {}).get(d["id_prop"])) == name
                              for f in fc.get("features") or [])}

    feats = [f for f in (fc.get("features") or [])
             if str((f.get("properties") or {}).get(d["id_prop"])) != name]
    replaced = len(feats) != len(fc.get("features") or [])
    # First in the list, so the outline draws UNDER everything it wraps.
    #
    # A rebuild REPLACES the outline, so it keeps what the old one carried --
    # its colour, and any damage ticked on it. Building minimal props from
    # scratch dropped all of that every time, and the outline is rebuilt often.
    old = next((f for f in (fc.get("features") or [])
                if str((f.get("properties") or {}).get(d["id_prop"])) == name), None)
    props = dict((old or {}).get("properties") or {})
    props[d["id_prop"]] = name
    if "classification" not in props:
        for f in fc.get("features") or []:
            if isinstance((f.get("properties") or {}).get("classification"), dict):
                props["classification"] = {"name": name}
                break
    feats.insert(0, {"type": "Feature", "properties": props,
                     "geometry": res["geometry"]})
    detail = (f"{name} {'rebuilt' if replaced else 'created'} by {res['method']}"
              f", {res['area']:,.0f} px² from {len(res['sources'])} regions"
              + (f" in {res['parts']} parts" if res["parts"] > 1 else ""))
    return {**provenance.stamped(feats, fc, "section-outline", detail, [name]),
            "name": name, "area": res["area"], "method": res["method"],
            "replaced": replaced, "sources": res["sources"], "parts": res["parts"]}


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
    kept, held = _hold_out(fc["features"], d["id_prop"],
                           _gap_exclude(fc["features"], d["id_prop"], body.get("exclude")))
    try:
        res = topology.fill_gap_with_region(kept, d["id_prop"], point,
                                            name=body.get("name"),
                                            tol=float(body.get("tol", 40.0)))
    except ValueError as e:
        raise HTTPException(422, str(e))
    except Exception as e:
        raise HTTPException(500, f"fill-gap failed: {e}")
    res["features"] = _put_back(res["features"], held)
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
    # Regions switched off are not checked: the hemisphere outline overlaps every
    # region by design, so leaving it in reports 22 "overlaps" that are correct.
    kept, _held = _hold_out(fc.get("features", []), d["id_prop"], (body or {}).get("exclude"))
    try:
        out = topology.validate_features(kept, d["id_prop"])
        if _held:
            out["excluded"] = sorted({str((f.get("properties") or {}).get(d["id_prop"]))
                                      for _i, f in _held})
        return JSONResponse(out)
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


@app.post("/api/datasets/{ds_id}/regions/export-anndata")
async def regions_export_anndata(ds_id: str, request: Request):
    """Regions x genes as an AnnData .h5ad: X = transcript-density counts per
    region (summed on the same 10 um grid the viewer draws), obs = regions
    (area, centroid), var = genes. Needs transcripts.zarr. Body: {fc?}."""
    try:
        d = ds.get_dataset(ds_id)
    except KeyError:
        raise HTTPException(404, "unknown dataset")
    body = await request.json()
    fc = body.get("fc") or geo.load_regions(ds_id)[0]
    try:
        gd = genedensity(ds_id)
    except Exception:
        raise HTTPException(422, "this dataset has no transcripts.zarr, so "
                                 "there are no counts to export")
    import tempfile
    import annexport
    try:
        names, counts, genes, areas, cents = annexport.region_gene_counts(
            gd, fc.get("features", []), d["id_prop"])
    except ValueError as e:
        raise HTTPException(422, str(e))
    # Repeated names are one region in parts -- collapse them so obs_names are
    # unique the way anndata expects.
    order, first = [], {}
    import numpy as _np
    for i, nm in enumerate(names):
        if nm in first:
            j = first[nm]
            counts[j] += counts[i]
            areas[j] += areas[i]
        else:
            first[nm] = len(order)
            order.append(i)
    if len(order) != len(names):
        counts = _np.asarray([counts[first[names[i]]] for i in order])
        names_u = [names[i] for i in order]
        areas_u = [areas[first[nm]] for nm in names_u]
        cents_u = [cents[i] for i in order]
    else:
        names_u, areas_u, cents_u = names, areas, cents
    with tempfile.NamedTemporaryFile(suffix=".h5ad", delete=False) as tmp:
        path = tmp.name
    try:
        annexport.write_h5ad(
            path, counts, names_u,
            [("area_px2", areas_u, "num"),
             ("n_transcript_counts", counts.sum(axis=1), "num")],
            genes,
            obsm={"spatial": cents_u},
            uns={"source": "FiveAtlas", "version": config.VERSION,
                 "dataset": ds_id, "pixel_size_um": gd.pixel_size,
                 "density_grid_um": gd.grid_x,
                 "counts_note": "X sums the 10um transcript-density grid "
                                "inside each region polygon"},
        )
        data = open(path, "rb").read()
    finally:
        try:
            os.unlink(path)
        except Exception:
            pass
    safe = re.sub(r"[^A-Za-z0-9_.-]+", "_", ds_id)
    return Response(content=data, media_type="application/octet-stream",
                    headers={"Content-Disposition":
                             f'attachment; filename="{safe}_regions.h5ad"'})


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
        fc = _in_original_frame(ds_id, fc)
        feats = fc["features"]
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

    # FiveAtlas's own per-feature bookkeeping (which file a region was loaded
    # from) never leaves the app.
    fc = geo.strip_internal_props(fc)
    feats = fc["features"]

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
    body = await request.json()
    # Legacy body was a bare list of channels; the new one wraps it so the
    # render mode and bin size ride along: {channels, mode, binUm}.
    if isinstance(body, list):
        spec, mode, bin_um, palette = body, "glow", None, None
    elif isinstance(body, dict):
        spec = body.get("channels") or []
        mode = str(body.get("mode") or "glow")
        bin_um = body.get("binUm")
        palette = body.get("palette")
    else:
        raise HTTPException(400, "body must be a channel list or {channels,...}")
    if not isinstance(spec, list):
        raise HTTPException(400, "channels must be a list")
    o = ORI.get(ds_id)
    if ORI.is_identity(o):
        return Response(content=gd.composite_png(spec, mode=mode, bin_um=bin_um,
                                                  palette=palette),
                        media_type="image/png")
    # Turn the composite itself, so it lands on the rotated bounds /genes reports.
    rgba = ORI.transform_image(
        gd.composite(spec, mode=mode, bin_um=bin_um, palette=palette), o)
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
    try:
        return ss.channel_contrast(idx)
    except KeyError:
        raise HTTPException(404, "stain stack closed while reading")


@app.get("/api/datasets/{ds_id}/stains/raw/{idx}/{z}/{x}/{y}")
def stains_raw_tile(ds_id: str, idx: int, z: int, x: int, y: int):
    """One stain channel's tile as raw little-endian uint16 (tile x tile), for
    the browser to colour and blend on the GPU. No contrast or colour in the
    URL: those never reach the server, so a slider move fetches nothing.

    Not in tilecache: StainStack already keeps the decoded channel tiles, and a
    second copy as bytes would only double the memory."""
    try:
        ss = stainstack(ds_id)
    except KeyError:
        raise HTTPException(404, "no morphology_focus stains")
    if not ss.has_channel(idx):
        raise HTTPException(404, "no such stain channel")
    level = (ss.nlevels - 1) - z
    if level < 0 or level >= ss.nlevels:
        raise HTTPException(404, "bad z")
    try:
        t = ss.channel_tile(idx, level, x, y, orient=ORI.get(ds_id))
    except KeyError:
        raise HTTPException(404, "stain stack closed while reading")
    except Exception as e:
        # as stains_tile: a read that broke on the way (network drive, Box)
        # answers "try again", and the viewer retries the tile
        print(f"[stains] {ds_id} raw ch={idx} z={z} x={x} y={y}: {type(e).__name__}: {e}",
              file=sys.stderr)
        raise HTTPException(503, "reading the stain tile failed -- try again")
    if t is None:
        return Response(status_code=204)
    data = t.astype("<u2", copy=False).tobytes()
    return Response(content=data, media_type="application/octet-stream",
                    headers={"Cache-Control": "max-age=3600",
                             "X-Tile-Size": str(ss.tile)})


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
    try:
        png = ss.composite_tile(spec, level, x, y, orient=ORI.get(ds_id))
    except KeyError:
        raise HTTPException(404, "stain stack closed while reading")
    except Exception as e:
        # A read that broke on the way -- a network drive or a cloud-synced
        # folder (Box) stalling mid-file. Log it and answer "try again": the
        # viewer retries a failed tile rather than leaving that square blank.
        print(f"[stains] {ds_id} tile z={z} x={x} y={y}: {type(e).__name__}: {e}",
              file=sys.stderr)
        raise HTTPException(503, "reading the stain tile failed -- try again")
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
        return _index_page()

    def _index_page():
        # The page must never come out of the browser's cache: every version is
        # served at the same address (http://127.0.0.1:8050/), so a cached copy
        # of an OLD version's page kept showing after an update. The page then
        # names hashed script files, so those can be cached as long as liked.
        return FileResponse(str(_DIST / "index.html"),
                            headers={"Cache-Control": "no-cache"})

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
            return _index_page()
        if f.is_file():
            return FileResponse(str(f))
        return _index_page()                              # SPA fallback

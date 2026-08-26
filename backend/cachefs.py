"""Local mirror of a network dataset's heavy imagery files.

Network drives are latency-bound: the viewer's thousands of small seeks into
the JP2 pyramid, the morphology_focus zip and the transcripts zarr each pay a
round-trip, which is what makes S:-hosted slides feel slow (and what makes a
slow request occasionally look like a broken stain channel). Sequential bulk
copy is the one thing SMB is good at -- so on first open of a network-hosted
dataset the big read-only files are copied once into the local workdir, and
every read after that is local.

Rules:
  * only datasets on a NETWORK location are mirrored (UNC path or a drive
    Windows reports as remote); local folders are read in place.
  * a mirror is keyed by the dataset's full root path and validated per file
    by (size, mtime) -- a re-exported slide invalidates itself.
  * the copy runs in a background thread; the dataset works normally off the
    network while it runs, and readers are switched to the mirror only when
    every file arrived intact.
  * ATLAS_NO_CACHE=1 turns the whole thing off; ATLAS_CACHE_ALL=1 mirrors
    local folders too (useful for testing).
"""
from __future__ import annotations

import ctypes
import hashlib
import json
import os
import shutil
import sys
import threading
from pathlib import Path

from config import WORKDIR

CACHE_ROOT = WORKDIR / "cache"

_lock = threading.Lock()
_state: dict[str, dict] = {}      # ds_id -> {state, copiedBytes, totalBytes, error}
_threads: dict[str, threading.Thread] = {}


# ---- what to mirror ---------------------------------------------------------

def _wanted(desc: dict) -> list[Path]:
    """The heavy read-only files/dirs the viewers actually seek into."""
    out: list[Path] = []
    src = desc.get("sources") or {}
    m = src.get("morphology")
    if m and m.get("path"):
        out.append(Path(m["path"]))
    mf = src.get("morphology_focus")
    if mf:
        if mf.get("kind") == "zip" and mf.get("path"):
            out.append(Path(mf["path"]))
        elif mf.get("files"):
            out.extend(Path(f) for f in mf["files"])
    tz = src.get("transcripts")
    if tz and tz.get("path"):
        out.append(Path(tz["path"]))
    return [p for p in out if p.exists()]


def _is_network(path: str) -> bool:
    if os.environ.get("ATLAS_CACHE_ALL") == "1":
        return True
    p = str(path)
    if p.startswith("\\\\") or p.startswith("//"):
        return True
    if sys.platform == "win32" and len(p) >= 2 and p[1] == ":":
        try:  # DRIVE_REMOTE == 4
            return ctypes.windll.kernel32.GetDriveTypeW(p[:2] + "\\") == 4
        except Exception:
            return False
    return False


def enabled_for(desc: dict) -> bool:
    # Opt-in for now (ATLAS_CACHE=1): a mirror is ~10 GB of local disk per
    # dataset, which should be a decision, not a side effect of clicking one.
    if os.environ.get("ATLAS_CACHE") != "1":
        return False
    if os.environ.get("ATLAS_NO_CACHE") == "1":
        return False
    return _is_network(str(desc.get("root") or ""))


# ---- mirror layout ----------------------------------------------------------

def _mirror_dir(root: str) -> Path:
    return CACHE_ROOT / hashlib.sha1(str(root).encode("utf-8")).hexdigest()[:12]


def _dst_for(src: Path, root: Path, mdir: Path) -> Path:
    try:
        return mdir / src.relative_to(root)
    except ValueError:
        return mdir / src.name


def _iter_files(p: Path):
    """(file, size) pairs under p -- p itself if it's a file, else the tree
    (a directory transcripts.zarr is thousands of small chunk files)."""
    if p.is_file():
        yield p, p.stat().st_size
    else:
        for f in sorted(p.rglob("*")):
            if f.is_file():
                yield f, f.stat().st_size


def _fresh(src: Path, dst: Path) -> bool:
    try:
        a, b = src.stat(), dst.stat()
        return b.st_size == a.st_size and abs(b.st_mtime - a.st_mtime) < 2.0
    except OSError:
        return False


# ---- the copy ---------------------------------------------------------------

def _copy_one(src: Path, dst: Path, progress):
    dst.parent.mkdir(parents=True, exist_ok=True)
    part = dst.with_name(dst.name + ".part")
    with open(src, "rb") as fi, open(part, "wb") as fo:
        while True:
            chunk = fi.read(8 * 1024 * 1024)
            if not chunk:
                break
            fo.write(chunk)
            progress(len(chunk))
    shutil.copystat(src, part)
    os.replace(part, dst)


def _run(ds_id: str, desc: dict, on_ready):
    root = Path(str(desc["root"]))
    mdir = _mirror_dir(str(root))
    try:
        plan = []          # (src_file, dst_file, size)
        total = done = 0
        for top in _wanted(desc):
            for f, size in _iter_files(top):
                dst = _dst_for(f, root, mdir)
                total += size
                if _fresh(f, dst):
                    done += size
                else:
                    plan.append((f, dst, size))
        with _lock:
            _state[ds_id] = {"state": "copying", "copiedBytes": done,
                             "totalBytes": total, "error": None}

        def progress(n):
            with _lock:
                _state[ds_id]["copiedBytes"] += n

        for f, dst, _size in plan:
            _copy_one(f, dst, progress)
        with _lock:
            _state[ds_id]["state"] = "ready"
        on_ready(ds_id, remap(desc))
    except Exception as e:
        # offline drive mid-copy, disk full... the dataset keeps working off
        # the network exactly as before; next open tries again.
        with _lock:
            _state[ds_id] = {"state": "error", "copiedBytes": 0, "totalBytes": 0,
                             "error": f"{type(e).__name__}: {e}"}
        print(f"[cache] {ds_id}: {type(e).__name__}: {e}", file=sys.stderr)


def remap(desc: dict) -> dict:
    """A copy of desc with every mirrored source path pointing at the mirror."""
    root = Path(str(desc["root"]))
    mdir = _mirror_dir(str(root))

    def local(p: str) -> str:
        return str(_dst_for(Path(p), root, mdir))

    out = json.loads(json.dumps(desc))
    src = out.get("sources") or {}
    if src.get("morphology", {}).get("path"):
        src["morphology"]["path"] = local(src["morphology"]["path"])
    mf = src.get("morphology_focus")
    if mf:
        if mf.get("kind") == "zip" and mf.get("path"):
            mf["path"] = local(mf["path"])
        elif mf.get("files"):
            mf["files"] = [local(f) for f in mf["files"]]
            mf["path"] = local(mf["path"]) if mf.get("path") else mf.get("path")
    if src.get("transcripts", {}).get("path"):
        src["transcripts"]["path"] = local(src["transcripts"]["path"])
    out["cached"] = True
    return out


# ---- public -----------------------------------------------------------------

def start(ds_id: str, desc: dict, on_ready) -> dict:
    """Kick off (or report) the mirror for one dataset. Idempotent; returns
    the current status. `on_ready(ds_id, remapped_desc)` runs on the copy
    thread once every file is local and verified."""
    if not enabled_for(desc):
        return {"state": "off"}
    with _lock:
        st = _state.get(ds_id)
        th = _threads.get(ds_id)
        if st and st["state"] == "ready":
            return dict(st)
        if th and th.is_alive():
            return dict(st or {"state": "copying"})
        _state[ds_id] = {"state": "copying", "copiedBytes": 0,
                         "totalBytes": 0, "error": None}
        t = threading.Thread(target=_run, args=(ds_id, desc, on_ready),
                             name=f"cache-{ds_id}", daemon=True)
        _threads[ds_id] = t
    t.start()
    return dict(_state[ds_id])


def status(ds_id: str) -> dict:
    with _lock:
        return dict(_state.get(ds_id) or {"state": "off"})


def usage() -> dict:
    total = 0
    if CACHE_ROOT.exists():
        for f in CACHE_ROOT.rglob("*"):
            if f.is_file():
                total += f.stat().st_size
    return {"dir": str(CACHE_ROOT), "bytes": total}


def clear() -> dict:
    """Delete the whole mirror. Only safe when no dataset is being read from
    it -- the caller evicts readers first."""
    with _lock:
        busy = [k for k, t in _threads.items() if t.is_alive()]
        if busy:
            raise RuntimeError(f"still copying: {', '.join(busy)}")
        _state.clear()
    if CACHE_ROOT.exists():
        shutil.rmtree(CACHE_ROOT, ignore_errors=True)
    return usage()

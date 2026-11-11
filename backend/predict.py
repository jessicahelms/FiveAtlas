"""Region prediction from the 18-gene transcript map: model idx=22 and the rules the
lab's production predictions were made under (the 234 sections of the 30 Oct batch,
84 of which went to Box on 4 Nov).

Everything here is the same arithmetic as that batch, on purpose, because the model
was only ever validated on inputs made this way:

  canvas     tissue box = largest blob of 25 um transcript density inside the outline,
             + 5 %; letterboxed to 640 x 640; gaussian sigma 0.8 px; flipped so the
             section has its hippocampus up and its medial edge on the right; one
             global p99.5 across the 18 genes; uint16.
  model      VMamba-small, 18 genes in, 12 regions + background out, no rescale.
  gate       outside the predicted tissue silhouette -> background; inside it the
             best foreground region.
  cut        vertical line through the middle of the medial ventricle (the most
             medial ventricle piece touching both hippocampus and thalamus; slivers
             under 100 px don't count); everything medial of the line is dropped.
  dissolves  in this order: ventricle pieces mostly inside hypothalamus -> HY; fiber
             pieces >= 60 % ringed by thalamus -> TH; any other ventricle / fiber piece
             >= 60 % ringed by one region -> that region (kept: pieces on the outer
             tissue surface, ventricle on the midline cut).

The result is a FeatureCollection in the dataset's displayed pixel frame, one feature
per region name (lobes are one MultiPolygon). Nothing is written from here.

More models can be added by dropping their .onnx files in a models folder (see
_model_files). They run under these same rules, so a model has to carry the same 12
regions to be usable; one that doesn't is listed with the reason.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import numpy as np

import config
import orientation as ORI

MODEL_FILE = "region_model_idx22.onnx"
METADATA_KEY = "fiveatlas_region_model"

CANVAS, SIGMA, MARGIN, BIN_UM = 640, 0.8, 0.05, 25.0
READ_THREADS = 8
#: the local copies of each section's panel points; oldest-used go first past this
CACHE_MAX_BYTES = 5_000_000_000
#: grid used to settle most points at once when testing them against the outline
TISSUE_CELL_UM = 50.0
SIMPLIFY_PX, MIN_AREA_PX = 0.35, 4
#: smooth outlines: gaussian along each border, in pixel-edge steps; then simplified (canvas px)
SMOOTH_SIGMA, SMOOTH_SIMPLIFY_PX = 2.0, 0.15
#: the ventricle a cut can be placed on has to be at least this big (canvas px)
MIN_CUT_PIECE_PX = 100
#: the strip beside the cut is not trimmed: an outline's medial edge is slanted, the cut is vertical
TRIM_EXEMPT_PX = 30
#: production sections ran 8.9 - 12.3 um per canvas pixel; the renders were ~10.4
SCALE_OK_UM = (7.5, 14.0)

VENT, ISO, HY, HPF, CP, TH, SAMY, CTXSP, OLF, PAL, FT = range(1, 12)
#: Kept apart only AFTER the production rules, which ran on one merged fiber-tract class
#: and one ventricle class: OPT is the model's own optic-tract output where the final map
#: still says FT; V3 is the ventricle piece on the midline cut. The rest is unchanged.
OPT, V3 = 12, 13
#: model output id -> region id (the optic tract is FT for the rules, see OPT)
TRAIN_TO_REGION = {1: VENT, 2: ISO, 3: OLF, 4: SAMY, 5: CP, 6: HY, 7: TH, 8: HPF, 9: FT, 10: FT, 11: CTXSP, 12: PAL}
MODEL_OPT = 9
#: what the model's outputs have to be called, checked against the model file
EXPECT_DIVISIONS = {1: "Ventricles", 2: "Isocortex", 3: "Olfactory", 4: "Striatum-Like Amygdalar Nuclei", 5: "Caudoputamen",
                    6: "Hypothalamus", 7: "Thalamus", 8: "Hippocampal Formation", 9: "Optic Tract", 10: "Fiber Tracts",
                    11: "Cortical Subplate", 12: "Pallidum"}
#: the names the annotators use (Allen CCF abbreviations, as in their region files).
#: ft is every fiber tract but the optic tract: the annotator cuts it into dft / lft.
NAMES = [None, "VL", "ISO", "HY", "HPF", "CP", "TH", "sAMY", "CTXsp", "OLF", "PAL", "ft", "opt", "V3"]
FULL_NAMES = [None, "Lateral ventricle", "Isocortex", "Hypothalamus", "Hippocampal Formation", "Caudoputamen", "Thalamus",
              "Striatum-Like Amygdalar Nuclei", "Cortical Subplate", "Olfactory", "Pallidum",
              "Fiber tracts (dft + lft, to split by hand)", "Optic tract", "Third ventricle"]
COLORS = [None, [60, 180, 240], [245, 176, 65], [96, 201, 117], [230, 92, 120], [163, 110, 224], [69, 201, 201],
          [238, 118, 44], [116, 160, 255], [218, 108, 188], [170, 185, 70], [210, 210, 210], [150, 150, 150],
          [30, 120, 200]]
N_REGIONS = len(NAMES)
POSTPROCESSING = ["ventricle cut (medial side cropped)", "ventricle pieces mostly inside hypothalamus -> Hypothalamus",
                  "fiber-tract pieces >=60% surrounded by thalamus -> Thalamus",
                  "any other ventricle / fiber-tract piece >=60% surrounded by one region -> that region "
                  "(kept: pieces on the outer tissue surface, ventricle on the midline cut)",
                  "names: optic tract (opt) where the model said optic tract; other fiber tracts are ft (dft + lft); "
                  "the ventricle on the midline cut is V3, the rest VL"]


class PredictError(Exception):
    """A reason the annotator can act on; shown as it is."""


# ---- the model file ------------------------------------------------------------


def _model_files():
    """Every model file this install can see, the default first: ATLAS_REGION_MODEL, then
    idx=22, then the rest of the app's models folder and of the one shipped with the build."""
    files = []
    override = os.environ.get("ATLAS_REGION_MODEL")
    if override and Path(override).is_file():
        files.append(Path(override))
    for d in (Path(config.WORKDIR) / "models", Path(config.BUNDLE_DIR) / "models"):
        if d.is_dir():
            files += sorted(d.glob("*.onnx"), key=lambda p: (p.name != MODEL_FILE, p.name.lower()))
    seen, out = set(), []
    for p in files:
        if p.name not in seen:
            seen.add(p.name)
            out.append(p)
    return out


_session_lock = threading.Lock()
_sessions: dict = {}         # path -> (mtime, InferenceSession, metadata)
_failed: dict = {}           # path -> (mtime, reason): an unusable file isn't reopened on every status()


def _open(path):
    """(session, metadata) of one model file. Every way it can fail is a PredictError."""
    with _session_lock:
        try:
            mtime = path.stat().st_mtime
        except OSError:
            raise PredictError(f"The model {path.name} is no longer installed.")
        hit = _sessions.get(path)
        if hit and hit[0] == mtime:
            return hit[1], hit[2]
        bad = _failed.get(path)
        if bad and bad[0] == mtime:
            raise PredictError(bad[1])
        try:
            sess, meta = _open_file(path)
        except PredictError as e:
            _failed[path] = (mtime, str(e))
            raise
        _failed.pop(path, None)
        _sessions[path] = (mtime, sess, meta)
        return sess, meta


def _open_file(path):
    try:
        import onnxruntime as ort
    except ImportError as e:
        raise PredictError(f"This build cannot run the model: {e}")
    so = ort.SessionOptions()
    so.intra_op_num_threads = min(os.cpu_count() or 4, 8)
    so.log_severity_level = 3
    try:
        sess = ort.InferenceSession(str(path), so, providers=["CPUExecutionProvider"])
    except Exception as e:
        raise PredictError(f"{path.name} could not be opened as a model: {e}")
    raw = sess.get_modelmeta().custom_metadata_map.get(METADATA_KEY)
    if not raw:
        raise PredictError(f"{path.name} is not a FiveAtlas region model (no {METADATA_KEY} metadata).")
    try:
        meta = json.loads(raw)
        got = {int(k): v for k, v in meta["division_names"].items() if int(k) != 0}
        meta["channel_names"], meta["model_id"], meta["norm_mean"], meta["norm_std"]   # all present
    except Exception as e:
        raise PredictError(f"{path.name} carries unreadable model metadata: {type(e).__name__}: {e}")
    if got != EXPECT_DIVISIONS:
        raise PredictError(f"{path.name} predicts different regions than this version of FiveAtlas expects.")
    return sess, meta


def _load(model=None):
    """The session and metadata of `model` (a file name from status()), or of the
    default: the first file that opens, the same one status() names."""
    files = _model_files()
    if not files:
        raise PredictError(f"The region model file ({MODEL_FILE}) is not installed.")
    if model is not None:
        path = next((p for p in files if p.name == model), None)
        if path is None:
            raise PredictError(f"The model {model} is no longer installed.")
        return _open(path)
    first = None
    for p in files:
        try:
            return _open(p)
        except PredictError as e:
            first = first or e
    raise first


def _label(meta, path):
    """'idx=22, mIoU 0.844' out of the run name, for the dropdown."""
    mid = str(meta.get("model_id", ""))
    idx, miou = re.search(r"idx=\d+", mid), re.search(r"miou=([\d.]+)", mid)
    if not idx:
        return path.stem
    return f"{idx.group(0)}, mIoU {miou.group(1)}" if miou else idx.group(0)


def status() -> dict:
    """What this install can predict with. Never raises: the top bar asks on every dataset."""
    models = []
    for p in _model_files():
        try:
            _, meta = _open(p)
            models.append({"id": p.name, "label": _label(meta, p), "title": meta["model_id"], "usable": True})
        except PredictError as e:
            models.append({"id": p.name, "label": p.stem, "title": str(e), "usable": False})
        except Exception as e:
            models.append({"id": p.name, "label": p.stem, "title": f"The model could not be opened: {e}", "usable": False})
    usable = [m for m in models if m["usable"]]
    if usable:
        return {"available": True, "default": usable[0]["id"], "model": usable[0]["title"], "models": models}
    reason = models[0]["title"] if models else f"The region model file ({MODEL_FILE}) is not installed."
    return {"available": False, "reason": reason, "models": models}


# ---- transcripts -> the model's input ------------------------------------------


def _gene_names(attrs):
    import ast
    for k in ("gene_names", "genes", "target_names", "feature_names"):
        if k in attrs:
            v = attrs[k]
            v = v if isinstance(v, (list, tuple)) else ast.literal_eval(v)
            return [x.decode() if isinstance(x, bytes) else str(x) for x in v]
    raise PredictError("The transcript file does not list its genes.")


def read_points(transcripts_path, genes):
    """Every transcript of the panel genes, in microns, plus the extent of ALL
    transcripts. Reads every grid level, like the batch that made the validated
    canvases: the counts (and with them the p99.5 scale) are part of the model's
    input definition."""
    import zarr

    p = Path(transcripts_path)
    stores, stores_guard, local = [], threading.Lock(), threading.local()

    def open_root():
        # A ZipStore serves one read at a time, so each thread gets its own.
        if p.is_dir():
            return zarr.open_group(str(p), mode="r")
        store = zarr.ZipStore(str(p), mode="r")
        with stores_guard:
            stores.append(store)
        return zarr.open_group(store, mode="r")

    def thread_root():
        if not hasattr(local, "root"):
            local.root = open_root()
        return local.root

    try:
        root = open_root()
        names = _gene_names(dict(root.attrs))
        if "grids" not in root:
            raise PredictError("The transcript file has no per-transcript grids to predict from.")
        panel = {g: i for i, g in enumerate(names)}
        missing = [g for g in genes if g not in panel]
        ids = np.array([panel[g] for g in genes if g in panel])
        cells = [(level, coord) for level in root["grids"].keys() for coord in root["grids"][level].keys()]

        def read_cell(cell):
            gg = thread_root()["grids"][cell[0]][cell[1]]
            if "location" not in gg or "gene_identity" not in gg:
                return None
            xy = gg["location"][:][:, :2]
            gid = gg["gene_identity"][:].flatten()
            if not len(xy):
                return None
            sel = np.isin(gid, ids)
            return xy.min(0), xy.max(0), xy[sel], gid[sel]

        with ThreadPoolExecutor(READ_THREADS) as pool:
            found = [r for r in pool.map(read_cell, cells) if r is not None]
    finally:
        for store in stores:
            store.close()
    keep = [r for r in found if len(r[2])]
    if not keep:
        raise PredictError("None of the model's 18 genes were found in this dataset.")
    lo, hi = np.min([r[0] for r in found], 0), np.max([r[1] for r in found], 0)
    return np.concatenate([r[2] for r in keep], 0), np.concatenate([r[3] for r in keep], 0), panel, missing, lo, hi


# ---- the panel's points, kept locally so a section is read from its share only once ---


def _cache_file(path, genes):
    st = os.stat(path)
    key = json.dumps([os.path.abspath(path), st.st_size, st.st_mtime_ns, list(genes)])
    return Path(config.WORKDIR) / "predict_cache" / f"{hashlib.sha1(key.encode()).hexdigest()}.npz"


def _cache_read(f):
    try:
        with np.load(f, allow_pickle=False) as z:
            info = json.loads(str(z["info"]))
            out = z["xy"], z["gid"], info["panel"], info["missing"], z["lo"], z["hi"]
        os.utime(f)                 # marks it most recently used
        return out
    except Exception:               # absent or damaged: read the transcripts again
        return None


def _cache_write(f, xy, gid, panel, missing, lo, hi):
    """Only ever a shortcut: a full disk or a locked file must not fail a prediction."""
    tmp = f.with_name(f"{f.stem}.{os.getpid()}.{threading.get_ident()}.tmp.npz")
    try:
        f.parent.mkdir(parents=True, exist_ok=True)
        np.savez(tmp, xy=xy, gid=gid, lo=lo, hi=hi, info=np.array(json.dumps({"panel": panel, "missing": missing})))
        os.replace(tmp, f)
    except Exception:
        try:
            tmp.unlink()            # a half-written copy (disk full) must not pile up
        except OSError:
            pass
        return
    try:
        now = time.time()
        for old in f.parent.glob("*.tmp.npz"):     # left by a crash mid-write
            if now - old.stat().st_mtime > 3600:
                old.unlink()
        kept = 0
        for old in sorted((p for p in f.parent.glob("*.npz") if ".tmp." not in p.name), key=lambda p: -p.stat().st_mtime):
            kept += old.stat().st_size
            if kept > CACHE_MAX_BYTES and old != f:
                old.unlink()
    except OSError:
        pass


_read_locks: dict = {}
_read_locks_guard = threading.Lock()


def load_points(transcripts_path, genes):
    """read_points through the local cache. One reader per section: a Predict that
    arrives during the read-ahead waits for it instead of reading the file twice."""
    f = _cache_file(transcripts_path, genes)
    with _read_locks_guard:
        lock = _read_locks.setdefault(f, threading.Lock())
    with lock:
        hit = _cache_read(f)
        if hit is not None:
            return hit
        out = read_points(transcripts_path, genes)
        _cache_write(f, *out)
        return out


_wanted = None
_wake = threading.Event()
_ahead = None
_ahead_guard = threading.Lock()


def _read_ahead():
    global _wanted
    while True:
        _wake.wait()
        _wake.clear()
        with _ahead_guard:          # prepare() sets it under the same lock: no request lost
            job, _wanted = _wanted, None
        if job:
            try:
                load_points(*job)
            except Exception:       # Predict reports it, if anyone asks for one
                pass


def prepare(transcripts_path, model=None):
    """Start reading a section's points in the background, so Predict finds them ready.
    Only the newest request counts: flicking through datasets doesn't queue up reads."""
    global _wanted, _ahead
    _, meta = _load(model)
    genes = list(meta["channel_names"])
    if _cache_file(transcripts_path, genes).exists():
        return
    with _ahead_guard:
        _wanted = (transcripts_path, genes)
        if _ahead is None:
            _ahead = threading.Thread(target=_read_ahead, daemon=True, name="predict-read-ahead")
            _ahead.start()
    _wake.set()


def to_display(xy, lo, hi, pixel_size, o, W, H):
    """Microns as stored -> microns in the displayed frame. Untouched when the
    dataset isn't turned, so an unturned dataset is the batch's arithmetic exactly."""
    if ORI.is_identity(o):
        return xy, lo, hi
    x, y = ORI.fwd(xy[:, 0] / pixel_size, xy[:, 1] / pixel_size, o, W, H)
    out = np.stack([x, y], 1) * pixel_size
    cx, cy = ORI.fwd(np.array([lo[0], lo[0], hi[0], hi[0]]) / pixel_size,
                     np.array([lo[1], hi[1], lo[1], hi[1]]) / pixel_size, o, W, H)
    return out, np.array([cx.min(), cy.min()]) * pixel_size, np.array([cx.max(), cy.max()]) * pixel_size


def _inside(roi, pts):
    """shapely.contains_xy for millions of points. A cell wholly inside or outside the
    outline settles all its points at once; only points in cells on the edge are tested."""
    import shapely

    x0, y0, x1, y1 = roi.bounds
    nx, ny = math.ceil((x1 - x0) / TISSUE_CELL_UM) + 1, math.ceil((y1 - y0) / TISSUE_CELL_UM) + 1
    X, Y = np.meshgrid(x0 + np.arange(nx) * TISSUE_CELL_UM, y0 + np.arange(ny) * TISSUE_CELL_UM, indexing="ij")
    cells = shapely.box(X.ravel(), Y.ravel(), X.ravel() + TISSUE_CELL_UM, Y.ravel() + TISSUE_CELL_UM)
    shapely.prepare(roi)
    inner, touched = shapely.contains_properly(roi, cells), shapely.intersects(roi, cells)
    x, y = pts[:, 0].astype(float), pts[:, 1].astype(float)
    cell = ((x - x0) / TISSUE_CELL_UM).astype(np.int64) * ny + ((y - y0) / TISSUE_CELL_UM).astype(np.int64)
    keep = inner[cell]
    edge = np.flatnonzero(touched[cell] & ~keep)
    keep[edge] = shapely.contains_xy(roi, x[edge], y[edge])
    return keep


def tissue_box(xy, roi, pieces=False):
    """Bounding box of the tissue inside `roi` (shapely, microns): the largest blob
    of 25 um transcript density, or every blob >= 15 % of it when the section is in
    pieces. Returns (x0, y0, x1, y1), np.float64 like the batch."""
    from scipy import ndimage
    from scipy.ndimage import gaussian_filter

    b = roi.bounds
    rx0, ry0, rx1, ry1 = (np.float64(v) for v in b)
    inroi = (xy[:, 0] >= rx0) & (xy[:, 0] <= rx1) & (xy[:, 1] >= ry0) & (xy[:, 1] <= ry1)
    pin = xy[inroi]
    pin = pin[_inside(roi, pin)]
    if len(pin) == 0:
        raise PredictError("There are no transcripts of the model's genes inside that outline.")
    nx, ny = int(np.ceil((rx1 - rx0) / BIN_UM)) + 1, int(np.ceil((ry1 - ry0) / BIN_UM)) + 1
    H, _, _ = np.histogram2d(pin[:, 0], pin[:, 1], bins=[nx, ny], range=[[rx0, rx0 + nx * BIN_UM], [ry0, ry0 + ny * BIN_UM]])
    Hs = gaussian_filter(H, 1.0)
    m = Hs > 0.15 * np.percentile(Hs[Hs > 0], 90)
    m = ndimage.binary_closing(m, iterations=2)
    lab, n = ndimage.label(m)
    if n == 0:
        raise PredictError("No tissue was found inside that outline.")
    sizes = np.asarray(ndimage.sum(m, lab, range(1, n + 1)))
    big = lab == (np.argmax(sizes) + 1)
    if pieces:
        big = np.isin(lab, np.flatnonzero(sizes >= 0.15 * sizes.max()) + 1)
    ix = np.where(big.any(1))[0]
    iy = np.where(big.any(0))[0]
    return rx0 + ix.min() * BIN_UM, ry0 + iy.min() * BIN_UM, rx0 + (ix.max() + 1) * BIN_UM, ry0 + (iy.max() + 1) * BIN_UM


def build_canvas(xy, gid, panel, genes, lo, hi, box_um, flip_h, flip_v):
    """The 18-gene 640 x 640 canvas. Returns uint16 [H, W, C] and its frame."""
    from scipy.ndimage import gaussian_filter

    gx0, gy0, gx1, gy1 = box_um
    mx, my = (gx1 - gx0) * MARGIN, (gy1 - gy0) * MARGIN
    x_min, y_min = max(gx0 - mx, float(lo[0])), max(gy0 - my, float(lo[1]))
    x_max, y_max = min(gx1 + mx, float(hi[0])), min(gy1 + my, float(hi[1]))
    dx, dy = float(x_max - x_min), float(y_max - y_min)
    scale = CANVAS / max(dx, dy)
    bw, bh = int(round(dx * scale)), int(round(dy * scale))
    pad_x, pad_y = (CANVAS - bw) // 2, (CANVAS - bh) // 2
    canv = []
    for gene in genes:
        c = np.zeros((CANVAS, CANVAS), np.float32)
        if gene in panel:
            pts = xy[gid == panel[gene]]
            inb = (pts[:, 0] >= x_min) & (pts[:, 0] <= x_max) & (pts[:, 1] >= y_min) & (pts[:, 1] <= y_max)
            pts = pts[inb]
            if len(pts):
                bx = np.clip(((pts[:, 0] - x_min) * scale).astype(np.int64) + pad_x, 0, CANVAS - 1)
                by = np.clip(((y_max - pts[:, 1]) * scale).astype(np.int64) + pad_y, 0, CANVAS - 1)
                c = np.bincount(by * CANVAS + bx, minlength=CANVAS * CANVAS).reshape(CANVAS, CANVAS).astype(np.float32)
        c = gaussian_filter(c, SIGMA)
        if flip_v:
            c = c[::-1, :]
        if flip_h:
            c = c[:, ::-1]
        canv.append(c)
    nz = [c[c > 0] for c in canv if c.any()]
    S = float(np.percentile(np.concatenate(nz), 99.5)) if nz else 1.0
    out = np.stack([(np.clip(c / S, 0.0, 1.0) * 65535).astype(np.uint16) for c in canv], 2)
    return out, {"scale": float(scale), "pad_x": pad_x, "pad_y": pad_y, "x_min": float(x_min), "y_max": float(y_max),
                 "flip_h": bool(flip_h), "flip_v": bool(flip_v)}


# ---- model + gate --------------------------------------------------------------


def gate(logits, hemi):
    """Outside the predicted silhouette -> background; inside -> best foreground."""
    from scipy import ndimage

    sil = ndimage.binary_fill_holes(hemi[0, 0] > 0)
    lab, n = ndimage.label(sil, structure=np.ones((3, 3)))
    if n > 1:
        counts = np.bincount(lab.ravel())
        counts[0] = 0
        sil = lab == int(counts.argmax())
    fg = logits[0, 1:].argmax(0) + 1
    out = np.zeros(fg.shape, np.uint8)
    out[sil] = fg[sil]
    return out


def segment(canvas_u16, model=None):
    """uint16 canvas -> gated model ids on the canvas."""
    sess, meta = _load(model)
    size = int(meta["img_size"])
    if canvas_u16.shape[:2] != (size, size):
        raise PredictError(f"The model expects a {size} px canvas.")
    image = canvas_u16.astype(np.float32) / 65535.0
    mean, std = np.asarray(meta["norm_mean"], np.float32), np.asarray(meta["norm_std"], np.float32)
    x = ((image - mean) / std).transpose(2, 0, 1)[None].astype(np.float32)
    logits, hemi = sess.run(["logits", "hemi"], {"x": x})
    return gate(logits, hemi)


# ---- the production rules, on region ids ----------------------------------------


def to_regions(model_ids):
    lut = np.zeros(13, np.uint8)
    for t, r in TRAIN_TO_REGION.items():
        lut[t] = r
    return lut[model_ids].astype(int)


def ventricle_cut(lab):
    """Column of the midline cut, or None when no ventricle touches hippocampus and thalamus."""
    from scipy import ndimage

    s8 = np.ones((3, 3))
    comps, n = ndimage.label(lab == VENT, structure=s8)
    pieces = []
    for i in range(1, n + 1):
        m = comps == i
        ring = ndimage.binary_dilation(m, s8)
        if (lab[ring] == HPF).any() and (lab[ring] == TH).any():
            cols = np.flatnonzero(m.any(0))
            pieces.append((int(m.sum()), int(cols.min()), int(cols.max())))
    if not pieces:
        return None
    best = max([p for p in pieces if p[0] >= MIN_CUT_PIECE_PX] or pieces, key=lambda p: p[2])
    return int(round((best[1] + best[2]) / 2))


def dissolve_hy_ventricle(lab):
    from scipy import ndimage

    lab = np.asarray(lab).copy()
    v = lab == VENT
    tissue = (lab > 0) & ~v
    if not v.any() or not tissue.any():
        return lab
    _, (ri, ci) = ndimage.distance_transform_edt(~tissue, return_indices=True)
    near_hy = lab[ri, ci] == HY
    comps, n = ndimage.label(v, structure=np.ones((3, 3)))
    if n == 0:
        return lab
    frac = ndimage.mean(near_hy, comps, range(1, n + 1))
    lab[np.isin(comps, np.flatnonzero(np.asarray(frac) > 0.5) + 1)] = HY
    return lab


def dissolve_th_fiber(lab, frac=0.6):
    from scipy import ndimage

    lab = np.asarray(lab).copy()
    comps, n = ndimage.label(lab == FT, structure=np.ones((3, 3)))
    for i in range(1, n + 1):
        m = comps == i
        ring = ndimage.binary_dilation(m, np.ones((5, 5))) & ~m & (lab > 0)
        if ring.any() and (lab[ring] == TH).mean() >= frac:
            lab[m] = TH
    return lab


def dissolve_enclosed(lab, cut_x=None, frac=0.6):
    from scipy import ndimage

    lab = np.asarray(lab).copy()
    cols = np.arange(lab.shape[1])[None, :]
    cut_side = (cols > cut_x) if cut_x is not None else np.zeros((1, lab.shape[1]), bool)
    for cls in (VENT, FT):
        comps, n = ndimage.label(lab == cls, structure=np.ones((3, 3)))
        for i in range(1, n + 1):
            m = comps == i
            dil = ndimage.binary_dilation(m, np.ones((5, 5)))
            bg = dil & (lab == 0)
            if (bg & ~cut_side).any() or (cls == VENT and bg.any()):
                continue
            ring = dil & ~m & (lab > 0)
            if not ring.any():
                continue
            v = np.bincount(lab[ring], minlength=N_REGIONS)
            v2 = v.copy()
            v2[[0, VENT, FT]] = -1
            dom = int(np.argmax(v2))
            if v[dom] / v.sum() >= frac:
                lab[m] = dom
    return lab


def apply_rules(model_ids):
    """Gated model ids -> (final region map, cut column, pixels changed per step)."""
    lab = to_regions(model_ids)
    cut_x = ventricle_cut(lab)
    cut = lab.copy()
    if cut_x is not None:
        cut[:, cut_x + 1:] = 0
    hy = dissolve_hy_ventricle(cut)
    th = dissolve_th_fiber(hy)
    fin = dissolve_enclosed(th, cut_x)
    steps = {"hy_ventricle_dissolved_px": int((hy != cut).sum()), "th_fiber_dissolved_px": int((th != hy).sum()),
             "enclosed_dissolved_px": int((fin != th).sum())}
    # the rules above are the production ones, on the merged class; only now does the
    # optic tract get its own name, where the model said optic tract and FT survived
    fin = np.where((fin == FT) & (np.asarray(model_ids) == MODEL_OPT), OPT, fin)
    return fin, cut_x, steps


def split_third_ventricle(lab, cut_x):
    """Ventricle pieces reaching the midline cut are the third ventricle (V3); the rest
    stay lateral ventricle (VL). No cut found -> nothing is called V3."""
    from scipy import ndimage

    if cut_x is None or not (lab == VENT).any():
        return lab
    comps, n = ndimage.label(lab == VENT, structure=np.ones((3, 3)))
    edge = np.unique(comps[:, cut_x])            # the cut column itself is the last one kept
    on_cut = edge[edge > 0]
    if not on_cut.size:
        return lab
    lab = lab.copy()
    lab[np.isin(comps, on_cut)] = V3
    return lab


# ---- back to the slide ----------------------------------------------------------


def canvas_to_um(c, r, frame):
    """Canvas (col, row) -> displayed-frame microns: the exact inverse of build_canvas."""
    if frame["flip_h"]:
        c = CANVAS - c
    if frame["flip_v"]:
        r = CANVAS - r
    return frame["x_min"] + (c - frame["pad_x"]) / frame["scale"], frame["y_max"] - (r - frame["pad_y"]) / frame["scale"]


def trim_outside(lab, roi, frame, cut_x):
    """Drop predicted pixels outside the outline: for a neighbouring section stacked on
    the same scan. The strip beside the cut stays. Returns (map, pixels removed)."""
    import shapely
    from scipy import ndimage

    rr, cc = np.mgrid[0:CANVAS, 0:CANVAS]
    x, y = canvas_to_um(cc + 0.5, rr + 0.5, frame)
    inside = ndimage.binary_dilation(shapely.contains_xy(roi, x, y).reshape(CANVAS, CANVAS), np.ones((3, 3)))
    out = (lab > 0) & ~inside
    if cut_x is not None:
        out[:, max(cut_x - TRIM_EXEMPT_PX, 0):] = False
    lab = lab.copy()
    lab[out] = 0
    return lab, int(out.sum())


def _class_polygons(mask):
    """Union of a mask's pixel squares (canvas px), so shared borders coincide."""
    from shapely.geometry import Polygon, box
    from shapely.ops import unary_union

    rects = []
    for r in np.flatnonzero(mask.any(1)):
        d = np.diff(np.concatenate([[0], mask[r].astype(np.int8), [0]]))
        for s, e in zip(np.flatnonzero(d == 1), np.flatnonzero(d == -1)):
            rects.append(box(int(s), int(r), int(e), int(r) + 1))
    g = unary_union(rects)
    return [g] if isinstance(g, Polygon) else list(g.geoms)


def _absorb_specks(lab):
    """Bits under MIN_AREA_PX take the label around them, so no pinhole is left behind."""
    from scipy import ndimage

    lab = np.asarray(lab).copy()
    for cls in np.unique(lab):
        comps, n = ndimage.label(lab == cls)
        if not n:
            continue
        sizes = np.bincount(comps.ravel())
        for i in np.flatnonzero(sizes[1:] < MIN_AREA_PX) + 1:
            m = comps == i
            ring = ndimage.binary_dilation(m) & ~m
            if ring.any():
                lab[m] = np.bincount(lab[ring]).argmax()
    return lab


def _border_chains(lab):
    """Every border between two different labels, as chains of pixel corners running
    from junction to junction (or as closed loops). Returns [(points, closed), ...]."""
    p = np.pad(lab, 1)
    adj: dict = {}

    def link(a, b):
        adj.setdefault(a, []).append(b)
        adj.setdefault(b, []).append(a)

    for r, c in zip(*np.nonzero(p[:-1, :] != p[1:, :])):
        link((int(c) - 1, int(r)), (int(c), int(r)))
    for r, c in zip(*np.nonzero(p[:, :-1] != p[:, 1:])):
        link((int(c), int(r) - 1), (int(c), int(r)))
    used = set()

    def walk(a, b):
        pts = [a, b]
        used.add(frozenset((a, b)))
        while len(adj[pts[-1]]) == 2 and pts[-1] != pts[0]:
            nxt = next((q for q in adj[pts[-1]] if frozenset((pts[-1], q)) not in used), None)
            if nxt is None:
                break
            used.add(frozenset((pts[-1], nxt)))
            pts.append(nxt)
        return pts

    chains = []
    for a, around in adj.items():
        if len(around) != 2:
            for b in around:
                if frozenset((a, b)) not in used:
                    chains.append((walk(a, b), False))
    for a, around in adj.items():
        for b in around:
            if frozenset((a, b)) not in used:
                chains.append((walk(a, b), True))
    return chains


def _smooth_chain(points, closed):
    """Gaussian along one border, to take the pixel stairs out. Junctions stay put."""
    from scipy.ndimage import gaussian_filter1d

    xy = np.asarray(points, float)
    n = len(xy)
    if n < 4:
        return xy
    sigma = min(SMOOTH_SIGMA, n / 8.0)
    if closed:
        out = gaussian_filter1d(xy[:-1], sigma, axis=0, mode="wrap")
        return np.vstack([out, out[:1]])
    # Point-reflected ends: the filter then can't move the first and last point.
    k = min(int(4 * sigma) + 1, n - 1)
    head, tail = 2 * xy[0] - xy[k:0:-1], 2 * xy[-1] - xy[-2:-k - 2:-1]
    out = gaussian_filter1d(np.vstack([head, xy, tail]), sigma, axis=0, mode="nearest")[k:k + n]
    out[0], out[-1] = xy[0], xy[-1]
    return out


def _smooth_regions(lab):
    """{region id: smoothed shapely geometry} in canvas px. Each border between two
    regions is ONE curve, smoothed once and used by both sides, so neighbours still
    share their border exactly: smoothing can't open a gap or make an overlap."""
    import shapely
    from shapely.geometry import LineString
    from shapely.ops import polygonize, unary_union

    clean = _absorb_specks(lab)
    lines = [LineString(_smooth_chain(pts, closed)).simplify(SMOOTH_SIMPLIFY_PX, preserve_topology=True)
             for pts, closed in _border_chains(clean)]
    by_region: dict = {}
    for face in polygonize(unary_union(lines)):        # the union nodes any two curves that cross
        x0, y0, x1, y1 = face.bounds
        rr, cc = np.mgrid[max(int(y0), 0):min(int(np.ceil(y1)), CANVAS), max(int(x0), 0):min(int(np.ceil(x1)), CANVAS)]
        shapely.prepare(face)
        inside = shapely.contains_xy(face, cc.ravel() + 0.5, rr.ravel() + 0.5)
        if inside.any():
            cls = int(np.bincount(clean[rr.ravel()[inside], cc.ravel()[inside]]).argmax())
        else:
            q = face.representative_point()
            cls = int(clean[min(int(q.y), CANVAS - 1), min(int(q.x), CANVAS - 1)])
        if cls:
            by_region.setdefault(cls, []).append(face)
    return {c: unary_union(v) for c, v in by_region.items()}


def to_features(lab, frame, pixel_size, id_prop, model_id, smooth=False):
    """One feature per region. The lobes of a region are ONE MultiPolygon: a name means
    one thing in this app, and Share borders and drags rely on it. `smooth` rounds off
    the pixel stairs; without it the outlines are the production (Box) ones."""
    from shapely.geometry import MultiPolygon, Polygon, mapping

    smoothed = _smooth_regions(lab) if smooth else None

    def tf(coords):
        out = []
        for c, r in coords:
            x, y = canvas_to_um(float(c), float(r), frame)
            out.append((round(x / pixel_size, 2), round(y / pixel_size, 2)))
        return out

    feats = []
    for cls in range(1, N_REGIONS):
        if not (lab == cls).any():
            continue
        parts = []
        if smooth:
            g = smoothed.get(cls)
            source = [] if g is None else [g] if isinstance(g, Polygon) else list(g.geoms)
        else:
            source = _class_polygons(lab == cls)
        for poly in source:
            if poly.area < MIN_AREA_PX:
                continue
            s = poly if smooth else poly.simplify(SIMPLIFY_PX, preserve_topology=True)
            q = Polygon(tf(s.exterior.coords), [tf(i.coords) for i in s.interiors])
            q = q if q.is_valid else q.buffer(0)
            parts += [q] if isinstance(q, Polygon) else list(q.geoms) if isinstance(q, MultiPolygon) else []
        if not parts:
            continue
        g = parts[0] if len(parts) == 1 else MultiPolygon(parts)
        feats.append({"type": "Feature", "geometry": mapping(g),
                      "properties": {"objectType": "annotation", id_prop: NAMES[cls], "name": NAMES[cls],
                                     "fullName": FULL_NAMES[cls],
                                     "classification": {"name": NAMES[cls], "color": COLORS[cls]}, "color": COLORS[cls],
                                     "class_id": cls, "model_id": model_id}})
    return feats


def qc(lab, cut_x, um_per_px, missing):
    """Things worth a look before trusting the map, in words. The prediction still stands."""
    warn = []
    if missing:
        warn.append(f"Missing from this panel: {', '.join(missing)}. The model was validated with all 18 genes.")
    if not SCALE_OK_UM[0] <= um_per_px <= SCALE_OK_UM[1]:
        warn.append(f"The section fills the canvas at {um_per_px:.1f} um per pixel, outside the {SCALE_OK_UM[0]}-{SCALE_OK_UM[1]} "
                    "the model has been used at. The outline may hold more than one section, or only part of one.")
    if cut_x is None:
        warn.append("No midline ventricle was found, so nothing was cropped. Check which way the section faces.")
    tissue = lab > 0
    for cls, what in ((ISO, "Isocortex"), (HPF, "Hippocampal Formation"), (TH, "Thalamus")):
        if (lab == cls).sum() < 50:
            warn.append(f"Almost no {what} was predicted. Check which way the section faces.")
    if tissue.any() and (lab == HPF).any() and (lab == TH).any() and (lab == ISO).any():
        r_hpf, r_th = np.nonzero(lab == HPF)[0].mean(), np.nonzero(lab == TH)[0].mean()
        c_iso, c_th = np.nonzero(lab == ISO)[1].mean(), np.nonzero(lab == TH)[1].mean()
        if r_hpf >= r_th:
            warn.append("Hippocampus came out below thalamus. The section may be upside down: check the dorsal setting.")
        if c_iso >= c_th:
            warn.append("Cortex came out medial of thalamus. The section may be mirrored: check the medial-edge setting.")
    return warn


def predict_section(transcripts_path, pixel_size, orient, W, H, roi_um, roi_name, medial, dorsal, trim, pieces, id_prop,
                    stage=lambda s: None, model=None, smooth=True):
    """All of it. `roi_um` is a shapely geometry in displayed-frame microns, or None for
    the whole slide. `medial` ('right'|'left') and `dorsal` ('up'|'down') say how the
    section lies on screen; the annotator states them, nothing here guesses."""
    from shapely.geometry import box

    _, meta = _load(model)
    genes = list(meta["channel_names"])
    stage("Reading transcripts")
    t0 = time.time()
    xy, gid, panel, missing, lo, hi = load_points(transcripts_path, genes)
    xy, lo, hi = to_display(xy, lo, hi, pixel_size, orient, W, H)
    roi = roi_um if roi_um is not None else box(float(lo[0]), float(lo[1]), float(hi[0]), float(hi[1]))
    stage("Finding the tissue")
    box_um = tissue_box(xy, roi, pieces)
    # The canvas row runs against y (row = y_max - y), so "dorsal up on screen" is the flip.
    flip_v, flip_h = dorsal == "up", medial == "left"
    stage("Building the 18-gene map")
    canvas, frame = build_canvas(xy, gid, panel, genes, lo, hi, box_um, flip_h, flip_v)
    del xy, gid
    stage("Predicting regions")
    model_ids = segment(canvas, model)
    stage("Applying the prediction rules")
    lab, cut_x, steps = apply_rules(model_ids)
    trimmed = 0
    if trim and roi_um is not None:
        lab, trimmed = trim_outside(lab, roi_um, frame, cut_x)
    lab = split_third_ventricle(lab, cut_x)
    stage("Tracing outlines")
    um_per_px = 1.0 / frame["scale"]
    feats = to_features(lab, frame, pixel_size, id_prop, meta["model_id"], smooth)
    if not feats:
        raise PredictError("The model found no regions here. Check the outline and which way the section faces.")
    px_area = (um_per_px / pixel_size) ** 2
    report = {"model": meta["model_id"], "cutX": cut_x, "umPerPx": round(um_per_px, 2), "outline": roi_name or "whole slide",
              "seconds": round(time.time() - t0, 1), "smooth": bool(smooth), "trimmedPx": trimmed, "steps": steps, "genesMissing": missing,
              "areas": {NAMES[c]: round(float((lab == c).sum() * px_area)) for c in range(1, N_REGIONS) if (lab == c).any()},
              "warnings": qc(lab, cut_x, um_per_px, missing)}
    fc = {"type": "FeatureCollection", "features": feats,
          "properties": {"model_id": meta["model_id"], "postprocessing": POSTPROCESSING,
                         "notes": "Automatic region prediction (not hand annotation). Medial of the ventricle midline cut is left out."}}
    return {"fc": fc, "report": report, "canvas": canvas, "labels": lab}


# ---- one running job per dataset --------------------------------------------------

_jobs: dict = {}
_jobs_lock = threading.Lock()


def submit(key, fn) -> bool:
    """Run fn(stage) on a worker thread. False when this dataset already has one running."""
    with _jobs_lock:
        cur = _jobs.get(key)
        if cur and cur["state"] == "running":
            return False
        job = {"state": "running", "stage": "Starting", "started": time.time()}
        _jobs[key] = job

    def stage(s):
        job["stage"] = s

    def work():
        try:
            job["result"] = fn(stage)
            job["state"] = "done"
        except PredictError as e:
            job["error"], job["state"] = str(e), "failed"
        except Exception as e:
            job["error"], job["state"] = f"Prediction failed: {type(e).__name__}: {e}", "failed"

    threading.Thread(target=work, daemon=True, name=f"predict-{key}").start()
    return True


def job_state(key) -> dict:
    job = _jobs.get(key)
    if not job:
        return {"state": "idle"}
    out = {"state": job["state"], "stage": job["stage"], "elapsed": round(time.time() - job["started"], 1)}
    if job["state"] == "failed":
        out["error"] = job["error"]
    if job["state"] == "done":
        out.update(job["result"])
    return out

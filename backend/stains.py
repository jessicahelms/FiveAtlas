"""morphology_focus stains -> full-res, zoomable, per-channel additive-blended
tiles (the Xenium Explorer look), placed natively over the morphology extent.

Each channel is a JP2 pyramid file read with is_ome=False (the multi-file OME
assembly loses the pyramid; per-file it keeps all 7 levels and reads a tile in
~0.1-0.5s on disk). A composite tile blends the visible channels
(colour x contrast, additive, alpha=brightness). Same tile scheme as the
morphology (1024 tiles, deck z 0..-maxLevel). Nothing hard-coded: channels,
dims, levels, tileSize all come from the files.
"""
from __future__ import annotations

import io
import re
import threading
import zipfile
from pathlib import Path

import numpy as np
import tifffile
import zarr
from PIL import Image

import orientation as ORI

# Xenium Explorer / Viv default channel palette, assigned BY CHANNEL INDEX
# (extracted verbatim from Xenium Explorer's app.asar). For the 4-channel
# Defaults keyed by the CHANNEL NAME so they hold on any dataset, matching the
# Xenium Explorer's own view: DAPI blue, the boundary stain
# (ATP1A1/CD45/E-Cadherin) magenta, interior RNA (18S) yellow, interior
# protein (alphaSMA/Vimentin) green. The index palette below is only the
# fallback for channels no rule names.
_BY_NAME = [
    ("dapi", [0, 0, 255]),
    ("cadherin", [255, 0, 255]), ("atp1a1", [255, 0, 255]), ("cd45", [255, 0, 255]),
    ("18s", [255, 255, 0]),
    ("vimentin", [0, 255, 0]), ("alphasma", [0, 255, 0]), ("sma", [0, 255, 0]),
]

_PALETTE = [
    [0, 0, 255],      # blue
    [0, 255, 0],      # green
    [255, 0, 255],    # magenta
    [255, 255, 0],    # yellow
    [255, 128, 0],    # orange
    [0, 255, 255],    # cyan
    [255, 255, 255],  # white
    [255, 0, 0],      # red
]


def _color_for(name, idx):
    n = str(name or "").lower()
    for key, col in _BY_NAME:
        if key in n:
            return col
    return _PALETTE[idx % len(_PALETTE)]


class StainStack:
    def __init__(self, mf: dict, workdir=None, tile: int = 1024):
        self.kind = mf["kind"]
        self.path = mf["path"]
        self.channels = mf.get("channels") or []
        self._files = mf.get("files")
        self.tile = tile
        self._lock = threading.Lock()
        self._zf = zipfile.ZipFile(self.path) if self.kind == "zip" else None
        self._member: dict[int, str] = {}
        self._resolve_members()
        self._z: dict[int, tuple] = {}          # idx -> (TiffFile, zarr, group?)
        self._contrast: dict[int, list] = {}    # idx -> [lo, hi, dataMax]
        self.nlevels = None
        self.level_shapes = None                # per level (H, W)
        self.W0 = self.H0 = None
        self._prime()

    def _resolve_members(self):
        if self.kind == "zip":
            names = [i.filename for i in self._zf.infolist()
                     if not i.is_dir() and i.filename.lower().endswith((".ome.tif", ".ome.tiff"))]
        else:
            names = self._files or [str(p) for p in Path(self.path).rglob("*.ome.tif*")]
        for n in names:
            base = Path(n).name
            m = (re.match(r".*ch(\d+)_", base, re.I)
                 or re.match(r"morphology_focus_(\d+)\.ome\.tiff?$", base, re.I))
            if m:
                self._member[int(m.group(1))] = n

    def _open(self, name):
        return self._zf.open(name) if self._zf else name

    def close(self):
        """Release the zip and every per-channel TIFF handle.

        One handle per stain channel plus the enclosing morphology_focus.zip, none
        of which tifffile/zipfile close on their own -- so closing a dataset left
        the whole folder locked on Windows and undeletable. Idempotent.
        """
        with self._lock:
            for tf, _z, _g in list(self._z.values()):
                try:
                    tf.close()
                except Exception:
                    pass
            self._z = {}
            zf, self._zf = getattr(self, "_zf", None), None
            if zf is not None:
                try:
                    zf.close()
                except Exception:
                    pass

    # ---- per-channel pyramid (opened is_ome=False, handle cached) ----------
    def _channel(self, idx):
        if idx not in self._z:
            with self._lock:
                # A request that raced a close() must fail loudly (the route
                # turns KeyError into 404), not reopen members through the
                # dropped zip handle -- self._zf is None then and _open would
                # hand tifffile a zip member NAME as if it were a path.
                if self.kind == "zip" and self._zf is None:
                    raise KeyError("stain stack is closed")
                if idx not in self._z:
                    tf = tifffile.TiffFile(self._open(self._member[idx]), is_ome=False)
                    s = tf.series[0]
                    z = zarr.open(s.aszarr(), mode="r")
                    is_group = isinstance(z, zarr.hierarchy.Group)
                    self._z[idx] = (tf, z, is_group)
                    if self.nlevels is None:
                        ax = s.axes
                        self.level_shapes = [(int(lv.shape[ax.index("Y")]),
                                              int(lv.shape[ax.index("X")])) for lv in s.levels]
                        self.nlevels = len(s.levels)
                        self.H0, self.W0 = self.level_shapes[0]
        return self._z[idx]

    def _level_arr(self, idx, level):
        _tf, z, is_group = self._channel(idx)
        if is_group:
            keys = sorted(z.array_keys(), key=lambda k: int(k))
            return z[keys[level]]
        return z

    def _prime(self):
        if self._member:
            self._channel(sorted(self._member)[0])

    def _tile(self, idx, level, tx, ty):
        arr = self._level_arr(idx, level)
        H, W = arr.shape[-2], arr.shape[-1]
        y0, x0 = ty * self.tile, tx * self.tile
        if y0 >= H or x0 >= W or y0 < 0 or x0 < 0:
            return None
        y1, x1 = min(y0 + self.tile, H), min(x0 + self.tile, W)
        with self._lock:
            return np.asarray(arr[..., y0:y1, x0:x1]).squeeze()

    # ---- contrast (from a coarse level, cached) ----------------------------
    def _stats_level(self):
        # NOT the coarsest: heavy downsampling averages bright peaks away, giving
        # a too-low max -> saturation. Use a mid-res level (<= ~4096 px).
        for i, (h, w) in enumerate(self.level_shapes):
            if max(h, w) <= 4096:
                return i
        return self.nlevels - 1

    def channel_contrast(self, idx):
        if idx not in self._contrast:
            arr = self._level_arr(idx, self._stats_level())
            with self._lock:
                data = np.asarray(arr[...]).squeeze()
            # Viv/Xenium getChannelStats: [0.05, 99.95] percentile of non-zero px.
            pos = data[data > 0]
            lo = float(np.percentile(pos, 0.05)) if pos.size else 0.0
            hi = float(np.percentile(pos, 99.95)) if pos.size else 65535.0
            self._contrast[idx] = [lo, max(hi, lo + 1), float(max(data.max(), hi))]
        lo, hi, dmax = self._contrast[idx]
        return {"index": idx, "min": lo, "max": hi, "dataMax": dmax}

    def _window(self, idx, level, x0, y0, x1, y1):
        """An arbitrary rectangle of one channel -- what a rotated displayed tile
        needs, since its source rectangle is not tile-aligned."""
        arr = self._level_arr(idx, level)
        H, W = arr.shape[-2], arr.shape[-1]
        x0, y0 = max(0, x0), max(0, y0)
        x1, y1 = min(W, x1), min(H, y1)
        if x1 <= x0 or y1 <= y0:
            return None
        with self._lock:
            return np.asarray(arr[..., y0:y1, x0:x1]).squeeze()

    # ---- composite tile ----------------------------------------------------
    def composite_tile(self, spec, level, tx, ty, orient=None):
        rotated = orient is not None and not ORI.is_identity(orient)
        win = None
        if rotated:
            H, W = self.level_shapes[level]
            win = ORI.source_window(tx, ty, self.tile, orient, W, H)
            if win is None:
                return None
        out = None
        for ch in spec:
            if not ch.get("visible", True):
                continue
            idx = int(ch.get("index", -1))
            if idx not in self._member:
                continue
            t = self._window(idx, level, *win) if rotated else self._tile(idx, level, tx, ty)
            if t is None:
                continue
            if out is None:
                out = np.zeros((t.shape[0], t.shape[1], 3), np.float32)
            lo = float(ch.get("min", 0.0))
            hi = float(ch.get("max", 65535.0))
            if hi <= lo:
                hi = lo + 1
            norm = np.clip((t.astype(np.float32) - lo) / (hi - lo), 0, 1)
            col = ch.get("color", [255, 255, 255])
            gain = float(ch.get("gain", 1.0))
            for k in range(3):
                out[..., k] += norm * col[k] * gain
        if out is None:
            return None
        rgb = np.clip(out, 0, 255).astype(np.uint8)
        alpha = rgb.max(axis=2).astype(np.uint8)
        rgba = np.dstack([rgb, alpha])
        if rotated:
            # Transform the blended tile, then paste it where the transform puts
            # it inside the displayed tile (same placement maths as the morphology
            # pyramid, so the two layers stay registered).
            H, W = self.level_shapes[level]
            x0, y0, x1, y1 = win
            rgba = ORI.transform_image(rgba, orient)
            cx = [ORI.fwd(x, y, orient, W, H) for x in (x0, x1) for y in (y0, y1)]
            dx0 = int(round(min(c[0] for c in cx))) - tx * self.tile
            dy0 = int(round(min(c[1] for c in cx))) - ty * self.tile
            pad = np.zeros((self.tile, self.tile, 4), np.uint8)
            sy0, sx0 = max(0, -dy0), max(0, -dx0)
            dy, dx = max(0, dy0), max(0, dx0)
            h = min(rgba.shape[0] - sy0, self.tile - dy)
            w = min(rgba.shape[1] - sx0, self.tile - dx)
            if h > 0 and w > 0:
                pad[dy:dy + h, dx:dx + w] = rgba[sy0:sy0 + h, sx0:sx0 + w]
            rgba = pad
        elif rgba.shape[:2] != (self.tile, self.tile):
            pad = np.zeros((self.tile, self.tile, 4), np.uint8)
            pad[: rgba.shape[0], : rgba.shape[1]] = rgba
            rgba = pad
        buf = io.BytesIO()
        Image.fromarray(rgba).save(buf, format="PNG")
        return buf.getvalue()

    def info(self):
        chans = [{"index": ch["index"], "name": ch["name"],
                  "color": _color_for(ch["name"], ch["index"])}
                 for ch in self.channels]
        return {
            "channels": chans,
            "tileSize": self.tile,
            "levels": self.nlevels,
            "width": self.W0,
            "height": self.H0,
            "bounds": [0, self.H0, self.W0, 0],  # flipY [left,bottom,right,top], native
        }

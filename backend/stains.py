"""morphology_focus stains -> full-res, zoomable, per-channel additive-blended
tiles (the Xenium Explorer look), placed natively over the morphology extent.

Each channel is a JP2 pyramid file read with is_ome=False (the multi-file OME
assembly loses the pyramid; per-file it keeps all 7 levels and reads a tile in
~0.1-0.5s on disk). A composite tile blends the visible channels
(colour x contrast, additive, alpha=brightness). Same tile scheme as the
morphology (1024 tiles, deck z 0..-maxLevel). Nothing hard-coded: channels,
dims, levels, tileSize all come from the files.

Reading, for speed on a network drive:
  * one lock PER CHANNEL, not one for the whole stack. Every channel is its own
    file handle, so a tile's four channels -- and the six tiles the browser asks
    for at once -- no longer queue behind each other. Measured on a real
    dataset on S:, cold tiles went from 0.38 s to 0.14 s each, byte-identical.
  * a bounded in-memory cache of the channel tiles already read. Moving a
    brightness slider re-asks for every visible tile with new colours; the
    pixels have not changed, so they are re-blended, not re-read over the
    network.
"""
from __future__ import annotations

import io
import re
import threading
import zipfile
from collections import OrderedDict
from pathlib import Path

import numpy as np
import tifffile
import zarr
from PIL import Image

import orientation as ORI

# Upper bound on the decoded channel tiles kept in memory, per open dataset. One
# 1024x1024 uint16 tile is 2 MiB, so this holds ~128 of them -- a few screens of
# a four-channel stack.
TILE_CACHE_BYTES = 256 * 2 ** 20

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
    def __init__(self, mf: dict, workdir=None, tile: int = 1024,
                 cache_bytes: int = TILE_CACHE_BYTES):
        self.kind = mf["kind"]
        self.path = mf["path"]
        self.channels = mf.get("channels") or []
        self._files = mf.get("files")
        self.tile = tile
        # _lock guards the stack's own bookkeeping (opening a channel, closing);
        # each channel's READS go under that channel's lock in _chlocks.
        self._lock = threading.Lock()
        self._chlocks: dict[int, threading.Lock] = {}
        self._closed = False
        self._zf = zipfile.ZipFile(self.path) if self.kind == "zip" else None
        self._member: dict[int, str] = {}
        self._resolve_members()
        self._z: dict[int, tuple] = {}          # idx -> (TiffFile, zarr, group?)
        self._contrast: dict[int, list] = {}    # idx -> [lo, hi, dataMax]
        self._cache: OrderedDict = OrderedDict()  # (idx, level, x0, y0, x1, y1) -> array
        self._cache_bytes = 0
        self._cache_limit = int(cache_bytes)
        self._cache_lock = threading.Lock()
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

        A read in flight on a channel finishes first (its lock is taken before
        that handle is closed); a request that arrives afterwards gets KeyError
        -- the routes answer 404 and the viewer asks again -- rather than
        reopening files behind a closed stack.
        """
        with self._lock:
            self._closed = True
            handles = list(self._z.items())
            self._z = {}
            zf, self._zf = getattr(self, "_zf", None), None
        for idx, (tf, _z, _g) in handles:
            with self._channel_lock(idx):
                try:
                    tf.close()
                except Exception:
                    pass
        if zf is not None:
            try:
                zf.close()
            except Exception:
                pass
        with self._cache_lock:
            self._cache.clear()
            self._cache_bytes = 0

    # ---- per-channel pyramid (opened is_ome=False, handle cached) ----------
    def _channel_lock(self, idx) -> threading.Lock:
        lk = self._chlocks.get(idx)
        if lk is None:
            with self._lock:
                lk = self._chlocks.setdefault(idx, threading.Lock())
        return lk

    def _channel(self, idx):
        entry = self._z.get(idx)
        if entry is None:
            with self._lock:
                # A request that raced a close() must fail loudly (the route
                # turns KeyError into 404), not reopen members through the
                # dropped zip handle -- self._zf is None then and _open would
                # hand tifffile a zip member NAME as if it were a path -- nor
                # reopen a folder's files and lock them again after close.
                if self._closed:
                    raise KeyError("stain stack is closed")
                entry = self._z.get(idx)
                if entry is None:
                    tf = tifffile.TiffFile(self._open(self._member[idx]), is_ome=False)
                    s = tf.series[0]
                    z = zarr.open(s.aszarr(), mode="r")
                    is_group = isinstance(z, zarr.hierarchy.Group)
                    entry = (tf, z, is_group)
                    self._z[idx] = entry
                    if self.nlevels is None:
                        ax = s.axes
                        self.level_shapes = [(int(lv.shape[ax.index("Y")]),
                                              int(lv.shape[ax.index("X")])) for lv in s.levels]
                        self.nlevels = len(s.levels)
                        self.H0, self.W0 = self.level_shapes[0]
        return entry

    @staticmethod
    def _level_of(entry, level):
        _tf, z, is_group = entry
        if is_group:
            keys = sorted(z.array_keys(), key=lambda k: int(k))
            return z[keys[level]]
        return z

    def _level_arr(self, idx, level):
        return self._level_of(self._channel(idx), level)

    def _prime(self):
        if self._member:
            self._channel(sorted(self._member)[0])

    # ---- reading pixels ------------------------------------------------------
    def _read(self, idx, level, x0, y0, x1, y1):
        """One channel's pixels in [x0, x1) x [y0, y1) of a level, clipped to the
        level; None when nothing of it is inside. Served from the tile cache when
        this exact window was read before. The returned array is read-only."""
        key = (idx, level, x0, y0, x1, y1)
        with self._cache_lock:
            hit = self._cache.get(key)
            if hit is not None:
                self._cache.move_to_end(key)
                return hit
        entry = self._channel(idx)
        with self._channel_lock(idx):
            # the handle may have been closed while this request waited its turn
            if self._closed or self._z.get(idx) is not entry:
                raise KeyError("stain stack is closed")
            arr = self._level_of(entry, level)
            H, W = arr.shape[-2], arr.shape[-1]
            cx0, cy0 = max(0, x0), max(0, y0)
            cx1, cy1 = min(W, x1), min(H, y1)
            if cx1 <= cx0 or cy1 <= cy0:
                return None
            data = np.asarray(arr[..., cy0:cy1, cx0:cx1])
        # drop only the leading singleton axes: squeeze() also flattened a
        # 1-pixel-wide edge tile to 1-D, which then failed to pad (a 500)
        data = data.reshape(data.shape[-2:])
        data.setflags(write=False)
        self._cache_put(key, data)
        return data

    def _cache_put(self, key, data):
        size = int(data.nbytes)
        if size > self._cache_limit:
            return
        with self._cache_lock:
            if self._closed:
                return
            old = self._cache.pop(key, None)
            if old is not None:
                self._cache_bytes -= int(old.nbytes)
            self._cache[key] = data
            self._cache_bytes += size
            while self._cache_bytes > self._cache_limit and self._cache:
                _k, gone = self._cache.popitem(last=False)
                self._cache_bytes -= int(gone.nbytes)

    def cache_info(self):
        with self._cache_lock:
            return {"entries": len(self._cache), "bytes": self._cache_bytes,
                    "limit": self._cache_limit}

    def _tile(self, idx, level, tx, ty):
        y0, x0 = ty * self.tile, tx * self.tile
        if y0 < 0 or x0 < 0:
            return None
        return self._read(idx, level, x0, y0, x0 + self.tile, y0 + self.tile)

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
            entry = self._channel(idx)
            with self._channel_lock(idx):
                if self._closed or self._z.get(idx) is not entry:
                    raise KeyError("stain stack is closed")
                arr = self._level_of(entry, self._stats_level())
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
        return self._read(idx, level, x0, y0, x1, y1)

    # ---- one channel, untouched --------------------------------------------
    def has_channel(self, idx) -> bool:
        return idx in self._member

    def channel_tile(self, idx, level, tx, ty, orient=None):
        """One channel's tile as raw uint16, tile x tile, in DISPLAY orientation.

        What the browser composites on the GPU: colour, contrast and the additive
        blend happen in a shader, so moving a slider costs a uniform update
        instead of a request per visible tile. Reads go through _read, so they
        share the decoded-tile cache; placement maths as composite_tile, so the
        two stay registered.
        """
        if idx not in self._member or self.nlevels is None:
            return None
        if level < 0 or level >= self.nlevels:
            return None
        T = self.tile
        if orient is None or ORI.is_identity(orient):
            t = self._tile(idx, level, tx, ty)
            if t is None:
                return None
            pad = np.zeros((T, T), np.uint16)
            pad[: t.shape[0], : t.shape[1]] = t
            return pad
        H, W = self.level_shapes[level]
        win = ORI.source_window(tx, ty, T, orient, W, H)
        if win is None:
            return None
        t = self._window(idx, level, *win)
        if t is None:
            return None
        x0, y0, x1, y1 = win
        t = ORI.transform_image(t, orient)
        cx = [ORI.fwd(x, y, orient, W, H) for x in (x0, x1) for y in (y0, y1)]
        dx0 = int(round(min(c[0] for c in cx))) - tx * T
        dy0 = int(round(min(c[1] for c in cx))) - ty * T
        pad = np.zeros((T, T), np.uint16)
        sy0, sx0 = max(0, -dy0), max(0, -dx0)
        dy, dx = max(0, dy0), max(0, dx0)
        h = min(t.shape[0] - sy0, T - dy)
        w = min(t.shape[1] - sx0, T - dx)
        if h > 0 and w > 0:
            pad[dy:dy + h, dx:dx + w] = t[sy0:sy0 + h, sx0:sx0 + w]
        return pad

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

"""Serve tiles from a pyramidal, JPEG2000-compressed OME-TIFF.

geotiff.js (what Viv uses in the browser) can't decode JPEG2000, so we decode
server-side with tifffile + imagecodecs and hand plain 8-bit PNG tiles to a
deck.gl TileLayer.

Tiling scheme (matches deck.gl TileLayer XYZ):
  * World coordinates = full-resolution image pixels, extent [0,0,W0,H0].
  * tileSize = the TIFF's native tile (1024).
  * deck zoom z in [0 .. nlevels-1]; pyramid level = (nlevels-1) - z.
    -> z = nlevels-1 is most-zoomed-in  = pyramid level 0 (full res)
    -> z = 0            is most-zoomed-out = coarsest level
  A tile (z, x, y) reads level L=(nlevels-1)-z, window
  [y*T:(y+1)*T, x*T:(x+1)*T] in that level's own pixel grid. Because a level-L
  pixel spans 2**L full-res pixels, the bitmap lands on the right world extent
  once deck stretches it over the tile's bbox.
"""
from __future__ import annotations

import io
import threading
from pathlib import Path
from typing import Optional

import numpy as np
import tifffile
import zarr
from PIL import Image

from config import TILE_SIZE


class Pyramid:
    def __init__(self, path: Path, tile: int = TILE_SIZE):
        self.path = str(path)
        self.tile = tile
        self._lock = threading.Lock()
        self._tif = tifffile.TiffFile(self.path)
        self.series = self._tif.series[0]
        self.axes = self.series.axes                 # e.g. "ZYX"
        self.levels = list(self.series.levels)       # [level0 .. levelN]
        self.nlevels = len(self.levels)
        self.level_shapes = [tuple(int(v) for v in lv.shape) for lv in self.levels]
        self.dtype = str(self.series.dtype)

        ax = self.axes
        self.zc = ax.index("Z") if "Z" in ax else None
        self.yc = ax.index("Y")
        self.xc = ax.index("X")
        s0 = self.level_shapes[0]
        self.nZ = s0[self.zc] if self.zc is not None else 1
        self.H0 = s0[self.yc]
        self.W0 = s0[self.xc]

        self._root = None            # whole multiscale series as one zarr obj
        self._level_keys: list = []  # group member keys, full-res -> coarsest
        self.lo = 0.0
        self.hi = 65535.0
        self._compute_contrast()

    # -- lazy zarr view of the whole pyramid (imagecodecs decodes touched tiles) --
    def _open_root(self):
        if self._root is None:
            with self._lock:
                if self._root is None:
                    self._root = zarr.open(self.series.aszarr(), mode="r")
        return self._root

    def _arr(self, level: int):
        """Zarr array for a pyramid level. The series opens as a group whose
        members are the levels (full-res first); index it by sorted key."""
        root = self._open_root()
        if isinstance(root, zarr.hierarchy.Group):
            if not self._level_keys:
                keys = list(root.array_keys())
                try:
                    keys = sorted(keys, key=lambda k: int(k))
                except ValueError:
                    keys = sorted(keys)
                self._level_keys = keys
            return root[self._level_keys[level]]
        return root  # single-level image: only level 0

    def _read_window(self, level, plane, y0, y1, x0, x1) -> np.ndarray:
        a = self._arr(level)
        with self._lock:
            if self.zc is None:
                return np.asarray(a[y0:y1, x0:x1])
            return np.asarray(a[plane, y0:y1, x0:x1])

    def _plane(self, plane: Optional[int]) -> int:
        if self.zc is None:
            return 0
        if plane is None:
            return self.nZ // 2
        return max(0, min(int(plane), self.nZ - 1))

    def _compute_contrast(self):
        """Global robust window from the coarsest level (cheap, consistent)."""
        lvl = self.nlevels - 1
        plane = self.nZ // 2 if self.zc is not None else 0
        a = self._arr(lvl)
        with self._lock:
            data = np.asarray(a[plane] if self.zc is not None else a[:])
        nz = data[data > 0]
        if nz.size:
            self.lo = float(np.percentile(nz, 1.0))
            self.hi = float(np.percentile(nz, 99.8))
        if self.hi <= self.lo:
            self.hi = self.lo + 1.0

    def _to_uint8(self, win: np.ndarray) -> np.ndarray:
        f = (win.astype(np.float32) - self.lo) / (self.hi - self.lo)
        return (np.clip(f, 0.0, 1.0) * 255.0).astype(np.uint8)

    def tile_png(self, level: int, tx: int, ty: int,
                 plane: Optional[int] = None) -> Optional[bytes]:
        if level < 0 or level >= self.nlevels:
            return None
        shp = self.level_shapes[level]
        H, W = shp[self.yc], shp[self.xc]
        y0, x0 = ty * self.tile, tx * self.tile
        if y0 >= H or x0 >= W or y0 < 0 or x0 < 0:
            return None
        y1, x1 = min(y0 + self.tile, H), min(x0 + self.tile, W)
        win = self._read_window(level, self._plane(plane), y0, y1, x0, x1)
        u8 = self._to_uint8(win)
        # Pad partial edge tiles to a uniform tile grid (black = outside image).
        if u8.shape != (self.tile, self.tile):
            out = np.zeros((self.tile, self.tile), np.uint8)
            out[: u8.shape[0], : u8.shape[1]] = u8
            u8 = out
        buf = io.BytesIO()
        Image.fromarray(u8, mode="L").save(buf, format="PNG")
        return buf.getvalue()

    def overview_png(self, max_side: int = 1024) -> bytes:
        """Whole image at (near) coarsest level -- for quick sanity checks."""
        lvl = self.nlevels - 1
        plane = self._plane(None)
        a = self._arr(lvl)
        with self._lock:
            data = np.asarray(a[plane] if self.zc is not None else a[:])
        u8 = self._to_uint8(data)
        img = Image.fromarray(u8, mode="L")
        img.thumbnail((max_side, max_side))
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        return buf.getvalue()

    def info(self) -> dict:
        return {
            "width": self.W0,
            "height": self.H0,
            "tileSize": self.tile,
            "levels": self.nlevels,
            "nZ": self.nZ,
            "axes": self.axes,
            "dtype": self.dtype,
            "levelShapes": [list(s) for s in self.level_shapes],
            "contrast": {"lo": self.lo, "hi": self.hi},
        }

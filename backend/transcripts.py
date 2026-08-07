"""Genes (and later transcript points) from transcripts.zarr, in MICRON space.

Gene expression comes from /density/gene -- a pre-computed per-gene density on a
10um grid (CSR, gene-major: gene g's rows x cols grid = CSR rows [g*rows:(g+1)*rows]).
Placed in full-res pixel space via `micron / pixel_size` -- a universal transform
(origin 0,0), so it aligns natively with the morphology on ANY Xenium dataset.
No baked rasters, no _grid_meta, no affine fit.
"""
from __future__ import annotations

import ast
import io
import threading
from pathlib import Path

import numpy as np
import zarr
from PIL import Image

# distinct default hues for gene channels (user recolours in the panel)
_PALETTE = [
    [255, 64, 64], [80, 220, 80], [90, 130, 255], [255, 215, 60],
    [255, 110, 245], [70, 235, 225], [255, 150, 60], [180, 120, 255],
]
_CONTROL_PREFIXES = ("NegControlProbe", "NegControlCodeword", "BLANK", "antisense",
                     "UnassignedCodeword", "DeprecatedCodeword", "Intergenic")


def _attr_list(v):
    return v if isinstance(v, list) else ast.literal_eval(v)


def _open_zarr(path):
    p = Path(path)
    if p.is_dir():
        return zarr.open(str(p), mode="r")
    return zarr.open(zarr.ZipStore(str(p), mode="r"), mode="r")


class GeneDensity:
    def __init__(self, transcripts_path, pixel_size):
        self.path = str(transcripts_path)
        self.pixel_size = float(pixel_size or 0.2125)
        self._lock = threading.Lock()
        self._root = _open_zarr(self.path)
        dg = self._root["density/gene"]
        self.gene_names = _attr_list(dg.attrs["gene_names"])
        self.rows = int(dg.attrs["rows"])
        self.cols = int(dg.attrs["cols"])
        gsz = _attr_list(dg.attrs["grid_size"])
        self.grid_x, self.grid_y = float(gsz[0]), float(gsz[1])
        # CSR (read once; ~130 MB): gene-major row blocks of length `rows`.
        self._indptr = np.asarray(dg["indptr"][:])
        self._indices = np.asarray(dg["indices"][:])
        self._data = np.asarray(dg["data"][:])
        self._cache: dict[str, tuple] = {}
        # The CSR arrays above are fully materialised, so the store is never read
        # again -- but zarr's ZipStore has no __del__, so leaving it open held
        # transcripts.zarr.zip locked for the life of the process. See close().
        # full-res world extent: micron / pixel_size
        self.W = self.cols * self.grid_x / self.pixel_size
        self.H = self.rows * self.grid_y / self.pixel_size

    def gene_list(self):
        return [g for g in self.gene_names if not g.startswith(_CONTROL_PREFIXES)]

    def bounds(self):
        return [0, self.H, self.W, 0]  # flipY [left, bottom, right, top]

    def close(self):
        """Release the zarr store. The CSR arrays stay usable -- they were read
        in full at construction -- so this only drops the file handle."""
        with self._lock:
            root, self._root = getattr(self, "_root", None), None
            store = getattr(root, "store", None) if root is not None else None
            if store is not None:
                try:
                    store.close()
                except Exception:
                    pass

    def _grid(self, gene):
        if gene not in self._cache:
            if gene not in self.gene_names:
                return None
            g = self.gene_names.index(gene)
            grid = np.zeros((self.rows, self.cols), np.float32)
            ip, idx, dat = self._indptr, self._indices, self._data
            base = g * self.rows
            for lr in range(self.rows):
                r = base + lr
                s, e = int(ip[r]), int(ip[r + 1])
                if e > s:
                    grid[lr, idx[s:e]] = dat[s:e]
            pos = grid[grid > 0]
            lo = float(np.percentile(pos, 1.0)) if pos.size else 0.0
            hi = float(np.percentile(pos, 99.5)) if pos.size else 1.0
            self._cache[gene] = (grid, [lo, max(hi, lo + 1), float(max(grid.max(), 1))])
        return self._cache[gene]

    def contrast(self, gene):
        gc = self._grid(gene)
        if not gc:
            return None
        _, c = gc
        return {"gene": gene, "min": c[0], "max": c[1], "dataMax": c[2]}

    def default_channels(self, names, colors=True):
        out = []
        for i, nm in enumerate(names):
            gc = self._grid(nm)
            c = gc[1] if gc else [0, 1, 1]
            out.append({"gene": nm, "color": _PALETTE[i % len(_PALETTE)],
                        "min": c[0], "max": c[1], "dataMax": c[2]})
        return out

    def composite(self, spec):
        out = np.zeros((self.rows, self.cols, 3), np.float32)
        for ch in spec:
            if not ch.get("visible", True):
                continue
            gc = self._grid(ch.get("gene"))
            if not gc:
                continue
            grid, c = gc
            lo = float(ch.get("min", c[0]))
            hi = float(ch.get("max", c[1]))
            if hi <= lo:
                hi = lo + 1
            norm = np.clip((grid - lo) / (hi - lo), 0, 1)
            col = ch.get("color", [255, 255, 255])
            for k in range(3):
                out[..., k] += norm * col[k]
        rgb = np.clip(out, 0, 255).astype(np.uint8)
        alpha = rgb.max(axis=2).astype(np.uint8)
        return np.dstack([rgb, alpha])

    def composite_png(self, spec):
        with self._lock:
            rgba = self.composite(spec)
        buf = io.BytesIO()
        Image.fromarray(rgba).save(buf, format="PNG")
        return buf.getvalue()

    def info(self, default_genes):
        return {
            "genes": self.gene_list(),
            "defaults": self.default_channels(default_genes),
            "bounds": self.bounds(),
            "grid": [self.cols, self.rows],
            "pixelSizeUm": self.pixel_size,
        }

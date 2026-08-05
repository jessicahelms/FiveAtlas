"""Gene-channel compositing for the 640-canvas transcript rasters.

The per-gene TIFs (Calb2.tif, Slc17a7.tif, ...) live in the 640x640 "canvas640"
space. We composite selected genes (each = colour x contrast, blended
additively) into one RGB image, and place it in full-resolution world space via
an affine fit from the region GeoJSON that exists in BOTH spaces
(merged.regions_canvas640.geojson  ->  merged.regions.geojson).
"""
from __future__ import annotations

import io
import json
import threading
from pathlib import Path

import numpy as np
import tifffile
from PIL import Image

# Distinct default hues for up to ~12 genes (user can recolour in the panel).
_PALETTE = [
    [255, 64, 64], [80, 220, 80], [90, 130, 255], [255, 215, 60],
    [255, 110, 245], [70, 235, 225], [255, 150, 60], [180, 120, 255],
    [130, 255, 160], [230, 230, 230], [255, 80, 140], [150, 200, 90],
]


def _centroid(feat):
    xs, ys = [], []

    def walk(c):
        if isinstance(c, list):
            if c and isinstance(c[0], (int, float)):
                xs.append(c[0]); ys.append(c[1])
            else:
                for x in c:
                    walk(x)

    walk(feat["geometry"]["coordinates"])
    if not xs:
        return None
    return (sum(xs) / len(xs), sum(ys) / len(ys))


def _fit_affine_xy(canvas_fc, full_fc):
    """Fit fx = Sx*cx + Tx and fy = Sy*cy + Ty from matched region centroids.
    Independent x/y so a baked flip (negative slope) is captured if present."""
    cby = {(f.get("properties") or {}).get("name"): _centroid(f) for f in canvas_fc["features"]}
    fby = {(f.get("properties") or {}).get("name"): _centroid(f) for f in full_fc["features"]}
    names = [n for n in cby if n in fby and cby[n] and fby[n]]
    C = np.array([cby[n] for n in names], dtype=float)
    F = np.array([fby[n] for n in names], dtype=float)
    Sx, Tx = np.polyfit(C[:, 0], F[:, 0], 1)
    Sy, Ty = np.polyfit(C[:, 1], F[:, 1], 1)
    return float(Sx), float(Sy), float(Tx), float(Ty), names


class GeneSet:
    def __init__(self, d: dict):
        self.root: Path = Path(d["root"])
        self._lock = threading.Lock()
        self.arrays: dict[str, np.ndarray] = {}
        self.defaults: dict[str, dict] = {}
        self.genes: list[str] = []
        self.H = self.W = 640
        self._load()
        self.Sx = self.Sy = 1.0
        self.Tx = self.Ty = 0.0
        self.fit_names: list[str] = []
        self._fit()

    # ---- gene rasters -------------------------------------------------------
    def _gene_names(self) -> list[str]:
        gm = self.root / "_grid_meta.json"
        if gm.exists():
            meta = json.load(open(gm, encoding="utf-8"))
            req = meta.get("genes_requested") or []
            if req:
                return list(req)
        # fall back: any single-channel .tif that isn't morphology/transcripts
        out = []
        for p in sorted(self.root.glob("*.tif")):
            n = p.stem
            if n.startswith(("morphology", "transcripts")):
                continue
            out.append(n)
        return out

    def _load(self):
        for g in self._gene_names():
            p = self.root / f"{g}.tif"
            if not p.exists():
                continue
            a = np.asarray(tifffile.imread(str(p)))
            if a.ndim != 2:
                a = a.reshape(a.shape[-2], a.shape[-1])
            self.arrays[g] = a
            self.genes.append(g)
            nz = a[a > 0]
            lo = float(np.percentile(nz, 1.0)) if nz.size else 0.0
            hi = float(np.percentile(nz, 99.5)) if nz.size else 65535.0
            if hi <= lo:
                hi = lo + 1.0
            self.defaults[g] = {"min": lo, "max": hi, "dmax": float(a.max())}
        if self.arrays:
            self.H, self.W = next(iter(self.arrays.values())).shape[:2]

    # ---- placement in full-res world ---------------------------------------
    def _fit(self):
        cpath = self.root / "merged.regions_canvas640.geojson"
        fpath = self.root / "merged.regions.geojson"
        if cpath.exists() and fpath.exists():
            cfc = json.load(open(cpath, encoding="utf-8"))
            ffc = json.load(open(fpath, encoding="utf-8"))
            self.Sx, self.Sy, self.Tx, self.Ty, self.fit_names = _fit_affine_xy(cfc, ffc)

    def bounds_world(self):
        """[left, bottom, right, top] for a flipY (y-down) deck view."""
        left = self.Tx
        right = self.Tx + self.W * self.Sx
        top = self.Ty
        bottom = self.Ty + self.H * self.Sy
        return [left, bottom, right, top]

    # ---- API payloads -------------------------------------------------------
    def info(self) -> dict:
        genes = []
        for i, g in enumerate(self.genes):
            d = self.defaults[g]
            genes.append({
                "name": g,
                "min": d["min"], "max": d["max"], "dataMax": d["dmax"],
                "color": _PALETTE[i % len(_PALETTE)],
            })
        return {
            "genes": genes,
            "canvas": [self.W, self.H],
            "bounds": self.bounds_world(),
            "affine": {"Sx": self.Sx, "Sy": self.Sy, "Tx": self.Tx, "Ty": self.Ty},
            "fitRegions": len(self.fit_names),
        }

    def composite(self, spec: list) -> np.ndarray:
        out = np.zeros((self.H, self.W, 3), np.float32)
        for ch in spec:
            if not ch.get("visible", True):
                continue
            a = self.arrays.get(ch.get("gene"))
            if a is None:
                continue
            lo = float(ch.get("min", 0.0))
            hi = float(ch.get("max", 65535.0))
            if hi <= lo:
                hi = lo + 1.0
            norm = np.clip((a.astype(np.float32) - lo) / (hi - lo), 0.0, 1.0)
            gain = float(ch.get("gain", 1.0))
            col = ch.get("color", [255, 255, 255])
            for k in range(3):
                out[..., k] += norm * col[k] * gain
        rgb = np.clip(out, 0, 255).astype(np.uint8)
        # Alpha = brightness, so no-expression areas are transparent and the
        # composite overlays the DAPI instead of blacking it out.
        alpha = rgb.max(axis=2).astype(np.uint8)
        return np.dstack([rgb, alpha])

    def composite_png(self, spec: list) -> bytes:
        with self._lock:
            rgba = self.composite(spec)
        buf = io.BytesIO()
        Image.fromarray(rgba, mode="RGBA").save(buf, format="PNG")
        return buf.getvalue()

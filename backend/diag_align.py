"""Diagnose gene<->morphology<->geojson alignment by rendering the overlay."""
import json
import sys
from pathlib import Path

import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

import datasets as ds
import scan
from genes import GeneSet
from tiles import Pyramid

OUT = Path(r"C:\Users\FIVE\AppData\Local\Temp\claude"
           r"\C--Users-FIVE-source-repos-Jess"
           r"\f2be11fb-56f3-4d18-a20c-251cb0d655b6\scratchpad")
DS = "DNMT3A_002_27_38_Het_F"
d = ds.get_dataset(DS)
gs = GeneSet(d)


def spec(g, c):
    dd = gs.defaults[g]
    return {"gene": g, "color": c, "min": dd["min"], "max": dd["max"], "visible": True}


rgba = gs.composite([spec("Slc17a7", [255, 40, 40]),
                     spec("Calb2", [40, 255, 40]),
                     spec("Tbr1", [80, 120, 255])])
H, W = rgba.shape[:2]


def rings(fc):
    out = []
    for f in fc.get("features", []):
        g = f.get("geometry") or {}
        t = g.get("type")
        coords = g.get("coordinates") or []
        polys = [coords] if t == "Polygon" else (coords if t == "MultiPolygon" else [])
        for poly in polys:
            for ring in poly:
                a = np.array(ring, dtype=float)
                if a.ndim == 2 and a.shape[0] >= 2:
                    out.append(a)
    return out


# ---- Check A: gene raster (640) vs canvas640 geojson, NO transform ----
cfc = json.load(open(Path(d["root"]) / "merged.regions_canvas640.geojson", encoding="utf-8"))
fig, ax = plt.subplots(figsize=(8, 8))
ax.set_facecolor("black")
ax.imshow(rgba, extent=[0, W, H, 0], origin="upper")
for r in rings(cfc):
    ax.plot(r[:, 0], r[:, 1], color="yellow", lw=0.8)
ax.set_title("A: gene raster (640) + canvas640 geojson (no transform)")
ax.set_xlim(0, W); ax.set_ylim(H, 0)
fig.savefig(OUT / "align_A_canvas.png", dpi=100, facecolor="black")
plt.close(fig)

# also try the OTHER 640 geojsons in case the raster matches a different frame
for name in ["merged.regions_sectionfit.geojson"]:
    p2 = Path(d["root"]) / name
    if p2.exists():
        gfc = json.load(open(p2, encoding="utf-8"))
        fig, ax = plt.subplots(figsize=(8, 8))
        ax.set_facecolor("black")
        ax.imshow(rgba, extent=[0, W, H, 0], origin="upper")
        for r in rings(gfc):
            ax.plot(r[:, 0], r[:, 1], color="cyan", lw=0.8)
        ax.set_title(f"A2: gene raster (640) + {name}")
        ax.set_xlim(0, W); ax.set_ylim(H, 0)
        fig.savefig(OUT / "align_A2_sectionfit.png", dpi=100, facecolor="black")
        plt.close(fig)

# ---- Check B: DAPI + gene(placed by affine) + fullres geojson ----
Sx, Sy, Tx, Ty = gs.Sx, gs.Sy, gs.Tx, gs.Ty
extent = [Tx, Tx + W * Sx, Ty + H * Sy, Ty]
p = Pyramid(ds.image_path(d))
ov = np.asarray(p._arr(p.nlevels - 1)[p.nZ // 2])
ov8 = np.clip((ov - p.lo) / (p.hi - p.lo), 0, 1)
ffc = json.load(open(scan.primary_regions_path(d), encoding="utf-8"))
fig, ax = plt.subplots(figsize=(9, 9))
ax.imshow(ov8, cmap="gray", extent=[0, p.W0, p.H0, 0], origin="upper", zorder=0)
ax.imshow(rgba, extent=extent, origin="upper", zorder=1)
for r in rings(ffc):
    ax.plot(r[:, 0], r[:, 1], color="yellow", lw=0.6, zorder=2)
ax.set_title("B: DAPI(gray) + gene(affine) + fullres geojson(yellow)")
ax.set_xlim(0, p.W0); ax.set_ylim(p.H0, 0)
fig.savefig(OUT / "align_B_fullres.png", dpi=100)
plt.close(fig)

print("Sx,Sy,Tx,Ty =", Sx, Sy, Tx, Ty)
print("gene raster WxH =", W, H)
print("gene extent (fullres) L,R,B,T =", [round(v, 1) for v in extent])
print("DAPI WxH =", p.W0, p.H0)
# bbox of the two 640 geojsons for reference
for nm in ["merged.regions_canvas640.geojson", "merged.regions_sectionfit.geojson"]:
    g = json.load(open(Path(d["root"]) / nm, encoding="utf-8"))
    rs = rings(g)
    allpts = np.concatenate(rs, axis=0)
    print(nm, "x[%.1f,%.1f] y[%.1f,%.1f]" % (allpts[:, 0].min(), allpts[:, 0].max(),
                                             allpts[:, 1].min(), allpts[:, 1].max()))
print("saved align_A_canvas.png, align_A2_sectionfit.png, align_B_fullres.png")

import json
from pathlib import Path
import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

import datasets as ds
import scan
from transcripts import GeneDensity
from tiles import Pyramid

OUT = Path(r"C:\Users\FIVE\AppData\Local\Temp\claude"
           r"\C--Users-FIVE-source-repos-Jess"
           r"\f2be11fb-56f3-4d18-a20c-251cb0d655b6\scratchpad")
DS = "DNMT3A_002_27_38_Het_F"
d = ds.get_dataset(DS)
tz = d["sources"]["transcripts"]["path"]
gd = GeneDensity(tz, d.get("pixel_size_um"))
print("genes:", len(gd.gene_list()), "grid", gd.cols, "x", gd.rows,
      "W,H", round(gd.W), round(gd.H), "bounds", [round(v) for v in gd.bounds()])


def spec(g, c):
    cc = gd.contrast(g)
    return {"gene": g, "color": c, "min": cc["min"], "max": cc["max"], "visible": True}


rgba = gd.composite([spec("Slc17a7", [255, 60, 60]),
                     spec("Calb2", [60, 255, 60]),
                     spec("Pvalb", [90, 130, 255])])

p = Pyramid(ds.image_path(d))
ov = np.asarray(p._arr(p.nlevels - 1)[p.nZ // 2])
ov8 = np.clip((ov - p.lo) / (p.hi - p.lo), 0, 1)

ffc = json.load(open(scan.primary_regions_path(d), encoding="utf-8"))


def rings(fc):
    out = []
    for f in fc.get("features", []):
        g = f.get("geometry") or {}
        t = g.get("type"); coords = g.get("coordinates") or []
        polys = [coords] if t == "Polygon" else (coords if t == "MultiPolygon" else [])
        for poly in polys:
            for ring in poly:
                a = np.array(ring, float)
                if a.ndim == 2 and len(a) >= 2:
                    out.append(a)
    return out


fig, ax = plt.subplots(figsize=(9, 9))
ax.imshow(ov8, cmap="gray", extent=[0, p.W0, p.H0, 0], origin="upper", zorder=0)
ax.imshow(rgba, extent=[0, gd.W, gd.H, 0], origin="upper", zorder=1)
for r in rings(ffc):
    ax.plot(r[:, 0], r[:, 1], color="yellow", lw=0.5, zorder=2)
ax.set_xlim(0, p.W0); ax.set_ylim(p.H0, 0)
ax.set_title("zarr-density genes (R=Slc17a7 G=Calb2 B=Pvalb) + morphology + regions")
fig.savefig(OUT / "verify_genes.png", dpi=100)
print("saved verify_genes.png  (morph WxH =", p.W0, p.H0, ")")

import json
from pathlib import Path
import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from shapely.geometry import shape

import datasets as ds
import scan
import topology

OUT = Path(r"C:\Users\FIVE\AppData\Local\Temp\claude"
           r"\C--Users-FIVE-source-repos-Jess"
           r"\f2be11fb-56f3-4d18-a20c-251cb0d655b6\scratchpad")
d = ds.get_dataset("DNMT3A_002_27_38_Het_F")
fc = json.load(open(scan.primary_regions_path(d), encoding="utf-8"))
feats = fc["features"]

# longest dft<->ISO arc
arcs = [a for a in topology.shared_borders(feats, "name", 4.0)
        if set(a["regions"]) == {"dft", "ISO"}]
arc = max(arcs, key=lambda a: a["length"])
pts = np.array(arc["points"], float)
print(f"dft<->ISO arc: {len(pts)} pts, len {arc['length']:.0f}")

# simulate a drag: push interior vertices +350px in x (into ISO)
new = pts.copy()
new[1:-1, 0] += 350.0

def area(feats, name):
    for f in feats:
        if str(f["properties"].get("name")) == name:
            return shape(f["geometry"]).area
    return 0

a0, i0 = area(feats, "dft"), area(feats, "ISO")
out = topology.move_border(feats, "name", "dft", "ISO", new.tolist())
a1, i1 = area(out, "dft"), area(out, "ISO")
print(f"dft area: {a0:.0f} -> {a1:.0f}  ({a1-a0:+.0f})")
print(f"ISO area: {i0:.0f} -> {i1:.0f}  ({i1-i0:+.0f})")
print(f"combined preserved: {abs((a0+i0)-(a1+i1)):.1f} (should be ~0)")

fig, axes = plt.subplots(1, 2, figsize=(16, 8))
for ax, ff, title in [(axes[0], feats, "before"), (axes[1], out, "after (border dragged +350)")]:
    for f in ff:
        nm = f["properties"].get("name")
        if nm not in ("dft", "ISO"):
            continue
        g = shape(f["geometry"])
        for p in (g.geoms if g.geom_type == "MultiPolygon" else [g]):
            xs, ys = p.exterior.xy
            ax.fill(xs, ys, alpha=0.5, label=nm, color=("#4C78A8" if nm == "dft" else "#F58518"))
    ax.plot(new[:, 0], new[:, 1], "r-", lw=1.5)
    ax.set_aspect("equal"); ax.invert_yaxis(); ax.set_title(title)
fig.savefig(OUT / "verify_move.png", dpi=90)
print("saved verify_move.png")

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
DS = "DNMT3A_002_27_38_Het_F"
d = ds.get_dataset(DS)
fc = json.load(open(scan.primary_regions_path(d), encoding="utf-8"))
feats = fc["features"]
print("regions:", len(feats))

for grid in [2, 4, 10, 25]:
    s = topology.summary(feats, "name", grid)
    print(f"grid={grid:>3}: {s['n_shared_borders']} shared borders, "
          f"total len {s['total_shared_length']:.0f}")

GRID = 10
borders = topology.shared_borders(feats, "name", GRID)
print(f"\n=== shared borders @ grid={GRID} ({len(borders)}) ===")
for b in sorted(borders, key=lambda x: -x["length"])[:20]:
    print(f"  {b['regions'][0]:>8} <-> {b['regions'][1]:<8}  len={b['length']:.0f}")

# plot: region fills + shared borders
fig, ax = plt.subplots(figsize=(10, 10))
ax.set_facecolor("black")
for f in feats:
    g = shape(f["geometry"])
    polys = g.geoms if g.geom_type == "MultiPolygon" else [g]
    for p in polys:
        if p.is_empty:
            continue
        xs, ys = p.exterior.xy
        ax.fill(xs, ys, alpha=0.15, lw=0.4, edgecolor="#888")
for b in borders:
    a = np.array(b["points"])
    ax.plot(a[:, 0], a[:, 1], color="#ff3b3b", lw=1.6)
ax.set_aspect("equal"); ax.invert_yaxis()
ax.set_title(f"shared borders (red) @ grid={GRID}: {len(borders)} arcs")
fig.savefig(OUT / "verify_topology.png", dpi=100, facecolor="black")
print("saved verify_topology.png")

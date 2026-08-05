"""Verify gene loading, the canvas640->fullres affine, and RGB compositing."""
import json
from pathlib import Path

import datasets as ds
from genes import GeneSet

OUT = Path(r"C:\Users\FIVE\AppData\Local\Temp\claude"
           r"\C--Users-FIVE-source-repos-Jess"
           r"\f2be11fb-56f3-4d18-a20c-251cb0d655b6\scratchpad")

DS = "DNMT3A_002_27_38_Het_F"
gs = GeneSet(ds.get_dataset(DS))
info = gs.info()
print("genes:", [g["name"] for g in info["genes"]])
print("canvas:", info["canvas"], "fitRegions:", info["fitRegions"])
print("affine:", info["affine"])
print("bounds [l,b,r,t]:", [round(v, 1) for v in info["bounds"]])
print("(full-res image is 28486 x 30687; regions bbox x[4008,27746] y[1457,29662])")
for g in info["genes"][:3]:
    print("  ", g["name"], "min=%.0f max=%.0f dataMax=%.0f color=%s"
          % (g["min"], g["max"], g["dataMax"], g["color"]))

# The classic falsecolor triplet: Slc17a7=R, Calb2=G, Tbr1=B
def spec(gene, color):
    d = gs.defaults[gene]
    return {"gene": gene, "color": color, "min": d["min"], "max": d["max"], "visible": True}

triplet = [
    spec("Slc17a7", [255, 40, 40]),
    spec("Calb2", [40, 255, 40]),
    spec("Tbr1", [80, 120, 255]),
]
(OUT / "genes_triplet.png").write_bytes(gs.composite_png(triplet))
print("wrote genes_triplet.png (Slc17a7=R, Calb2=G, Tbr1=B)")

# A single gene, default palette colour
single = [spec("Slc17a7", info["genes"][0]["color"])]
(OUT / "genes_single.png").write_bytes(gs.composite_png(single))
print("wrote genes_single.png")

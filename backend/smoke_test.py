"""Backend smoke test -- exercises the hard parts without HTTP.

Run:
    .venv\Scripts\python.exe atlas_editor\backend\smoke_test.py
Writes overview.png + a full-res tile PNG to the scratchpad for eyeballing.
"""
import copy
import json
import time
from pathlib import Path

import datasets as ds
import geo
from tiles import Pyramid

DS = "DNMT3A_002_27_38_Het_F"
OUT = Path(r"C:\Users\FIVE\AppData\Local\Temp\claude"
           r"\C--Users-FIVE-source-repos-Jess"
           r"\f2be11fb-56f3-4d18-a20c-251cb0d655b6\scratchpad")


def translate_feature(fc, name, dx, dy):
    fc = copy.deepcopy(fc)
    def walk(c):
        if isinstance(c, list):
            if c and isinstance(c[0], (int, float)):
                c[0] += dx; c[1] += dy
            else:
                for x in c:
                    walk(x)
    for f in fc["features"]:
        if (f.get("properties", {}) or {}).get("name") == name:
            walk(f["geometry"]["coordinates"])
    return fc


def main():
    d = ds.get_dataset(DS)
    print("== image ==", ds.image_path(d))
    t = time.time()
    p = Pyramid(ds.image_path(d))
    print("opened in %.2fs" % (time.time() - t))
    print("info:", json.dumps(p.info(), indent=0))

    # overview
    (OUT / "overview.png").write_bytes(p.overview_png())
    print("wrote overview.png")

    # a full-res (level 0) tile near the brain centre
    cx = (4008 + 27746) // 2
    cy = (1456 + 29661) // 2
    tx, ty = cx // p.tile, cy // p.tile
    t = time.time()
    png = p.tile_png(0, tx, ty)
    print("full-res tile (0,%d,%d): %s bytes in %.2fs"
          % (tx, ty, len(png) if png else 0, time.time() - t))
    if png:
        (OUT / "tile_fullres.png").write_bytes(png)

    # regions
    fc, src = geo.load_regions(DS)
    names = [(f.get("properties", {}) or {}).get("name") for f in fc["features"]]
    print("== regions ==", src)
    print("features:", len(fc["features"]))
    print("names:", names)

    # snap: nudge ISO and let the engine rebuild neighbours
    target = "ISO" if "ISO" in names else names[0]
    after = translate_feature(fc, target, 40.0, 0.0)
    t = time.time()
    result = geo.run_snap(DS, fc, after, moved=[target])
    print("== snap (moved=%r) ==" % target)
    print("movers:", result["_movers"])
    print("notes:", result["_notes"][:5])
    print("out features:", len(result["features"]), "in %.2fs" % (time.time() - t))


if __name__ == "__main__":
    main()

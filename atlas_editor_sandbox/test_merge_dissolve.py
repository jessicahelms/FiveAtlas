"""merge_regions -- merging must DISSOLVE the shared border, not shelve two
lobes side by side in a MultiPolygon with the old border still drawn between.

Offline: pure topology, nothing written anywhere.
"""
import sys

sys.path.insert(0, r"C:\Users\FIVE\source\repos\Jess\atlas_editor\backend")

from shapely.geometry import box, shape, mapping   # noqa: E402
import topology as T                               # noqa: E402

fails = []


def check(label, cond, detail=""):
    print(f"  {'ok  ' if cond else 'FAIL'}  {label}{('  ' + detail) if detail else ''}")
    if not cond:
        fails.append(label)


def feat(name, geom, colour=None):
    props = {"name": name}
    if colour:
        props["classification"] = {"name": name, "colorRGB": colour}
    return {"type": "Feature", "properties": props, "geometry": mapping(geom)}


def merged_geom(res):
    return shape(next(f for f in res["features"]
                      if f["properties"]["name"] == res["name"])["geometry"])


print("\nexactly shared border")
res = T.merge_regions([feat("ISO", box(0, 0, 100, 100), 123),
                       feat("TH", box(100, 0, 200, 100))], "name", ["ISO", "TH"])
g = merged_geom(res)
check("one polygon, border gone", res["parts"] == 1 and g.geom_type == "Polygon")
check("area is the sum", abs(g.area - 20000) < 0.01, f"{g.area:.1f}")
check("nothing needed sealing", res["sealed"] == 0.0)
check("keeps the first region's properties",
      next(f for f in res["features"] if f["properties"]["name"] == res["name"])
      ["properties"].get("classification", {}).get("colorRGB") == 123)

print("\nhairline gap between the two -- the real files look like this")
res = T.merge_regions([feat("ISO", box(0, 0, 100, 100)),
                       feat("TH", box(102.5, 0, 200, 100))], "name", ["ISO", "TH"])
g = merged_geom(res)
check("the seam is sealed into ONE polygon",
      res["parts"] == 1 and g.geom_type == "Polygon",
      f"parts={res['parts']}")
check("the gap area was taken in, nothing more",
      abs(g.area - 20000) < 25.0, f"{g.area:.1f} (plane = 20000)")
check("the seal is reported", res["sealed"] > 0, f"{res['sealed']:.0f}")
check("the coastline was not ballooned",
      abs(g.bounds[0]) < 0.01 and abs(g.bounds[2] - 200) < 0.01
      and abs(g.bounds[1]) < 0.01 and abs(g.bounds[3] - 100) < 0.01,
      str(g.bounds))

print("\nhairline overlap")
res = T.merge_regions([feat("ISO", box(0, 0, 101.5, 100)),
                       feat("TH", box(100, 0, 200, 100))], "name", ["ISO", "TH"])
g = merged_geom(res)
check("overlapping seam unions to one polygon",
      res["parts"] == 1 and g.geom_type == "Polygon")

print("\nthree regions meeting with hairlines")
res = T.merge_regions([feat("A", box(0, 0, 100, 49)),
                       feat("B", box(0, 51, 100, 100)),
                       feat("C", box(101, 0, 200, 100))], "name", ["A", "B", "C"])
g = merged_geom(res)
check("all three seams sealed", res["parts"] == 1, f"parts={res['parts']}")

print("\ngenuinely detached pieces stay detached")
res = T.merge_regions([feat("ISO", box(0, 0, 100, 100)),
                       feat("islet", box(300, 0, 340, 40))], "name", ["ISO", "islet"])
g = merged_geom(res)
check("no tissue is invented between them",
      res["parts"] == 2 and abs(g.area - (10000 + 1600)) < 0.01,
      f"parts={res['parts']} area={g.area:.1f}")
check("nothing sealed", res["sealed"] == 0.0)

print("\nbystanders and multi-part inputs")
regions = [feat("hemi", box(-20, -20, 220, 120)),
           feat("ISO", box(0, 0, 100, 100)),
           feat("TH", box(102, 0, 200, 100)),
           feat("TH", box(150, 105, 160, 115))]        # TH's detached lobe
res = T.merge_regions(regions, "name", ["ISO", "TH"])
names = [f["properties"]["name"] for f in res["features"]]
check("hemi is untouched",
      abs(shape(next(f for f in res["features"] if f["properties"]["name"] == "hemi")
                ["geometry"]).area - 240 * 140) < 0.01)
check("every lobe of a multi-part name is taken",
      names.count(res["name"]) == 1 and "TH" not in [n for n in names if n != res["name"]])
g = merged_geom(res)
check("the main seam sealed; the far lobe stays a separate part",
      res["parts"] == 2, f"parts={res['parts']}")

print("\n" + (f"{len(fails)} FAILED: " + "; ".join(fails) if fails else "all checks passed"))
sys.exit(1 if fails else 0)

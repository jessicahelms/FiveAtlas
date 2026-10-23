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

# ---------------------------------------------------------------------------
print("\na region standing in the seam is never merged over")

# Sealing a hairline seam used to take in anything sitting in it: a 14 px region
# in a 20 px seam was absorbed 100% into the merged body while its own feature
# stayed where it was, so two regions claimed the same ground. Now it is never
# taken, in either mode. What the modes decide is the strips BESIDE it:
#   strict (default)  a seam pixel seals only if its line to both bodies is
#                     clear, so the strips beside the intruder stay unsealed
#   fill_all          every free pixel of the seam seals
from shapely.ops import unary_union                 # noqa: E402


def merge_with(gap, probe=None, tol=40.0, blanket=False, fill_all=False):
    fs = [feat("A", box(0, 0, 1000, 500)),
          feat("B", box(1000 + gap, 0, 2000, 500))]
    pg = None
    if blanket:
        fs.append(feat("hemi", box(-50, -50, 2050, 550)))
    if probe:
        y0, y1, wd = probe
        pg = box(1000 + (gap - wd) / 2.0, y0, 1000 + (gap + wd) / 2.0, y1)
        fs.append(feat("probe", pg))
    res = T.merge_regions(fs, "name", ["A", "B"], seam_tol=tol, fill_all=fill_all)
    g = merged_geom(res)
    taken = T._safe_intersection(g, pg).area if pg is not None else 0.0
    still = None
    beside = 0.0
    if pg is not None:
        still = shape(next(f for f in res["features"]
                           if f["properties"]["name"] == "probe")["geometry"])
        y0, y1, wd = probe
        strip = box(1000, y0, 1000 + gap, y1).difference(pg)
        beside = T._safe_intersection(g, strip).area
    return res, g, taken, still, beside


res, g, _, _, _ = merge_with(12)
check("with nothing in the seam it seals", res["parts"] == 1,
      f"parts={res['parts']}, sealed {res.get('sealed', 0):,.0f} px")

for label, gap, probe in (("an 8 px region in a 12 px seam", 12, (150, 350, 8)),
                          ("a 14 px region in a 20 px seam", 20, (150, 350, 14)),
                          ("one spanning only part of it", 20, (200, 260, 14)),
                          ("a 0.3 px wide sliver region", 20, (150, 350, 0.3))):
    for fill_all in (False, True):
        mode = "fill-all" if fill_all else "strict"
        res, g, taken, still, beside = merge_with(gap, probe, fill_all=fill_all)
        check(f"{label} [{mode}]: none of it is taken", taken <= 1.0,
              f"{taken:,.1f} px taken")
        check(f"...its own geometry is untouched",
              abs(still.area - (probe[1] - probe[0]) * probe[2]) < 0.01)
        check(f"...and the clear stretches still seal", res["parts"] == 1,
              f"parts={res['parts']}")
        if fill_all:
            check(f"...and the strips beside it seal too", beside > 1.0 or probe[2] >= gap - 0.5,
                  f"{beside:,.1f} px sealed beside it")
        else:
            check(f"...but the strips beside it do NOT", beside <= 1.0,
                  f"{beside:,.1f} px sealed beside it")

for fill_all in (False, True):
    res, g, taken, _, _ = merge_with(20, (0, 500, 14), fill_all=fill_all)
    check(f"a region spanning the WHOLE seam blocks it [{'fill-all' if fill_all else 'strict'}]",
          res["parts"] == 2 and taken <= 1.0,
          f"parts={res['parts']}, {taken:,.1f} px taken")

# hemi contains every seam there is, so defending it would stop any seam ever
# sealing -- the one thing this is for.
res, g, _, _, _ = merge_with(12, blanket=True)
check("a region that wraps the whole merge does not block it",
      res["parts"] == 1, f"parts={res['parts']}")

print("\nthe reach means a quarter of what the slider says")
# Merge closes by HALF the reach, then keeps only corridors narrower than that
# again, so its limit is a quarter of the "bridges gaps up to N across" figure.
for tol, want in ((20.0, 10), (40.0, 20), (100.0, 50)):
    widest = 0
    for gap in range(2, int(tol * 1.4), 2):
        res, g, _, _, _ = merge_with(gap, tol=tol)
        if res["parts"] == 1:
            widest = gap
        else:
            break
    check(f"at reach {tol:.0f} it seals up to {want} px", widest == want,
          f"sealed up to {widest} px; the slider's own figure is {tol * 2:.0f}")

print("\n" + (f"{len(fails)} FAILED: " + "; ".join(fails) if fails else "all checks passed"))
sys.exit(1 if fails else 0)

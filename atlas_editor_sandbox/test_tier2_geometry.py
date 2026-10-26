"""Audit Tier 2 geometry fixes (FEATURE_AUDIT_2026-10-23.md, section 4), offline.

Each block names what the old code did. Synthetic shapes with exact answers,
plus the real fixture where the audit measured it (skipped if absent).
"""
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "backend"))
sys.path.insert(0, HERE)

from shapely.geometry import LineString, Polygon, box, mapping, shape   # noqa: E402
from shapely.ops import unary_union                                    # noqa: E402
import topology as T                                                   # noqa: E402
from geom_lint import lint                                             # noqa: E402

fails = []


def check(label, cond, detail=""):
    print(f"  {'ok  ' if cond else 'FAIL'}  {label}{('  ' + str(detail)) if detail != '' else ''}")
    if not cond:
        fails.append(label)


def feat(name, geom, **props):
    return {"type": "Feature", "properties": {"name": name, **props}, "geometry": mapping(geom)}


def by(fs, name):
    return unary_union([shape(f["geometry"]) for f in fs if f["properties"]["name"] == name])


print("\nRepair keeps both lobes of a self-crossing outline (buffer(0) kept one)")
fig8 = Polygon([(0, 0), (100, 100), (100, 0), (0, 100)])
r = T.repair_features([feat("A", fig8)], "name")
g = by(r["features"], "A")
check("figure-8 repaired to its full 5,000 px2 in 2 parts",
      abs(g.area - 5000) < 1 and len(T._polys(g)) == 2, f"{g.area:.0f} in {len(T._polys(g))}")

print("\nSplit cuts only where the stroke reaches; the larger piece keeps the name")
U = unary_union([box(0, 0, 100, 300), box(200, 0, 300, 300), box(0, 0, 300, 50)])  # a U
FS = [feat("U", U), feat("X", box(400, 0, 500, 100))]
try:
    T.split_region(FS, "name", "U", [(600, 150), (700, 150)])
    check("a stroke nowhere near the region is refused", False)
except ValueError as e:
    check("a stroke nowhere near the region is refused", "doesn't touch" in str(e), e)
r = T.split_region(FS, "name", "U", [(-20, 200), (50, 200)])   # ends inside the LEFT arm
a, b = by(r["features"], "U"), by(r["features"], r["names"][1])
check("a stroke ending in one arm cuts only that arm (right arm untouched)",
      a.contains(box(200, 60, 300, 300)) and b.area < 20000, f"cut off {b.area:.0f} px2")
check("the larger piece keeps the name", a.area > b.area, f"{a.area:.0f} vs {b.area:.0f}")
r2 = T.split_region(FS, "name", "U", [(50, 200), (-20, 200)])  # drawn the other way
check("...whichever way the stroke was drawn",
      by(r2["features"], "U").area == a.area)

print("\nMerge: the largest pick names it, notes are kept, damage never joins tissue")
FM = [feat("small", box(0, 0, 50, 100), note="small-note"),
      feat("big", box(50, 0, 250, 100), note="big-note", owner="x"),
      feat("separation.1", box(10, 10, 20, 20)), feat("hemi", box(-50, -50, 400, 400)),
      feat("Q", box(300, 300, 350, 350))]
r = T.merge_regions(FM, "name", ["small", "big"])
props = [f for f in r["features"] if f["properties"]["name"] == r["name"]][0]["properties"]
check("named after the larger pick, whatever the click order", r["name"] == "big", r["name"])
check("both notes kept", props.get("note") == "big-note | small-note", props.get("note"))
try:
    T.merge_regions(FM, "name", ["separation.1", "big"])
    check("damage + tissue is refused", False)
except ValueError as e:
    check("damage + tissue is refused", "damage" in str(e), e)

print("\nEdit shared borders (side by side)")
A, B = box(0, 0, 100, 100), box(100, 0, 200, 100)
FB = [feat("A", A), feat("B", B), feat("hemi", box(-50, -50, 300, 300)), feat("C", box(0, 200, 50, 250))]
old = [(100, 0), (100, 25), (100, 50), (100, 75), (100, 100)]
new = [(100, 0), (130, 25), (100, 50), (70, 75), (100, 100)]       # an S: out, then in
out = T.move_border(FB, "name", "A", "B", new, drag_start=old)
gA = by(out, "A")
s1 = Polygon([(100, 0), (130, 25), (100, 50)]).area
check("an S-shaped drag applies BOTH halves", gA.contains(Polygon([(101, 10), (125, 25), (101, 40)]))
      and not gA.intersects(Polygon([(72, 74), (99, 74), (99, 76)]).buffer(-0.5)),
      f"A {gA.area:.0f} (unchanged would be 10,000)")
deep = [(100, 0), (100, 30), (-60, 50), (100, 70), (100, 100)]   # runs right through A and out
out = T.move_border(FB, "name", "A", "B", deep, drag_start=old)
check("a deep drag into A is a push, not a growth", by(out, "A").area < 10000 - 1000,
      f"A {by(out, 'A').area:.0f}")
Bg = box(104.5, 0, 200, 100)                                      # a 4.5 px gap
FG = [feat("A", A), feat("B", Bg), feat("hemi", box(-50, -50, 300, 300)), feat("C", box(0, 200, 50, 250))]
into_a = [(100, 0), (100, 30), (80, 50), (100, 70), (100, 100)]
out = T.move_border(FG, "name", "A", "B", into_a, drag_start=old)
check("across a 4.5 px gap, dragging into A really moves the border",
      by(out, "A").area < 10000 - 300, f"A {by(out, 'A').area:.0f}")
Bo = box(80, 0, 200, 100)                                          # overlaps A by 20 x 100
FO = [feat("A", A), feat("B", Bo), feat("hemi", box(-50, -50, 300, 300)), feat("C", box(0, 200, 50, 250))]
nudge = [(100, 0), (100, 10), (103, 15), (100, 20), (100, 100)]
out = T.move_border(FO, "name", "A", "B", nudge, drag_start=[(100, 0), (100, 10), (100, 15), (100, 20), (100, 100)])
ov = by(out, "A").intersection(by(out, "B")).area
check("an overlap the pair already had stays away from the drag (was handed to pick #1)",
      ov > 1500, f"overlap {ov:.0f} of 2,000")
check("...and no needle is left", lint(by(out, "B"))["needles"] == 0)

print("\nDelete a point on a hole")
H = Polygon([(0, 0), (200, 0), (200, 200), (100, 210), (0, 200)],
            [[(50, 50), (150, 50), (150, 150), (100, 160), (50, 150)]])
r = T.delete_point([feat("A", H), feat("B", box(200, 0, 300, 200))], "name", "A", (100, 160))
g = by(r["features"], "A")
check("the hole point goes, the outline is untouched",
      len(g.interiors[0].coords) - 1 == 4 and len(g.exterior.coords) - 1 == 5, r.get("stretch"))

print("\nClean takes a thin spike poking out of a healthy region")
Asp = unary_union([box(0, 0, 100, 100), box(100, 50, 130, 51)])
r = T.clean_lines([feat("A", Asp), feat("B", box(200, 0, 300, 100))], "name",
                  [(90, 30), (140, 30), (140, 70), (90, 70), (90, 30)], width=12)
check("the 30 x 1 px spike is removed", abs(by(r["features"], "A").area - 10000) < 1)
try:
    T.clean_lines([feat("A", box(0, 0, 100, 100)), feat("B", box(200, 0, 300, 100))], "name",
                  [(80, 80), (120, 80), (120, 120), (80, 120), (80, 80)], width=12)
    check("a healthy corner in the loop is left alone", False)
except ValueError:
    check("a healthy corner in the loop is left alone", True)

print("\nFix a gap gives no ground to a damage shape")
FD = [feat("A", box(0, 0, 100, 100)), feat("B", box(104, 0, 200, 100)),
      feat("separation.1", box(100, 40, 104, 60)), feat("C", box(0, 200, 50, 250))]
r = T.dissolve_gap(FD, "name", (102, 20), tol=40)
check("the damage shape is unchanged", abs(by(r["features"], "separation.1").area - 80) < 1e-6,
      by(r["features"], "separation.1").area)

REAL = r"C:\Users\FIVE\source\repos\FiveAtlas_handoff_fixtures\regions_20261021_112134.geojson"
if os.path.exists(REAL):
    import itertools
    F = json.load(open(REAL, encoding="utf-8"))["features"]

    def bodies(fs):
        out_ = {}
        for f in fs:
            out_.setdefault(str(f["properties"]["name"]), []).append(shape(f["geometry"]))
        return {k: unary_union(v).buffer(0) for k, v in out_.items()}

    B0 = bodies(F)
    print("\nreal: strict Merge seals what fill-all seals (shadows only from regions IN the seam)")
    names = sorted(n for n in B0 if n != "hemi")
    withheld = 0.0
    for x, y in itertools.combinations(names, 2):
        if B0[x].distance(B0[y]) > 1.0 or T.containment(F, "name", [x, y]):
            continue
        gs = by(T.merge_regions(F, "name", [x, y], seam_tol=40)["features"],
                T.merge_regions(F, "name", [x, y], seam_tol=40)["name"])
        gf = by(T.merge_regions(F, "name", [x, y], seam_tol=40, fill_all=True)["features"],
                T.merge_regions(F, "name", [x, y], seam_tol=40, fill_all=True)["name"])
        withheld += max(0.0, gf.area - gs.area)
    check("reach 40: strict withholds under 50 px2 over all touching pairs (was 465)",
          withheld < 50, f"{withheld:.1f} px2")
    print("\nreal: a 5 px nudge on dft/RSP keeps their 836,053 px2 overlap")
    arcs = T.border_between(F, "name", "dft", "RSP")
    ring = max(arcs, key=lambda a_: LineString(a_).length)
    mid = len(ring) // 2
    moved = [list(p) for p in ring]
    moved[mid] = [ring[mid][0] + 5, ring[mid][1]]
    out = T.move_border(F, "name", "dft", "RSP", moved, drag_start=ring)
    G = bodies(out)
    ov = G["dft"].intersection(G["RSP"]).area
    check("the overlap survives (was cut to 0)", ov > 800000, f"{ov:,.0f} px2")
else:
    print("\n(real fixture not found -- skipping the real-file checks)")

print()
if fails:
    print(f"{len(fails)} FAILED")
    sys.exit(1)
print("all passed")

"""Merge into one seals every seam -- and changes nothing else (audit item 1.7).

Before the fix: a slit open at one end was never sealed (only a union in several
parts or with holes went through the seal), 13,004 px2 on the real lft + TH;
the seal spilled 89,523 px2 past hemi (TH + V3, reach 5000); a grid snap and
clean_geom over the WHOLE outline re-shaped it (3,193 vertices -> 13) and
dropped parts under 25 px2; 9-10 real pairs per reach took up to 104 px2 of
a neighbour; and PAL.1 + PAL.2 was named 'PAL.'.
"""
import itertools
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "backend"))
sys.path.insert(0, HERE)

from shapely.geometry import Point, box, shape, mapping         # noqa: E402
from shapely.ops import unary_union                            # noqa: E402
import topology as T                                           # noqa: E402
from geom_lint import lint                                     # noqa: E402

fails = []


def check(label, cond, detail=""):
    print(f"  {'ok  ' if cond else 'FAIL'}  {label}{('  ' + str(detail)) if detail != '' else ''}")
    if not cond:
        fails.append(label)


def feat(name, geom):
    return {"type": "Feature", "properties": {"name": name}, "geometry": mapping(geom)}


def merged_geom(res):
    return shape([f for f in res["features"] if f["properties"]["name"] == res["name"]][0]["geometry"])


HEMI = box(-50, -50, 450, 450)

print("\na slit open at one end is sealed")
A = box(0, 0, 100, 100)
B = box(100, 0, 200, 100).difference(box(100, 50, 102, 100))
for tol in (40, 500):
    res = T.merge_regions([feat("hemi", HEMI), feat("A", A), feat("B", B), feat("C", box(300, 300, 350, 350))],
                          "name", ["A", "B"], seam_tol=tol)
    g = merged_geom(res)
    check(f"reach {tol}: the slit is sealed (area 20,000)", abs(g.area - 20000) < 1.0, f"{g.area:.1f}")
    check(f"reach {tol}: one part, no new lint", len(T._polys(g)) == 1 and lint(g)["spikes"] == 0
          and lint(g)["needles"] == 0, lint(g))

print("\na tiny islet merged along with a sealed pair is kept")
for s in (4, 16):
    isl = box(300, 300, 300 + s ** 0.5, 300 + s ** 0.5)
    res = T.merge_regions([feat("hemi", HEMI), feat("A", A), feat("B", box(102, 0, 200, 100)),
                           feat("I", isl), feat("C", box(0, 300, 50, 350))],
                          "name", ["A", "B", "I"], seam_tol=40)
    g = merged_geom(res)
    check(f"a {s} px2 islet is still there", g.intersection(isl).area > 0.9 * s, f"{g.intersection(isl).area:.1f}")

print("\nthe outline away from the seal keeps every vertex")
import math
ring = [(200 + 150 * math.cos(t / 400 * 2 * math.pi) + 0.8 * math.sin(t * 0.9),
         200 + 150 * math.sin(t / 400 * 2 * math.pi) + 0.8 * math.cos(t * 1.1)) for t in range(400)]
from shapely.geometry import Polygon
D = Polygon(ring)
assert D.is_valid, "the test outline must be a valid ring"
E = box(353, 150, 420, 250)               # 3 px gap to D's right edge -> sealed
res = T.merge_regions([feat("hemi", box(-100, -100, 600, 600)), feat("D", D), feat("E", E),
                       feat("F", box(0, 500, 50, 550))], "name", ["D", "E"], seam_tol=40)
g = merged_geom(res)
seal_zone = E.buffer(30)              # the seal reaches seam_tol/2 = 20 px
far_old = {(round(x, 6), round(y, 6)) for x, y in D.exterior.coords if not seal_zone.contains(Point(x, y))}
far_new = {(round(x, 6), round(y, 6)) for p in T._polys(g) for x, y in p.exterior.coords
           if not seal_zone.contains(Point(x, y))}
missing = far_old - far_new
check("a dense outline is not re-shaped away from the seal", len(missing) == 0,
      f"{len(missing)} of {len(far_old)} far vertices gone")

print("\nnames")
for names, want in ((["PAL.1", "PAL.2"], "PAL"), (["sAMY.1", "sAMY.4"], "sAMY"),
                    (["Fiber Tracts 1", "Fiber Tracts 2"], "Fiber Tracts"),
                    (["separation.2", "separation.1"], "separation.1"), (["CP", "dft"], "CP")):
    got = T._common_base_name(names)
    check(f"{' + '.join(names)} -> {want!r}", got == want, repr(got))

REAL = r"C:\Users\FIVE\source\repos\FiveAtlas_handoff_fixtures\regions_20261021_112134.geojson"
if os.path.exists(REAL):
    F = json.load(open(REAL, encoding="utf-8"))["features"]

    def bodies(fs):
        out = {}
        for f in fs:
            out.setdefault(str(f["properties"]["name"]), []).append(shape(f["geometry"]))
        return {k: unary_union(v).buffer(0) for k, v in out.items()}

    B0 = bodies(F)
    hemi = B0["hemi"]
    names = sorted(n for n in B0 if n != "hemi")
    pairs = [(a, b) for a, b in itertools.combinations(names, 2)
             if B0[a].distance(B0[b]) <= 80 and not T.containment(F, "name", [a, b])]
    print("\nall 41 real pairs")
    for tol in (40, 500, 5000):
        out_h, ov, lost = [], [], []
        for a, b in pairs:
            res = T.merge_regions(F, "name", [a, b], seam_tol=tol)
            g = merged_geom(res)
            u = unary_union([B0[a], B0[b]])
            if g.difference(hemi).area - u.difference(hemi).area > 1.0:
                out_h.append(f"{a}+{b}")
            others = [n for n in names if n not in (a, b)]
            add = sum(max(0.0, g.intersection(B0[o]).area - u.intersection(B0[o]).area) for o in others)
            if add > 1.0:
                ov.append(f"{a}+{b} {add:.0f}")
            if u.difference(g).area > 1.0:
                lost.append(f"{a}+{b}")
        check(f"reach {tol}: nothing sealed past hemi", not out_h, out_h[:4])
        check(f"reach {tol}: no neighbour's ground taken", not ov, ov[:4])
        check(f"reach {tol}: no ground lost", not lost, lost[:4])
else:
    print("\n(real regions fixture not found -- skipping the real-pair checks)")

print()
if fails:
    print(f"{len(fails)} FAILED")
    sys.exit(1)
print("all passed")

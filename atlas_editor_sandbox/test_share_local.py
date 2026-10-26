"""Share borders changes the picks only where they meet (audit item 1.5).

Before the fix the territory simplify reshaped the WHOLE outline of both
picks: HY + PAL.2, 111 px apart with nothing to bridge, gave HY 28,945 px2 in
pieces up to 9,557 px from PAL.2; across the 41 real pairs at reach 40 every
pair gained ground far from its seam (321,000 px2 in all), 6 filled a pick's
own hole and 19 grew past hemi; at reach 5000 OLF + CTXsp filled OLF's 146,324
px2 hole. Offline, on the real fixture (skipped if it is not there).
"""
import itertools
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "backend"))
sys.path.insert(0, HERE)

from shapely.geometry import Polygon, shape                   # noqa: E402
from shapely.ops import unary_union                           # noqa: E402
import topology as T                                          # noqa: E402
from geom_lint import lint                                    # noqa: E402

fails = []


def check(label, cond, detail=""):
    print(f"  {'ok  ' if cond else 'FAIL'}  {label}{('  ' + str(detail)) if detail != '' else ''}")
    if not cond:
        fails.append(label)


def gmap(fs):
    out = {}
    for f in fs:
        n = f["properties"]["name"]
        g = shape(f["geometry"])
        g = g if g.is_valid else g.buffer(0)
        out[n] = unary_union([out[n], g]) if n in out else g
    return out


def holes(g):
    return unary_union([Polygon(h) for p in T._polys(g) for h in p.interiors] or [Polygon()])


REAL = r"C:\Users\FIVE\source\repos\FiveAtlas_handoff_fixtures\regions_20261021_112134.geojson"
if not os.path.exists(REAL):
    print("(real regions fixture not found -- skipping)")
    sys.exit(0)
F = json.load(open(REAL, encoding="utf-8"))["features"]
B = gmap(F)
hemi = B["hemi"]

print("\nHY + PAL.2: 111 px apart, nothing to bridge")
for tol in (40, 500):
    A = gmap(T.partition_regions(F, "name", ["HY", "PAL.2"], tol=tol)["features"])
    far = A["HY"].difference(B["HY"]).difference(B["PAL.2"].buffer(2 * tol + 50)).area
    check(f"reach {tol}: HY gains nothing beyond 2 x reach + 50 px of PAL.2", far < 50, f"{far:.1f} px2")

print("\nOLF keeps its own 146,324 px2 hole")
for nb in ("CTXsp", "ISO", "sAMY.1"):
    A = gmap(T.partition_regions(F, "name", ["OLF", nb], tol=5000)["features"])
    filled = A["OLF"].intersection(holes(B["OLF"])).area
    check(f"OLF + {nb} at reach 5000: the hole stays open", filled < 10, f"{filled:.1f} px2 filled")

print("\nall 41 real pairs, strict, reach 40 and 500")
names = [n for n in B if n != "hemi"]
pairs = [(a, b) for a, b in itertools.combinations(sorted(names), 2)
         if B[a].distance(B[b]) <= 80 and not T.containment(F, "name", [a, b])]
for tol in (40, 500):
    far_runs, hole_runs, out_runs, flaw_runs, worst_far = 0, 0, 0, 0, (0.0, "")
    for a, b in pairs:
        A = gmap(T.partition_regions(F, "name", [a, b], tol=tol)["features"])
        for p, q in ((a, b), (b, a)):
            far = A[p].difference(B[p]).difference(B[q].buffer(2 * tol + 50)).area
            if far > 100:
                far_runs += 1
            if far > worst_far[0]:
                worst_far = (far, f"{p}+{q}")
            if A[p].intersection(holes(B[p])).area > 10:
                hole_runs += 1
            if A[p].difference(hemi).area - B[p].difference(hemi).area > 400:
                out_runs += 1
            la, lb = lint(A[p]), lint(B[p])
            if la["needles"] > lb["needles"] or la["spikes"] > lb["spikes"] or not la["valid"]:
                flaw_runs += 1
    # (a close of radius `reach` bridges gaps up to 2 x reach wide, so ground
    # within 2 x reach of the other pick can be a gap between them; beyond that
    # it is a bay of one pick. A hole ringed by BOTH picks is still a gap they
    # share, and closing it is what Share is for -- a few such at reach 500.)
    limit = 0 if tol == 40 else 4
    check(f"reach {tol}: picks gaining > 100 px2 beyond 2 x reach + 50 of the other: <= {limit}",
          far_runs <= limit, f"{far_runs} (worst {worst_far[1]} {worst_far[0]:.0f} px2)")
    check(f"reach {tol}: no pick's own hole filled", hole_runs == 0, hole_runs)
    check(f"reach {tol}: no pick grows > 400 px2 past hemi", out_runs == 0, out_runs)
    # reach 500: one pre-existing seam spike remains (opt + sAMY.1, where the
    # Voronoi midline meets opt's edge at a sharp angle) -- 5 such runs before
    # since each pick keeps its own ground exactly, that seam spike shows at
    # reach 40 too (the seam meets sAMY.1's exact edge at under 2 degrees)
    flaw_limit = 1
    check(f"reach {tol}: runs with a new needle / spike / invalid: <= {flaw_limit}",
          flaw_runs <= flaw_limit, flaw_runs)

print()
if fails:
    print(f"{len(fails)} FAILED")
    sys.exit(1)
print("all passed")

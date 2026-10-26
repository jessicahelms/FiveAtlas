"""Edit shared borders on a NESTED pair changes only what the drag moved
(audit item 1.8).

Before the fix every drag on ISO + SSp / RSP -- even one that moved nothing --
grew ISO by the inner's whole overhang (1,133,470 / 1,597,067 px2) and cut a
1,229 px needle into it; an inward drag near the coast orphaned up to 798,445
px2 of ISO and split it in three; dragging back did not undo it; and VL.2 lost
865 px2 per drag because it was rebuilt from its simplified handles. Offline,
on the real fixture (skipped if it is not there).
"""
import json
import math
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "backend"))
sys.path.insert(0, HERE)

from shapely.geometry import LineString, Point                 # noqa: E402
from shapely.ops import unary_union                            # noqa: E402
import topology as T                                           # noqa: E402
from geom_lint import lint                                     # noqa: E402

fails = []


def check(label, cond, detail=""):
    print(f"  {'ok  ' if cond else 'FAIL'}  {label}{('  ' + str(detail)) if detail != '' else ''}")
    if not cond:
        fails.append(label)


REAL = r"C:\Users\FIVE\source\repos\FiveAtlas_handoff_fixtures\regions_20261021_112134.geojson"
if not os.path.exists(REAL):
    print("(real regions fixture not found -- skipping)")
    sys.exit(0)
F = json.load(open(REAL, encoding="utf-8"))["features"]


def bodies(fs):
    idx = T._index_all(fs, "name")
    return {n: T._body(fs, ks) for n, ks in idx.items()}


def flaws(g):
    r = lint(g)
    return r["needles"] + r["spikes"] + r["hairlines"] + (0 if r["valid"] else 100)


def normals(arc):
    out, n = [], len(arc)
    for i in range(n):
        p0, p1 = arc[max(i - 1, 0)], arc[min(i + 1, n - 1)]
        dx, dy = p1[0] - p0[0], p1[1] - p0[1]
        L = math.hypot(dx, dy) or 1.0
        out.append((-dy / L, dx / L))
    return out


G0 = bodies(F)
for outer, inner in (("ISO", "SSp"), ("ISO", "RSP"), ("dft", "VL.2")):
    print(f"\n{inner} inside {outer}")
    O0, I0 = G0[outer], G0[inner]
    ring = max(T.border_between(F, "name", outer, inner), key=lambda a: LineString(a).length)
    n = len(ring) - 1
    nr = normals(ring[:-1] + [ring[0]])
    dist = [O0.exterior.distance(Point(*ring[k])) for k in range(n)]
    coast = min(range(1, n), key=lambda k: dist[k])
    inland = max(range(1, n), key=lambda k: dist[k])

    def out_sign(k):
        x, y = ring[k]
        return -1 if I0.contains(Point(x + 3 * nr[k][0], y + 3 * nr[k][1])) else 1

    cases = [("no-op", [list(p) for p in ring])]
    for lab, k in (("coast", coast), ("inland", inland)):
        for d in (5, 50, 500):
            for dirn, sgn in (("out", 1), ("in", -1)):
                new = [list(p) for p in ring]
                s = out_sign(k) * sgn * d
                new[k] = [ring[k][0] + s * nr[k][0], ring[k][1] + s * nr[k][1]]
                cases.append((f"{lab} {dirn} {d}", new))
    for lab, new in cases:
        out = T.move_border(F, "name", outer, inner, new, drag_start=ring)
        G1 = bodies(out)
        O1, I1 = G1[outer], G1[inner]
        tag = f"{inner} {lab}"
        if lab == "no-op":
            check(f"{tag}: nothing changes", O1.symmetric_difference(O0).area < 1.0
                  and I1.symmetric_difference(I0).area < 1.0,
                  f"outer {O1.symmetric_difference(O0).area:.1f}, inner {I1.symmetric_difference(I0).area:.1f}")
        # the container grows only by what the inner newly pushed past it
        grew = O1.difference(O0).area
        pushed = I1.difference(I0).difference(O0).area
        check(f"{tag}: container grows only by the inner's new overhang",
              grew <= pushed + 5.0, f"grew {grew:.0f}, new overhang {pushed:.0f}")
        orphan = O0.difference(unary_union([O1, I1])).area
        check(f"{tag}: no ground orphaned", orphan < 5.0, f"{orphan:.0f} px2")
        check(f"{tag}: container stays one part", len(T._polys(O1)) == len(T._polys(O0)))
        if not (inner == "VL.2" and lab.endswith("in 500")):   # a 500 px push across a ventricle may cut it
            check(f"{tag}: inner stays one part", len(T._polys(I1)) == len(T._polys(I0)))
        check(f"{tag}: no new needle / spike / hairline",
              flaws(O1) <= flaws(O0) and flaws(I1) <= flaws(I0),
              f"outer {flaws(O0)}->{flaws(O1)}, inner {flaws(I0)}->{flaws(I1)}")
        if lab in ("coast in 500", "inland out 500", "inland in 500", "coast out 5"):
            back = T.move_border(out, "name", outer, inner, ring, drag_start=new)
            Gb = bodies(back)
            r_i = Gb[inner].symmetric_difference(I0).area
            check(f"{tag}: dragging back restores the inner (< 200 px2)", r_i < 200, f"{r_i:.0f} px2")

print()
if fails:
    print(f"{len(fails)} FAILED")
    sys.exit(1)
print("all passed")

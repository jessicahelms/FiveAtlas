"""Snap neighbours touches only the regions an edit reaches (audit item 1.3),
and the gap route says when a pair is nested (item 1.6).

Before: every region on the map was written back through a 1 px grid, so with
NO edit at all ISO moved 13,198 px2 from 5,900 px away, ~14,000 px2 of map was
lost and +2,663 px2 of overlap appeared. Offline, on the real fixture.
"""
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "backend"))

from shapely import affinity                                    # noqa: E402
from shapely.geometry import shape, mapping                     # noqa: E402
from shapely.ops import unary_union                             # noqa: E402
import topology as T                                            # noqa: E402

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
kept = [f for f in F if f["properties"]["name"] != "hemi"]      # what the /snap route does


def bodies(fs):
    out = {}
    for f in fs:
        out.setdefault(str(f["properties"]["name"]), []).append(shape(f["geometry"]))
    return {k: unary_union(v).buffer(0) for k, v in out.items()}


B0 = bodies(kept)
print("\nno edit at all")
B1 = bodies(T.snap_to_edits(kept, kept, "name", ["HPF"], tol=40.0))
moved = {n: B0[n].symmetric_difference(B1[n]).area for n in B0}
# the snap makes neighbours conform to the moved region, so one that already
# overlapped HPF gives that overlap up (dft, 742 px2) -- and nothing else moves
expect = {n: B0[n].intersection(B0["HPF"]).area for n in B0 if n != "HPF"}
odd = {n: round(a, 1) for n, a in moved.items() if n != "HPF" and abs(a - expect.get(n, 0.0)) > 1.0}
check("only regions overlapping HPF change, and only by that overlap", not odd, odd)
check("nothing else moves (ISO used to move 13,198 px2)", moved["ISO"] < 1e-6, moved["ISO"])

print("\nHPF pushed 90 px into TH")
after = json.loads(json.dumps(kept))
for f in after:
    if f["properties"]["name"] == "HPF":
        g = shape(f["geometry"])
        f["geometry"] = mapping(affinity.translate(g, 90, 0).union(g).buffer(0))
B2 = bodies(T.snap_to_edits(kept, after, "name", ["HPF"], tol=40.0))
Ba = bodies(after)
mover = Ba["HPF"]
far = [n for n in B0 if n != "HPF" and B0[n].distance(mover) > 200
       and B2[n].symmetric_difference(B0[n]).area > 1e-6]
check("no region more than 200 px from the edit changes", not far, far)
ov = sum(B2[n].intersection(mover).area for n in B2 if n != "HPF")
check("nobody overlaps the moved region afterwards", ov < 1.0, f"{ov:.2f} px2")

print("\nnested pairs are reported (the sidebar hides Snap to / Fill all for them)")
for a, b in (("ISO", "SSp"), ("dft", "VL.2")):
    c = T.containment(F, "name", [a, b])
    check(f"{a} + {b} is nested", bool(c) and c["outer"] == a and c["inner"] == b, c)
c = T.containment(F, "name", ["HPF", "TH"])
check("HPF + TH is not", not c, c)

print()
if fails:
    print(f"{len(fails)} FAILED")
    sys.exit(1)
print("all passed")

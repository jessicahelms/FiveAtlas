"""Share borders with "Snap to X" leaves the partner in one clean piece
(audit item 1.4).

Before the fix the partner was handed every bit of ground the partition had
given X beyond X's exact outline -- slivers from all round X, and voids far
from the seam: CP+dft "Snap to dft" left CP in 25 parts, 24 of them up to
11,565 px from the seam, with +18 needles / +42 spikes; over the 41 real pairs
x both directions x reach 40/500, 110 runs shattered the partner, 102 left
detached shards and 90 added lint (+592 needles, +1,535 spikes). Offline, on
the real fixture (skipped if it is not there).
"""
import itertools
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "backend"))
sys.path.insert(0, HERE)

from shapely.geometry import shape                             # noqa: E402
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
    out = {}
    for f in fs:
        out.setdefault(str(f["properties"]["name"]), []).append(shape(f["geometry"]))
    return {k: unary_union(v).buffer(0) for k, v in out.items()}


def flaws(g):
    r = lint(g)
    return r["needles"], r["spikes"], r["hairlines"]


B0 = bodies(F)

print("\nCP + dft, Snap to dft (the audit's case)")
for tol in (40, 500):
    B1 = bodies(T.partition_regions(F, "name", ["CP", "dft"], tol=tol, snap_to="dft")["features"])
    check(f"reach {tol}: dft untouched", B1["dft"].symmetric_difference(B0["dft"]).area < 1.0)
    check(f"reach {tol}: CP stays in {len(T._polys(B0['CP']))} part(s)",
          len(T._polys(B1["CP"])) == len(T._polys(B0["CP"])), len(T._polys(B1["CP"])))
    n0, n1 = flaws(B0["CP"]), flaws(B1["CP"])
    check(f"reach {tol}: no new needle / spike / hairline on CP", all(a <= b for a, b in zip(n1, n0)),
          f"{n0} -> {n1}")

print("\nall 41 real pairs, both directions")
names = sorted(n for n in B0 if n != "hemi")
pairs = [(a, b) for a, b in itertools.combinations(names, 2)
         if B0[a].distance(B0[b]) <= 80 and not T.containment(F, "name", [a, b])]
for tol in (40, 500):
    moved, shattered, flawed, refused = [], [], [], 0
    for a, b in pairs:
        for keep, part in ((a, b), (b, a)):
            try:
                B1 = bodies(T.partition_regions(F, "name", [a, b], tol=tol, snap_to=keep)["features"])
            except ValueError:
                refused += 1
                continue
            if B1[keep].symmetric_difference(B0[keep]).area > 1.0:
                moved.append(f"{keep}<-{part}")
            if len(T._polys(B1[part])) > len(T._polys(B0[part])):
                shattered.append(f"{part} snap->{keep}")
            f0, f1 = flaws(B0[part]), flaws(B1[part])
            if f1[0] > f0[0] or f1[2] > f0[2] or f1[1] > f0[1]:
                flawed.append(f"{part} snap->{keep}")
    check(f"reach {tol}: the snapped region is never moved", not moved, moved[:4])
    check(f"reach {tol}: the partner never gains a part", not shattered, shattered[:4])
    # reach 500 keeps the plain Share's own seam spike (opt / sAMY.1); 90 runs before
    limit = 0 if tol == 40 else 2
    check(f"reach {tol}: runs adding a needle / spike / hairline to the partner <= {limit}",
          len(flawed) <= limit, flawed[:4])

print()
if fails:
    print(f"{len(fails)} FAILED")
    sys.exit(1)
print("all passed")

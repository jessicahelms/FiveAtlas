"""Share borders never gives a pick's OWN ground to its partner, and sharing
again changes (almost) nothing (audit Tier 2, "not idempotent").

Before the fix the nearest-region split ran over the whole territory and the
seam thinning re-simplified borders the picks already shared exactly: 33 of the
41 real pairs at reach 40 handed over 100+ px2 of a pick's exclusive ground
(TH + lft 48,576 px2), and sharing the result again moved it once more (13 pairs
over 100 px2, RSP + dft 30,206; at reach 500, HY + TH 531,927). Offline, real
fixture (skipped if it is not there).
"""
import itertools
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "backend"))

from shapely.geometry import shape                             # noqa: E402
from shapely.ops import unary_union                            # noqa: E402
import topology as T                                           # noqa: E402

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


B0 = bodies(F)
names = sorted(n for n in B0 if n != "hemi")
pairs = [(a, b) for a, b in itertools.combinations(names, 2)
         if B0[a].distance(B0[b]) <= 80 and not T.containment(F, "name", [a, b])]

print("\nreach 40, all 41 real pairs, strict")
handed, moved, third = [], [], []
for a, b in pairs:
    r1 = T.partition_regions(F, "name", [a, b], tol=40)["features"]
    B1 = bodies(r1)
    for p, q in ((a, b), (b, a)):
        lost = B0[p].difference(B0[q]).intersection(B1[q]).area
        # RSP and dft already overlap by 836,053 px2; splitting that overlap can
        # leak a few thousand px2 across its edge
        if lost > (10000 if {p, q} == {"RSP", "dft"} else 200):
            handed.append(f"{p}->{q} {lost:.0f}")
    B2 = bodies(T.partition_regions(r1, "name", [a, b], tol=40)["features"])
    d = max(B1[n].symmetric_difference(B2[n]).area for n in (a, b))
    if d > 100:
        moved.append(f"{a}+{b} {d:.0f}")
    t = max(B2[o].symmetric_difference(B0[o]).area for o in names if o not in (a, b))
    if t > 1e-6:
        third.append(f"{a}+{b} {t:.1f}")
check("no pick hands its own ground to its partner (> 200 px2)", not handed, handed[:5])
check("sharing the result again moves nothing (> 100 px2)", not moved, moved[:5])
check("no third region changes", not third, third[:5])

print()
if fails:
    print(f"{len(fails)} FAILED")
    sys.exit(1)
print("all passed")

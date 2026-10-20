"""The bounding-box fast path in `overlap_report` -- the preflight that runs
while the annotator is drawing, before the "on top, underneath, or overlap?"
prompt.

`overlap_report` has to reject the regions nowhere near the new outline cheaply,
because the alternative is `_safe_intersection` against every polygon in the
file -- two buffer(0)s, an intersection, and up to four precision-grid retries
EACH -- on an interactive path.

The guard was once written `other.intersects(other.envelope)`, which is true of
every geometry that ever existed, so nothing was ever skipped. A test that only
looked at the returned hits could not have caught that: the fast path is
invisible in the answer by design. So this file checks BOTH halves of "cheap and
identical":

* the expensive call is actually skipped for regions that cannot overlap, and
* the answer is exactly what the slow all-pairs version would have given.

Entirely offline and entirely synthetic -- no backend, no dataset, no network
imports at all. Nothing here may ever be pointed at a running FiveAtlas.
"""
import math
import os
import random
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "backend"))

from shapely.geometry import Polygon, box, mapping, shape   # noqa: E402
from shapely.ops import unary_union                         # noqa: E402

import topology as T                                        # noqa: E402

fails = []


def check(label, cond, detail=""):
    print(f"  {'ok  ' if cond else 'FAIL'}  {label}{('  ' + detail) if detail else ''}")
    if not cond:
        fails.append(label)


def feat(name, geom):
    return {"type": "Feature", "properties": {"name": name},
            "geometry": mapping(geom)}


class Counted:
    """Stands in for `_safe_intersection` and keeps score."""

    def __init__(self, real):
        self.real = real
        self.n = 0

    def __call__(self, a, b):
        self.n += 1
        return self.real(a, b)


def count_calls(features, points, min_area=25.0):
    """(report, how many times _safe_intersection was reached)."""
    real = T._safe_intersection
    spy = Counted(real)
    T._safe_intersection = spy
    try:
        return T.overlap_report(features, "name", points, min_area=min_area), spy.n
    finally:
        T._safe_intersection = real


def reference_report(features, id_prop, points, min_area=25.0):
    """`overlap_report` with NO bounding-box reject -- the all-pairs version.

    Deliberately a transcription of the function's own body, so that the only
    difference between the two is the fast path under test.
    """
    ring = [(float(x), float(y)) for x, y in points]
    if ring[0] != ring[-1]:
        ring.append(ring[0])
    g = Polygon(ring)
    if not g.is_valid:
        g = g.buffer(0)
    g = unary_union(T._polys(g)).buffer(0) if T._polys(g) else g

    hits = []
    for f in features:
        if (f.get("geometry") or {}).get("type") not in ("Polygon", "MultiPolygon"):
            continue
        try:
            other = shape(f["geometry"])
        except Exception:
            continue
        if not other.is_valid:
            other = other.buffer(0)
        if other.is_empty or other.area <= 0:
            continue
        inter = T._safe_intersection(other, g).area
        if inter <= min_area:
            continue
        if inter / other.area >= T.CONTAINED_FRAC:
            rel = "envelops"
        elif inter / g.area >= T.CONTAINED_FRAC:
            rel = "inside"
        else:
            rel = "partial"
        hits.append({"name": str((f.get("properties") or {}).get(id_prop)),
                     "relationship": rel, "area": float(inter)})
    hits.sort(key=lambda h: -h["area"])
    return {"overlaps": hits, "area": float(g.area)}


def same_report(a, b, tol=1e-9):
    if len(a["overlaps"]) != len(b["overlaps"]):
        return False
    if abs(a["area"] - b["area"]) > tol:
        return False
    for x, y in zip(a["overlaps"], b["overlaps"]):
        if x["name"] != y["name"] or x["relationship"] != y["relationship"]:
            return False
        if abs(x["area"] - y["area"]) > tol:
            return False
    return True


DRAWN = [[0, 0], [100, 0], [100, 100], [0, 100]]       # the outline being drawn

print("\nthe far-away regions are rejected without an intersection")
# One real overlap, one that shares no bounding box, and a crowd on the far side
# of the section -- the shape of a real file, where a new outline touches one or
# two regions out of a couple of hundred.
crowd = [feat(f"far{i}", box(1000 + 40 * i, 1000, 1030 + 40 * i, 1030))
         for i in range(200)]
near = feat("A", box(80, 80, 150, 150))                # really does overlap
miss = feat("B", box(120, 120, 200, 200))              # bbox clears the outline
rep, calls = count_calls([near, miss] + crowd, DRAWN)
check("only the region whose bbox meets the outline is costed", calls == 1,
      f"{calls} intersection(s) for {2 + len(crowd)} regions")
check("...and it is the one reported",
      [h["name"] for h in rep["overlaps"]] == ["A"], str(rep["overlaps"]))

print("\nthe fast path never decides the answer, it only saves work")
# A thin L wrapped around the outline: its bounding box swallows the outline
# whole, so the bbox test waves it through, but the geometry misses entirely and
# the `inter <= min_area` test is what drops it -- exactly as before the fix.
L = Polygon([(-50, -50), (200, -50), (200, 200), (150, 200),
             (150, -10), (-50, -10)])
rep, calls = count_calls([feat("L", L)], DRAWN)
check("a bbox that overlaps but a geometry that does not is still costed",
      calls == 1, f"{calls}")
check("...and correctly reports nothing", rep["overlaps"] == [], str(rep["overlaps"]))

print("\na real overlap is never skipped, however thin or however cornered")
sliver = box(99, 0, 180, 100)          # a 1 x 100 strip inside the outline's edge
rep = T.overlap_report([feat("S", sliver)], "name", DRAWN)
check("a sliver overlap larger than min_area is reported",
      [h["name"] for h in rep["overlaps"]] == ["S"], str(rep["overlaps"]))
corner = box(99.9, 99.9, 200, 200)     # 0.01 x 0.01 of overlap -- below min_area
rep = T.overlap_report([feat("C", corner)], "name", DRAWN)
check("...while one smaller than min_area is not, as before", rep["overlaps"] == [])
touch = box(100, 0, 200, 100)          # shares the edge, encloses no area
rep, calls = count_calls([feat("T", touch)], DRAWN)
check("a region that only touches the edge is let through the bbox test",
      calls == 1, f"{calls}")
check("...and reports nothing", rep["overlaps"] == [])

print("\nthe relationships the prompt is built from are unchanged")
A = box(0, 0, 100, 100)
for pts, want in (([[80, 80], [150, 80], [150, 150], [80, 150]], "partial"),
                  ([[40, 40], [60, 40], [60, 60], [40, 60]], "inside"),
                  ([[-50, -50], [200, -50], [200, 200], [-50, 200]], "envelops")):
    rep = T.overlap_report([feat("A", A)], "name", pts)
    got = [h["relationship"] for h in rep["overlaps"]]
    check(f"{want} is still recognised", got == [want], str(got))

print("\nidentical to the all-pairs version over a scattered field")
rng = random.Random(20261020)
field = []
for i in range(140):
    cx, cy = rng.uniform(-300, 400), rng.uniform(-300, 400)
    if i % 3 == 0:                                     # some plain boxes
        w, h = rng.uniform(10, 90), rng.uniform(10, 90)
        gm = box(cx, cy, cx + w, cy + h)
    else:                                              # some wobbly blobs
        r = rng.uniform(8, 70)
        gm = Polygon([(cx + r * (1 + rng.uniform(-0.35, 0.35)) * math.cos(t),
                       cy + r * (1 + rng.uniform(-0.35, 0.35)) * math.sin(t))
                      for t in [k * 2 * math.pi / 11 for k in range(11)]])
    if not gm.is_valid:
        gm = gm.buffer(0)
    if gm.is_empty or gm.area <= 0:
        continue
    field.append(feat(f"R{i}", gm))

agreed, worst, hit_total = 0, 0, 0
for trial in range(12):
    ox, oy = rng.uniform(-250, 350), rng.uniform(-250, 350)
    w, h = rng.uniform(40, 220), rng.uniform(40, 220)
    pts = [[ox, oy], [ox + w, oy], [ox + w, oy + h], [ox, oy + h]]
    fast, calls = count_calls(field, pts)
    slow = reference_report(field, "name", pts)
    worst = max(worst, calls)
    hit_total += len(fast["overlaps"])
    if same_report(fast, slow):
        agreed += 1
    else:
        check(f"trial {trial}: same answer as all-pairs", False,
              f"fast {fast['overlaps']} vs slow {slow['overlaps']}")
        break

check("all 12 outlines give the all-pairs answer exactly", agreed == 12,
      f"{agreed}/12 agreed, at most {worst} of {len(field)} regions costed")
# Guards the field itself: if the random outlines landed on nothing, the
# agreement above would be twelve comparisons of two empty lists.
check("...over a field the outlines genuinely overlap", hit_total > 0,
      f"{hit_total} hit(s) across the 12 trials")
check("...and the fast path really did skip most of the field", worst < len(field),
      f"{worst} of {len(field)}")

print("\n" + (f"{len(fails)} FAILED: " + "; ".join(fails) if fails else "all checks passed"))
sys.exit(1 if fails else 0)

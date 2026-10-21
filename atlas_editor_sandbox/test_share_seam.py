"""Share borders: Middle, or snap the seam onto one region's own edge.

It always split the contested ground down the middle -- every point of the gap or
overlap went to its nearest region. Often one of the two outlines is the one you
trust, and you want the other brought onto it instead of both moving.

The geometry here is deliberately arithmetic: two 100x100 boxes with a 10 px gap
between them, so the three answers are three different numbers.

      middle    A 0..105   B 105..210     10500 / 10500
      snap A    A 0..100   B 100..210     10000 / 11000
      snap B    A 0..110   B 110..210     11000 / 10000

Offline: pure topology, nothing written anywhere.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "backend"))

from shapely.geometry import box, mapping, shape   # noqa: E402

import topology as T                               # noqa: E402

fails = []


def check(label, cond, detail=""):
    print(f"  {'ok  ' if cond else 'FAIL'}  {label}{('  ' + detail) if detail else ''}")
    if not cond:
        fails.append(label)


def feat(name, geom):
    return {"type": "Feature", "properties": {"name": name},
            "geometry": mapping(geom)}


def body_of(res, name):
    gs = [shape(f["geometry"]) for f in res["features"]
          if f["properties"]["name"] == name]
    if not gs:
        return None
    return gs[0] if len(gs) == 1 else T.unary_union(gs)


def share(feats, names, snap_to=None, tol=40.0):
    return T.partition_regions(feats, "name", names, tol=tol, snap_to=snap_to)


def near(a, b, frac=0.01):
    return abs(a - b) <= max(abs(b) * frac, 5.0)


# --- a 10 px GAP ------------------------------------------------------------
GAP = [feat("A", box(0, 0, 100, 100)), feat("B", box(110, 0, 210, 100))]

print("\nMiddle -- what it has always done")
r = share(GAP, ["A", "B"])
mid_a, mid_b = body_of(r, "A"), body_of(r, "B")
print(f"       A {mid_a.area:,.0f}   B {mid_b.area:,.0f}"
      f"   seam at x={mid_a.bounds[2]:.1f}")
check("they tile -- no overlap left", T._safe_intersection(mid_a, mid_b).area < 5.0,
      f"{T._safe_intersection(mid_a, mid_b).area:.3f}")
check("...and no gap left", near(T.unary_union([mid_a, mid_b]).area, 21000),
      f"{T.unary_union([mid_a, mid_b]).area:.0f}")
# This used to be the headline failure: Middle put the seam at x=140 -- 30 px PAST
# B's own edge at 110, so B came out smaller than it started -- and swapping which
# region was clicked first moved it to x=105. The Voronoi it partitioned with was
# not a partition at all (cells containing 23 sites, pairs overlapping by 142,924
# px), the overlapping claims were settled by the cede pass that favours whoever
# was listed first, and the seam followed the click order. _nearest_partition now
# checks its own answer and falls back to a construction that cannot overlap or
# leak, so the midline is the midline.
check("Middle lands on the true midline", near(mid_a.area, 10500, 0.001)
      and near(mid_b.area, 10500, 0.001), f"A {mid_a.area:.1f}  B {mid_b.area:.1f}")
check("...at x = 105 exactly", abs(mid_a.bounds[2] - 105.0) < 0.05,
      f"x={mid_a.bounds[2]:.4f}")
rev = share(GAP, ["B", "A"])
check("...and it does not matter which region was clicked first",
      abs(body_of(rev, "A").area - mid_a.area) < 1.0,
      f"A first {mid_a.area:.1f}, B first {body_of(rev, 'A').area:.1f}")

print("\nSnap to A -- A is left alone, B comes up to its edge")
r = share(GAP, ["A", "B"], snap_to="A")
a, b = body_of(r, "A"), body_of(r, "B")
print(f"       A {a.area:,.0f}   B {b.area:,.0f}")
check("A keeps its own shape", near(a.area, 10000), f"{a.area:.0f}")
check("B takes the whole gap", near(b.area, 11000), f"{b.area:.0f}")
check("A is the one that did not move, by some margin",
      abs(a.area - 10000) < abs(a.area - 10500), f"{a.area:.0f}")
check("they still tile", T._safe_intersection(a, b).area < 5.0
      and near(T.unary_union([a, b]).area, 21000),
      f"overlap {T._safe_intersection(a, b).area:.3f}, "
      f"union {T.unary_union([a, b]).area:.0f}")
check("A's right edge is where the seam is", near(a.bounds[2], 100.0, 0.001),
      f"x={a.bounds[2]:.3f}")

print("\nSnap to B -- the mirror image")
r = share(GAP, ["A", "B"], snap_to="B")
a, b = body_of(r, "A"), body_of(r, "B")
print(f"       A {a.area:,.0f}   B {b.area:,.0f}")
check("B keeps its own shape", near(b.area, 10000), f"{b.area:.0f}")
check("A takes the whole gap", near(a.area, 11000), f"{a.area:.0f}")
check("B's left edge is where the seam is", near(b.bounds[0], 110.0, 0.001),
      f"x={b.bounds[0]:.3f}")

# --- a 10 px OVERLAP --------------------------------------------------------
OVER = [feat("A", box(0, 0, 110, 100)), feat("B", box(100, 0, 210, 100))]

print("\nan OVERLAP, not a gap: snapping gives the contested strip back whole")
r = share(OVER, ["A", "B"], snap_to="A")
a, b = body_of(r, "A"), body_of(r, "B")
print(f"       A {a.area:,.0f}   B {b.area:,.0f}")
check("A keeps all of it, including the strip", near(a.area, 11000), f"{a.area:.0f}")
check("B gives the strip up", near(b.area, 10000), f"{b.area:.0f}")
check("no overlap survives", T._safe_intersection(a, b).area < 5.0,
      f"{T._safe_intersection(a, b).area:.3f}")

print("\nthree picks: the snapped one is untouched, the rest split what is left")
THREE = [feat("A", box(0, 0, 100, 100)),
         feat("B", box(110, 0, 210, 100)),
         feat("C", box(220, 0, 320, 100))]
r = share(THREE, ["A", "B", "C"], snap_to="B")
a, b, c = body_of(r, "A"), body_of(r, "B"), body_of(r, "C")
print(f"       A {a.area:,.0f}   B {b.area:,.0f}   C {c.area:,.0f}")
check("B is the one left alone", near(b.area, 10000), f"{b.area:.0f}")
check("A grows to B's left edge", near(a.area, 11000), f"{a.area:.0f}")
check("C grows to B's right edge", near(c.area, 11000), f"{c.area:.0f}")
check("all three tile with no overlap",
      T._safe_intersection(a, b).area < 5.0 and T._safe_intersection(b, c).area < 5.0
      and T._safe_intersection(a, c).area < 5.0)

print("\nguards")
# Middle is still the default, so every existing caller behaves as before.
r1 = share(GAP, ["A", "B"])
r2 = share(GAP, ["A", "B"], snap_to=None)
check("snap_to=None is the old behaviour exactly",
      near(body_of(r1, "A").area, body_of(r2, "A").area, 0.0001))
# A name that is not one of the picks is ignored rather than throwing.
r3 = share(GAP, ["A", "B"], snap_to="NOPE")
check("an unknown name falls back to the middle, exactly",
      near(body_of(r3, "A").area, mid_a.area, 0.0001),
      f"{body_of(r3, 'A').area:.0f} vs {mid_a.area:.0f}")
# Snapping to a region that swallows the other has no answer, and says so.
INSIDE = [feat("A", box(0, 0, 100, 100)), feat("B", box(10, 10, 20, 20))]
try:
    share(INSIDE, ["A", "B"], snap_to="A")
    check("snapping to a region that contains the other is refused", False,
          "it was allowed")
except ValueError as e:
    check("snapping to a region that contains the other is refused", True, str(e)[:52])

print("\nthe three answers really are three different places")
sa = body_of(share(GAP, ["A", "B"], snap_to="A"), "A").area
sb = body_of(share(GAP, ["A", "B"], snap_to="B"), "A").area
check("Middle, snap-to-A and snap-to-B all put A somewhere different",
      len({round(mid_a.area), round(sa), round(sb)}) == 3,
      f"middle {mid_a.area:.0f}, snapA {sa:.0f}, snapB {sb:.0f}")
check("...and the snapped pair is the exact one", near(sa, 10000, 0.0005)
      and near(sb, 11000, 0.0005), f"snapA {sa:.1f}, snapB {sb:.1f}")

print("\n" + (f"{len(fails)} FAILED: " + "; ".join(fails) if fails else "all checks passed"))
sys.exit(1 if fails else 0)

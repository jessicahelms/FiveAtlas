"""Merging must not leave a needle -- the "line going into the region".

Unioning outlines that nearly coincide (which is how these files are drawn)
leaves a hairline slot or a zero-width spur in the result. The geometry is valid,
so analysis downstream is unaffected, but Xenium Explorer refuses to load it and
the repair was manual: drag those vertices out of the polygon and delete them.

`merge_regions` had `clean_geom` to hand but called it only inside the
seam-sealing branch -- skipped whenever the union already came back as one piece,
which is when needles are commonest -- and even there with `smooth=False`, the
flag that switches spike shaving OFF. So `despike` is a separate surgical pass:
it deletes only vertices that enclose no area, and refuses its own result if the
area moved, so it cannot erode a region that is legitimately thin.

The checks below measure the HAIRLINE GAP -- the smallest distance between two
non-adjacent edges of a ring -- rather than looking for the vertex pattern the
implementation happens to search for. A needle is exactly a ring doubling back
within a hair of itself, however its vertices are arranged, and the fix only
works because two steps compose (near-duplicate points collapse a thin slot into
a spur, then the spur unwinds). Measuring the gap tests the outcome, not either
step.

Entirely offline and synthetic -- no backend, no dataset, no network imports.
"""
import math
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "backend"))

from shapely.geometry import LineString, Polygon, box, mapping, shape   # noqa: E402

import topology as T                                                    # noqa: E402

fails = []


def check(label, cond, detail=""):
    print(f"  {'ok  ' if cond else 'FAIL'}  {label}{('  ' + detail) if detail else ''}")
    if not cond:
        fails.append(label)


def feat(name, geom):
    return {"type": "Feature", "properties": {"name": name},
            "geometry": mapping(geom)}


def hairline_gap(geom):
    """Smallest distance between two NON-ADJACENT edges of any ring.

    A clean polygon's edges are far apart; a needle or hairline slot is a ring
    running back alongside itself, so this collapses to the slot's width.
    Adjacent edges share a vertex and are skipped.
    """
    best = math.inf
    for p in T._polys(geom):
        for ring in [p.exterior, *p.interiors]:
            pts = list(ring.coords)
            if pts[0] == pts[-1]:
                pts = pts[:-1]
            n = len(pts)
            if n < 5:
                continue
            segs = [LineString([pts[i], pts[(i + 1) % n]]) for i in range(n)]
            for i in range(n):
                for j in range(i + 1, n):
                    if j == i + 1 or (i == 0 and j == n - 1):
                        continue            # share a vertex
                    best = min(best, segs[i].distance(segs[j]))
    return best


def nverts(geom):
    return sum(len(p.exterior.coords) + sum(len(r.coords) for r in p.interiors)
               for p in T._polys(geom))


# A square with a hairline slot cut in from the top edge: 0.002 px wide, 50 px
# deep. Valid, simple, and exactly the artifact that had to be cleaned by hand.
SLOTTED = Polygon([(0, 0), (100, 0), (100, 100),
                   (50.001, 100), (50.001, 50), (49.999, 50), (49.999, 100),
                   (0, 100)])

print("\nthe slot is really there to begin with")
check("the test shape is valid, so this is not an invalid-geometry bug",
      SLOTTED.is_valid, f"area {SLOTTED.area:.4f}")
check("...and it has a hairline", hairline_gap(SLOTTED) < 0.01,
      f"gap {hairline_gap(SLOTTED):.4f}")

print("\ndespike removes it without reshaping the region")
d = T.despike(SLOTTED)
check("the hairline is gone", hairline_gap(d) > 1.0, f"gap {hairline_gap(d):.1f}")
check("the area is unchanged", abs(d.area - SLOTTED.area) < 1.0,
      f"{SLOTTED.area:.4f} -> {d.area:.4f}")
check("it is valid and a single polygon", d.is_valid and len(T._polys(d)) == 1)
check("and it carries fewer vertices", nverts(d) < nverts(SLOTTED),
      f"{nverts(SLOTTED)} -> {nverts(d)}")

print("\n...and it cannot erode a region that is legitimately thin")
# clean_geom's morphological open is why this is a separate pass: a 2 px strip is
# real anatomy in these files and has to come through untouched.
THIN = box(0, 0, 2, 500)
t = T.despike(THIN)
check("a 2 x 500 px strip survives whole", abs(t.area - THIN.area) < 1e-6,
      f"{THIN.area:.1f} -> {t.area:.1f}")
check("...still 2 px wide", abs((t.bounds[2] - t.bounds[0]) - 2) < 1e-6,
      f"{t.bounds[2] - t.bounds[0]:.3f}")

print("\na hole is still a hole")
RING = Polygon([(0, 0), (100, 0), (100, 100), (0, 100)],
               [[(40, 40), (60, 40), (60, 60), (40, 60)]])
r = T.despike(RING)
check("the interior ring is kept", len(T._polys(r)[0].interiors) == 1)
check("...and the area with it", abs(r.area - RING.area) < 1e-6,
      f"{RING.area:.1f} -> {r.area:.1f}")

print("\nmerge_regions de-spikes on the ordinary path, not only when it seals")
# A and B share the edge x=100 exactly, so the union comes back as ONE piece and
# the seam-sealing branch -- the only place clean_geom used to be called -- never
# runs. A carries the slot, so an un-de-spiked merge hands it straight through.
A = SLOTTED
B = box(100, 0, 200, 100)
res = T.merge_regions([feat("A", A), feat("B", B)], "name", ["A", "B"])
merged = [f for f in res["features"] if f["properties"]["name"] == res["name"]]
check("the two regions became one feature", len(merged) == 1, str(len(merged)))
mg = shape(merged[0]["geometry"])
check("the union is a single polygon, so sealing was skipped",
      res["parts"] == 1 and res["sealed"] == 0.0,
      f"parts {res['parts']}, sealed {res['sealed']}")
check("the merged region has NO hairline", hairline_gap(mg) > 1.0,
      f"gap {hairline_gap(mg):.1f}")
check("...and still covers both bodies", abs(mg.area - (A.area + B.area)) < 1.0,
      f"{mg.area:.3f} vs {A.area + B.area:.3f}")

print("\nmerging still does what it did")
C = box(0, 0, 100, 100)
D = box(100, 0, 200, 100)
res = T.merge_regions([feat("C", C), feat("D", D)], "name", ["C", "D"], new_name="CD")
mg = shape([f for f in res["features"] if f["properties"]["name"] == "CD"][0]["geometry"])
check("a clean pair merges to one rectangle",
      res["name"] == "CD" and len(T._polys(mg)) == 1 and abs(mg.area - 20000) < 1.0,
      f"area {mg.area:.1f}")
check("...and the inputs are gone from the list",
      not any(f["properties"]["name"] in ("C", "D") for f in res["features"]))
# Bodies that genuinely do not touch must stay apart: merging a region with a
# detached islet must not invent tissue between them.
E = box(0, 0, 100, 100)
F = box(5000, 0, 5100, 100)
res = T.merge_regions([feat("E", E), feat("F", F)], "name", ["E", "F"], new_name="EF")
mg = shape([f for f in res["features"] if f["properties"]["name"] == "EF"][0]["geometry"])
check("detached bodies stay a MultiPolygon", len(T._polys(mg)) == 2,
      f"{len(T._polys(mg))} part(s)")
check("...with nothing invented between them", abs(mg.area - 20000) < 1.0,
      f"area {mg.area:.1f}")

print("\n" + (f"{len(fails)} FAILED: " + "; ".join(fails) if fails else "all checks passed"))
sys.exit(1 if fails else 0)

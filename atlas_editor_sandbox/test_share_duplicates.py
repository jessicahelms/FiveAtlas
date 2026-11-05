"""A region stored as two (near-)identical features is ONE region to Share
borders and to a border drag, not two lobes.

XETG keeps every coarse region twice. Shared out as lobes, the two copies tied
on every piece, the first took the lot, the second starved, and the nearest-point
split between two identical generators shredded the body: Share borders left lft
in 222 pieces (160 -> 3055 points) and a drag on ISO/dft made 253,949 points.

Offline, synthetic shapes: the file with copies must give exactly what the same
file without them gives, written to every copy. Set ATLAS_XETG to a copy of the
real XETG regions to also check the real pairs (read-only).
"""
import copy
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "backend"))

from shapely.geometry import Polygon, shape, mapping            # noqa: E402
import topology as T                                            # noqa: E402

fails = []


def check(label, cond, detail=""):
    print(f"  {'ok  ' if cond else 'FAIL'}  {label}{('  ' + str(detail)) if detail != '' else ''}")
    if not cond:
        fails.append(label)


def dense_rect(x0, y0, x1, y1, step=5.0):
    def edge(a, b):
        n = int(round(max(abs(b[0] - a[0]), abs(b[1] - a[1])) / step))
        return [(a[0] + (b[0] - a[0]) * i / n, a[1] + (b[1] - a[1]) * i / n) for i in range(n)]
    return Polygon(edge((x0, y0), (x1, y0)) + edge((x1, y0), (x1, y1))
                   + edge((x1, y1), (x0, y1)) + edge((x0, y1), (x0, y0)))


def feat(name, geom):
    return {"type": "Feature", "properties": {"name": name}, "geometry": mapping(geom)}


def nudged(g, d):
    """`g` with one vertex moved by `d` px: a near-exact copy."""
    cs = list(g.exterior.coords)[:-1]
    cs[3] = (cs[3][0], cs[3][1] + d)
    return Polygon(cs)


A = dense_rect(0, 0, 400, 300)
B = dense_rect(406, 0, 800, 300)          # a 6 px gap to Share across
C = dense_rect(0, 306, 800, 500)          # a neighbour, stored twice as well
single = [feat("A", A), feat("B", B), feat("C", C)]
doubled = [feat("A", A), feat("B", B), feat("C", C),
           feat("A", A), feat("B", nudged(B, 0.01)), feat("C", C)]


def stats(fs, name):
    ks = T._index_all(fs, "name")[name]
    gs = [T._geom_at(fs, k) for k in ks]
    return gs, [len(T._polys(g)) for g in gs], [T._vertex_count(g) for g in gs]


def same_as_single(label, got, want, names):
    for n in names:
        gs, pieces, pts = stats(got, n)
        g1, p1, n1 = stats(want, n)
        check(f"{label}: {n}'s copies stay one piece each", pieces == [p1[0]] * len(gs), pieces)
        check(f"{label}: {n}'s copies are written the same",
              all(g.equals_exact(gs[0], 1e-9) for g in gs))
        check(f"{label}: {n} matches the file without copies (points)",
              abs(pts[0] - n1[0]) <= 2, f"{pts[0]} vs {n1[0]}")
        check(f"{label}: {n} matches the file without copies (ground)",
              gs[0].symmetric_difference(g1[0]).area <= 1.0,
              round(gs[0].symmetric_difference(g1[0]).area, 3))


print("copies are recognised")
olds = [(k, T._geom_at(doubled, k)) for k in range(len(doubled))]
check("exact and near-exact copies, nothing else",
      T._copies(olds) == {3: 0, 4: 1, 5: 2}, T._copies(olds))
lobe = dense_rect(0, 0, 400, 290)          # 3% smaller: a second version, not a copy
check("a feature that differs by more than 0.01% is not a copy",
      T._copies([(0, A), (1, lobe)]) == {})
check("_body of an exact copy is the outline itself, vertex for vertex",
      T._body(doubled, [0, 3]).equals_exact(A, 0))

print("\nShare borders")
for snap in (None, "A"):
    tag = f"share ({'snap to A' if snap else 'middle'})"
    r1 = T.partition_regions(copy.deepcopy(single), "name", ["A", "B"], tol=40.0, snap_to=snap)
    r2 = T.partition_regions(copy.deepcopy(doubled), "name", ["A", "B"], tol=40.0, snap_to=snap)
    same_as_single(tag, r2["features"], r1["features"], ["A", "B"])
    check(f"{tag}: the neighbour is untouched",
          all(shape(r2["features"][k]["geometry"]).equals(C) for k in (2, 5)))

print("\nborder drag")
s1 = T.partition_regions(copy.deepcopy(single), "name", ["A", "B"], tol=40.0)["features"]
s2 = T.partition_regions(copy.deepcopy(doubled), "name", ["A", "B"], tol=40.0)["features"]
arc = max(T.border_between(s1, "name", "A", "B", 4.0), key=len)
new = [list(p) for p in arc]
k = len(new) // 2
new[k] = [new[k][0] + 30.0, new[k][1]]
m1 = T.move_border(copy.deepcopy(s1), "name", "A", "B", new, drag_start=arc)
m2 = T.move_border(copy.deepcopy(s2), "name", "A", "B", new, drag_start=arc)
same_as_single("drag", m2, m1, ["A", "B"])
gA, gB = T._body(m2, [0, 3]), T._body(m2, [1, 4])
check("drag: no overlap between the pair", T._safe_intersection(gA, gB).area <= 1e-6)

XETG = os.environ.get("ATLAS_XETG")
if XETG and os.path.exists(XETG):
    print("\nreal XETG pairs (read-only)")
    F = json.load(open(XETG, encoding="utf-8"))["features"]
    for a, b in [("TH", "lft"), ("OLF", "CTXsp"), ("ISO", "dft"), ("HPF", "dft")]:
        out = T.partition_regions(copy.deepcopy(F), "name", [a, b], tol=40.0)["features"]
        for n in (a, b):
            _, pieces, pts = stats(out, n)
            check(f"{a}+{b}: {n} stays one piece per copy", max(pieces) == 1, pieces)
            check(f"{a}+{b}: {n} stays under 400 points per copy", max(pts) < 400, pts)
else:
    print("\n(ATLAS_XETG not set -- real XETG check skipped)")

print()
if fails:
    print(f"{len(fails)} FAILED")
    sys.exit(1)
print("all passed")

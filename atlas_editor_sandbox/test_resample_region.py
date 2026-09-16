"""Resample points for ONE region (Edit points): even spacing all the way round,
without opening a gap along any border it shares.

Offline topology on synthetic shapes whose right answer is exact, a read-only
quality check on the real DNMT3A regions (skipped if S: is not connected), and
the route against the isolated backend (:8060).
"""
import json
import os
import sys
import urllib.error
import urllib.request

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "backend"))

from shapely.geometry import Polygon, Point, shape, mapping   # noqa: E402
from shapely.ops import unary_union                           # noqa: E402
import topology as T                                          # noqa: E402

BASE = os.environ.get("ATLAS_TEST_BASE", "http://127.0.0.1:8060")
if ":8050" in BASE or ":8000" in BASE:
    raise SystemExit("refusing to run against the live backend -- see the docstring")

fails = []


def check(label, cond, detail=""):
    print(f"  {'ok  ' if cond else 'FAIL'}  {label}{('  ' + str(detail)) if detail != '' else ''}")
    if not cond:
        fails.append(label)


def dense_rect(x0, y0, x1, y1, step=1.0):
    """A rectangle with a point every `step` px along every edge -- the kind of
    over-dense outline resampling exists to thin."""
    def edge(a, b):
        n = int(round(max(abs(b[0] - a[0]), abs(b[1] - a[1])) / step))
        return [(a[0] + (b[0] - a[0]) * i / n, a[1] + (b[1] - a[1]) * i / n) for i in range(n)]
    ring = (edge((x0, y0), (x1, y0)) + edge((x1, y0), (x1, y1))
            + edge((x1, y1), (x0, y1)) + edge((x0, y1), (x0, y0)))
    return Polygon(ring)


def feat(name, geom):
    return {"type": "Feature", "properties": {"name": name}, "geometry": mapping(geom)}


def geom(fs, name):
    return unary_union([shape(f["geometry"]) for f in fs if f["properties"]["name"] == name])


def verts(g):
    out = set()
    polys = [g] if g.geom_type == "Polygon" else list(g.geoms)
    for p in polys:
        out.update((round(x, 6), round(y, 6)) for x, y in p.exterior.coords)
    return out


def nverts(g):
    polys = [g] if g.geom_type == "Polygon" else list(g.geoms)
    return sum(len(p.exterior.coords) - 1 for p in polys)


# ---------------------------------------------------------------- two neighbours
print("\ntwo regions sharing a border")
A, B = dense_rect(0, 0, 100, 100), dense_rect(100, 0, 200, 100)
res = T.resample_region([feat("A", A), feat("B", B)], "name", "A", tol=20)
gA, gB = geom(res["features"], "A"), geom(res["features"], "B")
check("A's points are thinned", nverts(gA) < nverts(A) / 5, f"{nverts(A)} -> {nverts(gA)}")
shared_A = {v for v in verts(gA) if abs(v[0] - 100) < 1e-6}
shared_B = {v for v in verts(gB) if abs(v[0] - 100) < 1e-6}
check("B carries EXACTLY A's new points along the shared border", shared_A == shared_B,
      f"A {len(shared_A)} / B {len(shared_B)}")
check("B's other sides are untouched", {v for v in verts(gB) if v[0] > 100.5} == {v for v in verts(B) if v[0] > 100.5})
check("no overlap opened", gA.intersection(gB).area < 1e-6, f"{gA.intersection(gB).area:.6f}")
check("no gap opened: the two still close into one piece",
      unary_union([gA, gB]).geom_type == "Polygon" and not unary_union([gA, gB]).interiors)
check("areas unchanged", abs(gA.area - A.area) < 1e-6 and abs(gB.area - B.area) < 1e-6)
names = [c["region"] for c in res["counts"]]
check("the neighbour is reported as changed too", "B" in names, names)

# ---------------------------------------------------------------- three meet at a point
print("\na junction where three regions meet")
C = dense_rect(0, 100, 200, 200)
res = T.resample_region([feat("A", A), feat("B", B), feat("C", C)], "name", "A", tol=20)
gA, gB, gC = (geom(res["features"], n) for n in "ABC")
J = (100.0, 100.0)
check("the junction point is still a point of all three",
      all(J in verts(g) for g in (gA, gB, gC)))
check("still no gap and no overlap anywhere",
      abs(unary_union([gA, gB, gC]).area - (gA.area + gB.area + gC.area)) < 1e-6
      and not unary_union([gA, gB, gC]).interiors)

# ---------------------------------------------------------------- the coast with hemi
print("\na coastal region: its edge on the section outline stays on it")
HEMI = dense_rect(0, 0, 300, 300)
R = dense_rect(0, 100, 100, 200)                 # left side lies ON hemi's outline
res = T.resample_region([feat("hemi", HEMI), feat("R", R)], "name", "R", tol=20)
gH, gR = geom(res["features"], "hemi"), geom(res["features"], "R")
coast_R = {v for v in verts(gR) if abs(v[0]) < 1e-6}
check("every coastal point of R is a point of hemi's outline",
      coast_R and coast_R <= verts(gH), f"{len(coast_R)} coastal points")
check("no point of R stands off the outline along the coast",
      all(Point(v).distance(gH.exterior) < 1e-6 for v in coast_R))
check("hemi still covers R", gH.buffer(1e-6).covers(gR))
check("hemi's area unchanged", abs(gH.area - HEMI.area) < 1e-6)
check("R is thinned", nverts(gR) < nverts(R) / 5, f"{nverts(R)} -> {nverts(gR)}")

# ---------------------------------------------------------------- an island
print("\na region touching nothing")
circle = Point(500, 500).buffer(80, quad_segs=128)
res = T.resample_region([feat("I", circle)], "name", "I", tol=25)
gI = geom(res["features"], "I")
check("thinned", nverts(gI) < nverts(circle), f"{nverts(circle)} -> {nverts(gI)}")
check("area drift small", abs(gI.area - circle.area) / circle.area < 0.02,
      f"{(gI.area - circle.area) / circle.area * 100:.2f}%")

print("\ntoo coarse for the shape: left exactly as it was")
tiny = Point(0, 0).buffer(10, quad_segs=32)
res = T.resample_region([feat("t", tiny)], "name", "t", tol=500)
check("unchanged", geom(res["features"], "t").equals_exact(tiny, 1e-9))
check("and reported as kept", res["kept"] == ["t"], res["kept"])

# ---------------------------------------------------------------- real data, read-only
print("\nreal regions (read-only, in memory)")
SRC = r"S:\Phys\FIV911 Atlas\RealDS\AllenBA 3D\Images from Box\DNMT3A_002_27_38_Het_F\merged.regions.geojson"
if not os.path.exists(SRC):
    print("  (skipped: S: is not connected)")
else:
    feats = json.load(open(SRC, encoding="utf-8"))["features"]

    def seam(fs, a, b):
        """Gap + overlap between two regions where they meet: the sliver a
        2 px closing adds that touches both, plus their overlap."""
        ga, gb = geom(fs, a), geom(fs, b)
        both = unary_union([ga, gb])
        closed = both.buffer(2, join_style=2).buffer(-2, join_style=2)
        gap = closed.difference(both)
        slivers = [p for p in (gap.geoms if hasattr(gap, "geoms") else [gap])
                   if not p.is_empty and p.distance(ga) < 0.5 and p.distance(gb) < 0.5]
        return sum(p.area for p in slivers) + ga.intersection(gb).area

    def uncovered(fs, inner, outer):
        """How much of `inner` its container does NOT cover."""
        return geom(fs, inner).difference(geom(fs, outer)).area

    for target in ("SSp", "HY", "CP"):
        res = T.resample_region(feats, "name", target, tol=150)
        me = [c for c in res["counts"] if c["region"] == target][0]
        nbrs = [c["region"] for c in res["counts"] if c.get("shared")]
        worst = 0.0
        for nb in nbrs:
            t0, n0 = geom(feats, target), geom(feats, nb)
            if t0.area and t0.intersection(n0).area / t0.area >= 0.9:
                # a container (hemi around HY): "overlap" is the whole inner
                # region, so the seam measure means nothing -- what must hold is
                # that the container still covers it, no worse than before
                worst = max(worst, uncovered(res["features"], target, nb)
                            - uncovered(feats, target, nb))
            else:
                worst = max(worst, seam(res["features"], target, nb) - seam(feats, target, nb))
        # even spacing can ADD points to a sparse outline (30 points on long
        # straight runs, spaced every 150 px) -- so the check is the shape, not
        # the count
        check(f"{target}: {me['before']} -> {me['after']} points, area drift "
              f"{me['areaDrift']}%, neighbours re-pointed {nbrs}",
              abs(me["areaDrift"]) < 1.0)
        check(f"{target}: no new gap or overlap along any border (worst change "
              f"{worst:.1f} px²)", worst < 5.0)

# ---------------------------------------------------------------- the route
print("\nthe route")


def post(path, body):
    req = urllib.request.Request(BASE + path, data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=300) as r:
            return r.status, json.load(r)
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode()[:300]


fc = {"type": "FeatureCollection", "features": [feat("A", A), feat("B", B)]}
code, res = post("/api/datasets/DNMT3A_002_27_38_Het_F/regions/resample",
                 {"regions": ["A"], "fc": fc, "tol": 20})
check("one region is accepted", code == 200, res if code != 200 else "")
if code == 200:
    check("handles are A's own points, before -> after",
          res["handles"]["before"] == nverts(A) and res["handles"]["after"] < nverts(A) / 5,
          res["handles"])
    check("the neighbour is listed as re-pointed",
          any(c["region"] == "B" and c.get("shared") for c in res["counts"]), res["counts"])
code, res = post("/api/datasets/DNMT3A_002_27_38_Het_F/regions/resample",
                 {"regions": ["A", "B"], "fc": fc, "tol": 20})
check("two regions still mean the shared-border resample", code == 200 and "arcPoints" in res)

print("\n" + (f"{len(fails)} FAILED: " + "; ".join(fails) if fails else "all checks passed"))
sys.exit(1 if fails else 0)

"""Deleting a point: from both regions on a shared border, the rest of that
stretch re-spaced to close the gap, and never a border opened.

Offline topology on synthetic shapes with exact answers, then the route against
the isolated backend (:8060).
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
    def edge(a, b):
        n = int(round(max(abs(b[0] - a[0]), abs(b[1] - a[1])) / step))
        return [(a[0] + (b[0] - a[0]) * i / n, a[1] + (b[1] - a[1]) * i / n) for i in range(n)]
    return Polygon(edge((x0, y0), (x1, y0)) + edge((x1, y0), (x1, y1))
                   + edge((x1, y1), (x0, y1)) + edge((x0, y1), (x0, y0)))


def feat(name, geom):
    return {"type": "Feature", "properties": {"name": name}, "geometry": mapping(geom)}


def geom(fs, name):
    return unary_union([shape(f["geometry"]) for f in fs if f["properties"]["name"] == name])


def verts(g):
    return {(round(x, 6), round(y, 6)) for x, y in g.exterior.coords}


def refused(fn, needle):
    try:
        fn()
    except ValueError as e:
        return needle in str(e), str(e)[:90]
    return False, "it was allowed"


def spacing_spread(pts):
    d = [Point(pts[i]).distance(Point(pts[i + 1])) for i in range(len(pts) - 1)]
    return max(d) - min(d)


A, B = dense_rect(0, 0, 100, 100), dense_rect(100, 0, 200, 100)

print("\na point on a SHARED border goes from both regions")
res = T.delete_point([feat("A", A), feat("B", B)], "name", "A", (100, 50))
gA, gB = geom(res["features"], "A"), geom(res["features"], "B")
check("removed from both", res["removedFrom"] == ["A", "B"], res["removedFrom"])
check("neither still has it", (100.0, 50.0) not in verts(gA) and (100.0, 50.0) not in verts(gB))
edge_A = sorted(v for v in verts(gA) if abs(v[0] - 100) < 1e-6)
edge_B = sorted(v for v in verts(gB) if abs(v[0] - 100) < 1e-6)
check("the two still carry exactly the same points along it", edge_A == edge_B,
      f"A {len(edge_A)} / B {len(edge_B)}")
check("one point fewer on that border", len(edge_A) == 100, len(edge_A))
check("and they are evenly spaced again (no long gap where it was)",
      spacing_spread(edge_A) < 1e-4, f"spread {spacing_spread(edge_A):.6f}")
check("the ends of the border did not move", (100.0, 0.0) in edge_A and (100.0, 100.0) in edge_A)
check("no gap, no overlap", gA.intersection(gB).area < 1e-6
      and unary_union([gA, gB]).geom_type == "Polygon" and not unary_union([gA, gB]).interiors)
check("reported as re-spaced", res["respaced"] is True)

print("\nwithout re-spacing, only that point goes")
res = T.delete_point([feat("A", A), feat("B", B)], "name", "A", (100, 50), respace=False)
gA, gB = geom(res["features"], "A"), geom(res["features"], "B")
check("every other point stayed exactly where it was",
      verts(gA) == verts(A) - {(100.0, 50.0)} and verts(gB) == verts(B) - {(100.0, 50.0)})

print("\na point on FREE ground goes from this region only")
res = T.delete_point([feat("A", A), feat("B", B)], "name", "A", (0, 50))
gA, gB = geom(res["features"], "A"), geom(res["features"], "B")
check("removed from A only", res["removedFrom"] == ["A"], res["removedFrom"])
check("B is untouched", verts(gB) == verts(B))
check("A's shared border is untouched",
      sorted(v for v in verts(gA) if abs(v[0] - 100) < 1e-6)
      == sorted(v for v in verts(A) if abs(v[0] - 100) < 1e-6))
check("A's area barely moves", abs(gA.area - A.area) / A.area < 0.01,
      f"{(gA.area - A.area) / A.area * 100:.3f}%")

print("\nwhat is refused")
C = dense_rect(0, 100, 200, 200)
ok, why = refused(lambda: T.delete_point([feat("A", A), feat("B", B), feat("C", C)],
                                         "name", "A", (100, 100)), "meets")
check("a point where three regions meet", ok, why)
ok, why = refused(lambda: T.delete_point([feat("A", A), feat("B", B)], "name", "A", (100, 0)),
                  "ends")
check("the end of a shared border", ok, why)
tri = Polygon([(0, 0), (10, 0), (5, 8)])
ok, why = refused(lambda: T.delete_point([feat("t", tri)], "name", "t", (10, 0)), "at least 3")
check("a triangle's point", ok, why)
ok, why = refused(lambda: T.delete_point([feat("A", A)], "name", "A", (50, 50)), "no point")
check("a click nowhere near a point", ok, why)

print("\nthe coast: a point on hemi's outline goes from hemi too")
HEMI = dense_rect(0, 0, 300, 300)
R = dense_rect(0, 100, 100, 200)
res = T.delete_point([feat("hemi", HEMI), feat("R", R)], "name", "R", (0, 150))
gH, gR = geom(res["features"], "hemi"), geom(res["features"], "R")
check("removed from R and hemi", res["removedFrom"] == ["R", "hemi"], res["removedFrom"])
coast = {v for v in verts(gR) if abs(v[0]) < 1e-6}
check("every coastal point of R is still a point of hemi", coast <= verts(gH), len(coast))
check("hemi still covers R", gH.buffer(1e-6).covers(gR))

print("\nan island re-spaces all the way round")
circle = Point(0, 0).buffer(50, quad_segs=32)
first = list(circle.exterior.coords)[5]
res = T.delete_point([feat("I", circle)], "name", "I", first)
gI = geom(res["features"], "I")
ring = list(gI.exterior.coords)
check("one point fewer", len(ring) == len(circle.exterior.coords) - 1,
      f"{len(circle.exterior.coords)} -> {len(ring)}")
check("evenly spaced", spacing_spread(ring) < 0.05, f"spread {spacing_spread(ring):.4f}")

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
DP = "/api/datasets/DNMT3A_002_27_38_Het_F/regions/delete-point"
code, res = post(DP, {"fc": fc, "region": "A", "point": [100, 50]})
check("deletes from both, over the wire",
      code == 200 and res.get("removedFrom") == ["A", "B"] and res.get("respaced") is True,
      res if code != 200 else "")
code, res = post(DP, {"fc": fc, "region": "A", "point": [100, 0]})
check("a refusal is a 422 with the reason", code == 422 and "ends" in str(res), code)

print("\n" + (f"{len(fails)} FAILED: " + "; ".join(fails) if fails else "all checks passed"))
sys.exit(1 if fails else 0)

"""Audit Tier 2 route fixes, over HTTP against the ISOLATED test backend (:8060,
its own ATLAS_WORKDIR -- never the live app):

  * Fix a gap with hemi switched off no longer fills background OUTSIDE the
    section (the route held hemi out, which removed the section's bound: one
    click by the tissue edge handed 18.5 M px2 outside hemi to seven regions).
  * Draw with regions switched off leaves them untouched and does not ask
    about them (it carved them like any other region).
  * Orientation: Restore original, and reading a region file, while the image
    is rotated come back in the ROTATED frame, tagged (they came back as
    imaged, 24,745 px off on the real set, and untagged).

The rotation is always put back to as-imaged at the end.
"""
import json
import math
import os
import sys
import urllib.error
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "backend"))
from shapely.geometry import shape                             # noqa: E402
from shapely.ops import unary_union                            # noqa: E402

BASE = os.environ.get("ATLAS_TEST_BASE", "http://127.0.0.1:8060")
if ":8050" in BASE or ":8000" in BASE or ":8070" in BASE:
    raise SystemExit("refusing to run against a live backend -- use the isolated :8060")
DS = "DNMT3A_002_27_38_Het_F"
ORIG = r"C:\Users\FIVE\source\repos\FiveAtlas_handoff_fixtures\test_backend\DNMT3A_002_27_38_Het_F\regions.geojson"
REAL = r"C:\Users\FIVE\source\repos\FiveAtlas_handoff_fixtures\regions_20261021_112134.geojson"

fails = []


def check(label, cond, detail=""):
    print(f"  {'ok  ' if cond else 'FAIL'}  {label}{('  ' + str(detail)) if detail != '' else ''}")
    if not cond:
        fails.append(label)


def call(method, path, body=None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(BASE + path, data=data, method=method,
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=300) as r:
            return r.status, json.load(r)
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode()[:300]


def coords(fc):
    out = []

    def walk(c):
        if c and isinstance(c[0], (int, float)):
            out.append((float(c[0]), float(c[1])))
        else:
            for x in c:
                walk(x)
    for f in fc["features"]:
        walk(f["geometry"]["coordinates"])
    return out


def max_off(a, b):
    """largest distance between the two collections' outlines (by region name)"""
    def bodies(fc):
        d = {}
        for f in fc["features"]:
            d.setdefault(f["properties"].get("name"), []).append(shape(f["geometry"]).buffer(0))
        return {k: unary_union(v) for k, v in d.items()}
    A, B = bodies(a), bodies(b)
    return max(A[n].hausdorff_distance(B[n]) for n in A if n in B)


code, _ = call("GET", "/api/datasets")
if code != 200:
    raise SystemExit(f"no test backend on {BASE} -- start one with its own ATLAS_WORKDIR")

print("\nFix a gap with hemi switched off stays inside the section")
if os.path.exists(REAL):
    real = json.load(open(REAL, encoding="utf-8"))
    hemi = unary_union([shape(f["geometry"]).buffer(0) for f in real["features"]
                        if f["properties"]["name"] == "hemi"])
    code, res = call("POST", f"/api/datasets/{DS}/regions/dissolve-gap",
                     {"point": [20343, 28490], "fc": real, "tol": 40, "exclude": ["hemi"]})
    if code == 200:
        gap = shape(res["gap"])
        outside = gap.difference(hemi).area
        check("the click by the tissue edge fills nothing outside hemi", outside < 1.0,
              f"{outside:,.0f} px2 outside (was 18.5 M)")
    else:
        check("the click by the tissue edge is refused or stays inside hemi", code == 422, code)
else:
    print("  (real fixture not found -- skipped)")

print("\nOrientation: restore and read-file come back in the rotated frame")
code, o0 = call("GET", f"/api/datasets/{DS}/orientation")
ident = {"rot": 0, "flipH": False, "flipV": False}
if code != 200:
    check("orientation is readable", False, o0)
else:
    try:
        code, cur = call("GET", f"/api/datasets/{DS}/regions")
        start = cur
        if not (o0.get("rot", 0) == 0 and not o0.get("flipH") and not o0.get("flipV")):
            call("PUT", f"/api/datasets/{DS}/orientation", {**ident, "fc": cur})
            code, start = call("GET", f"/api/datasets/{DS}/regions")
        code, rot = call("PUT", f"/api/datasets/{DS}/orientation",
                         {"rot": 90, "flipH": False, "flipV": False, "fc": start})
        check("rotate 90 succeeds", code == 200, rot if code != 200 else "")
        if code == 200:
            code, rest = call("POST", f"/api/datasets/{DS}/regions/restore-original", {})
            check("Restore original succeeds while rotated", code == 200, rest if code != 200 else "")
            if code == 200:
                check("...tagged with the current rotation",
                      (rest.get("_orientation") or {}).get("rot") == 90, rest.get("_orientation"))
                off = max_off(rest, rot)
                check("...and in the rotated frame (within 3 px of the rotated regions)",
                      off <= 3.0, f"{off:,.1f} px off")
            code, rd = call("POST", f"/api/datasets/{DS}/regions/read-file", {"path": ORIG})
            check("reading the dataset's own file while rotated succeeds", code == 200,
                  rd if code != 200 else "")
            if code == 200:
                check("...tagged with the current rotation",
                      (rd.get("_orientation") or {}).get("rot") == 90, rd.get("_orientation"))
                off = max_off(rd, rot)
                check("...and in the rotated frame (within 3 px)", off <= 3.0, f"{off:,.1f} px off")
    finally:
        code, now = call("GET", f"/api/datasets/{DS}/regions")
        call("PUT", f"/api/datasets/{DS}/orientation", {**ident, "fc": now})
        code, o1 = call("GET", f"/api/datasets/{DS}/orientation")
        check("the rotation is put back to as-imaged", code == 200 and o1.get("rot") == 0, o1)

print("\nDraw leaves switched-off regions untouched (as Fix a gap does)")
from shapely.geometry import box, mapping                      # noqa: E402


def feat(name, geom):
    return {"type": "Feature", "properties": {"name": name}, "geometry": mapping(geom)}


def area_of(fc, name):
    return unary_union([shape(f["geometry"]) for f in fc["features"]
                        if f["properties"]["name"] == name]).area


def names_of(fc):
    return [f["properties"]["name"] for f in fc["features"]]


SYN = {"type": "FeatureCollection", "features": [
    feat("hemi", box(-100, -100, 600, 600)), feat("A", box(0, 0, 100, 100)),
    feat("B", box(100, 0, 200, 100)), feat("New region", box(300, 300, 350, 350)),
    feat("C", box(0, 200, 50, 250))]}
over_ab = [[60, 20], [140, 20], [140, 80], [60, 80]]          # 40 x 60 on each of A and B
code, res = call("POST", f"/api/datasets/{DS}/regions/add",
                 {"fc": SYN, "points": over_ab, "overlap": "top", "exclude": ["A", "New region"]})
check("draw on top with A switched off succeeds", code == 200, res if code != 200 else "")
if code == 200:
    check("...A is untouched (it was cut by 2,400 px2)", abs(area_of(res, "A") - 10000) < 1e-6,
          f"{area_of(res, 'A'):.0f}")
    check("...B still cedes its part", abs(area_of(res, "B") - 7600) < 1, f"{area_of(res, 'B'):.0f}")
    check("...A is not listed as ceding", "A" not in (res.get("ceded") or []), res.get("ceded"))
    check("...hemi is untouched", abs(area_of(res, "hemi") - 490000) < 1e-6)
    check("...the held-out 'New region' keeps its name; the new one is 'New region 2'",
          res.get("name") == "New region 2" and names_of(res).count("New region") == 1, res.get("name"))
    check("...every held-out region keeps its place in the draw order",
          names_of(res)[:4] == ["hemi", "A", "B", "New region"], names_of(res))
only_a = [[20, 20], [80, 20], [80, 80], [20, 80]]
code, res = call("POST", f"/api/datasets/{DS}/regions/add",
                 {"fc": SYN, "points": only_a, "ask": True})
check("drawn inside A with A on, it asks about A (control)",
      code == 200 and res.get("needsOverlapChoice")
      and [o["name"] for o in res["overlaps"]] == ["A"], res)
code, res = call("POST", f"/api/datasets/{DS}/regions/add",
                 {"fc": SYN, "points": only_a, "ask": True, "exclude": ["A"]})
check("with A switched off, it does not ask about A",
      code == 200 and not res.get("needsOverlapChoice"),
      res if code != 200 else res.get("overlaps", ""))
if code == 200 and not res.get("needsOverlapChoice"):
    check("...and A is untouched", abs(area_of(res, "A") - 10000) < 1e-6, f"{area_of(res, 'A'):.0f}")
code, res = call("POST", f"/api/datasets/{DS}/regions/add",
                 {"fc": SYN, "points": over_ab, "overlap": "top", "exclude": ["hemi"]})
check("with hemi switched off, a draw inside the section still works and hemi stays whole",
      code == 200 and abs(area_of(res, "hemi") - 490000) < 1e-6 and names_of(res)[0] == "hemi",
      res if code != 200 else names_of(res))
# "Show only this": everything but hemi and one region switched off. What is
# left no longer shows hemi wrapping two others, and the draw used to treat it
# as a peer -- asked about it on every draw and carved a hole on "on top".
free = [[400, 400], [500, 400], [500, 500], [400, 500]]       # 100 x 100, inside hemi only
only = ["B", "C", "New region"]
code, res = call("POST", f"/api/datasets/{DS}/regions/add",
                 {"fc": SYN, "points": free, "ask": True, "exclude": only})
check("Show only A: a draw inside the section does not ask about hemi",
      code == 200 and not res.get("needsOverlapChoice"),
      res if code != 200 else res.get("overlaps", ""))
code, res = call("POST", f"/api/datasets/{DS}/regions/add",
                 {"fc": SYN, "points": free, "overlap": "top", "exclude": only})
check("Show only A: drawing on top leaves hemi whole (it was carved by 10,000 px2)",
      code == 200 and abs(area_of(res, "hemi") - 490000) < 1e-6,
      res if code != 200 else f"{area_of(res, 'hemi'):.0f}")

print()
if fails:
    print(f"{len(fails)} FAILED")
    sys.exit(1)
print("all passed")

"""Regions stored twice, holes and lobes through Delete point, Tidy points and the
points slider (found by the v1.4.2 release review).

  * deleting a point next to a region stored twice keeps its copies identical
    (one HPF point deleted left TH's copies 14-24k px2 apart);
  * the slider's counts below the guarded fewest never empty a feature, keep
    copies identical, and say how many handles there really are -- holes too
    (HY: the slider said 72 for 96);
  * "Keep the same name" on near-exact copies gives the original outline, not
    a saw-tooth union of the two (dft 186 -> 332 points);
  * every neighbour a count step changes is listed in its counts.

Offline: the real 23-region fixture. Usage:  python test_copies_counts.py
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend"))

from shapely.geometry import mapping, shape   # noqa: E402
from shapely import affinity                  # noqa: E402

import topology as T                          # noqa: E402

fails = []


def check(label, cond, detail=""):
    print(f"  {'ok  ' if cond else 'FAIL'}  {label}{('  ' + str(detail)) if detail != '' else ''}")
    if not cond:
        fails.append(label)


real = (Path(__file__).resolve().parents[2] / "FiveAtlas_handoff_fixtures" / "test_backend"
        / "ATLAS_TEST_DS" / "regions.geojson")
if not real.exists():
    print("fixture missing -- skipped")
    sys.exit(0)
feats = json.loads(real.read_text(encoding="utf-8"))["features"]
by = {f["properties"]["name"]: f for f in feats}


def copies(fs, nm):
    gs = [shape(f["geometry"]) for f in fs if f["properties"]["name"] == nm]
    return gs


print("Delete point next to a region stored twice")
dup = feats + [json.loads(json.dumps(by["TH"]))]
hpf = shape(by["HPF"]["geometry"])
th = shape(by["TH"]["geometry"])
on = [c for c in hpf.exterior.coords[:-1] if th.boundary.distance(shape({"type": "Point", "coordinates": c})) < 0.5]
done = 0
for c in on[:4]:
    try:
        r = T.delete_point(dup, "name", "HPF", c)
    except ValueError:
        continue
    done += 1
    a, b = copies(r["features"], "TH")
    check(f"TH's two copies identical after deleting HPF point {c[0]:.0f},{c[1]:.0f}",
          a.equals_exact(b, 1e-9), round(a.symmetric_difference(b).area, 1))
check("some HPF point on TH could be deleted", done > 0)

print("\nslider counts with a region stored twice")
o = T.resample_options(dup, "name", ["TH"], "shape")
below = [x for x in o["options"] if x["tol"] < 0]
check("TH (stored twice) counted once", o["before"] == T.point_count(feats, "name", "TH"),
      (o["before"], T.point_count(feats, "name", "TH")))
bad = []
for x in below:
    r = T.resample_at(dup, "name", ["TH"], x["tol"], "shape")
    a, b = copies(r["features"], "TH")
    if a.is_empty or b.is_empty or not a.equals_exact(b, 1e-9) \
            or T.point_count(r["features"], "name", "TH") != x["points"]:
        bad.append(x["points"])
check("every count: both copies, identical, with the count it says", below and not bad, bad[:5])

print("\nslider counts with two lobes under one name")
lobe = json.loads(json.dumps(by["PAL.3"]))
lobe["properties"]["name"] = "PAL.1"
two = [f for f in feats if f["properties"]["name"] != "PAL.3"] + [lobe]
for method in ("shape", "even"):
    o = T.resample_options(two, "name", ["PAL.1"], method)
    empties = []
    for x in [x for x in o["options"] if x["tol"] < 0]:
        r = T.resample_at(two, "name", ["PAL.1"], x["tol"], method)
        empties += [x["points"] for g in copies(r["features"], "PAL.1") if g.is_empty or not g.is_valid]
    check(f"{method}: no count empties a lobe or makes it invalid", not empties, empties[:5])

print("\nholes are counted")
hy = shape(by["HY"]["geometry"])
if list(hy.interiors):
    o = T.resample_options(feats, "name", ["HY"], "shape")
    check("before = every ring's points", o["before"] == T.point_count(feats, "name", "HY"))
    wrong = []
    for x in o["options"]:
        r = T.resample_at(feats, "name", ["HY"], x["tol"], "shape")
        if T.point_count(r["features"], "name", "HY") != x["points"]:
            wrong.append(x["points"])
    check("each notch's count is the handles it really has", not wrong, wrong[:5])

print("\ncount steps list every region they change")
o = T.resample_options(feats, "name", ["TH"], "shape")
x = [x for x in o["options"] if x["tol"] < 0][0]
r = T.resample_at(feats, "name", ["TH"], x["tol"], "shape")
idx = T._index_all(feats, "name")
changed = {n for n, ks in idx.items()
           if not T._body(feats, ks).equals_exact(T._body(r["features"], ks), 1e-9)}
listed = {c["region"] for c in r["counts"]}
check("counts list every changed region", changed <= listed, sorted(changed - listed))

print("\nKeep the same name on near-exact copies")
near = json.loads(json.dumps(by["dft"]))
near["geometry"] = mapping(affinity.translate(shape(near["geometry"]), 0.02, 0.0))
res = T.resolve_duplicates(feats + [near], "name", "merge")
g = shape(next(f for f in res["features"] if f["properties"]["name"] == "dft")["geometry"])
n0 = T.point_count(feats, "name", "dft")
check("dft keeps its own outline (no saw-tooth)", T.point_count(res["features"], "name", "dft") == n0,
      (T.point_count(res["features"], "name", "dft"), n0))

print()
if fails:
    print(f"{len(fails)} FAILED")
    sys.exit(1)
print("all checks passed")

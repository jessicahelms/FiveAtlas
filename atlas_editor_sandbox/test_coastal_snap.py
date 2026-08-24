"""Share borders on a nested pair = merge the borders where they neighbour.

The hairline band between the inner's edge and the container's outline joins
the inner; nothing else moves. Offline topology + the partition route.
"""
import json
import os
import sys
import urllib.error
import urllib.request

sys.path.insert(0, r"C:\Users\FIVE\source\repos\Jess\atlas_editor\backend")

from shapely.geometry import Point, box, shape, mapping   # noqa: E402
import topology as T                                      # noqa: E402

BASE = os.environ.get("ATLAS_TEST_BASE", "http://127.0.0.1:8060")
if ":8050" in BASE or ":8000" in BASE:
    raise SystemExit("refusing to run against the live backend — see the docstring")

fails = []


def check(label, cond, detail=""):
    print(f"  {'ok  ' if cond else 'FAIL'}  {label}{('  ' + detail) if cond is False else ''}")
    if not cond:
        fails.append(label)


def feat(name, geom):
    return {"type": "Feature", "properties": {"name": name},
            "geometry": mapping(geom)}


def geom_of(res, name):
    return shape(next(f for f in res["features"]
                      if f["properties"]["name"] == name)["geometry"])


# hemi 0..200; VISp is coastal but stops 3 px short of the outline on two sides
HEMI = box(0, 0, 200, 200)
VISP = box(3, 3, 80, 80)             # gaps: x in [0,3] and y in [0,3] along the coast
C = box(3, 90, 80, 200)              # a neighbour above, running to the coast itself

print("\nthe coastal band joins the inner")
res = T.snap_to_container([feat("hemi", HEMI), feat("VISp", VISP), feat("C", C)],
                          "name", ["hemi", "VISp"], tol=40)
gV = geom_of(res, "VISp")
check("VISp now reaches the outline", gV.covers(Point(1, 40)) and gV.covers(Point(40, 1)),
      f"bounds {gV.bounds}")
# the two 3px bands plus at most `reach` (tol/2) of wrap past the corners
check("the sealed area is the coastal bands (small corner wrap allowed)",
      430 < res["sealed"] < 760, f"{res['sealed']:.0f}")
check("the band never runs farther than the reach past the inner",
      not gV.covers(Point(1, 130)))
check("hemi untouched", abs(geom_of(res, "hemi").area - 200 * 200) < 0.01)
check("the neighbour untouched", abs(geom_of(res, "C").area - 77 * 110) < 0.01)
check("shared stretches reported for the drag UI", len(res["borders"]) >= 1)

print("\nwhat must NOT be absorbed")
# a FAT unclaimed pocket right of VISp (80..190 wide) -- future territory
res2 = T.snap_to_container([feat("hemi", HEMI), feat("VISp", VISP)],
                           "name", ["hemi", "VISp"], tol=40)
gV2 = geom_of(res2, "VISp")
check("a fat unclaimed pocket is not swallowed", not gV2.covers(Point(150, 40)),
      f"area {gV2.area:.0f}")
check("...only the coastal bands were taken",
      5929 + 400 < gV2.area < 5929 + 800, f"{gV2.area:.0f}")

# a hairline corridor INLAND between VISp and C (y 80..90 is 10px -- inland,
# away from the outline except at its coastal end)
res3 = T.snap_to_container([feat("hemi", HEMI), feat("VISp", VISP), feat("C", C)],
                           "name", ["hemi", "VISp"], tol=8)
gV3 = geom_of(res3, "VISp")
check("an inland corridor toward a neighbour stays (mostly) unclaimed",
      not gV3.covers(Point(40, 86)), "")

print("\nan inner poking PAST the outline: the container grows to cover it")
POKY = box(3, 3, 80, 80).union(box(20, -12, 60, 3))    # wedge past the coast
resP = T.snap_to_container([feat("hemi", HEMI), feat("POKY", POKY)],
                           "name", ["hemi", "POKY"], tol=40)
gH, gP = geom_of(resP, "hemi"), geom_of(resP, "POKY")
check("the container now covers the protrusion", gH.covers(gP.buffer(-0.5)),
      f"covered {resP.get('covered', 0):.0f}")
check("the covered area is reported", 400 < resP.get("covered", 0) < 560,
      f"{resP.get('covered', 0):.0f}")
check("...and it converges: a second run covers nothing",
      T.snap_to_container(resP["features"], "name", ["hemi", "POKY"],
                          tol=40).get("covered", 0) < 1.0)

print("\na flush region between flush neighbours: nothing to do, twice")
# the real-file case: coastal regions shoulder to shoulder, all on the outline
D = box(20, 120, 80, 200)
E = box(0, 120, 20, 200)
F = box(80, 120, 110, 200)
sandwich = [feat("hemi", HEMI), feat("D", D), feat("E", E), feat("F", F)]
res4 = T.snap_to_container(sandwich, "name", ["hemi", "D"], tol=40)
check("nothing sealed when everything is already flush",
      res4["sealed"] < 1.0, f"sealed {res4['sealed']:.0f}")
res5 = T.snap_to_container(res4["features"], "name", ["hemi", "D"], tol=40)
check("...and again nothing on a second run", res5["sealed"] < 1.0,
      f"sealed {res5['sealed']:.2f}")

print("\nnon-nested input refuses with a sentence")
try:
    T.snap_to_container([feat("A", box(0, 0, 50, 50)), feat("B", box(60, 0, 100, 50))],
                        "name", ["A", "B"])
    check("refusal", False)
except ValueError as e:
    check("refusal", "not nested" in str(e), str(e)[:60])

print("\nthe partition route")


def post(path, body):
    req = urllib.request.Request(
        BASE + path, data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=300) as r:
            return r.status, json.load(r)
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode()[:300]


DS = "DNMT3A_002_27_38_Het_F"
fc = {"type": "FeatureCollection",
      "features": [feat("hemi", HEMI), feat("VISp", VISP), feat("C", C)]}
code, res = post(f"/api/datasets/{DS}/regions/partition",
                 {"regions": ["hemi", "VISp"], "fc": fc})
ok = code == 200 and res.get("nested") and res["nested"]["sealed"] > 0
check("Share borders on hemi + a coastal region merges where they neighbour",
      ok, str(res)[:90] if not ok else "")
if ok:
    gV = shape(next(f for f in res["features"]
                    if f["properties"]["name"] == "VISp")["geometry"])
    check("...and the region reaches the outline end to end",
          gV.covers(Point(1, 40)))

code, res = post(f"/api/datasets/{DS}/regions/partition",
                 {"regions": ["hemi", "VISp", "C"], "fc": fc})
check("three picks with a container still refuse", code == 422, f"code {code}")

print("\n" + (f"{len(fails)} FAILED: " + "; ".join(fails) if fails else "all checks passed"))
sys.exit(1 if fails else 0)

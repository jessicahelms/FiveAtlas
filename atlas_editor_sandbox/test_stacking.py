"""Stacking: "on top, underneath, or overlap?" when a drawn outline lands on
ground another region already holds -- and the draw order that follows from it.

Offline topology first, then the add route against the isolated backend
(ask-before-committing, the three answers, and the legacy call unchanged).
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
    raise SystemExit("refusing to run against the live backend -- see the docstring")

fails = []


def check(label, cond, detail=""):
    print(f"  {'ok  ' if cond else 'FAIL'}  {label}{('  ' + detail) if detail else ''}")
    if not cond:
        fails.append(label)


def feat(name, geom):
    return {"type": "Feature", "properties": {"name": name},
            "geometry": mapping(geom)}


def order_of(feats):
    return [f["properties"]["name"] for f in feats]


def area_of(feats, name):
    g = [shape(f["geometry"]) for f in feats if f["properties"]["name"] == name]
    return sum(x.area for x in g)


A = box(0, 0, 100, 100)                       # what is already on the map
PARTIAL = [[80, 80], [150, 80], [150, 150], [80, 150]]     # clips A's corner
INSIDE = [[40, 40], [60, 40], [60, 60], [40, 60]]          # sits within A
AROUND = [[-50, -50], [200, -50], [200, 200], [-50, 200]]  # swallows A whole
CLEAR = [[300, 300], [400, 300], [400, 400], [300, 400]]   # touches nothing

print("\nwhat the outline lands on")
for pts, want in ((PARTIAL, "partial"), (INSIDE, "inside"), (AROUND, "envelops")):
    rep = T.overlap_report([feat("A", A)], "name", pts)
    got = [h["relationship"] for h in rep["overlaps"]]
    check(f"{want} is recognised", got == [want], str(got))
rep = T.overlap_report([feat("A", A)], "name", CLEAR)
check("an outline over free ground reports nothing", rep["overlaps"] == [])

print("\non top -- the new region takes the ground")
r = T.add_region([feat("A", A)], "name", PARTIAL, name="N", overlap="top")
check("the new region keeps its whole shape", abs(area_of(r["features"], "N") - 4900) < 1,
      f"{area_of(r['features'], 'N'):.0f}")
check("the old region cedes the overlap", abs(area_of(r["features"], "A") - 9600) < 1,
      f"{area_of(r['features'], 'A'):.0f}")
check("...and it is named as having ceded", r["ceded"] == ["A"], str(r["ceded"]))

print("\nunderneath -- the new region gives way instead")
r = T.add_region([feat("A", A)], "name", PARTIAL, name="N", overlap="bottom")
check("the old region is untouched", abs(area_of(r["features"], "A") - 10000) < 1,
      f"{area_of(r['features'], 'A'):.0f}")
check("the new region is trimmed to the free ground",
      abs(area_of(r["features"], "N") - 4500) < 1, f"{area_of(r['features'], 'N'):.0f}")
check("nothing is recorded as ceded", r["ceded"] == [])

print("\n...and underneath refuses when it would leave nothing")
try:
    T.add_region([feat("A", A)], "name", INSIDE, name="N", overlap="bottom")
    check("refused", False, "it was allowed to vanish")
except ValueError as e:
    check("refused with a sentence that says what to do instead",
          "on top" in str(e) or "overlap" in str(e), str(e)[:60])

print("\noverlap -- nobody is trimmed")
r = T.add_region([feat("A", A)], "name", PARTIAL, name="N", overlap="both")
check("both keep their whole shape",
      abs(area_of(r["features"], "A") - 10000) < 1
      and abs(area_of(r["features"], "N") - 4900) < 1)
check("a peer overlap draws the new one on top", order_of(r["features"]) == ["A", "N"],
      str(order_of(r["features"])))

print("\na region drawn AROUND another goes behind it (the cover convention)")
r = T.add_region([feat("A", A)], "name", AROUND, name="N", overlap="both")
check("neither is carved -- no donut",
      abs(area_of(r["features"], "A") - 10000) < 1
      and abs(area_of(r["features"], "N") - 62500) < 1,
      f"A {area_of(r['features'], 'A'):.0f}, N {area_of(r['features'], 'N'):.0f}")
check("the container is drawn FIRST, so what it contains stays visible",
      order_of(r["features"]) == ["N", "A"], str(order_of(r["features"])))
check("...and it says what it went under", r.get("placedUnder") == ["A"],
      str(r.get("placedUnder")))

print("\n'on top' cannot apply to an outline drawn AROUND a region")
# Taking all of A's ground would erase it, so this is refused rather than a
# region silently disappearing -- which is why the prompt does not offer it.
try:
    T.add_region([feat("A", A)], "name", AROUND, name="N", overlap="top")
    check("refused", False, "A was erased")
except ValueError as e:
    check("refused, naming the region it would erase", "erase A" in str(e), str(e)[:60])

print("\nthe container is never carved into a donut")
r = T.add_region([feat("A", A)], "name", AROUND, name="N", overlap="both")
holes = 0
for f in r["features"]:
    g = shape(f["geometry"])
    for part in ([g] if g.geom_type == "Polygon" else list(g.geoms)):
        holes += len(part.interiors)
check("no ring-shaped leftovers anywhere", holes == 0, f"{holes} hole(s)")

print("\nthe route")


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
FC = {"type": "FeatureCollection", "features": [feat("A", A)]}
ADD = f"/api/datasets/{DS}/regions/add"

code, res = post(ADD, {"fc": FC, "points": PARTIAL, "name": "N", "ask": True})
check("ask: a peer overlap comes back as a question, not a change",
      code == 200 and res.get("needsOverlapChoice") and "features" not in res,
      str(res)[:80] if code != 200 else "")
check("...suggesting the clean partition", res.get("suggest") == "top", str(res.get("suggest")))

code, res = post(ADD, {"fc": FC, "points": AROUND, "name": "N", "ask": True})
check("ask: drawing around a region suggests overlapping instead of carving",
      code == 200 and res.get("suggest") == "both", str(res.get("suggest")))

code, res = post(ADD, {"fc": FC, "points": CLEAR, "name": "N", "ask": True})
check("ask: free ground is added straight away, no question",
      code == 200 and not res.get("needsOverlapChoice") and "features" in res)

for mode, a_area in (("top", 9600), ("bottom", 10000), ("both", 10000)):
    code, res = post(ADD, {"fc": FC, "points": PARTIAL, "name": "N", "overlap": mode})
    ok = code == 200 and abs(area_of(res["features"], "A") - a_area) < 1
    check(f"answering '{mode}' does what it says", ok,
          str(res)[:80] if code != 200 else f"A {area_of(res['features'], 'A'):.0f}")

code, res = post(ADD, {"fc": FC, "points": PARTIAL, "name": "N"})
check("a call with no question asked still carves, as it always did",
      code == 200 and abs(area_of(res["features"], "A") - 9600) < 1)

code, res = post(ADD, {"fc": FC, "points": INSIDE, "damage": "bubble", "ask": True})
check("damage never asks -- it sits inside its host by definition",
      code == 200 and not res.get("needsOverlapChoice") and "features" in res,
      str(res)[:80] if code != 200 else "")
check("...and leaves the host whole",
      code == 200 and abs(area_of(res["features"], "A") - 10000) < 1)

print("\n" + (f"{len(fails)} FAILED: " + "; ".join(fails) if fails else "all checks passed"))
sys.exit(1 if fails else 0)

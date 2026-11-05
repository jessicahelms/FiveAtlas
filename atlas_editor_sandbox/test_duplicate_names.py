"""The question asked when a file is opened with a name used by more than one
shape: rename them apart (PAL -> PAL.1), or keep each name as ONE region.

Offline on synthetic shapes, then the route against the isolated backend
(ATLAS_TEST_BASE, default :8060) with the FC in the body -- nothing is read
from or written to any dataset file.
"""
import json
import os
import sys
import urllib.error
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "backend"))

from shapely.geometry import Polygon, box, shape, mapping      # noqa: E402
from shapely.ops import unary_union                            # noqa: E402
import topology as T                                           # noqa: E402

BASE = os.environ.get("ATLAS_TEST_BASE", "http://127.0.0.1:8060")
if ":8050" in BASE or ":8000" in BASE:
    raise SystemExit("refusing to run against the live backend")

fails = []


def check(label, cond, detail=""):
    print(f"  {'ok  ' if cond else 'FAIL'}  {label}{('  ' + str(detail)) if detail != '' else ''}")
    if not cond:
        fails.append(label)


def feat(name, geom, **props):
    return {"type": "Feature", "properties": {"name": name, **props}, "geometry": mapping(geom)}


TH = box(0, 0, 100, 100)
PAL_A = box(200, 0, 300, 100)
PAL_B = Polygon([(200, 0), (310, 0), (310, 100), (200, 100)])     # a second VERSION
VL1 = box(0, 200, 50, 250)
FEATS = [feat("TH", TH, objectType="annotation"), feat("PAL", PAL_A), feat("VL.1", VL1),
         feat("VL.2", box(60, 200, 90, 230)), feat("TH", TH), feat("PAL", PAL_B),
         feat("VL.1", VL1), feat("CP", box(400, 0, 450, 50)),
         feat("separation.1", box(10, 10, 20, 20)), feat("separation.1", box(30, 30, 40, 40))]


def names(fs):
    return [f["properties"]["name"] for f in fs]


print("which names are used twice")
G = {g["name"]: g for g in T.duplicate_groups(FEATS, "name")}
check("every duplicated name, nothing else", sorted(G) == ["PAL", "TH", "VL.1", "separation.1"],
      sorted(G))
check("TH's copies are identical", G["TH"]["identical"] and G["TH"]["diffArea"] == 0)
check("PAL's versions differ, by the ground only one covers",
      not G["PAL"]["identical"] and abs(G["PAL"]["diffArea"] - 1000) < 1e-6, G["PAL"])

print("\nrename")
before = json.dumps(FEATS)
r = T.resolve_duplicates(FEATS, "name", "rename")
out = r["features"]
check("the input is untouched", json.dumps(FEATS) == before)
check("same number of features", len(out) == len(FEATS))
check("no name is used twice afterwards", len(set(names(out))) == len(out), names(out))
check("the first keeps its name, the second is numbered .1",
      names(out)[0] == "TH" and names(out)[4] == "TH.1" and names(out)[5] == "PAL.1")
check("a numbered name takes the next free number (VL.1 -> VL.3)", names(out)[6] == "VL.3")
check("a damage name stays a damage name", names(out)[9] == "separation.2", names(out)[9])
check("geometry is not touched", all(shape(out[k]["geometry"]).equals_exact(
    shape(FEATS[k]["geometry"]), 0) for k in range(len(FEATS))))
check("other properties are kept", out[0]["properties"].get("objectType") == "annotation")
check("changed lists every renamed name",
      {c["name"]: c["into"] for c in r["changed"]}.get("PAL") == ["PAL", "PAL.1"])

print("\nkeep the same name")
r = T.resolve_duplicates(FEATS, "name", "merge")
out = r["features"]
check("one feature per name", len(set(names(out))) == len(out) == 6, names(out))
check("in the first one's place, in file order",
      names(out) == ["TH", "PAL", "VL.1", "VL.2", "CP", "separation.1"], names(out))
by = {f["properties"]["name"]: shape(f["geometry"]) for f in out}
check("identical copies collapse to the one outline", by["TH"].equals(TH))
check("differing versions become the ground either covered", by["PAL"].equals(unary_union([PAL_A, PAL_B])))
check("separate lobes become one multi-part shape",
      by["separation.1"].geom_type == "MultiPolygon" and len(by["separation.1"].geoms) == 2)
check("the first one's properties are kept", out[0]["properties"].get("objectType") == "annotation")
check("names nobody repeats are untouched", by["CP"].equals(box(400, 0, 450, 50)))
try:
    T.resolve_duplicates(FEATS, "name", "nonsense")
    check("an unknown action is refused", False)
except ValueError:
    check("an unknown action is refused", True)

print("\nthe route")


def post(path, body):
    req = urllib.request.Request(BASE + path, data=json.dumps(body).encode(), method="POST",
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            return resp.status, json.loads(resp.read())
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode()[:200]


try:
    with urllib.request.urlopen(BASE + "/api/datasets", timeout=5) as resp:
        dss = json.loads(resp.read())
except Exception as e:                                  # noqa: BLE001
    dss = None
    print(f"  (no isolated backend at {BASE}: {e} -- route checks skipped)")
if dss:
    ds = dss[0]["id"]
    fc = {"type": "FeatureCollection", "features": FEATS, "_provenance": [{"action": "x"}]}
    code, res = post(f"/api/datasets/{ds}/regions/duplicates", {"fc": fc})
    check("no action: the groups, nothing else", code == 200 and set(res) == {"groups"}
          and len(res["groups"]) == 4, (code, str(res)[:120]))
    code, res = post(f"/api/datasets/{ds}/regions/duplicates", {"fc": fc, "action": "rename"})
    check("rename over HTTP", code == 200 and "PAL.1" in names(res["features"]), code)
    check("...and the edit is on the trail",
          isinstance(res, dict) and res.get("_provenance", [{}])[-1].get("action") == "duplicate-names")
    code, res = post(f"/api/datasets/{ds}/regions/duplicates", {"fc": fc, "action": "merge"})
    check("merge over HTTP", code == 200 and len(res["features"]) == 6, code)
    code, res = post(f"/api/datasets/{ds}/regions/duplicates", {"fc": fc, "action": "bogus"})
    check("a bad action is a 400", code == 400, code)

print()
if fails:
    print(f"{len(fails)} FAILED")
    sys.exit(1)
print("all passed")

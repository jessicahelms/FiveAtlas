"""Snap neighbours must never treat the whole-section outline as a peer.

Snapped as a neighbour, the engine carves the moved region's shape straight
through hemi -- a hole in the outline and hairline slivers along the border.
The route now holds any wrap-everything region out and puts it back untouched.

Needs the isolated backend:
    ATLAS_WORKDIR=<scratch>/wd python -m uvicorn app:app --app-dir backend --port 8060
"""
import json
import os
import sys
import urllib.error
import urllib.request

sys.path.insert(0, r"C:\Users\FIVE\source\repos\Jess\atlas_editor\backend")

from shapely.geometry import shape   # noqa: E402
import topology as T                 # noqa: E402

BASE = os.environ.get("ATLAS_TEST_BASE", "http://127.0.0.1:8060")
if ":8050" in BASE or ":8000" in BASE:
    raise SystemExit("refusing to run against the live backend — see the docstring")

fails = []


def check(label, cond, detail=""):
    print(f"  {'ok  ' if cond else 'FAIL'}  {label}{('  ' + detail) if detail else ''}")
    if not cond:
        fails.append(label)


def post(path, body):
    req = urllib.request.Request(
        BASE + path, data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=300) as r:
            return r.status, json.load(r)
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode()[:300]


def poly(name, ring):
    return {"type": "Feature", "properties": {"name": name},
            "geometry": {"type": "Polygon", "coordinates": [ring]}}


HEMI = [[-20, -20], [220, -20], [220, 120], [-20, 120], [-20, -20]]
ISO = [[0, 0]] + [[100, y] for y in range(0, 101, 10)] + [[0, 100], [0, 0]]
TH = list(reversed([[200, 0]] + [[100, y] for y in range(0, 101, 10)]
                   + [[200, 100], [200, 0]]))

ds_id = "DNMT3A_002_27_38_Het_F"   # any registered dataset; snap only uses the body


def dragged(before):
    after = json.loads(json.dumps(before))
    for f in after["features"]:
        if f["properties"]["name"] != "ISO":
            continue
        ring = f["geometry"]["coordinates"][0]
        for i, (x, y) in enumerate(ring):
            if x == 100 and y == 50:
                ring[i] = [130, 50]
    return after


print("\nthe blanket detector")
feats = [poly("hemi", HEMI), poly("ISO", ISO), poly("TH", TH)]
check("the outline is spotted whatever its name",
      T.blankets(feats, "name") == ["hemi"], str(T.blankets(feats, "name")))
check("a file with no outline flags nothing",
      T.blankets([poly("ISO", ISO), poly("TH", TH)], "name") == [])
check("the dragged region is never held, even if it IS the outline",
      T.blankets(feats, "name", keep=["hemi"]) == [])

print("\nsnap with the outline in the file")
before = {"type": "FeatureCollection",
          "features": [poly("hemi", HEMI), poly("ISO", ISO), poly("TH", TH)]}
code, res = post(f"/api/datasets/{ds_id}/snap",
                 {"before": before, "after": dragged(before), "moved": ["ISO"]})
check("the snap succeeds", code == 200, str(res)[:120])
gs = {f["properties"]["name"]: shape(f["geometry"]) for f in res["features"]}
check("hemi comes back untouched -- no hole carved through it",
      abs(gs["hemi"].area - 240 * 140) < 0.01
      and len(gs["hemi"].interiors) == 0,
      f"area {gs['hemi'].area:,.0f}, holes {len(gs['hemi'].interiors)}")
check("the drag survives", any(abs(x - 130) < 0.5 and abs(y - 50) < 0.5
                               for x, y in gs["ISO"].exterior.coords))
check("the neighbour follows -- no overlap, no gap",
      gs["ISO"].intersection(gs["TH"]).area < 0.01
      and abs(gs["ISO"].union(gs["TH"]).area - 20000) < 1.0,
      f"overlap {gs['ISO'].intersection(gs['TH']).area:.2f}")
check("hemi is still first in the file, so it still draws underneath",
      res["features"][0]["properties"]["name"] == "hemi")

print("\nsnap without the outline is unchanged")
before2 = {"type": "FeatureCollection", "features": [poly("ISO", ISO), poly("TH", TH)]}
code, res2 = post(f"/api/datasets/{ds_id}/snap",
                  {"before": before2, "after": dragged(before2), "moved": ["ISO"]})
gs2 = {f["properties"]["name"]: shape(f["geometry"]) for f in res2["features"]}
check("plain two-region snap still behaves", code == 200
      and gs2["ISO"].intersection(gs2["TH"]).area < 0.01
      and abs(gs2["ISO"].union(gs2["TH"]).area - 20000) < 1.0)

print("\n" + (f"{len(fails)} FAILED: " + "; ".join(fails) if fails else "all checks passed"))
sys.exit(1 if fails else 0)

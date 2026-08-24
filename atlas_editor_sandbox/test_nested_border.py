"""Nested regions: the inner's outline is the shared border.

Offline topology first, then the two routes against the isolated backend
(border arcs on pick; move-border accepting nested pairs; partition still
refusing them).
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
    print(f"  {'ok  ' if cond else 'FAIL'}  {label}{('  ' + detail) if detail else ''}")
    if not cond:
        fails.append(label)


def feat(name, geom):
    return {"type": "Feature", "properties": {"name": name},
            "geometry": mapping(geom)}


def geom_of(out, name):
    return shape(next(f for f in out if f["properties"]["name"] == name)["geometry"])


ISO = box(0, 0, 200, 200)                      # container, covers SSp entirely
SSP = box(50, 50, 120, 120)                    # nested inside
C = box(200, 0, 300, 200)                      # a real side-by-side neighbour

print("\nborder_between on a nested pair")
for order in (["SSp", "ISO"], ["ISO", "SSp"]):
    arcs = T.border_between([feat("ISO", ISO), feat("SSp", SSP)], "name", *order)
    ok = len(arcs) == 1 and arcs[0][0] == arcs[0][-1] and len(arcs[0]) >= 4
    ring_pts = {tuple(p) for p in arcs[0]} if arcs else set()
    on_ring = all(Point(x, y).distance(shape(mapping(SSP)).exterior) < 0.1
                  for x, y in ring_pts) if arcs else False
    check(f"picked {' then '.join(order)}: the inner's ring comes back",
          ok and on_ring, f"{len(arcs)} arc(s)")

print("\ndragging the ring outward (inner grows)")
ring = [[50, 50], [120, 50], [120, 120], [50, 120], [50, 50]]
grown = [[50, 50], [150, 50], [150, 150], [50, 150], [50, 50]]
out = T.move_border([feat("ISO", ISO), feat("SSp", SSP)], "name",
                    "SSp", "ISO", grown, drag_start=ring)
gS, gI = geom_of(out, "SSp"), geom_of(out, "ISO")
check("the inner takes the dragged shape", abs(gS.area - 100 * 100) < 1.0,
      f"{gS.area:.0f}")
check("the container still covers it", gI.covers(gS.buffer(-0.5)))
check("the container itself is unchanged in extent",
      abs(gI.area - 200 * 200) < 1.0)

print("\ndragging the ring inward (inner shrinks)")
shrunk = [[60, 60], [100, 60], [100, 100], [60, 100], [60, 60]]
out = T.move_border([feat("ISO", ISO), feat("SSp", SSP)], "name",
                    "SSp", "ISO", shrunk, drag_start=ring)
gS, gI = geom_of(out, "SSp"), geom_of(out, "ISO")
check("the inner shrinks to the drag", abs(gS.area - 40 * 40) < 1.0, f"{gS.area:.0f}")
check("the container is whole -- no hole carved where the inner withdrew",
      abs(gI.area - 200 * 200) < 1.0 and len(gI.interiors) == 0)

print("\npick order does not matter for the drag")
out = T.move_border([feat("ISO", ISO), feat("SSp", SSP)], "name",
                    "ISO", "SSp", grown, drag_start=ring)
check("outer picked first, inner still reshapes",
      abs(geom_of(out, "SSp").area - 100 * 100) < 1.0)

print("\na real neighbour is protected; the container grows with a poke-out")
poke = [[50, 50], [230, 50], [230, 120], [50, 120], [50, 50]]   # into C and past ISO
out = T.move_border([feat("ISO", ISO), feat("SSp", SSP), feat("C", C)], "name",
                    "SSp", "ISO", poke, drag_start=ring)
gS, gI, gC = geom_of(out, "SSp"), geom_of(out, "ISO"), geom_of(out, "C")
check("the drag never takes C's ground", gS.intersection(gC).area < 0.5)
check("C is whole", abs(gC.area - 100 * 200) < 1.0)
check("the container grew to keep covering", gI.covers(gS.buffer(-0.5)))

print("\nmulti-lobe inner: only the dragged lobe changes")
ssp2 = SSP.union(box(150, 150, 180, 180))
out = T.move_border([feat("ISO", ISO), feat("SSp", ssp2)], "name",
                    "SSp", "ISO", grown, drag_start=ring)
gS = geom_of(out, "SSp")
check("dragged lobe reshaped, far lobe untouched",
      abs(gS.area - (100 * 100 + 30 * 30)) < 1.0
      and gS.covers(Point(165, 165)), f"{gS.area:.0f}")

print("\nhemi as the container behaves the same (never carved)")
hemi = box(-20, -20, 320, 220)
out = T.move_border([feat("hemi", hemi), feat("SSp", SSP)], "name",
                    "SSp", "hemi", grown, drag_start=ring)
check("hemi unchanged", abs(geom_of(out, "hemi").area - 340 * 240) < 1.0)
check("the inner reshaped", abs(geom_of(out, "SSp").area - 100 * 100) < 1.0)

print("\nthe routes")


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
      "features": [feat("ISO", ISO), feat("SSp", SSP), feat("C", C)]}

code, res = post(f"/api/datasets/{DS}/regions/shared-border",
                 {"regionA": "ISO", "regionB": "SSp", "fc": fc})
check("shared-border returns the ring for a nested pick",
      code == 200 and res.get("contained") and len(res.get("arcs") or []) == 1,
      str(res)[:90] if code != 200 else f"{len(res.get('arcs') or [])} arc(s)")
check("...and the message says drag, not refusal",
      code == 200 and "shared border" in (res.get("message") or ""))

code, res = post(f"/api/datasets/{DS}/regions/move-border",
                 {"fc": fc, "regionA": "SSp", "regionB": "ISO",
                  "points": grown, "dragStart": ring})
ok = code == 200
if ok:
    gS = shape(next(f for f in res["features"]
                    if f["properties"]["name"] == "SSp")["geometry"])
    ok = abs(gS.area - 100 * 100) < 1.0
check("move-border accepts the nested pair end to end", ok,
      str(res)[:90] if code != 200 else "")

code, res = post(f"/api/datasets/{DS}/regions/partition",
                 {"regions": ["ISO", "SSp"], "fc": fc})
check("Share borders on a nested PAIR now succeeds (coastal merge semantics)",
      code == 200 and "nested" in res, f"code {code}")
check("...deep inside the container there is nothing near the outline to seal",
      code == 200 and res.get("nested", {}).get("sealed", -1) == 0.0)
code, res = post(f"/api/datasets/{DS}/regions/partition",
                 {"regions": ["ISO", "SSp", "C"], "fc": fc})
check("three picks with a container still refuse", code == 422, f"code {code}")
check("...and the refusal points at the drag",
      code == 422 and "drag" in str(res).lower())

print("\n" + (f"{len(fails)} FAILED: " + "; ".join(fails) if fails else "all checks passed"))
sys.exit(1 if fails else 0)

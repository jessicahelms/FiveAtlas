"""Split finds its own region: topology.split_candidates.

Picking the region in the list before every cut was a click per split, so a
line drawn with nothing picked now names the region it is meant for. The line
alone is ambiguous -- its ends sit in the neighbours, and split_region would
happily "cut" a neighbour by carrying an end on to its far edge -- so this
checks the neighbours are NOT offered, the gap between a region's separate
pieces IS found, the outline (hemi) never is, and a line that could mean two
regions gives both (the page then asks) instead of a guess.

Offline: synthetic boxes, plus the real 23-region fixture if it is present.
Usage:  python test_split_auto.py
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend"))

from shapely.geometry import MultiPolygon, Polygon, box, mapping, shape   # noqa: E402

import topology as T                                                     # noqa: E402

fails = []


def check(label, cond, detail=""):
    print(f"  {'ok  ' if cond else 'FAIL'}  {label}{('  ' + detail) if detail else ''}")
    if not cond:
        fails.append(label)


def feat(n, g):
    return {"type": "Feature", "properties": {"name": n}, "geometry": mapping(g)}


def cands(feats, pts, exclude=()):
    return T.split_candidates(feats, "name", pts, exclude)


# hemi around a row of three tiled boxes, plus a two-piece region D below them
A, B, C = box(0, 0, 100, 100), box(100, 0, 200, 100), box(200, 0, 300, 100)
D = MultiPolygon([box(0, 150, 100, 250), box(200, 150, 300, 250)])
hemi = box(-50, -50, 350, 300)
F = [feat("hemi", hemi), feat("A", A), feat("B", B), feat("C", C), feat("D", D)]

print("\nordinary cuts")
r = cands(F, [[150, -20], [150, 120]])                  # across B, ends outside everything
check("a line across B from outside names B", r["names"] == ["B"], str(r))
r = cands(F, [[50, 50], [250, 50]])                     # from inside A, across B, into C
check("ends inside A and C: only B, the region crossed all the way", r["names"] == ["B"], str(r))
r = cands(F, [[150, 20], [150, 80]])                    # entirely inside B, short of both edges
check("a line stopping short inside B still names B", r["names"] == ["B"], str(r))
r = cands(F, [[50, 50], [150, 50]])                     # from inside A to inside B: ambiguous
check("a line from inside A to inside B offers both", sorted(r["names"]) == ["A", "B"], str(r))
r = cands(F, [[20, 50], [130, 50]])                     # 80 px inside A, 30 inside B
check("...the one with more of the line first", r["names"] == ["A", "B"], str(r))
r = cands(F, [[-20, 50], [320, 50]])                    # all the way across A, B and C
check("across all three boxes: all three offered", sorted(r["names"]) == ["A", "B", "C"], str(r))

print("\nthe gap between separate pieces")
r = cands(F, [[150, 140], [150, 260]])                  # between D's two pieces, in hemi only
check("a line through D's gap names D", r["names"] == ["D"], str(r))
r = cands(F, [[150, 270], [150, 290]])                  # below D, outside its hull
check("a line outside D's hull does not name D", "D" not in r["names"], str(r))

print("\nthe outline and switched-off regions")
r = cands(F, [[-40, 280], [340, 280]])                  # only hemi under it
check("hemi is never offered", "hemi" not in r["names"], str(r))
check("...and an empty answer says why", not r["names"] and bool(r["reason"]), str(r))
r = cands(F, [[150, -20], [150, 120]], exclude=["B"])
check("a switched-off region is not offered", "B" not in r["names"], str(r))

print("\nthe answer is what Split then does")
r = cands(F, [[150, -20], [150, 120]])
res = T.split_region(F, "name", r["names"][0], [[150, -20], [150, 120]])
check("B splits into two with A and C untouched",
      len(res["names"]) == 2 and all(shape(f["geometry"]).equals(g) for f in res["features"]
                                     for n, g in (("A", A), ("C", C))
                                     if f["properties"]["name"] == n), str(res["names"]))

real = (Path(__file__).resolve().parents[2] / "FiveAtlas_handoff_fixtures" / "test_backend"
        / "ATLAS_TEST_DS" / "regions.geojson")
if real.exists():
    print("\nreal 23-region file")
    fc = json.loads(real.read_text(encoding="utf-8"))
    feats = fc["features"]
    idp = "name"
    names = [f["properties"][idp] for f in feats]
    singles = sorted((shape(f["geometry"]).area, f["properties"][idp]) for f in feats
                     if f["properties"][idp] != "hemi" and names.count(f["properties"][idp]) == 1
                     and shape(f["geometry"]).geom_type == "Polygon")
    tgt = singles[len(singles) // 2][1]
    G = shape(feats[names.index(tgt)]["geometry"])
    c = G.representative_point()
    minx, miny, maxx, maxy = G.bounds
    # a horizontal line through the representative point, from just outside G on each side
    xs = sorted(p[0] for p in G.boundary.intersection(
        Polygon([(minx - 1, c.y - 0.5), (maxx + 1, c.y - 0.5), (maxx + 1, c.y + 0.5),
                 (minx - 1, c.y + 0.5)])).envelope.exterior.coords)
    pts = [[xs[0] - 40, c.y], [xs[-1] + 40, c.y]]
    r = T.split_candidates(feats, idp, pts)
    check(f"a line across {tgt} names {tgt} first", r["names"][:1] == [tgt], str(r))
    # a stray triangle off to one side, a line through the gap
    w = maxx - minx
    tri = Polygon([(maxx + 0.3 * w, miny), (maxx + 0.5 * w, miny), (maxx + 0.4 * w, miny + 0.2 * w)])
    f2 = json.loads(json.dumps(feats))
    f2[names.index(tgt)]["geometry"] = mapping(MultiPolygon([G, tri]))
    gx = maxx + 0.15 * w
    r = T.split_candidates(f2, idp, [[gx, miny - 0.05 * w], [gx, miny + 0.15 * w]])
    check(f"a line through the gap to a stray triangle names {tgt}", tgt in r["names"], str(r))

print()
if fails:
    print(f"{len(fails)} FAILED")
    sys.exit(1)
print("all checks passed")

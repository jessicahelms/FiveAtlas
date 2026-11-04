"""Resample points -> Fewest points (method="shape").

Even spacing at its coarsest (600 px) still left 45-130 points per region on the
real files and moved their corners by ~200 px. Fewest points keeps only the
points the shape needs within a distance, so this checks: the points left are
the outline's OWN points (none moved), nothing strays further than the
distance asked for, shared borders stay coincident in both regions with no gap
or overlap, a third region's ground is never taken, and on the real file it
goes far lower than even spacing does while moving the outline far less.

Offline: synthetic shapes, plus the real 23-region fixture if present.
Usage:  python test_resample_fewest.py
"""
import json
import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend"))

from shapely.geometry import Polygon, mapping, shape   # noqa: E402
from shapely.ops import unary_union                   # noqa: E402

import topology as T                                  # noqa: E402

fails = []


def check(label, cond, detail=""):
    print(f"  {'ok  ' if cond else 'FAIL'}  {label}{('  ' + str(detail)) if detail != '' else ''}")
    if not cond:
        fails.append(label)


def feat(n, g):
    return {"type": "Feature", "properties": {"name": n}, "geometry": mapping(g)}


def geom(feats, n):
    return shape(next(f for f in feats if f["properties"]["name"] == n)["geometry"])


def verts(g):
    return {tuple(round(c, 6) for c in p) for poly in T._polys(g)
            for p in list(poly.exterior.coords)}


print("\none region on its own")
# a dense 'square with a bump': straight sides carry many points, the bump few
pts = []
for i in range(50):
    pts.append((i * 4.0, 0.0))
for i in range(50):
    pts.append((200.0, i * 4.0))
for i in range(50):
    x = 200.0 - i * 4.0
    bump = 30.0 * math.exp(-((x - 100.0) / 15.0) ** 2)
    pts.append((x, 200.0 + bump))
for i in range(50):
    pts.append((0.0, 200.0 - i * 4.0))
R = Polygon(pts)
F = [feat("R", R)]
res = T.resample_region(F, "name", "R", tol=2.0, method="shape")
gR = geom(res["features"], "R")
check("far fewer points", T._vertex_count(gR) < T._vertex_count(R) / 4,
      f"{T._vertex_count(R)} -> {T._vertex_count(gR)}")
check("every point left is one of R's own points (none moved)", verts(gR) <= verts(R))
check("the outline moved no more than asked (2 px)",
      R.boundary.hausdorff_distance(gR.boundary) <= 2.0 + 1e-6,
      f"{R.boundary.hausdorff_distance(gR.boundary):.2f} px")
peak = max(p[1] for p in verts(gR))
check("the bump's peak is kept (structure kept)", peak >= 228.0, f"top at y={peak:.1f}")
check("...and so is most of the bump's ground",
      R.difference(gR).area < 0.25 * (R.area - 200.0 * 200.0), f"{R.difference(gR).area:.0f} px2 lost")
res_even = T.resample_region(F, "name", "R", tol=150.0, method="even")
check("even spacing is unchanged by default",
      T.resample_region(F, "name", "R", tol=150.0)["handles"] == res_even["handles"])

print("\na shared border")
A = Polygon([(0, 0)] + [(100, y) for y in range(0, 101, 2)] + [(0, 100)])
B = Polygon([(100, y) for y in range(0, 101, 2)] + [(200, 100), (200, 0)])
H = Polygon([(-50, -50), (250, -50), (250, 150), (-50, 150)])
F2 = [feat("hemi", H), feat("A", A), feat("B", B)]
res = T.resample_regions(F2, "name", ["A", "B"], tol=3.0, method="shape")
gA, gB = geom(res["features"], "A"), geom(res["features"], "B")
check("the straight shared border collapses to a few points",
      T._vertex_count(gA) < T._vertex_count(A) / 4, f"{T._vertex_count(A)} -> {T._vertex_count(gA)}")
check("no overlap opened", gA.intersection(gB).area < 1e-6, f"{gA.intersection(gB).area:.6f}")
check("no gap opened", unary_union([gA, gB]).buffer(1e-6).buffer(-1e-6).geom_type == "Polygon")
check("areas unchanged", abs(gA.area - A.area) < 1e-6 and abs(gB.area - B.area) < 1e-6)
try:
    T.resample_region(F, "name", "R", tol=5.0, method="wobbly")
    check("an unknown method is refused", False)
except ValueError:
    check("an unknown method is refused", True)

real = (Path(__file__).resolve().parents[2] / "FiveAtlas_handoff_fixtures" / "test_backend"
        / "ATLAS_TEST_DS" / "regions.geojson")
if real.exists():
    print("\nreal 23-region file")
    feats = json.loads(real.read_text(encoding="utf-8"))["features"]
    others0 = {f["properties"]["name"]: shape(f["geometry"]) for f in feats}
    for nm in ("CP", "TH", "VL.1"):
        if nm not in others0:
            continue
        g0 = others0[nm]
        ev = T.resample_region(feats, "name", nm, tol=600.0, method="even")
        sh = T.resample_region(feats, "name", nm, tol=80.0, method="shape")
        g_ev, g_sh = geom(ev["features"], nm), geom(sh["features"], nm)
        d_ev = g0.boundary.hausdorff_distance(g_ev.boundary)
        d_sh = g0.boundary.hausdorff_distance(g_sh.boundary)
        check(f"{nm}: Fewest points at 80 px moves the outline at most ~80 px",
              d_sh <= 80.0 + 1.0, f"{sh['handles']['before']} -> {sh['handles']['after']} points, {d_sh:.0f} px")
        check(f"{nm}: ...far less than even spacing at 600 does", d_sh < d_ev,
              f"even@600: {ev['handles']['after']} points, {d_ev:.0f} px")
        # no third region loses ground: every other region either unchanged or a
        # shared-border partner whose area moved by < 0.5%
        changed = {c["region"] for c in sh["counts"]}
        bad = []
        for f in sh["features"]:
            o = f["properties"]["name"]
            if o == nm or o == "hemi":
                continue
            g1 = shape(f["geometry"])
            if o not in changed and not g1.equals(others0[o]):
                bad.append(o)
        check(f"{nm}: regions it does not border are untouched", not bad, bad)
        ov = [o for o in others0 if o not in (nm, "hemi")
              and geom(sh["features"], o).intersection(g_sh).area
              > others0[o].intersection(g0).area + 1.0]
        check(f"{nm}: no new overlap with any neighbour", not ov, ov)

    print("\nthe slider never adds points back (resample_region_fewest)")
    for nm in ("CP", "TH", "dft", "VL.1"):
        if nm not in others0:
            continue
        counts, prev = [], None
        for t, deform in ((20, False), (80, False), (150, False), (300, True),
                          (600, True), (1000, True)):
            r = T.resample_region_fewest(feats, "name", nm, tol=t,
                                         max_drift=T.FEWEST_DEFORM_DRIFT if deform else 0.05)
            counts.append(r["handles"]["after"])
            if not deform:
                g1 = geom(r["features"], nm)
                check(f"{nm} at {t} px: moves at most {t} px",
                      others0[nm].boundary.hausdorff_distance(g1.boundary) <= t + 1.0)
        check(f"{nm}: points only go down as the slider goes up", counts == sorted(counts, reverse=True),
              counts)
        r = T.resample_region_fewest(feats, "name", nm, tol=1000, max_drift=T.FEWEST_DEFORM_DRIFT)
        g1 = geom(r["features"], nm)
        check(f"{nm} at 1000 px (deform allowed): area within 25%",
              abs(g1.area - others0[nm].area) / others0[nm].area <= 0.25 + 1e-9,
              f"{100 * (g1.area - others0[nm].area) / others0[nm].area:+.1f}%")
        check(f"{nm}: 'used' never exceeds the setting asked for",
              r["used"] is None or r["used"] <= 1000)

print()
if fails:
    print(f"{len(fails)} FAILED")
    sys.exit(1)
print("all checks passed")

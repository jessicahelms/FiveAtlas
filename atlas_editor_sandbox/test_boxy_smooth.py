"""Old, unsmoothed predictions are smoothed on load (backend/boxy.py) -- and an
edited region is NEVER changed.

The promise being pinned: a region is only touched if it is still exact pixel
stairs (every edge horizontal or vertical, every corner on the model's grid).
So each kind of hand edit is made here and the edited region must come back
untouched, while its boxy neighbours are fitted to it without overlapping it:

  * one point dragged off its stair (the region is 99%+ stairs, not 100%)
  * a region redrawn smooth (Resample / Share borders / Snap leave diagonals)
  * a rectangle drawn by hand, and an ROI box lying on top of the regions
  * hemi, the outline round everything
  * a region from a file that is not a prediction

Also: the faster speck clean-up in predict.py gives exactly the old result.

Offline: boxy.smooth and predict's helpers are pure.
"""
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "backend"))

_SCRATCH = tempfile.mkdtemp(prefix="boxy_")
os.environ["ATLAS_WORKDIR"] = _SCRATCH

import config                                   # noqa: E402

if str(config.WORKDIR) != _SCRATCH:
    raise SystemExit(f"refusing to run: WORKDIR is {config.WORKDIR}, not the scratch dir")

import numpy as np                              # noqa: E402
import shapely                                  # noqa: E402
from shapely import affinity                    # noqa: E402
from shapely.geometry import box, mapping, shape, Polygon   # noqa: E402

import boxy as B                                # noqa: E402
import predict as P                             # noqa: E402

fails = []


def check(label, cond, detail=""):
    print(f"  {'ok  ' if cond else 'FAIL'}  {label}{('  ' + detail) if detail else ''}")
    if not cond:
        fails.append(label)


STEP, X0, Y0 = 47.26, 1000.0, 2000.0


def boxy_map():
    """A label picture of 5 blobby regions, traced as pixel stairs the way an old
    prediction file has them (model pixels STEP image px wide)."""
    H, W = 90, 110
    yy, xx = np.mgrid[0:H, 0:W]
    lab = np.zeros((H, W), np.int32)
    lab[((xx - 55) / 50) ** 2 + ((yy - 45) / 40) ** 2 < 1] = 1
    lab[(lab == 1) & (xx + 0.4 * yy < 50)] = 2
    lab[(lab == 1) & (xx - 0.3 * yy > 62)] = 3
    lab[(lab == 1) & (((xx - 50) / 14) ** 2 + ((yy - 50) / 10) ** 2 < 1)] = 4
    lab[(lab == 1) & (yy < 22)] = 5
    feats = []
    for k in range(1, 6):
        polys = P._class_polygons(lab == k)
        polys = [affinity.affine_transform(p, [STEP, 0, 0, STEP, X0, Y0]) for p in polys]
        g = polys[0] if len(polys) == 1 else shapely.MultiPolygon(polys)
        feats.append({"type": "Feature", "properties": {"name": f"R{k}"}, "geometry": mapping(g)})
    return feats


def geoms(feats):
    return [shape(f["geometry"]) for f in feats]


def apply(feats, res):
    out = [dict(f) for f in feats]
    for i, g in res["smoothed"].items():
        out[i] = {**feats[i], "geometry": g}
    return out


def overlap(ga, gb):
    return ga.intersection(gb).area


print("a boxy prediction, nothing edited")
F = boxy_map()
res = B.smooth(F, range(len(F)), P._smooth_regions)
G0, G1 = geoms(F), geoms(apply(F, res))
check("every region is smoothed", sorted(res["smoothed"]) == list(range(5)), str(sorted(res["smoothed"])))
pts0 = sum(len(shapely.get_coordinates(g)) for g in G0)
pts1 = sum(len(shapely.get_coordinates(g)) for g in G1)
check("far fewer points", pts1 < 0.6 * pts0, f"{pts0} -> {pts1}")
check("all valid", all(g.is_valid for g in G1))
ov = sum(overlap(G1[a], G1[b]) for a in range(5) for b in range(a + 1, 5))
check("no overlaps between the smoothed regions", ov < 1.0, f"{ov:.2f} px2")
u0, u1 = shapely.union_all(G0), shapely.union_all(G1)
check("the section keeps its size", abs(u1.area - u0.area) < 0.02 * u0.area,
      f"{u0.area:,.0f} -> {u1.area:,.0f}")
check("the grid was found", len(res["grids"]) == 1 and abs(res["grids"][0][2] - STEP) < 0.05, str(res["grids"]))

print("one point of R2 dragged off its stair (still 99% stairs)")
F = boxy_map()
g = shape(F[1]["geometry"])
p = g.geoms[0] if hasattr(g, "geoms") else g
cs = list(p.exterior.coords)
k = len(cs) // 3
cs[k] = (cs[k][0] + 9.3, cs[k][1] + 4.1)
cs[-1] = cs[0]
edited = Polygon(cs, [r.coords for r in p.interiors]).buffer(0)
F[1] = {**F[1], "geometry": mapping(edited)}
before = F[1]["geometry"]
res = B.smooth(F, range(len(F)), P._smooth_regions)
out = apply(F, res)
check("the edited region is not touched", 1 not in res["smoothed"] and out[1]["geometry"] is before)
check("the others are smoothed", sorted(res["smoothed"]) == [0, 2, 3, 4], str(sorted(res["smoothed"])))
G1 = geoms(out)
ov = sum(overlap(G1[1], G1[j]) for j in (0, 2, 3, 4))
check("nothing smoothed overlaps the edited region", ov < 1.0, f"{ov:.3f} px2")

print("R4 redrawn smooth (as Resample / Share borders / Snap leave it)")
F = boxy_map()
smooth4 = shape(F[3]["geometry"]).simplify(STEP * 0.6).buffer(0)
F[3] = {**F[3], "geometry": mapping(smooth4)}
before = F[3]["geometry"]
res = B.smooth(F, range(len(F)), P._smooth_regions)
out = apply(F, res)
check("the edited region is not touched", 3 not in res["smoothed"] and out[3]["geometry"] is before)
G1 = geoms(out)
ov = sum(overlap(G1[3], G1[j]) for j in (0, 1, 2, 4))
check("nothing smoothed overlaps it", ov < 1.0, f"{ov:.3f} px2")
# its edge is kept exactly: ground beside it is still covered (no gap opened)
check("all valid", all(g.is_valid for g in G1))


def areas_only(g):
    return shapely.union_all([p for p in shapely.get_parts(g) if p.geom_type in ("Polygon", "MultiPolygon")])


# measured on a 0.01 px grid: the regions are fitted to the edited edge exactly,
# so plain floating point overlay of the band against them trips over it
others = areas_only(shapely.union_all([G1[j] for j in (0, 1, 2, 4)], grid_size=0.01))
band = areas_only(shapely.intersection(
    shapely.difference(smooth4.buffer(STEP * 0.5), smooth4, grid_size=0.01),
    shapely.union_all(geoms(F), grid_size=0.01), grid_size=0.01))
band = areas_only(shapely.difference(band, smooth4.buffer(0.05), grid_size=0.01))
cov = shapely.difference(band, others, grid_size=0.01).area / max(band.area, 1)
check("no gap opens along its edge", cov < 0.02, f"{100 * cov:.2f}% of the band uncovered")

print("a hand-drawn rectangle, an ROI box on top, and hemi round everything")
F = boxy_map()
a, b, c, d = shapely.union_all(geoms(F)).bounds
F.append({"type": "Feature", "properties": {"name": "drawn"},
          "geometry": mapping(box(c + 37.1, b + 11.3, c + 900.7, b + 640.2))})
F.append({"type": "Feature", "properties": {"name": "ROI"},
          "geometry": mapping(box(a + 600.5, b + 500.5, a + 2400.5, b + 1800.5))})
F.append({"type": "Feature", "properties": {"name": "hemi"},
          "geometry": mapping(box(a - 300, b - 300, c + 1200, d + 300))})
keep = [F[i]["geometry"] for i in (5, 6, 7)]
res = B.smooth(F, range(len(F)), P._smooth_regions)
out = apply(F, res)
check("drawn rectangle, ROI and hemi are not touched",
      all(i not in res["smoothed"] for i in (5, 6, 7))
      and all(out[i]["geometry"] is keep[j] for j, i in enumerate((5, 6, 7))))
check("the ROI box does not cut holes in the regions under it",
      sorted(res["smoothed"]) == list(range(5))
      and abs(shapely.union_all(geoms(out)[:5]).area - shapely.union_all(geoms(F)[:5]).area)
      < 0.02 * shapely.union_all(geoms(F)[:5]).area)

print("only regions from a prediction file")
F = boxy_map()
res = B.smooth(F, [], P._smooth_regions)
check("nothing eligible: nothing smoothed", res["smoothed"] == {})
res = B.smooth(F, [0, 2], P._smooth_regions)
check("only the eligible ones can change", set(res["smoothed"]) <= {0, 2}, str(sorted(res["smoothed"])))
G1 = geoms(apply(F, res))
ov = sum(overlap(G1[a], G1[b]) for a in range(5) for b in range(a + 1, 5))
check("still no overlaps", ov < 1.0, f"{ov:.3f} px2")
check("file names", B.is_prediction_file("CNTNAP2_008_prediction_left.geojson")
      and B.is_prediction_file("x_Prediction_v2.geojson")
      and not B.is_prediction_file("CNTNAP2_008_regions.geojson")
      and not B.is_prediction_file(None))

print("a smoothed map is not boxy any more (loading it again changes nothing)")
F = boxy_map()
F2 = apply(F, B.smooth(F, range(5), P._smooth_regions))
res = B.smooth(F2, range(5), P._smooth_regions)
check("nothing to do the second time", res["smoothed"] == {})

def ov_all(G, idx):
    return sum(overlap(G[a], G[b]) for n, a in enumerate(idx) for b in idx[n + 1:])


print("found by the reviewers: a region moved whole (Move region) is never smoothed")
for k, (dx, dy) in enumerate([(-133.7, -88.1), (-20.0, 0.0), (1.3, -0.9), (0.0, 13.7)]):
    for who in range(5):
        F = boxy_map()
        moved = affinity.translate(shape(F[who]["geometry"]), dx, dy)
        F[who] = {**F[who], "geometry": mapping(moved)}
        before = F[who]["geometry"]
        res = B.smooth(F, range(5), P._smooth_regions)
        out = apply(F, res)
        if who in res["smoothed"] or out[who]["geometry"] is not before:
            check(f"moved R{who + 1} by ({dx}, {dy}) is left alone", False, str(sorted(res["smoothed"])))
            break
        G1 = geoms(out)
        o0, o1 = ov_all(geoms(F), list(range(5))), ov_all(G1, list(range(5)))
        if o1 > o0 + 1.0:
            check(f"moving R{who + 1} by ({dx}, {dy}): no new overlap", False, f"{o0:.0f} -> {o1:.0f}")
            break
    else:
        check(f"every region moved by ({dx}, {dy}) is left alone, no new overlap", True)

print("moved by exactly one grid step (still on the grid, now overlapping): left alone")
F = boxy_map()
F[3] = {**F[3], "geometry": mapping(affinity.translate(shape(F[3]["geometry"]), STEP, 0))}
before = F[3]["geometry"]
res = B.smooth(F, range(5), P._smooth_regions)
check("not smoothed", 3 not in res["smoothed"] and apply(F, res)[3]["geometry"] is before,
      str(sorted(res["smoothed"])))

print("damage and an ROI hanging off the tissue edge cut nothing out of the regions")
for nm_, frac in (("bubble.1", 0.4), ("ROI", 0.3), ("drawn box", 0.45)):
    F = boxy_map()
    u = shapely.union_all(geoms(F))
    a, b, c, d = u.bounds
    cx = a                                   # on the left edge of the section
    cy = (b + d) / 2
    r = 400.0
    shp = shapely.Point(cx + (frac - 0.5) * 2 * r, cy).buffer(r) if nm_.startswith("bubble")         else box(cx - r * (1 - frac) * 2, cy - r, cx + r * frac * 2, cy + r)
    F.append({"type": "Feature", "properties": {"name": nm_}, "geometry": mapping(shp)})
    overlays = [5] if nm_.startswith("bubble") else []
    res = B.smooth(F, range(5), P._smooth_regions, overlays)
    G0, G1 = geoms(F), geoms(apply(F, res))
    lost = shapely.union_all(G0[:5]).intersection(shp).area - shapely.union_all(G1[:5]).intersection(shp).area
    check(f"{nm_} ({frac:.0%} on tissue): the ground under it stays in the regions", lost < 0.02 * shp.area,
          f"{lost:,.0f} px2 lost of {shp.area:,.0f}")

print("hemi drawn as stairs round the regions: the regions are still smoothed, hemi untouched")
F = boxy_map()
u = shapely.union_all(geoms(F))
hemi = u.buffer(STEP * 2, join_style="mitre").envelope
F.append({"type": "Feature", "properties": {"name": "hemi"}, "geometry": mapping(hemi)})
hemi_before = F[5]["geometry"]
res = B.smooth(F, range(6), P._smooth_regions)
out = apply(F, res)
check("hemi untouched, the 5 regions smoothed", 5 not in res["smoothed"] and out[5]["geometry"] is hemi_before
      and sorted(res["smoothed"]) == list(range(5)), str(sorted(res["smoothed"])))
check("smoothed regions stay inside hemi",
      shapely.union_all(geoms(out)[:5]).difference(hemi).area < 1.0)

print("a hand-drawn (smooth) hemi hugging the tissue edge: no ground inside it is left to no region")
for pad in (0.0, STEP * 0.5, STEP * 3):
    F = boxy_map()
    u = shapely.union_all(geoms(F))
    hemi = u.buffer(pad + 1.0, quad_segs=16) if pad else u.convex_hull.intersection(u.buffer(STEP * 0.6))
    F.append({"type": "Feature", "properties": {"name": "hemi"}, "geometry": mapping(hemi)})
    res = B.smooth(F, range(5), P._smooth_regions)
    out = geoms(apply(F, res))
    lost = u.intersection(hemi).difference(shapely.union_all(out[:5]))
    over = [out[i].intersection(out[j]).area for i in range(5) for j in range(i + 1, 5)]
    check(f"hemi {pad:.0f} px out: all 5 smoothed, nothing uncovered, no overlap",
          sorted(res["smoothed"]) == list(range(5)) and lost.area < 1.0 and max(over) < 1.0,
          f"smoothed {sorted(res['smoothed'])} lost {lost.area:.1f} px2 overlap {max(over):.1f}")

print("two predictions on different grids (left + right files) are each smoothed")
L = boxy_map()
R = [{**f, "properties": {"name": f["properties"]["name"] + "_R"},
      "geometry": mapping(affinity.affine_transform(shape(f["geometry"]), [1.11, 0, 0, 1.11, 9000.3, 517.9]))}
     for f in boxy_map()]
F = L + R
res = B.smooth(F, range(10), P._smooth_regions)
check("all 10 smoothed on 2 grids", sorted(res["smoothed"]) == list(range(10)) and len(res["grids"]) == 2,
      f"{sorted(res['smoothed'])} grids {res['grids']}")

print("a broken (self-crossing) neighbour does not stop the rest")
F = boxy_map()
a, b, c, d = shapely.union_all(geoms(F)).bounds
F.append({"type": "Feature", "properties": {"name": "bowtie"},
          "geometry": {"type": "Polygon", "coordinates": [[[c, b], [c + 500, d], [c + 500, b], [c, d], [c, b]]]}})
try:
    res = B.smooth(F, range(5), P._smooth_regions)
    check("no exception, the 5 regions smoothed", sorted(res["smoothed"]) == list(range(5)), str(sorted(res["smoothed"])))
except Exception as e:
    check("no exception", False, repr(e)[:120])

print("a small boxy piece above and left of everything")
F = boxy_map()
a, b, c, d = shapely.union_all(geoms(F)).bounds
F.append({"type": "Feature", "properties": {"name": "piece"},
          "geometry": mapping(box(a - 5 * STEP, b - 4 * STEP, a - 3 * STEP, b - 2 * STEP).union(
              box(a - 4 * STEP, b - 2 * STEP, a - 3 * STEP, b - 1 * STEP)))})
try:
    res = B.smooth(F, range(6), P._smooth_regions)
    check("no exception", True, str(sorted(res["smoothed"])))
except Exception as e:
    check("no exception", False, repr(e)[:120])

print("the faster speck clean-up gives exactly the old result")


def old_absorb(lab):
    from scipy import ndimage
    lab = np.asarray(lab).copy()
    for cls in np.unique(lab):
        comps, n = ndimage.label(lab == cls)
        if not n:
            continue
        sizes = np.bincount(comps.ravel())
        for i in np.flatnonzero(sizes[1:] < P.MIN_AREA_PX) + 1:
            m = comps == i
            ring = ndimage.binary_dilation(m) & ~m
            if ring.any():
                lab[m] = np.bincount(lab[ring]).argmax()
    return lab


rng = np.random.default_rng(0)
same = True
for trial in range(25):
    lab = rng.integers(0, 6, (50, 70)).astype(np.int32)
    lab = np.kron(lab, np.ones((int(rng.integers(1, 4)),) * 2, np.int32))
    same &= bool(np.array_equal(old_absorb(lab), P._absorb_specks(lab)))
check("25 random speckled pictures: identical", same)

print()
print("FAILED: " + ", ".join(fails) if fails else "all passed")
sys.exit(1 if fails else 0)

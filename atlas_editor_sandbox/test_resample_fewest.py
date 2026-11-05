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

    print("\nno gap is opened (a stretch NEAR a neighbour gave up ground to nobody)")
    # HPF lost 300,340 px2 to nobody at 150 px before free stretches were checked:
    # the user's screenshot. Measured against the NEW hemi, so the coast moving
    # in together with hemi is not counted as a gap.
    def cover(fs):
        return unary_union([shape(f["geometry"]) for f in fs if f["properties"]["name"] != "hemi"])
    c0 = cover(feats)
    for nm in ("HPF", "TH", "dft", "V3", "CP"):
        if nm not in others0:
            continue
        for t, deform in ((40, False), (150, False), (600, True)):
            r = T.resample_region_fewest(feats, "name", nm, tol=t,
                                         max_drift=T.FEWEST_DEFORM_DRIFT if deform else 0.05)
            void = c0.difference(cover(r["features"])).intersection(geom(r["features"], "hemi"))
            check(f"{nm} at {t} px: no new gap inside the section", void.area <= 250.0,
                  f"{void.area:.0f} px2, {r['handles']['before']} -> {r['handles']['after']} points")
    r = T.resample_region_fewest(feats, "name", "HPF", tol=600, max_drift=T.FEWEST_DEFORM_DRIFT)
    before_v3 = sum(1 for p in T._polys(others0["HPF"])[0].exterior.coords
                    if others0["V3"].boundary.distance(__import__("shapely").geometry.Point(p)) < 0.5)
    after_v3 = sum(1 for p in T._polys(geom(r["features"], "HPF"))[0].exterior.coords
                   if geom(r["features"], "V3").boundary.distance(__import__("shapely").geometry.Point(p)) < 0.5)
    check("HPF's border with V3 (a thin ventricle) is thinned too, not left as a dense bunch",
          after_v3 < before_v3 / 3, f"{before_v3} -> {after_v3} points")

    print("\nthe slider goes by NUMBER of points (fewest_options)")
    for nm in ("HPF", "CP", "TH"):
        o = T.fewest_options(feats, "name", nm)
        pts = [x["points"] for x in o["options"]]
        check(f"{nm}: counts only go down along the slider", pts == sorted(set(pts), reverse=True), pts)
        check(f"{nm}: every count is below what it has now", all(p < o["before"] for p in pts),
              f"before {o['before']}")
        old = {nm: others0[nm]}
        wrong = [x for x in o["options"]
                 if x["deform"] != (T._outline_moved(
                     old, T.resample_region_at(feats, "name", nm, x["tol"])["features"], "name")
                     > T.FEWEST_SAFE)]
        check(f"{nm}: an option is marked as deforming exactly when the outline moves "
              f"more than {T.FEWEST_SAFE:g} px", not wrong, wrong[:2])
        check(f"{nm}: the deforming ones come last", [x["deform"] for x in o["options"]]
              == sorted(x["deform"] for x in o["options"]))
        bad = [x for x in o["options"]
               if T.resample_region_at(feats, "name", nm, x["tol"])["handles"]["after"] != x["points"]]
        check(f"{nm}: picking a notch gives exactly the count it showed", not bad, bad[:2])

    print("\nby number for even spacing, and on a shared border")
    for names, meth in ((["HPF"], "even"), (["TH"], "even"), (["HPF", "dft"], "even"),
                        (["HPF", "dft"], "shape"), (["CP", "dft"], "shape")):
        o = T.resample_options(feats, "name", names, meth)
        pts = [x["points"] for x in o["options"]]
        tag = f"{'+'.join(names)} {meth}"
        check(f"{tag}: counts only go down along the slider", pts == sorted(set(pts), reverse=True), pts)
        check(f"{tag}: there is a choice to make", len(pts) >= 3, pts)
        bad = [x for x in o["options"]
               if T.resample_at(feats, "name", names, x["tol"], meth)["handles"]["after"] != x["points"]]
        check(f"{tag}: picking a notch gives exactly the count it showed", not bad, bad[:2])
        if meth == "shape":
            check(f"{tag}: Fewest points only ever lowers the count", all(p < o["before"] for p in pts))
    o = T.resample_options(feats, "name", ["HPF"], "even")
    check("even spacing can also ADD points (a fine spacing)", o["options"][0]["points"] > o["before"],
          f"{o['before']} -> {o['options'][0]['points']}")

    print("\nTidy points (one click): no points bunched together, outline barely moves")

    def bunched(g, px=40.0):
        n = 0
        for poly in T._polys(g):
            c = list(poly.exterior.coords)[:-1]
            m = len(c)
            near = [False] * m
            for i in range(m):
                a, b = c[i], c[(i + 1) % m]
                if math.hypot(a[0] - b[0], a[1] - b[1]) < px:
                    near[i] = near[(i + 1) % m] = True
            n += sum(near)
        return n

    for nm, most_left in (("HPF", 0), ("CP", 0), ("TH", 4), ("dft", 4)):
        o = T.resample_options(feats, "name", [nm], "shape")
        gentle = [x for x in o["options"] if not x["deform"] and x["moved"] <= T.TIDY_MOVE]
        check(f"{nm}: there is a gentle Tidy option", bool(gentle))
        if not gentle:
            continue
        pick = gentle[-1]
        r = T.resample_at(feats, "name", [nm], pick["tol"], "shape")
        g1 = geom(r["features"], nm)
        b0, b1 = bunched(others0[nm]), bunched(g1)
        check(f"{nm}: Tidy moves the outline no more than {T.TIDY_MOVE:g} px",
              others0[nm].boundary.hausdorff_distance(g1.boundary) <= T.TIDY_MOVE + 1e-6,
              f"{pick['moved']} px")
        check(f"{nm}: Tidy leaves at most {most_left} points bunched together", b1 <= most_left,
              f"{b0} -> {b1} bunched, {o['before']} -> {pick['points']} points")
        void = c0.difference(cover(r["features"])).intersection(geom(r["features"], "hemi"))
        check(f"{nm}: Tidy opens no gap inside the section", void.area <= 250.0, f"{void.area:.0f} px2")

if real.exists():
    print("\nthe whole tidy (tidy_region + the delete-style finish), as the page runs it")

    def overlaps(fs, nm):
        g = geom(fs, nm)
        return {f["properties"]["name"]: g.intersection(shape(f["geometry"])).area
                for f in fs if f["properties"]["name"] not in (nm, "hemi")}

    for nm, most_left in (("HPF", 0), ("CP", 0), ("TH", 2), ("dft", 2), ("ISO", 0)):
        f1, pick = T.tidy_region(feats, "name", nm)
        fin = T.tidy_finish(f1, "name", nm, respace=False, original=feats, merge_fixed=True)
        fs = fin["features"]
        b1 = T.bunched_count(fs, "name", nm)
        check(f"{nm}: at most {most_left} points left bunched", b1 <= most_left,
              f"{T.bunched_count(feats, 'name', nm)} -> {b1}, "
              f"{T.point_count(feats, 'name', nm)} -> {T.point_count(fs, 'name', nm)} points")
        void = c0.difference(cover(fs)).intersection(geom(fs, "hemi"))
        check(f"{nm}: no new gap inside the section", void.area <= 250.0, f"{void.area:.0f} px2")
        o0, o1 = overlaps(feats, nm), overlaps(fs, nm)
        grew = {k: round(o1[k] - o0.get(k, 0.0), 1) for k in o1 if o1[k] - o0.get(k, 0.0) > 0.5}
        check(f"{nm}: no overlap with any region grows", not grew, grew)
        check(f"{nm}: the outline moves no more than 60 px",
              others0[nm].boundary.hausdorff_distance(geom(fs, nm).boundary) <= 60.0)
    # RSP lies inside ISO: tidying ISO must not push it out (it pushed 72,292 px2 out)
    f1, _ = T.tidy_region(feats, "name", "ISO")
    out0 = others0["RSP"].difference(others0["ISO"]).area
    out1 = geom(f1, "RSP").difference(geom(f1, "ISO")).area
    check("tidying ISO doesn't push RSP (which lies inside it) out", out1 - out0 <= 0.5,
          f"{out1 - out0:+.0f} px2")

print("\n_ring_flaws (plain arithmetic) agrees with the shapely version")
from shapely.geometry import LineString as _LS   # noqa: E402


def _flaws_shapely(cs, max_deg=2.0, min_len=3.0, needle_tol=0.01, window=8):
    pts = cs[:-1] if len(cs) > 1 and tuple(cs[0]) == tuple(cs[-1]) else cs
    n = len(pts)
    if n < 3:
        return 0
    flaws = 0
    for i in range(n):
        p0, p1, p2 = pts[i - 1], pts[i], pts[(i + 1) % n]
        ax, ay = p0[0] - p1[0], p0[1] - p1[1]
        bx, by = p2[0] - p1[0], p2[1] - p1[1]
        la, lb = math.hypot(ax, ay), math.hypot(bx, by)
        if la >= min_len and lb >= min_len:
            c = max(-1.0, min(1.0, (ax * bx + ay * by) / (la * lb)))
            if math.degrees(math.acos(c)) < max_deg:
                flaws += 1
    ring = list(pts) + [pts[0]]
    for k in range(n):
        ea = _LS([ring[k], ring[k + 1]])
        if ea.length < 2.0:
            continue
        for m in range(k + 2, min(k + window, n)):
            if {tuple(ring[k]), tuple(ring[k + 1])} & {tuple(ring[m]), tuple(ring[m + 1])}:
                continue
            eb = _LS([ring[m], ring[m + 1]])
            if eb.length < 2.0 or ea.distance(eb) >= needle_tol:
                continue
            short, other = (ea, eb) if ea.length <= eb.length else (eb, ea)
            if short.interpolate(0.5, normalized=True).distance(other) < needle_tol:
                flaws += 1
    return flaws


import random   # noqa: E402
random.seed(7)
rings = []
for _ in range(400):
    p = [(random.uniform(0, 40), random.uniform(0, 40)) for _ in range(random.randint(4, 12))]
    if random.random() < 0.4:
        p += [p[1], (p[0][0] + 0.004, p[0][1])]          # needles and doubled-back edges
    rings.append(p + [p[0]])
rings.append([(0, 0), (10, 0), (10, 10), (10, 0.005), (20, 0), (20, 20), (0, 20), (0, 0)])
if real.exists():
    for f in feats:
        for poly in T._polys(shape(f["geometry"])):
            rings.append([tuple(c) for c in poly.exterior.coords])
mism = [r for r in rings if _flaws_shapely(r) != T._ring_flaws(r)]
check(f"same answer on {len(rings)} rings (random, needles, real)", not mism, len(mism))

# "the slider is equivalent to number of points. it should go down to 3": past
# the guarded fewest, every count down to 3, each a valid outline with exactly
# that many points, only that region changing, the page warned (deform)
print("\nany count down to 3")
sq = [feat("A", Polygon([(0, 0), (50, 0), (100, 0), (100, 50), (100, 100), (50, 100),
                         (0, 100), (0, 50)], [[(40, 40), (60, 40), (60, 60), (40, 60)]])),
      feat("B", Polygon([(100, 0), (200, 0), (200, 100), (100, 100), (100, 50)]))]
for method in ("shape", "even"):
    o = T.resample_options(sq, "name", ["A"], method)
    pts = [x["points"] for x in o["options"]]
    check(f"{method}: the square with a hole reaches 3 points", pts and pts[-1] == 3, pts)
    r = T.resample_at(sq, "name", ["A"], -3.0, method)
    g = geom(r["features"], "A")
    check(f"{method}: 3 points is a valid triangle, hole gone",
          g.is_valid and g.geom_type == "Polygon" and len(g.exterior.coords) == 4
          and not list(g.interiors), g.wkt)
    check(f"{method}: B is left alone", geom(r["features"], "B").equals(geom(sq, "B")))
if real.exists():
    for nm in ("TH", "HPF", "VL.1"):
        for method in ("shape", "even"):
            o = T.resample_options(feats, "name", [nm], method)
            below = [x for x in o["options"] if x["tol"] < 0]
            pts = [x["points"] for x in o["options"]]
            bad = []
            # the counts carry on from the fewest guarded result, whose shared
            # borders already moved in the neighbours too: from there on, alone
            safe = [x for x in o["options"] if x["tol"] > 0]
            base = (T.resample_at(feats, "name", [nm], safe[-1]["tol"], method)["features"]
                    if safe else feats)
            for x in below:
                r = T.resample_at(feats, "name", [nm], x["tol"], method)
                ks = T._index_all(r["features"], "name")[nm]
                ok = all(shape(r["features"][k]["geometry"]).is_valid for k in ks)
                others = all(shape(r["features"][k]["geometry"]).equals_exact(
                                 shape(base[k]["geometry"]), 0) for k in range(len(feats))
                             if k not in ks)
                if not ok or not others or T.point_count(r["features"], "name", nm) != x["points"]:
                    bad.append(x["points"])
            check(f"{method} {nm}: one point at a time down to 3, all deform-marked, valid, alone",
                  pts[-1] == 3 and all(x["deform"] for x in below)
                  and [x["points"] for x in below] == list(range(below[0]["points"], 2, -1))
                  and not bad, (pts[-4:], bad))

print()
if fails:
    print(f"{len(fails)} FAILED")
    sys.exit(1)
print("all checks passed")

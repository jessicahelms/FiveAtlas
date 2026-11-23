"""Smooth the pixel stairs of an old, unsmoothed prediction as it is loaded.

Predictions made before outlines were always smoothed (v1.5.1 and earlier, and the
`*_prediction*.geojson` files on Box) trace the model's ~10 um pixels: every edge is
horizontal or vertical, 400-2,000 points a region. They look boxy and made Resample
points' counts take minutes. This finds those regions and smooths them the way
Predict does now.

What it must never do is change a region somebody has edited, or move ground out
from under one. So a region is smoothed only if ALL of this holds:

  * the caller says it may be (it came from a `*_prediction*` file, and nothing on
    the file's edit trail names it -- app.py decides that);
  * EVERY edge is horizontal or vertical, it has >= MIN_EDGES of them, and it is not
    an outline round the others (hemi);
  * it sits on the SAME pixel grid as at least MIN_GROUP other such regions -- the
    grid is found per region (pixel size and offset) and regions are grouped by it,
    so a region moved by hand (its corners now off the grid of the rest) is on a
    grid of its own and left alone, and two predictions at different scales are
    each smoothed on their own grid;
  * EVERY corner is on that grid, within GRID_TOL_PX;
  * it does not overlap another region of its grid by more than one model pixel
    (a prediction never overlaps itself; a region moved by whole pixels does).

Then each grid's regions are smoothed TOGETHER, from one pixel picture, so every
border between two of them is one curve used by both sides. A region that is not
smoothed and lies BESIDE them (it touches, it does not sit on top of them) is kept
exactly: the smoothed ones are fitted to its edge. A shape lying ON the regions --
damage, an ROI, a drawn box, hemi -- is never fitted to and never changed; a
smoothed region stays inside an outline that held it. A region whose area would
change by more than MAX_AREA_CHANGE is left as it was (its neighbours fit to it).

Only the smoothed regions come back; the caller swaps their outlines in and leaves
every other feature as it is.
"""
from __future__ import annotations

import collections
import math
import re
import time

import numpy as np

MIN_EDGES = 50          # a whole region; a small extra piece of a boxy map needs >= 8
MIN_GROUP = 3           # regions that must share a grid before any of them is smoothed
MAX_AREA_CHANGE = 0.03  # real predictions change by <= 0.7%
AXIS_TOL_PX = 0.02      # an edge is horizontal/vertical if it is off by less than this
GRID_TOL_PX = 0.6       # a corner this close to the grid is on it (a file rounded to whole px still is)
TOUCH_FRAC = 0.10       # a fixed neighbour has less than this share of its area on the boxy regions
MAX_PICTURE_PX = 16_000_000

PREDICTION_FILE = re.compile(r"_prediction", re.I)


def is_prediction_file(name) -> bool:
    return bool(name) and bool(PREDICTION_FILE.search(str(name)))


def _rings(g):
    for p in getattr(g, "geoms", [g]):
        if p.geom_type != "Polygon" or p.is_empty:
            continue
        yield np.asarray(p.exterior.coords)[:, :2]
        for r in p.interiors:
            yield np.asarray(r.coords)[:, :2]


def _all_axis(g):
    """(edges, every edge horizontal or vertical?)"""
    e, ok = 0, True
    for cs in _rings(g):
        d = np.diff(cs, axis=0)
        e += len(d)
        if ok and not bool(((np.abs(d[:, 0]) < AXIS_TOL_PX) | (np.abs(d[:, 1]) < AXIS_TOL_PX)).all()):
            ok = False
    return e, ok


def _lengths(g):
    hx, hy, xs, ys = [], [], [], []
    for cs in _rings(g):
        d = np.diff(cs, axis=0)
        hx.append(np.abs(d[np.abs(d[:, 1]) < AXIS_TOL_PX, 0]))
        hy.append(np.abs(d[np.abs(d[:, 0]) < AXIS_TOL_PX, 1]))
        xs.append(cs[:, 0])
        ys.append(cs[:, 1])
    cat = lambda v: np.concatenate(v) if v else np.zeros(0)   # noqa: E731
    return cat(hx), cat(hy), cat(xs), cat(ys)


def _off_multiple(L, s):
    k = np.maximum(np.round(L / s), 1)
    return np.abs(L - k * s) > max(0.1 * s, 2 * GRID_TOL_PX)


def _step(lengths):
    """The pixel size: the unit every stair length is a whole multiple of."""
    L = np.asarray(lengths, float)
    L = L[L > 0.5]
    if len(L) < 4:
        return None
    s = float(collections.Counter(np.round(L, 1)).most_common(1)[0][0])
    # the commonest length can be 2 or 3 pixels; if many lengths are not multiples
    # of it, the pixel is a fraction of it
    if _off_multiple(L, s).mean() > 0.02:
        for d in (2, 3):
            if s / d > 0.5 and _off_multiple(L, s / d).mean() <= 0.02:
                s /= d
                break
        else:
            return None
    for _ in range(3):                       # refine on every length, long ones included
        k = np.maximum(np.round(L / s), 1)
        ok = np.abs(L - k * s) < 0.1 * s
        if not ok.any():
            return None
        s = float(L[ok].sum() / k[ok].sum())
    return s


def _offset(v, s):
    """Where the grid of step s sits, from corner coordinates v (a circular mean)."""
    a = 2 * math.pi * (np.asarray(v, float) / s)
    return (math.atan2(np.sin(a).mean(), np.cos(a).mean()) / (2 * math.pi) * s) % s


def _off_grid(v, s, o):
    r = (np.asarray(v, float) - o) / s
    return np.abs(r - np.round(r)) * s          # distance to the nearest grid line, px


def _circ_diff(a, b, s):
    d = abs(a - b) % s
    return min(d, s - d)


def _area_geom(g):
    return (g is not None and not g.is_empty and g.geom_type in ("Polygon", "MultiPolygon")
            and g.area > 0)


def find_groups(geoms, eligible):
    """[(member indexes, (sx, sy, ox, oy))] -- one entry per pixel grid that at least
    MIN_GROUP eligible, all-stairs regions share; plus why others were passed over."""
    import shapely
    from shapely.ops import unary_union

    why = {}
    cand = []
    for i in eligible:
        g = geoms[i]
        if not _area_geom(g) or not g.is_valid:
            continue
        e, axis = _all_axis(g)
        if axis and e >= MIN_EDGES:
            cand.append(i)
    if len(cand) < MIN_GROUP:
        return [], why
    # an outline round the others (hemi drawn as stairs) is not a peer
    u_all = unary_union([geoms[i] for i in cand])
    keep = []
    for i in cand:
        if geoms[i].area > 0.5 * u_all.area and u_all.difference(geoms[i]).area < 0.2 * u_all.area:
            why[i] = "outline round the others"
            continue
        keep.append(i)
    cand = keep
    # each region's own grid
    own = {}
    for i in cand:
        hx, hy, xs, ys = _lengths(geoms[i])
        sx, sy = _step(hx), _step(hy)
        if not sx or not sy:
            continue
        own[i] = (sx, sy, _offset(xs, sx), _offset(ys, sy))
    clusters = []
    for i, (sx, sy, ox, oy) in own.items():
        for c in clusters:
            gsx, gsy, gox, goy = own[c[0]]
            if (abs(sx - gsx) < 0.005 * gsx and abs(sy - gsy) < 0.005 * gsy
                    and _circ_diff(ox, gox, gsx) < 2 * GRID_TOL_PX + 0.01 * gsx
                    and _circ_diff(oy, goy, gsy) < 2 * GRID_TOL_PX + 0.01 * gsy):
                c.append(i)
                break
        else:
            clusters.append([i])
    groups = []
    for c in clusters:
        if len(c) < MIN_GROUP:
            for i in c:
                why[i] = "on a pixel grid of its own (moved or edited)"
            continue
        hx = np.concatenate([_lengths(geoms[i])[0] for i in c])
        hy = np.concatenate([_lengths(geoms[i])[1] for i in c])
        sx, sy = _step(hx), _step(hy)
        if not sx or not sy:
            continue
        xs = np.concatenate([_lengths(geoms[i])[2] for i in c])
        ys = np.concatenate([_lengths(geoms[i])[3] for i in c])
        ox, oy = _offset(xs, sx), _offset(ys, sy)
        grid = (sx, sy, ox, oy)

        def on_grid(i):
            _, _, x, y = _lengths(geoms[i])
            return bool((_off_grid(x, sx, ox) < GRID_TOL_PX).all()
                        and (_off_grid(y, sy, oy) < GRID_TOL_PX).all())

        members = []
        for i in c:
            if on_grid(i):
                members.append(i)
            else:
                why[i] = "corners off the pixel grid (edited)"
        # small pieces of the same map: 8-49 edges, all stairs, every corner on THIS grid
        for i in eligible:
            if i in members or i in why or i in own or not _area_geom(geoms[i]) \
                    or not geoms[i].is_valid:
                continue
            e, axis = _all_axis(geoms[i])
            if axis and e >= 8 and on_grid(i):
                members.append(i)
        # a prediction never overlaps itself; a region moved by whole pixels does
        if members:
            tree = shapely.STRtree([geoms[i] for i in members])
            a, b = tree.query([geoms[i] for i in members], predicate="intersects")
            bad = set()
            for p, q in zip(a, b):
                if p < q and geoms[members[p]].intersection(geoms[members[q]]).area > sx * sy:
                    bad.update((members[p], members[q]))
            for i in bad:
                why[i] = "overlaps another region (moved or edited)"
            members = [i for i in members if i not in bad]
        if len(members) >= MIN_GROUP:
            groups.append((sorted(members), grid))
        else:
            for i in members:
                why[i] = "too few regions left on its pixel grid"
    return groups, why


def smooth(features, eligible, smooth_lab, overlays=()):
    """features: GeoJSON features. eligible: indexes that may be smoothed (from a
    prediction file, not named by any edit). overlays: indexes of shapes that lie ON
    the regions (damage) -- never fitted to. smooth_lab: predict._smooth_regions.

    Returns {"smoothed": {index: geometry dict}, "kept": {index: why},
             "grids": [[W, H, sx, sy], ...], "seconds": float}."""
    import shapely
    from shapely import affinity
    from shapely.geometry import MultiPolygon, Polygon, mapping, shape
    from shapely.ops import unary_union

    t0 = time.perf_counter()
    geoms = []
    for f in features:
        try:
            g = shape(f["geometry"]) if isinstance(f, dict) and f.get("geometry") else Polygon()
            if not np.isfinite(shapely.get_coordinates(g)).all():
                g = Polygon()
        except Exception:
            g = Polygon()
        geoms.append(g)
    n = len(features)
    eligible = sorted({int(i) for i in eligible if 0 <= int(i) < n})
    overlays = {int(i) for i in overlays if 0 <= int(i) < n}
    eligible = [i for i in eligible if i not in overlays]
    groups, why = find_groups(geoms, eligible)
    out, kept, grids = {}, {}, []

    def valid(g):
        return g if g.is_valid else shapely.make_valid(g)

    def polys(g):
        ps = [p for p in shapely.get_parts(valid(g)) if p.geom_type == "Polygon" and p.area > 0]
        return ps[0] if len(ps) == 1 else MultiPolygon(ps) if ps else Polygon()

    # everything not smoothed is judged once against all candidate stairs
    boxy_all = {i for m, _ in groups for i in m}
    if not boxy_all:
        return {"smoothed": {}, "kept": {}, "grids": [], "seconds": round(time.perf_counter() - t0, 3)}
    boxy_u = unary_union([geoms[i] for i in boxy_all])
    containers, fixed, on_top = [], [], []
    for i, g in enumerate(geoms):
        if i in boxy_all or not _area_geom(g):
            continue
        gv = valid(g)
        inside = gv.intersection(boxy_u).area
        if i in overlays:
            on_top.append(gv)                           # damage: lies on its host
        elif inside > 0.8 * boxy_u.area and gv.area >= boxy_u.area * 0.8:
            containers.append(gv)                       # hemi / the section outline
        elif inside < TOUCH_FRAC * gv.area:
            fixed.append(gv)                            # a real neighbour: kept exactly
        else:
            on_top.append(gv)                           # an ROI, a drawn box, a moved region
    # under a shape lying on the regions nothing changes: the stairs stay there, so
    # what it covers (cells, damage hosts, overlaps) is exactly what it covered
    containers_u = unary_union(containers) if containers else None
    on_top_u = unary_union(on_top) if on_top else Polygon()
    if not on_top_u.is_empty:
        shapely.prepare(on_top_u)

    done = {}                                           # smoothed so far (earlier grids)
    for members, (sx, sy, ox, oy) in groups:
        b = np.array([geoms[i].bounds for i in members])
        x0 = ox + math.floor((b[:, 0].min() - ox) / sx + 1e-6) * sx
        y0 = oy + math.floor((b[:, 1].min() - oy) / sy + 1e-6) * sy
        W = int(math.ceil((b[:, 2].max() - x0) / sx - 1e-6))
        H = int(math.ceil((b[:, 3].max() - y0) / sy - 1e-6))
        if W * H > MAX_PICTURE_PX or W <= 0 or H <= 0:
            for i in members:
                kept[i] = "too big to smooth here"
            continue
        grids.append([W, H, round(sx, 3), round(sy, 3)])
        lab = np.zeros((H, W), np.int32)
        for k, i in enumerate(members, 1):
            g = geoms[i]
            a, bb, c, d = g.bounds
            c0, c1 = max(int((a - x0) / sx) - 1, 0), min(int((c - x0) / sx) + 2, W)
            r0, r1 = max(int((bb - y0) / sy) - 1, 0), min(int((d - y0) / sy) + 2, H)
            cy, cx = np.mgrid[r0:r1, c0:c1]
            shapely.prepare(g)
            m = shapely.contains_xy(g, x0 + (cx.ravel() + 0.5) * sx, y0 + (cy.ravel() + 0.5) * sy)
            sub = lab[r0:r1, c0:c1].ravel()
            sub[m] = k
            lab[r0:r1, c0:c1] = sub.reshape(r1 - r0, c1 - c0)
        sm = smooth_lab(lab)
        smooth_g = {}
        for k, i in enumerate(members, 1):
            g = sm.get(k)
            smooth_g[i] = (None if g is None or g.is_empty
                           else affinity.affine_transform(g, [sx, 0, 0, sy, x0, y0]))
        # what this grid's regions fit to: real neighbours, other grids' regions (as
        # smoothed, or as they are), and every boxy region this grid keeps
        others = [done[j] if j in done else valid(geoms[j]) for j in boxy_all if j not in members]
        held = [i for i in members if smooth_g[i] is None]
        for i in held:
            kept[i] = "vanished when drawn on the pixel grid"
        new = {}
        for _ in range(6):
            fix = fixed + others + [geoms[i] for i in held]
            fixed_u = unary_union(fix) if fix else Polygon()
            if not fixed_u.is_empty:
                shapely.prepare(fixed_u)
            new = {}
            for i in members:
                if i in held:
                    continue
                g = smooth_g[i]
                if not fixed_u.is_empty and g.intersects(fixed_u):
                    g = g.difference(fixed_u)           # the fixed edge wins, exactly
                new[i] = g
            if not fixed_u.is_empty:
                # ground a region had right beside a fixed one that smoothing let go of
                # goes back to it, so no gap opens against the region kept as it is
                near = fixed_u.buffer(2 * max(sx, sy))
                taken = unary_union(list(new.values()) + [fixed_u])
                for i, g in new.items():
                    back = geoms[i].intersection(near).difference(taken)
                    if not back.is_empty and back.area > 0:
                        new[i] = unary_union([g, back])
            for c in containers:                        # stay inside hemi, as before
                for i, g in list(new.items()):
                    if geoms[i].difference(c).area < 0.01 * geoms[i].area:
                        new[i] = g.intersection(c)
            if containers_u is not None:
                # inside hemi, ground a region covered that smoothing left to no region
                # (rounded-off stair corners along the tissue edge) goes back to it:
                # cells there stay in the region they were in, never "unassigned"
                taken = unary_union(list(new.values()) + ([fixed_u] if not fixed_u.is_empty else []))
                for i, g in list(new.items()):
                    back = valid(geoms[i]).intersection(containers_u).difference(taken)
                    if not back.is_empty and back.area > 0:
                        new[i] = unary_union([g, back])
                        taken = unary_union([taken, back])
            if not on_top_u.is_empty:
                for i, g in list(new.items()):
                    if g.intersects(on_top_u) or geoms[i].intersects(on_top_u):
                        new[i] = unary_union([g.difference(on_top_u),
                                              valid(geoms[i]).intersection(on_top_u)])
            bad = []
            for i, g in list(new.items()):
                g = polys(g)
                a0 = geoms[i].area
                if g.is_empty or not g.is_valid or abs(g.area - a0) > MAX_AREA_CHANGE * a0:
                    bad.append(i)
                new[i] = g
            if not bad:
                break
            for i in bad:
                kept[i] = f"would change its area by more than {MAX_AREA_CHANGE:.0%}"
            held += bad
        else:
            # still changing after six rounds: nothing from this grid, rather than a
            # kept region overlapping a neighbour smoothed against its old self
            for i in members:
                kept.setdefault(i, "could not be fitted to its neighbours")
            continue
        for i, g in new.items():
            done[i] = g
            out[i] = mapping(g)
    return {"smoothed": out, "kept": kept, "grids": grids,
            "seconds": round(time.perf_counter() - t0, 3)}

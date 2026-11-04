"""Planar topology for region GeoJSON -- the foundation for shared-border editing.

A border shared by two regions lives in the spaghetti GeoJSON as two independent
(and usually non-coincident) copies. Here we snap coordinates to a grid (so near-
coincident borders become exactly coincident) and intersect region boundaries to
recover the ONE shared arc between each adjacent pair. That shared arc is what the
editor will let you drag so both regions move together.

Fresh build (no dependency on snap_borders); uses only shapely/GEOS geometry.
"""
from __future__ import annotations

import itertools
import json
import math
import os
import re

from shapely import set_precision
from shapely.geometry import shape, mapping, LineString, Polygon, Point, MultiPoint
from shapely.ops import (linemerge, nearest_points, polygonize, split, unary_union,
                         voronoi_diagram)
from shapely.strtree import STRtree

# Damage shapes sit INSIDE the tissue, so anything that reasons about the extent
# of the section has to leave them out. damage.py is pure (shapely only), so this
# import adds no cycle.
import damage as _damage


def _lines(geom):
    """Flatten any geometry to its LineString parts."""
    out = []
    if geom is None or geom.is_empty:
        return out
    t = geom.geom_type
    if t == "LineString":
        out.append(geom)
    elif t == "MultiLineString":
        out.extend(geom.geoms)
    elif t == "GeometryCollection":
        for g in geom.geoms:
            out.extend(_lines(g))
    return out


def _polys(geom):
    """Flatten any geometry to its Polygon parts."""
    if geom is None or geom.is_empty:
        return []
    t = geom.geom_type
    if t == "Polygon":
        return [geom]
    if t == "MultiPolygon":
        return list(geom.geoms)
    if t == "GeometryCollection":
        out = []
        for g in geom.geoms:
            out.extend(_polys(g))
        return out
    return []


def _valid(geom):
    """`geom` made valid WITHOUT dropping ground. buffer(0) keeps only one lobe
    of a self-crossing outline -- a figure-8 of two 5,000 px2 lobes came back as
    5,000 px2 and was reported "repaired" -- where make_valid keeps both. Only the
    polygon parts are kept (make_valid can also return the crossing's lines)."""
    if geom is None or geom.is_empty or geom.is_valid:
        return geom
    try:
        from shapely import make_valid
        parts = _polys(make_valid(geom))
        if parts:
            g = parts[0] if len(parts) == 1 else unary_union(parts)
            if not g.is_valid:
                g = g.buffer(0)
            if not g.is_empty:
                return g
    except Exception:
        pass
    return geom.buffer(0)


def _holes(geom):
    """Return interior rings as Polygon objects."""
    out = []
    for p in _polys(geom):
        for ring in p.interiors:
            h = Polygon(ring)
            if not h.is_empty and h.area > 0:
                out.append(h)
    return out


def _fill_new_unprotected_holes(geom, protected=None, preserve=None):
    """Fill holes created by partitioning unless they represent protected territory.

    Existing holes in the selected region are kept, and holes occupied by other
    non-selected regions are kept so pair-only sharing cannot cover a third region.
    """
    parts = _polys(geom)
    if not parts:
        return geom
    preserve_holes = _holes(preserve)
    preserve_holes = unary_union(preserve_holes) if preserve_holes else Polygon()
    protected = protected if protected is not None else Polygon()
    rebuilt = []
    for p in parts:
        keep = []
        for ring in p.interiors:
            h = Polygon(ring)
            if h.is_empty or h.area <= 0:
                continue
            keep_hole = False
            for guard in (protected, preserve_holes):
                if guard is None or guard.is_empty:
                    continue
                try:
                    if guard.intersection(h).area >= h.area * 0.25:
                        keep_hole = True
                        break
                except Exception:
                    pass
            if keep_hole:
                keep.append(list(ring.coords))
        rebuilt.append(Polygon(p.exterior.coords, keep))
    return (unary_union(rebuilt) if len(rebuilt) > 1 else rebuilt[0]).buffer(0)


def _snap_polys(geom, grid=0.01):
    """Snap polygon parts to a fine grid and union them, for hairline splits."""
    try:
        parts = _polys(set_precision(geom, float(grid)))
        if parts:
            return unary_union(parts).buffer(0)
    except Exception:
        pass
    return geom.buffer(0)


def _safe_difference(a, b):
    """Robust polygon difference for messy high-resolution region outlines."""
    last = None
    for grid in (None, 0.01, 0.1, 1.0):
        try:
            aa = a.buffer(0)
            bb = b.buffer(0)
            if grid is not None:
                aa = set_precision(aa, grid).buffer(0)
                bb = set_precision(bb, grid).buffer(0)
            return aa.difference(bb).buffer(0)
        except Exception as e:
            last = e
    if last:
        raise last
    return a.difference(b).buffer(0)


def _safe_intersection(a, b):
    """Robust polygon intersection for messy high-resolution region outlines."""
    last = None
    for grid in (None, 0.01, 0.1, 1.0):
        try:
            aa = a.buffer(0)
            bb = b.buffer(0)
            if grid is not None:
                aa = set_precision(aa, grid).buffer(0)
                bb = set_precision(bb, grid).buffer(0)
            return aa.intersection(bb).buffer(0)
        except Exception as e:
            last = e
    if last:
        raise last
    return a.intersection(b).buffer(0)


def _drop_small_parts(geom, min_area, preserve=None, focus=None, focus_pad=2.0,
                      drop_new_detached=False):
    """Drop tiny newly-created detached parts while preserving existing islands."""
    parts = _polys(geom)
    if len(parts) <= 1:
        return geom.buffer(0)
    largest_i = max(range(len(parts)), key=lambda i: parts[i].area)
    zone = None
    if focus is not None and not focus.is_empty:
        try:
            zone = focus.buffer(focus_pad)
        except Exception:
            zone = focus
    kept = []
    for i, p in enumerate(parts):
        existed = False
        if preserve is not None and not preserve.is_empty:
            try:
                existed = p.intersection(preserve).area >= min(p.area * 0.25, 1.0)
            except Exception:
                existed = p.distance(preserve) <= 1e-6
        near_focus = zone is None or p.distance(zone) <= 1e-6
        if drop_new_detached and i != largest_i and not existed:
            continue
        if i == largest_i or p.area >= min_area or (existed and not near_focus):
            kept.append(p)
    if not kept:
        kept = [parts[largest_i]]
    return (unary_union(kept) if len(kept) > 1 else kept[0]).buffer(0)


def _clean_complement_pair(gA, U, sliver, preserve_a=None, preserve_b=None,
                           protected=None):
    """Clean a moved shared border without breaking A/B complementarity.

    Cleaning A and B independently can leave little detached islands and matching
    holes along a formerly-gapped border. Instead, clean/prune one side and always
    rebuild the other from the same pair union.
    """
    # Endpoint crumbs from a local border drag can be hundreds of full-res pixels:
    # bigger than GEOS dust, but still not meaningful anatomy. Existing islands
    # are protected by `preserve`; this threshold only prunes newly-created bits.
    artifact_area = min(5000.0, max(750.0, sliver.area * 0.08))
    gA = clean_geom(_safe_intersection(gA, U), smooth=False)
    for _ in range(3):
        gA = _drop_small_parts(_safe_intersection(_snap_polys(gA), U), artifact_area,
                               preserve=preserve_a, focus=sliver,
                               drop_new_detached=True)
        gB = _drop_small_parts(_snap_polys(_safe_difference(U, gA)), artifact_area,
                               preserve=preserve_b, focus=sliver,
                               drop_new_detached=True)
        gA = _snap_polys(_safe_difference(U, gB))
    gA = _drop_small_parts(_safe_intersection(gA, U), artifact_area,
                           preserve=preserve_a, focus=sliver,
                           drop_new_detached=True)
    gB = _drop_small_parts(_snap_polys(_safe_difference(U, gA)), artifact_area,
                           preserve=preserve_b, focus=sliver,
                           drop_new_detached=True)
    gA = _snap_polys(_safe_difference(U, gB))
    protected = protected if protected is not None else Polygon()

    def _guard(other):
        if protected.is_empty:
            return other
        return unary_union([other, protected]).buffer(0)

    for _ in range(2):
        gA = _fill_new_unprotected_holes(_safe_intersection(gA, U), _guard(gB),
                                         preserve=preserve_a)
        gB = _drop_small_parts(_snap_polys(_safe_difference(U, gA)), artifact_area,
                               preserve=preserve_b, focus=sliver,
                               drop_new_detached=True)
        gB = _fill_new_unprotected_holes(gB, _guard(gA), preserve=preserve_b)
        gA = _snap_polys(_safe_difference(U, gB))
    gA = _snap_polys(_safe_intersection(gA, U))
    gB = _drop_small_parts(_snap_polys(_safe_difference(U, gA)), artifact_area,
                           preserve=preserve_b, focus=sliver,
                           drop_new_detached=True)
    gB = _fill_new_unprotected_holes(gB, _guard(gA), preserve=preserve_b)
    gA = _safe_difference(U, gB)
    gA = _drop_small_parts(_snap_polys(_safe_intersection(gA, U)), artifact_area,
                           preserve=preserve_a, focus=sliver,
                           drop_new_detached=True)
    gB = _drop_small_parts(_snap_polys(_safe_difference(U, gA)), artifact_area,
                           preserve=preserve_b, focus=sliver,
                           drop_new_detached=True)
    gA = _drop_small_parts(_snap_polys(_safe_difference(U, gB)), artifact_area,
                           preserve=preserve_a, focus=sliver,
                           drop_new_detached=True)
    gB = _snap_polys(_safe_difference(U, gA))
    return gA.buffer(0), gB.buffer(0)


def clean_geom(geom, spike=2.0, smooth=True):
    """Heal a polygon for display/editing: fix validity, shave off thin
    spikes/needles (a morphological open, kept only if it preserves the bulk of
    the shape so genuinely-thin regions survive), and drop tiny shards. This is
    what removes the 'weird line' artifacts that boolean ops can leave behind.

    smooth=False skips the morphological open -- use it after a border move, where a
    freshly-transferred sliver is legitimately thin and the open would erode it away
    (the inputs are already smoothed on load, so nothing new needs de-hairing)."""
    if geom is None or geom.is_empty:
        return geom
    g = geom if geom.is_valid else geom.buffer(0)
    if g.is_empty:
        return g
    if smooth:
        try:
            o = g.buffer(-spike, join_style=2).buffer(spike, join_style=2).buffer(0)
            if (not o.is_empty) and o.is_valid and o.area >= 0.7 * g.area:
                g = o
        except Exception:
            pass
    parts = _polys(g)
    if len(parts) > 1:
        # smooth: prune relative shards (boolean crumbs next to a big region).
        # gentle: keep everything real, drop only sub-pixel specks -- a border move
        # can legitimately leave B as body+sliver sharing an edge.
        #
        # The relative cutoff is CAPPED in absolute terms. Uncapped it scales with
        # the region, so the bigger a region is the bigger the islands it eats:
        # 0.2% of a 9,000,000 px^2 structure is 18,000 px^2, which silently deleted
        # real satellite lobes on every LOAD of the user's own file -- no error, no
        # warning, and persisted by the next Save. A numerical crumb left by a
        # boolean op is a handful of square pixels; anything appreciably larger is
        # somebody's anatomy, and this function has no business deciding otherwise.
        cutoff = min(max(p.area for p in parts) * 0.002, 100.0) if smooth else 25.0
        kept = [p for p in parts if p.area >= cutoff]
        if kept:
            g = unary_union(kept) if len(kept) > 1 else kept[0]
    g = g.buffer(0)
    # drop the extra vertices the buffer/open added, so outlines stay sparse
    try:
        s = g.simplify(max(spike * 0.75, 1.0))
        if (not s.is_empty) and s.is_valid and s.area >= 0.95 * g.area:
            g = s
    except Exception:
        pass
    return g


def _despike_open_ring(pts, eps=0.05):
    """An open ring's vertices with every needle and duplicate taken out.

    A needle tip is a vertex whose two edges leave in nearly the SAME direction
    and span no area: the outline goes out to it and comes back down the same
    line. A plain collinear vertex has its edges leaving in OPPOSITE directions,
    and is left alone. Removing a tip shortens the needle by one vertex, so this
    repeats until the ring stops changing.

    The answer is always a SUBSEQUENCE of the input, which is what lets the
    caller see which vertices went and group them back into needles.
    """

    def dedupe(seq):
        out = []
        for p in seq:
            if not out or abs(p[0] - out[-1][0]) > eps or abs(p[1] - out[-1][1]) > eps:
                out.append(p)
        while len(out) > 1 and abs(out[0][0] - out[-1][0]) <= eps \
                and abs(out[0][1] - out[-1][1]) <= eps:
            out.pop()
        return out

    pts = dedupe(pts)
    changed = True
    while changed and len(pts) > 3:
        changed = False
        n = len(pts)
        keep = [True] * n
        for i in range(n):
            # never take both tips of the same needle in one pass -- walk it down
            if not (keep[(i - 1) % n] and keep[(i + 1) % n]):
                continue
            a, b, c = pts[(i - 1) % n], pts[i], pts[(i + 1) % n]
            v1 = (a[0] - b[0], a[1] - b[1])
            v2 = (c[0] - b[0], c[1] - b[1])
            n1 = (v1[0] * v1[0] + v1[1] * v1[1]) ** 0.5
            n2 = (v2[0] * v2[0] + v2[1] * v2[1]) ** 0.5
            if n1 <= eps or n2 <= eps:
                keep[i] = False
                changed = True
                continue
            # |cross| is twice the triangle's area; divided by the longer edge it
            # is how wide the needle is at its base
            cross = abs(v1[0] * v2[1] - v1[1] * v2[0])
            if cross > eps * max(n1, n2):
                continue                      # encloses real area
            if v1[0] * v2[0] + v1[1] * v2[1] <= 0:
                continue                      # collinear pass-through, not a tip
            keep[i] = False
            changed = True
        if changed:
            pts = dedupe([p for p, k in zip(pts, keep) if k])
    return pts


def _needle_runs(pts, kept):
    """The dropped vertices of `pts`, grouped into the needles they came from.

    `kept` is a subsequence of `pts`, so the dropped ones fall into consecutive
    runs and one run is one needle. Returns (runs, keep_flags); each run is
    (indices, path), where `path` carries the kept vertex on either side too --
    the needle's way out and back, which is what a traced loop has to touch.
    Returns None if `kept` is not a clean subsequence, so the caller can decline
    to guess.
    """
    n = len(pts)
    flag = [False] * n
    j = 0
    for i, p in enumerate(pts):
        if j < len(kept) and p == kept[j]:
            flag[i] = True
            j += 1
    if j != len(kept) or not any(flag):
        return None
    # start the walk on a KEPT vertex so no run wraps past the end, and close
    # the cycle by visiting it again
    start = flag.index(True)
    order = list(range(start, n)) + list(range(start)) + [start]
    runs, run, prev = [], [], None
    for i in order:
        if flag[i]:
            if run:
                runs.append((list(run),
                             [pts[prev]] + [pts[r] for r in run] + [pts[i]]))
                run = []
            prev = i
        else:
            run.append(i)
    return runs, flag


def _despike_ring(coords, eps=0.05, within=None):
    """One ring with its needles removed, or None if nothing of it survives.

    `within`: a polygon -- usually a loop the annotator traced. A needle goes if
    the loop meets it ANYWHERE, so circling a stretch of a long line is enough
    (the same rule clean_lines applies to the sliver parts it removes); a needle
    the loop never reaches is put back, so cleaning one spot leaves the rest of
    the outline exactly as it was.
    """
    pts = [(float(x), float(y)) for x, y in coords]
    if len(pts) > 1 and pts[0] == pts[-1]:
        pts = pts[:-1]
    kept = _despike_open_ring(pts, eps)
    if within is not None and not within.is_empty and len(kept) != len(pts):
        grouped = _needle_runs(pts, kept)
        if grouped is None:
            kept = pts                      # cannot tell what went: change nothing
        else:
            runs, flag = grouped
            wx0, wy0, wx1, wy1 = within.bounds
            for idxs, path in runs:
                xs = [p[0] for p in path]
                ys = [p[1] for p in path]
                # bounding boxes first -- a real outline carries thousands
                touches = not (max(xs) < wx0 or min(xs) > wx1
                               or max(ys) < wy0 or min(ys) > wy1)
                if touches:
                    touches = LineString(path).intersects(within)
                if not touches:
                    for i in idxs:
                        flag[i] = True      # out of scope: put this needle back
            kept = [p for p, k in zip(pts, flag) if k]
    if len(kept) < 3:
        return None
    return kept + [kept[0]]


def despike(geom, eps=0.05, within=None):
    """Remove zero-width needles from a polygon WITHOUT reshaping it.

    Unlike clean_geom's morphological open this cannot erode a genuinely thin
    region, because it only deletes vertices that enclose no area. A needle has
    no area, so the area must come out unchanged; if it does not, the input is
    returned untouched rather than quietly reshaping somebody's anatomy.

    `within`: a polygon -- usually a loop the annotator traced -- limiting this
    to needles whose path crosses it, so cleaning one spot leaves the rest of
    the outline exactly as it was.
    """
    if geom is None or geom.is_empty:
        return geom
    parts = _polys(geom)
    if not parts:
        return geom
    out = []
    for p in parts:
        ext = _despike_ring(p.exterior.coords, eps, within)
        if ext is None:
            continue
        holes = []
        for r in p.interiors:
            h = _despike_ring(r.coords, eps, within)
            if h is not None:
                holes.append(h)
        try:
            q = Polygon(ext, holes)
            if not q.is_valid:
                q = q.buffer(0)
        except Exception:
            q = p
        if not q.is_empty:
            out.append(q)
    if not out:
        return geom
    g = out[0].buffer(0) if len(out) == 1 else unary_union(out).buffer(0)
    if g.is_empty or not g.is_valid:
        return geom
    if abs(g.area - geom.area) > max(1.0, geom.area * 1e-4):
        return geom
    return g


def clean_features(features, id_prop, spike=2.0):
    """Return a copy of the feature list with every polygon cleaned."""
    out = json.loads(json.dumps(features))
    for f in out:
        try:
            g = shape(f["geometry"])
            f["geometry"] = mapping(clean_geom(g, spike))
        except Exception:
            pass
    return out


#: Thinning the shared-boundary network is about vertex density, not about how
#: far a gap may be bridged, so it must NOT scale with the snap reach. It used
#: to (`tol / 2`), which meant a long reach ran Douglas-Peucker at hundreds of
#: pixels and shredded the outlines: on the real TH, reach 5000 cost 24,232,344
#: px of a 95,303,653 px region and took it from 85 vertices to 26. The cap is
#: the value the default reach of 40 already produced, so that case is untouched.
MAX_THIN = 20.0

#: Douglas-Peucker tolerance for collinear fluff on a region's FREE edge.
#: The Voronoi sampling leaves all-but-collinear vertices in their thousands --
#: `opt` came back from a 36-vertex region with 3,227, `ISO` with 2,369 from 80,
#: and one click grew the 23-region file from 74,350 to 207,270 bytes (x2.79),
#: which is what Xenium Explorer then has to read. A hundredth of a pixel cannot
#: move an outline anywhere a person could see, and removes 3,178 of those 3,227
#: while keeping 100.000000% of the area. It is NOT used on the shared seam,
#: which is thinned at the reach instead.
FREE_EDGE_FLUFF = 0.01


def _partition_spike(tol):
    """Small, bounded spike width for share-border partition cleanup."""
    try:
        return max(2.0, min(6.0, float(tol) * 0.10))
    except Exception:
        return 3.0


def _clean_for_partition(geom, tol):
    """Remove needle artifacts before tiling, WITHOUT eroding the region.

    This was a morphological open/close -- clean_geom(smooth=True) -- whose only
    guard let it lose 35% of a region before falling back, so it quietly ate the
    outline it was handed. It accounted for essentially ALL of the ground a pair
    ended up losing; turning it off and measuring what the pair no longer
    claimed:

        CP + sAMY.1    3,938 px  ->  50 px
        CP + CTXsp     3,538 px  ->  40 px
        CP + lft       3,446 px  ->  56 px
        TH + HPF       1,323 px  ->  43 px
        HY + lft         822 px  ->  65 px

    despike() does the job this is here for -- it deletes vertices that enclose
    no area, which is exactly what a needle is -- and it returns its input
    untouched if the area moves at all, so it cannot erode anything. Across ten
    real pairs the needle counts came out no worse (dft+ISO's went 82.8 px to
    68.4), with overlap and order dependence unchanged at 0.000.

    An invalid outline is still repaired, because a self-intersecting polygon
    cannot be partitioned at all -- that is buffer(0), which does not erode.
    """
    if geom is None or geom.is_empty:
        return geom
    g = geom if geom.is_valid else geom.buffer(0)
    if g.is_empty:
        return geom
    d = despike(g, eps=max(_partition_spike(tol) * 0.25, 0.05))
    if not d.is_empty and abs(d.area - g.area) <= max(1.0, g.area * 1e-6):
        g = d
    return g


def _prep_regions(features, id_prop, grid):
    regions = {}
    for i, f in enumerate(features):
        nm = (f.get("properties") or {}).get(id_prop)
        nm = str(nm) if nm is not None else f"__idx_{i}"
        g = shape(f["geometry"])
        if not g.is_valid:
            g = g.buffer(0)
        if grid and grid > 0:
            g = set_precision(g, float(grid))
        if not g.is_empty:
            # same name twice = one multi-part region, not a replacement
            regions[nm] = unary_union([regions[nm], g]).buffer(0) if nm in regions else g
    return regions


def shared_borders(features, id_prop="name", grid=4.0, min_len=None):
    """Return the shared arcs between adjacent regions:
        [{regions: [A, B], points: [[x,y]...], length: L}]
    grid = snap tolerance in coordinate units (px). Larger => more borders merge.
    """
    min_len = (grid * 2) if min_len is None else min_len
    regions = _prep_regions(features, id_prop, grid)
    names = list(regions)
    bounds = {nm: regions[nm].boundary for nm in names}
    out = []
    for i in range(len(names)):
        A = regions[names[i]]
        Ab = bounds[names[i]]
        for j in range(i + 1, len(names)):
            B = regions[names[j]]
            if A.distance(B) > grid:
                continue
            parts = _lines(Ab.intersection(bounds[names[j]]))
            if not parts:
                continue
            merged = linemerge(parts) if len(parts) > 1 else parts[0]
            for ln in _lines(merged):
                if ln.length >= min_len:
                    out.append({
                        "regions": [names[i], names[j]],
                        "points": [[round(x, 2), round(y, 2)] for x, y in ln.coords],
                        "length": round(ln.length, 1),
                    })
    return out


def border_between(features, id_prop, region_a, region_b, grid=4.0, corridor=25.0,
                   simplify=4.0):
    """FAST shared-border detection: the slice of region_a's OWN outline that runs
    alongside region_b (within `corridor` px). Clean (one smooth outline -> no
    boundary-vs-boundary spikes) and cheap (no Voronoi / partition), so it's safe to
    call on every pick without the app bogging down."""
    idx = _index_all(features, id_prop)
    if region_a not in idx or region_b not in idx:
        return []
    ga = _body(features, idx[region_a])       # every lobe of the name, not the first
    gb = _body(features, idx[region_b])
    if ga.is_empty or gb.is_empty:
        return []
    # One inside the other: there is no coincident edge, but there IS a border
    # -- the inner's own outline is the line between them. Return it whole
    # (one arc per lobe), whichever of the two was picked first; the corridor
    # trick below is order-dependent for nested pairs and useless here.
    small, big = (ga, gb) if ga.area <= gb.area else (gb, ga)
    if small.area > 0:
        inter = _safe_intersection(small, big).area
        if inter / small.area >= CONTAINED_FRAC:
            out = []
            for poly in _polys(small):
                ring = LineString(poly.exterior.coords)
                if simplify and simplify > 0:
                    cand = ring.simplify(float(simplify))
                    if not cand.is_empty and cand.geom_type == "LineString"                             and len(cand.coords) >= 4:
                        ring = cand
                out.append([[round(x, 2), round(y, 2)] for x, y in ring.coords])
            return out
    parts = _lines(ga.boundary.intersection(gb.buffer(corridor, join_style=2)))
    if not parts:
        return []
    merged = linemerge(parts) if len(parts) > 1 else parts[0]
    out = []
    for ln in _lines(merged):
        if ln.length >= max(grid * 2, corridor):
            # simplify=0 returns the outline's OWN points. That matters after a
            # resample: evenly spaced points on a straight stretch are collinear,
            # so Douglas-Peucker deletes every one in between and turns 150 px
            # spacing into a 1,200 px jump -- silently undoing the redistribution.
            ls = ln
            if simplify and simplify > 0:
                cand = ln.simplify(float(simplify))
                if not cand.is_empty and cand.geom_type == "LineString":
                    ls = cand
            out.append([[round(x, 2), round(y, 2)] for x, y in ls.coords])
    return out


def bridge_if_needed(features, id_prop, region_a, region_b, tol=40.0):
    """If the two regions already touch, return them unchanged (no expensive work);
    only if there's a real gap do we partition the pair to make them adjacent."""
    idx = _index_all(features, id_prop)
    if region_a not in idx or region_b not in idx:
        return features
    A = _body(features, idx[region_a])
    B = _body(features, idx[region_b])
    if A.distance(B) <= 0.5:
        return features
    return partition_regions(features, id_prop, [region_a, region_b], tol=tol)["features"]


def region_gap(features, id_prop, region_a, region_b):
    """Pixel distance between two named regions; 0 means touching or overlapping."""
    idx = _index_all(features, id_prop)
    if region_a not in idx or region_b not in idx:
        raise ValueError(f"region not found: {region_a!r}/{region_b!r}")
    return float(_body(features, idx[region_a]).distance(_body(features, idx[region_b])))


# A region is "inside" another when this much of its area is swallowed. On real
# data the separation is total: genuine side-by-side neighbours overlap 0% of the
# smaller (they only touch), while a container swallows 94-100% of its contents.
# Anywhere in between would do; 90% sits in the empty middle of that gap.
CONTAINED_FRAC = 0.90


def containment(features, id_prop, names, thresh=CONTAINED_FRAC):
    """The first picked pair where one region simply contains the other.

    Sharing a border assumes two regions that sit SIDE BY SIDE and meet along a
    line. Given a container and its contents -- an outline of the whole hemisphere
    and a structure within it -- there is no such line: the nearest-region
    partition has nothing to divide and shreds the overlap into a fan of slivers.
    It used to do that silently, and the only way to find out was to look at the
    picture afterwards.

    Returns {outer, inner, frac} for the worst offending pair, or None.
    """
    idx = _index_all(features, id_prop)
    sel = [str(n) for n in names if str(n) in idx]
    worst = None
    for i in range(len(sel)):
        for j in range(i + 1, len(sel)):
            try:
                A, B = _body(features, idx[sel[i]]), _body(features, idx[sel[j]])
                a, b = sel[i], sel[j]
                if A.area < B.area:                     # A is always the bigger
                    A, B, a, b = B, A, b, a
                if B.area <= 0 or not A.intersects(B):
                    continue
                frac = A.intersection(B).area / B.area
            except Exception:
                continue
            if frac >= thresh and (worst is None or frac > worst["frac"]):
                worst = {"outer": a, "inner": b, "frac": round(float(frac), 4)}
    return worst


def containment_message(c) -> str:
    """Why the pick was refused, in the words the user would use."""
    pct = f"{100 * c['frac']:.0f}%"
    return (f'"{c["outer"]}" contains "{c["inner"]}"'
            + (f" ({pct} of it)" if c["frac"] < 0.999 else "")
            + " - one is inside the other, so tiling (Share borders) has "
              "nothing to divide. But their border IS editable: with just "
              f'these two picked, "{c["inner"]}"\'s outline appears as the '
              "shared border - drag it, and "
              f'"{c["outer"]}" keeps wrapping it.')


#: The widest band between a nested pair's coast and the container's outline
#: that Share borders treats as a border artefact and absorbs, however far the
#: reach slider goes. Wider ground is an unlabelled pocket -- somebody's future
#: region. Without it the close grew with the reach: at reach 5000, HY took
#: 7,461,012 px^2 of margin and ISO 4,011,440 (with a new spike).
NESTED_BAND_MAX = 100.0


def snap_to_container(features, id_prop, names, tol=40.0):
    """Merge a nested pair's borders WHERE THEY NEIGHBOUR: the hairline band
    between the inner's edge and the container's outline joins the inner, so
    the inner runs exactly to the outline along their shared stretch.

    "Share borders" for hemi + a coastal region, in other words. Absorbed
    ground is strictly limited:
      * only unclaimed ground (never another region's),
      * only hairline bands (erodes to nothing at tol/2 -- a fat unlabelled
        pocket is somebody's future region, not a border artefact),
      * only within `tol` of the container's own outline -- the band along
        the coast, never a corridor running inland.
    The container itself is untouched: it covered that ground before and
    covers it after. Returns {features, borders, sealed, stretches}.
    """
    c = containment(features, id_prop, names)
    if not c:
        raise ValueError("these regions are not nested -- Share borders "
                         "handles side-by-side pairs directly")
    idx = _index_all(features, id_prop)
    outer = _body(features, idx[c["outer"]])
    inner = _body(features, idx[c["inner"]])
    pair = set(idx[c["outer"]]) | set(idx[c["inner"]])
    blanket_names = set(blankets(features, id_prop, keep=[c["outer"], c["inner"]]))
    others = []
    for k, f in enumerate(features):
        if k in pair:
            continue
        if str((f.get("properties") or {}).get(id_prop)) in blanket_names:
            continue
        try:
            g = shape(f["geometry"])
            if not g.is_valid:
                g = g.buffer(0)
            if not g.is_empty:
                others.append(g)
        except Exception:
            pass
    protected = unary_union(others).buffer(0) if others else Polygon()

    free = _safe_difference(_safe_difference(outer, inner), protected)
    w = min(max(float(tol), 4.0), NESTED_BAND_MAX)
    # The band is the ground BETWEEN the two boundaries. A morphological
    # CLOSING of (inner + the outline) yields exactly that: the fill a
    # radius-tol/2 close adds is the ground spanning gaps up to `tol` wide
    # between the two -- the whole band, however it meanders. (The old
    # double-proximity zone at tol/4 qualified NOTHING once the band was
    # wider than tol/2, so the snap reported "merged" while visibly doing
    # nothing.) A closing also cannot creep: where the pair is already
    # flush there is no gap to fill, so repeat clicks converge instead of
    # each nibbling further along the coast. Fills between two stretches
    # of the OUTLINE itself (a notch in the coast) never touch the inner
    # and fall to the filters below, as do the inner's own inland notches
    # (they never reach the outline).
    r = w / 2.0
    U = unary_union([inner, outer.boundary.buffer(0.5)]).buffer(0)
    closed = U.buffer(r, join_style=2).buffer(-r, join_style=2).buffer(0)
    zone = _safe_intersection(_safe_difference(closed, U), free)
    fill = []
    edge = outer.boundary.buffer(1.0)
    for piece in _polys(zone):
        if piece.area <= 1e-6:
            continue
        if piece.distance(inner) > 0.5:
            continue                       # does not touch the inner
        if not piece.intersects(edge):
            continue                       # does not reach the outline
        # The docstring's promise, enforced: only a hairline BAND (it erodes to
        # nothing just past the close radius). A close can still fan out into a
        # concave corner of the coast; that fan is a pocket, not a band.
        try:
            if not piece.buffer(-(r + 0.5), join_style=2).is_empty:
                continue
        except Exception:
            pass
        # (No tip-shaving here: cutting a band's tip short left a razor notch
        # between it and the inner -- a new spike on SSp. The ISO spike that
        # prompted it came from the uncapped fan, which the cap above removes.)
        fill.append(piece)
    # The inner may also POKE PAST the container's outline (the containment
    # threshold admits up to ~10% outside). No amount of gap-filling merges
    # the borders there -- the band is on the wrong side. The cover
    # convention settles it: containers cover contents, so the container
    # GROWS to the inner's edge along the protrusion, and the two borders
    # coincide there too (exactly what a nested border drag already does).
    spill = _safe_difference(inner, outer)
    covered = float(spill.area) if not spill.is_empty else 0.0
    if covered <= 1e-6:
        covered = 0.0

    if not fill and covered == 0.0:
        return {"features": json.loads(json.dumps(features)), "borders": [],
                "sealed": 0.0, "stretches": 0, "covered": 0.0,
                "gapPx": round(float(inner.distance(outer.boundary)), 1),
                "outer": c["outer"], "inner": c["inner"]}

    new_inner = unary_union([inner] + fill).buffer(0)
    if fill:
        # The closing measured the gap from a hairline-thick outline, so a last
        # half-pixel skin against the coast is still unclaimed; take it in
        # wherever the inner now runs against the outline, so the two borders
        # end up genuinely coincident. Clipped to the inner's own footprint --
        # never along coast the inner does not reach.
        skin = _safe_intersection(_safe_intersection(free, outer.boundary.buffer(0.75)),
                                  new_inner.buffer(1.5))
        if not skin.is_empty and skin.area > 1e-6:
            fill.append(skin)
            new_inner = unary_union([new_inner, skin]).buffer(0)
    new_inner = _snap_polys(new_inner, 0.01)
    new_inner = despike(clean_geom(new_inner, smooth=False))   # needles only

    new_outer = outer
    if covered > 0.0:
        new_outer = despike(clean_geom(unary_union([outer, new_inner]).buffer(0),
                                       smooth=False))

    out = json.loads(json.dumps(features))
    _write_body(out, features, idx[c["inner"]], new_inner)
    if covered > 0.0:
        _write_body(out, features, idx[c["outer"]], new_outer)

    # the stretches now genuinely shared with the outline, for the drag UI
    arcs = []
    try:
        shared = new_inner.boundary.intersection(new_outer.boundary.buffer(2.0))
        parts = _lines(shared)
        merged = linemerge(parts) if len(parts) > 1 else (parts[0] if parts else None)
        if merged is not None:
            for ln in _lines(merged):
                if ln.length >= 8.0:
                    arcs.append({"points": [[round(x, 2), round(y, 2)]
                                            for x, y in ln.coords]})
    except Exception:
        arcs = []                # the merge stands even if the arc report fails
    return {"features": out, "borders": arcs,
            "sealed": float(sum(g.area for g in fill)), "stretches": len(fill),
            "covered": covered,
            "gapPx": round(float(new_inner.distance(new_outer.boundary)), 1),
            "outer": c["outer"], "inner": c["inner"]}


def summary(features, id_prop="name", grid=4.0):
    borders = shared_borders(features, id_prop, grid)
    per_region = {}
    total = 0.0
    for b in borders:
        total += b["length"]
        for r in b["regions"]:
            per_region[r] = per_region.get(r, 0.0) + b["length"]
    return {
        "n_regions": len({(f.get("properties") or {}).get(id_prop) for f in features}),
        "n_shared_borders": len(borders),
        "total_shared_length": round(total, 1),
        "per_region": {k: round(v, 1) for k, v in sorted(per_region.items())},
    }


def _splice_handles(lobe, old_ring, new_ring, eps=1e-6):
    """`lobe` with each run of moved handles spliced into its real outline.

    `old_ring` / `new_ring` are the drag handles before and after (closed, same
    length): a simplified copy of the lobe's exterior, so every handle is one of
    its vertices. For each run of handles that moved, the stretch of the real
    outline between the unmoved handles either side is replaced by the moved
    handles; everything else keeps every vertex. Returns None when that cannot
    be done exactly (handles added or removed, a handle not on the outline, the
    lobe not one polygon), and the caller falls back."""
    try:
        if lobe is None or lobe.geom_type != "Polygon":
            return None
        old = [tuple(map(float, p)) for p in old_ring[:-1]]
        new = [tuple(map(float, p)) for p in new_ring[:-1]]
        if len(old) != len(new) or len(old) < 3:
            return None
        changed = [abs(a[0] - b[0]) > eps or abs(a[1] - b[1]) > eps for a, b in zip(old, new)]
        if not any(changed):
            return lobe
        if all(changed):
            return None                    # the whole ring moved: nothing to anchor to
        coords = [tuple(c) for c in list(lobe.exterior.coords)[:-1]]
        n, m = len(coords), len(old)

        def at(p):
            # border_between hands the handles back to within ~0.007 px of the
            # outline's own vertices, not exactly on them
            hits = [i for i, c in enumerate(coords)
                    if abs(c[0] - p[0]) <= 0.05 and abs(c[1] - p[1]) <= 0.05]
            return hits[0] if len(hits) == 1 else None

        pos = [at(p) if not changed[j] else None for j, p in enumerate(old)]
        anchors = [j for j in range(m) if pos[j] is not None]
        if len(anchors) < 2:
            return None
        # handle order runs along the outline one way or the other
        a0, a1 = anchors[0], anchors[1]
        step = (pos[a1] - pos[a0]) % n
        back = (pos[a0] - pos[a1]) % n
        forward = step <= back
        out = list(coords)
        # splice from the end of the outline backwards so earlier indices hold
        runs, j = [], 0
        start = next(k for k in range(m) if not changed[k])
        k = start
        while True:
            nxt = (k + 1) % m
            if changed[nxt]:
                run = []
                while changed[nxt]:
                    run.append(nxt)
                    nxt = (nxt + 1) % m
                if pos[k] is None or pos[nxt] is None:
                    return None
                runs.append((k, run, nxt))
            k = nxt
            if k == start:
                break
        pieces = []                         # (index after which to cut, count, points)
        for k, run, nxt in runs:
            i0, i1 = pos[k], pos[nxt]
            if forward:
                span = (i1 - i0) % n
                pts = [new[r] for r in run]
                pieces.append((i0, span, pts))
            else:
                span = (i0 - i1) % n
                pts = [new[r] for r in reversed(run)]
                pieces.append((i1, span, pts))
        # rebuild once: walk the outline, replacing each spanned stretch
        cut = {}
        for i0, span, pts in pieces:
            cut[i0] = (span, pts)
        ring, i, seen = [], 0, 0
        # start at a vertex that begins no cut and lies in none
        inside = set()
        for i0, (span, _) in cut.items():
            for t in range(1, span):
                inside.add((i0 + t) % n)
        start_i = next((v for v in range(n) if v not in inside), None)
        if start_i is None:
            return None
        i = start_i
        while seen < n:
            ring.append(coords[i])
            if i in cut:
                span, pts = cut[i]
                ring.extend(pts)
                i = (i + span) % n
                seen += span
            else:
                i = (i + 1) % n
                seen += 1
        holes = [list(r.coords) for r in lobe.interiors]
        cand = Polygon(ring + [ring[0]], holes)
        if not cand.is_valid:
            cand = cand.buffer(0)
        cand = unary_union(_polys(cand))
        return cand if not cand.is_empty else None
    except Exception:
        return None


def move_border(features, id_prop, region_a, region_b, points, drag_start=None, orig=None):
    """Locally reshape the shared edge of region_a/region_b: the dragged polyline
    `points` replaces the matching stretch of the border. Only the thin sliver
    swept between this drag's start arc and the new arc changes hands, so:

      * ANY sub-segment can be nudged -- the arc's ends do NOT have to reach the
        outer boundary (that was the old "does not divide the regions" error), and
      * neither region can collapse, because we only ever add/remove a local sliver.

    `drag_start` is the current on-screen arc captured when this drag began; if
    it's missing we recover the arc from the current geometry. `orig` is accepted
    as a legacy alias. Returns the updated list."""
    if drag_start is None:
        drag_start = orig
    idx = _index_all(features, id_prop)
    if region_a not in idx or region_b not in idx:
        raise ValueError(f"region not found: {region_a!r}/{region_b!r}")

    A = _body(features, idx[region_a])         # all lobes of the name, as one region
    B = _body(features, idx[region_b])
    pair = set(idx[region_a]) | set(idx[region_b])
    pair_base = unary_union([A, B]).buffer(0)
    # The whole-section outline is NOT an obstacle. hemi covers the pair and a
    # band beyond them, so counting it among the "others" made that band -- and
    # with it the coastline -- protected ground the border could never move
    # into. Real neighbours still are protected; the wrap-everything region is
    # the one thing a border drag is allowed to poke past.
    blanket_names = set(blankets(features, id_prop, keep=[region_a, region_b]))
    other_geoms = []
    for k, f in enumerate(features):
        if k in pair:
            continue
        if str((f.get("properties") or {}).get(id_prop)) in blanket_names:
            continue
        try:
            g = shape(f["geometry"])
            if not g.is_valid:
                g = g.buffer(0)
            if not g.is_empty:
                other_geoms.append(g)
        except Exception:
            pass
    other_union = unary_union(other_geoms).buffer(0) if other_geoms else Polygon()
    # When one of the picks IS the section outline, it covers every neighbour, so
    # subtracting it (as part of the pair) left nothing protected: picking hemi +
    # CP let a drag take 104,798 px^2 of dft. Real neighbours are protected
    # against the non-container pick alone.
    wrap_picks = section_containers(features, id_prop) & {str(region_a), str(region_b)}
    shield = pair_base
    if wrap_picks:
        kept = [g for nm, g in ((region_a, A), (region_b, B)) if str(nm) not in wrap_picks]
        shield = unary_union(kept).buffer(0) if kept else Polygon()
    protected = other_union.difference(shield).buffer(0) if not other_union.is_empty else Polygon()

    new_arc = [(float(x), float(y)) for x, y in points]
    if len(new_arc) < 2:
        raise ValueError("border needs at least 2 points")

    # The drag-start arc: prefer the client's exact copy; else re-derive the slice
    # of A's outline that faces B (matches what was on screen for a whole-arc drag).
    old_arc = None
    if drag_start and len(drag_start) >= 2:
        old_arc = [(float(x), float(y)) for x, y in drag_start]
    else:
        arcs = border_between(features, id_prop, region_a, region_b)
        if arcs:
            longest = max(arcs, key=lambda a: LineString(a).length)
            old_arc = [(float(x), float(y)) for x, y in longest]
    if not old_arc or len(old_arc) < 2:
        raise ValueError("couldn't locate the border being edited")

    # One region inside the other: the dragged arc is the INNER's outline (its
    # ring is the shared border). The inner is rebuilt from the dragged ring;
    # the container is never carved -- it simply keeps covering, by taking the
    # union with the new inner, so the files' cover convention survives every
    # drag. Real neighbours are still protected ground.
    inner_is_a = A.area <= B.area
    small, big = (A, B) if inner_is_a else (B, A)
    inter = _safe_intersection(small, big).area if small.area > 0 else 0.0
    if small.area > 0 and inter / small.area >= CONTAINED_FRAC:
        inner_keys = idx[region_a if inner_is_a else region_b]
        outer_keys = idx[region_b if inner_is_a else region_a]
        inner_name = region_a if inner_is_a else region_b

        # the dragged lobe: the part(s) of the inner the dragged outline runs
        # along. Not just the one nearest its first point -- an outward drag can
        # leave the inner in two pieces, and dragging back then rebuilt one and
        # kept the other, so drag-back never undid it (765,938 px2 left on SSp).
        arc_line = LineString(old_arc)
        lobes = _polys(small)
        touched = [pg for pg in lobes if pg.exterior.distance(arc_line) <= 1.0]
        if not touched:
            start_pt = Point(*old_arc[0])
            touched = [min(lobes, key=lambda pg: pg.exterior.distance(start_pt))]
        lobe = unary_union(touched)
        rest = _safe_difference(small, lobe)

        ring = list(new_arc)
        if ring[0] != ring[-1]:
            ring.append(ring[0])
        if len(ring) < 4:
            raise ValueError("the dragged outline collapsed -- try a smaller drag")
        # Apply what the drag CHANGED to the real outline, not the dragged ring
        # itself: the handles are a simplified copy of the outline, and
        # rebuilding the inner from them dropped the detail between handles on
        # every drag (VL.2: 865 px2 gone on a drag that moved nothing).
        new_lobe = None
        old_ring = list(old_arc)
        if old_ring[0] != old_ring[-1]:
            old_ring.append(old_ring[0])
        # Best: the handles ARE vertices of the real outline, so between the two
        # unmoved handles either side of each dragged one, splice the dragged
        # handles in place of the real outline -- every other vertex stays put.
        new_lobe = _splice_handles(lobe, old_ring, ring)
        if new_lobe is None and len(old_ring) >= 4:
            try:
                oldP = unary_union(_polys(Polygon(old_ring).buffer(0)))
                newP = unary_union(_polys(Polygon(ring).buffer(0)))
                if not oldP.is_empty and not newP.is_empty \
                        and oldP.symmetric_difference(lobe).area < 0.05 * lobe.area:
                    grow = _safe_difference(newP, oldP)
                    shrink = _safe_difference(oldP, newP)
                    # where the real outline dips INSIDE the handle ring, the
                    # grown piece does not touch it -- fill the dips that bridge
                    # the two, or an outward drag splits the inner in two
                    bridge = []
                    if not grow.is_empty:
                        dips = _safe_difference(_safe_intersection(oldP, newP), lobe)
                        bridge = [d for d in _polys(dips)
                                  if d.distance(grow) <= 0.5 and d.distance(lobe) <= 0.5]
                    new_lobe = _safe_difference(unary_union([lobe, grow] + bridge).buffer(0),
                                                shrink)
                    # where the real outline bulged past the handle ring, cutting
                    # the shrink band strands the bulge as crumbs (VL.2 came back
                    # in 10 parts): a small piece lying mostly OUTSIDE the new
                    # ring is one of those, not ground the drag meant to keep
                    pieces = _polys(new_lobe)
                    if len(pieces) > 1:
                        total = sum(p.area for p in pieces)
                        pieces = [p for p in pieces
                                  if p.area >= 0.01 * total
                                  or _safe_intersection(p, newP).area >= 0.5 * p.area]
                        pieces = [p for p in pieces if not p.buffer(-0.5).is_empty]
                        new_lobe = unary_union(pieces) if pieces else new_lobe
            except Exception:
                new_lobe = None
        if new_lobe is None:
            new_lobe = Polygon(ring).buffer(0)
        new_lobe = unary_union(_polys(new_lobe))
        if new_lobe.is_empty or new_lobe.area < 1.0:
            raise ValueError(f"that drag would erase {inner_name} -- try a smaller one")
        if not protected.is_empty:
            # Never take a neighbour -- with the ground this drag GAINS. Clipping
            # the whole lobe also stripped overlap the inner already had, far
            # from the drag: VL.2 lost 865-1,357 px2 on every drag, a no-op too.
            kept_part = _safe_intersection(new_lobe, lobe)
            new_lobe = unary_union([kept_part, _safe_difference(
                _safe_difference(new_lobe, lobe), protected)]).buffer(0)
            # Clipping the dragged outline against a neighbour leaves zero-width
            # needles and razor-thin wedges where the two boundaries meet at a
            # shallow angle (measured: hemi + CP, 800 px drag). Shave only the
            # ground this drag GAINED -- a 1 px opening of the new ground, so the
            # existing outline is never eroded -- unless the gain is itself mostly
            # that thin (a tiny nudge), which is kept as drawn.
            gained = _safe_difference(new_lobe, lobe)
            if not gained.is_empty and gained.area > 0:
                try:
                    shaved = gained.buffer(-1.0, join_style=2).buffer(1.0, join_style=2)
                    shaved = _safe_intersection(shaved, gained)
                except Exception:
                    shaved = gained
                if not shaved.is_empty and shaved.area >= 0.5 * gained.area:
                    # ...but only the slivers next to a neighbour: shaving all of
                    # it also cut the thin neck that joins newly gained ground to
                    # the inner, leaving it in two parts touching at a point
                    thin = _safe_difference(gained, shaved)
                    thin = _safe_intersection(thin, protected.buffer(2.0))
                    if not thin.is_empty and thin.area > 0:
                        new_lobe = _safe_difference(new_lobe, thin)
        # Only the ground this drag changed is touched. clean_geom over the WHOLE
        # inner shaved hairline slivers off it far from the drag, and the coast
        # recession below then cut a 1,229 px needle into ISO -- on a drag that
        # moved nothing.
        moved = unary_union([_safe_difference(new_lobe, lobe),
                             _safe_difference(lobe, new_lobe)]).buffer(2.0)
        new_inner = unary_union([rest, new_lobe]).buffer(0)
        new_inner = unary_union([p for p in _polys(new_inner)
                                 if p.area >= 1.0 or not p.intersects(moved)] or [Polygon()])
        new_inner = despike(new_inner, within=moved)   # needles only, only here
        # and sharp tips the cut leaves where it meets the old outline -- only in
        # the moved zone, and never closing a crack over a neighbour
        new_inner = _trim_spikes(new_inner, moved, protected)
        if new_inner.is_empty or new_inner.area < 1.0:
            raise ValueError(f"that drag would erase {inner_name} -- try a smaller one")
        # The container keeps covering the inner -- but it grows only by the
        # ground the inner GAINED in this drag. Uniting it with the whole inner
        # also pulled in everything the inner already stuck out by: ISO grew
        # 1,133,470 px2 (SSp) or 1,597,067 px2 (RSP) on every drag, even one
        # that moved nothing.
        gained_inner = _safe_difference(new_inner, small)
        new_outer = big
        # Only real ground that joins the container: rebuilding the inner from
        # its ring leaves sub-pixel slivers along its overhang, and adding those
        # left ISO in 6 parts with 18 hairlines on a drag that moved nothing.
        add = [p for p in _polys(gained_inner)
               if p.area >= 1.0 and not p.buffer(-0.5).is_empty
               and p.distance(big) <= 0.5]
        if add:
            new_outer = unary_union([big] + add).buffer(0)
            new_outer = despike(new_outer, within=unary_union(add).buffer(2.0))

        # Where the dragged stretch COINCIDED with the container's own outline
        # (borders merged, e.g. after Share borders on a coastal pair), the two
        # edges are one line and move as one -- exactly like a side-by-side
        # pair's shared border. So ground the inner ceded ALONG the coast
        # recedes from the container too. A ceded piece only counts as coastal
        # if most of the edge it was cut from lay on the coast: one that merely
        # touches it at a vertex is an inland wedge (SSp's vertex 14, 1,724 px
        # inland, orphaned 766,027 px2 because its neighbour vertex sat on the
        # coast), and the container keeps covering it. Nothing another region
        # owns is ever cut away.
        ceded = _safe_difference(lobe, new_lobe)
        if not ceded.is_empty and ceded.area > 1e-6:
            coast = big.boundary.buffer(2.0)
            lobe_edge = lobe.boundary.buffer(0.5)
            coastal = []
            for p in _polys(ceded):
                old_side = p.boundary.intersection(lobe_edge).length
                on_coast = p.boundary.intersection(lobe_edge).intersection(coast).length
                if old_side > 0 and on_coast >= 0.5 * old_side:
                    coastal.append(p)
            if coastal:
                keep_cover = unary_union([new_inner, other_union]).buffer(0)
                cut = _safe_difference(unary_union(coastal), keep_cover)
                if not cut.is_empty and cut.area > 1e-6:
                    receded = _safe_difference(new_outer, cut).buffer(0)
                    receded = despike(receded, within=cut.buffer(2.0))
                    if not receded.is_empty and receded.area >= 1.0:
                        new_outer = receded

        out = json.loads(json.dumps(features))
        _write_body(out, features, inner_keys, new_inner)
        _write_body(out, features, outer_keys, new_outer)
        return out

    # The old and new arc share endpoints, so old_arc + reversed(new_arc) closes a
    # thin loop = the area the border swept across. buffer(0) heals any self-touch;
    # if the drag crossed itself we keep every resulting lobe.
    # make_valid, not buffer(0): an S-shaped drag sweeps a bow-tie, and buffer(0)
    # kept ONE of its lobes -- the other half of the drag was silently ignored
    # (CP/dft, 300 px S: 1,321,380 px2).
    sliver = unary_union(_polys(_valid(Polygon(new_arc + old_arc[::-1]))))
    if sliver.is_empty or sliver.area < 1e-6:
        return json.loads(json.dumps(features))       # no real movement

    # Which way did it move -- lobe by lobe, since an S-drag pushes one way here
    # and the other way there. A lobe covering more of A than of B pushed the
    # border into A (A shrinks); otherwise A grew. Judging the whole sweep by
    # "is half of it inside A" misread a deep drag whose far end ran past A as
    # growth, and nothing moved (dft/CP, 1,500 px: 4.73 M px2 ignored).
    gA = A
    for lobe in _polys(sliver):
        in_a = lobe.intersection(A).area
        in_b = lobe.intersection(B).area
        gA = gA.difference(lobe) if in_a > in_b else gA.union(lobe)
    gA = gA.buffer(0)
    # Is B right up against the swept sliver? True for a shared border even with the
    # few px of slop these spaghetti GeoJSONs carry, but False across a real gap --
    # so a genuinely-separated B never teleports a slab across the gap to "fill".
    b_adjacent = sliver.distance(B) <= 6.0
    if b_adjacent:
        # The pair's outer extent is fixed -- only the internal divider moves. Close
        # the couple into ONE solid (bridging the spaghetti slop between their non-
        # coincident edges), carve out the authoritative A, and B is exactly what's
        # left: area conserved, no gap, no overlap, whichever way the border went.
        gap_ab = A.distance(B)
        if gap_ab <= 0.5:
            U = pair_base
        else:
            # close the slop by HALF the actual gap (+ a hair), up to 3 px: a fixed
            # 1.5 px spanned only 3 px, so across a 3-6 px gap the ground A gave up
            # never joined B and came back to A -- "Border moved" and nothing had
            r = min(max(1.5, gap_ab / 2.0 + 0.5), 3.5)
            U = pair_base.buffer(r, join_style=2).buffer(-r, join_style=2)
        if not protected.is_empty:
            U = unary_union([pair_base, _safe_difference(U, protected)]).buffer(0)
        # A drag may reach PAST the pair's outer boundary -- past the section
        # outline itself. The swept ground outside the pair joins the couple's
        # territory as long as no real neighbour owns it, so the dragged region
        # keeps the bulge instead of having it clipped back to the old coast.
        grow = _safe_difference(sliver, unary_union([pair_base, protected]))
        if not grow.is_empty and grow.area > 1e-6:
            U = unary_union([U, grow]).buffer(0)
        U = _fill_new_unprotected_holes(U, protected=protected, preserve=pair_base)
        gA = _safe_intersection(gA, U)
        gB = _safe_difference(U, gA)       # exact complement -> conserved, zero overlap
        # difference can leave B's reclaimed sliver hairline-split from its body. Snap
        # to a fine grid so the coincident split edges fuse, then union to one polygon
        # (a precision snap, unlike a morphological close, doesn't expand gB into gA).
        gA, gB = _clean_complement_pair(gA, U, sliver, preserve_a=A, preserve_b=B,
                                        protected=protected)
    else:
        gB = _safe_difference(B, gA)                   # gapped: B only cedes real overlap
        gA = clean_geom(gA.buffer(0), smooth=False)   # keep the thin transferred sliver
        gB = clean_geom(gB.buffer(0), smooth=False)
    # An overlap the two ALREADY had stays as it was away from the drag: taking
    # the complement handed all of it to the dragged region, so a 5 px nudge on
    # dft/RSP took 832,449 px2 from RSP, 654,546 of it over 200 px away.
    # (Real overlap only: a hairline band of it -- dft/HPF's 742 px2 is one --
    # pasted back left an 899 px zero-width needle in dft.)
    old_ov = _safe_intersection(A, B)
    if not old_ov.is_empty and old_ov.area > 1.0:
        keep_ov = _safe_difference(old_ov, sliver.buffer(1.0))
        keep_ov = unary_union([p for p in _polys(keep_ov) if not _hairline(p)] or [Polygon()])
        if not keep_ov.is_empty and keep_ov.area > 1.0:
            gB = unary_union([gB, keep_ov]).buffer(0)
            gB = despike(gB, within=keep_ov.buffer(2.0))
    if gA.is_empty or gA.area < 1.0:
        raise ValueError(f"that move would erase {region_a} -- try a smaller drag")
    if gB.is_empty or gB.area < 1.0:
        raise ValueError(f"that move would erase {region_b} -- try a smaller drag")

    out = json.loads(json.dumps(features))
    _write_body(out, features, idx[region_a], gA)
    _write_body(out, features, idx[region_b], gB)
    return out


def _shared_arcs(newg, sel, min_len, corridor=6.0):
    """Shared boundary arcs among a set of already-partitioned geometries.

    Uses the segment of ga's OWN (already-smoothed) outline that runs alongside gb
    (within `corridor` px) rather than a boundary-vs-boundary intersection. The
    latter emits spurious spikes/hairs wherever the two edges aren't perfectly
    coincident; a slice of a single clean outline never does."""
    borders = []
    for a in range(len(sel)):
        for b in range(a + 1, len(sel)):
            ga, gb = newg[sel[a]], newg[sel[b]]
            if ga.is_empty or gb.is_empty:
                continue
            inter = ga.boundary.intersection(gb.buffer(corridor, join_style=2))
            parts = _lines(inter)
            if not parts:
                continue
            merged = linemerge(parts) if len(parts) > 1 else parts[0]
            for ln in _lines(merged):
                if ln.length >= min_len:
                    ls = ln.simplify(4.0)          # fewer handles on the draggable arc
                    if ls.is_empty or ls.geom_type != "LineString":
                        ls = ln
                    borders.append({
                        "regions": [sel[a], sel[b]],
                        "points": [[round(x, 2), round(y, 2)] for x, y in ls.coords],
                        "length": round(ln.length, 1),
                    })
    return borders


def _generator_inset(g, inset):
    """Pull a region's outline toward its interior for Voronoi seeding -- but only
    as far as the region can actually afford.

    The inset exists so two regions that already touch don't contribute COINCIDENT
    generators (which makes the diagram degenerate). A fixed inset, though, is
    sized from the pair's scale rather than from this region's own width, so on a
    thin sprawling ribbon it can erode the shape badly or empty it outright. What
    survives no longer runs the ribbon's whole length, the compact neighbour is
    then nearest almost everywhere, and the ribbon loses its own interior.

    So back the inset off until the region keeps essentially all of its area and
    doesn't shatter into extra pieces. A thin region ends up with a small inset,
    which is all it ever needed: generators only have to be strictly inside their
    own region, not deep inside it.
    """
    if g is None or g.is_empty or inset <= 0:
        return g
    # Measure what the region can afford instead of guessing. Eroding by d costs
    # roughly d * perimeter of area, so area/perimeter is the shape's mean
    # half-width and d <= 0.1 * area/perimeter keeps ~90% of it at ANY aspect
    # ratio. A compact blob is far wider than the sampling scale and keeps the
    # full inset; a ribbon gets a proportionally small one. One measurement, no
    # trial and error, so this costs a single buffer in the normal case.
    step = float(inset)
    try:
        perim = g.length
        if perim > 0:
            step = min(step, 0.1 * g.area / perim)
    except Exception:
        pass
    for s in (step, step * 0.25):
        if s <= 0:
            break
        try:
            src = g.buffer(-s)
        except Exception:
            continue
        if (not src.is_empty) and src.is_valid and src.area > 0:
            return src
    return g


def _exclusive_bodies(geoms_by_name):
    """Each region's own ground, with anything another region also covers taken
    out -- so a starting point is never ambiguous. Returns (exclusive, shared)."""
    names = list(geoms_by_name)
    exclusive, shared = {}, []
    for n in names:
        others = [geoms_by_name[m] for m in names if m != n]
        g = geoms_by_name[n]
        if not others:
            exclusive[n] = g
            continue
        u = unary_union(others).buffer(0)
        exclusive[n] = _safe_difference(g, u)
        shared.append(_safe_intersection(g, u))
    sh = unary_union([s for s in shared if not s.is_empty]).buffer(0) if shared else Polygon()
    return exclusive, sh


def _split_tie(piece, bodies, lo, hi, rounds=10):
    """Where two regions reach the same ground in one step, find where they
    really meet by bisecting the band rather than giving it to one of them.

    `bodies` is {name: geometry} for exactly the regions that reached it, and
    the answer is accurate to (hi - lo) / 2**rounds. The bisection only ever
    touches the thin band where the two met in one step, so the rounds are
    nearly free: on the reference pair, going from 3 to 10 took the gap between
    the two click orders from 10.42 px to 0.24 px with no measurable cost.
    """
    names = list(bodies)
    if len(names) != 2 or piece.is_empty:
        return None
    a, b = names
    for _ in range(max(1, rounds)):
        mid = (lo + hi) / 2.0
        try:
            got_a = _safe_intersection(piece, bodies[a].buffer(mid))
            got_b = _safe_intersection(piece, bodies[b].buffer(mid))
        except Exception:
            return None
        both = _safe_intersection(got_a, got_b)
        # Look for the SMALLEST reach at which they meet -- that is where the
        # line between them runs. Both having arrived already means the answer is
        # nearer in, not further out.
        if both.is_empty:
            lo = mid
        else:
            hi = mid
    try:
        ga = _safe_intersection(piece, bodies[a].buffer((lo + hi) / 2.0))
        out_a = _safe_difference(ga, bodies[b].buffer((lo + hi) / 2.0))
        out_b = _safe_difference(piece, out_a)
        rest = _safe_difference(_safe_difference(piece, out_a), out_b)
    except Exception:
        return None
    if not rest.is_empty and rest.area > 0:
        out_b = _safe_difference(piece, out_a)
    return {a: out_a, b: out_b}


def _grown_partition(area, geoms_by_name, spacing):
    """Split `area` by nearest region, built so it CANNOT overlap or leak.

    Every region claims the ground it already covers, then they grow outwards in
    steps; each piece a region takes is cut out of what remains. Disjointness and
    full coverage are therefore properties of the construction, not of whatever
    an overlay routine happened to return. Ground two regions reach in the same
    step is bisected to find the line between them; anything still unclaimed at
    the end goes to the nearest region, so nothing is ever left behind.
    """
    names = list(geoms_by_name)
    out = {n: [] for n in names}
    if area.is_empty:
        return {n: Polygon() for n in names}

    exclusive, _ = _exclusive_bodies(geoms_by_name)
    rem = area if area.is_valid else area.buffer(0)

    # 1. what each region already covers, uncontested
    for n in names:
        if rem.is_empty:
            break
        take = _safe_intersection(rem, exclusive[n])
        if not take.is_empty and take.area > 0:
            out[n].append(take)
            rem = _safe_difference(rem, take)

    # 2. grow into what is left. Only the parts of each region NEAR the leftover
    #    matter, and they are far cheaper to buffer than whole outlines.
    if not rem.is_empty and rem.area > 0:
        x0, y0, x1, y1 = area.bounds
        reach_max = max(x1 - x0, y1 - y0)
        step = max(float(spacing) / 4.0, 0.5)
        near = rem.buffer(reach_max * 0.5 + step)
        local = {}
        for n in names:
            g = _safe_intersection(exclusive[n], near)
            local[n] = g if not g.is_empty else exclusive[n]
        d = step
        guard = 0
        while not rem.is_empty and rem.area > 0 and d <= reach_max and guard < 400:
            guard += 1
            got = {}
            for n in names:
                if local[n].is_empty:
                    continue
                try:
                    g = _safe_intersection(local[n].buffer(d), rem)
                except Exception:
                    g = Polygon()
                if not g.is_empty and g.area > 0:
                    got[n] = g
            if got:
                taken = []
                for n, g in got.items():
                    others = [got[m] for m in got if m != n]
                    solo = _safe_difference(g, unary_union(others).buffer(0)) \
                        if others else g
                    if not solo.is_empty and solo.area > 0:
                        out[n].append(solo)
                        taken.append(solo)
                reached = unary_union(list(got.values())).buffer(0)
                tie = _safe_difference(reached,
                                       unary_union(taken).buffer(0)) if taken else reached
                for piece in _polys(tie):
                    if piece.area <= 0:
                        continue
                    here = {n: local[n] for n in got
                            if not _safe_intersection(piece, got[n]).is_empty}
                    split = _split_tie(piece, here, max(d - step, 0.0), d)
                    if split:
                        for n, part in split.items():
                            if not part.is_empty and part.area > 0:
                                out[n].append(part)
                    else:
                        rp = piece.representative_point()
                        best = min(here or local,
                                   key=lambda m: (local[m].distance(rp), m))
                        out[best].append(piece)
                rem = _safe_difference(rem, reached)
            d += step

        # 3. nothing is ever left behind
        for piece in _polys(rem):
            if piece.area <= 0:
                continue
            rp = piece.representative_point()
            best = min(names, key=lambda m: (geoms_by_name[m].distance(rp), m))
            out[best].append(piece)

    res = {}
    for n in names:
        v = [p for p in out[n] if not p.is_empty]
        if not v:
            res[n] = Polygon()
            continue
        try:
            res[n] = unary_union(v).buffer(0)
        except Exception:
            res[n] = unary_union([p.buffer(0) for p in v]).buffer(0)
    return res


def _partition_is_exact(area, claims, tol_area):
    """Do these claims actually tile `area` -- disjoint, and covering it?"""
    live = [g for g in claims.values() if not g.is_empty]
    if not live:
        return False
    for i in range(len(live)):
        for j in range(i + 1, len(live)):
            try:
                if live[i].intersects(live[j]) \
                        and _safe_intersection(live[i], live[j]).area > tol_area:
                    return False
            except Exception:
                return False                      # cannot even be compared
    try:
        covered = unary_union(live).buffer(0)
        if _safe_difference(area, covered).area > tol_area:
            return False
        if _safe_difference(covered, area).area > tol_area:
            return False
    except Exception:
        return False
    return True


def _nearest_partition(area, geoms_by_name, spacing):
    """Split `area` among the named regions: every bit goes to its NEAREST region.
    Returns {name: claimed Polygon}, which ALWAYS tiles `area` -- disjoint, and
    covering it.

    The Voronoi of their densified boundaries is tried first and is usually both
    right and quick. It is not trusted, though: on regularly-sampled outlines
    GEOS hands back cells that are not a partition at all (measured on two boxes
    with a 10 px gap: 48 cells for 48 sites, two of them containing 23 sites
    each, pairs overlapping by up to 142,924 px, and "side location conflict"
    when two are intersected). The claims then overlapped by 7,756 px of a
    21,000 px territory, partition_regions settled that with the cede pass that
    favours whichever region was listed first, and the seam ended up depending on
    which region was clicked first -- with a void left over in 22 pairs out of 24.

    So the result is CHECKED, and if it is not a tiling the growth method below
    takes over, which cannot produce one that is not.
    """
    if not area.is_valid:
        area = area.buffer(0)
    _tol_area = max(area.area * 1e-6, 1e-6)
    _voronoi = _voronoi_partition(area, geoms_by_name, spacing)
    if _voronoi is not None and _partition_is_exact(area, _voronoi, _tol_area):
        return _voronoi
    return _grown_partition(area, geoms_by_name, spacing)


def _voronoi_partition(area, geoms_by_name, spacing):
    """The original Voronoi split, or None if it cannot be built at all."""
    # Sample each region's boundary INSET toward its interior. Where two regions
    # already touch, their raw boundary points coincide, which makes the Voronoi
    # degenerate (crashes / slivers). Insetting keeps every generator strictly
    # inside its own region, so the midline still lands on the true shared edge.
    inset = spacing * 0.5
    pts, labels = [], []
    # Sorted, not dict order: GEOS's Voronoi and the nearest-site lookup below
    # both settle degenerate cases by input position, so the generator list has
    # to be the same whichever way the caller named the regions.
    for n in sorted(geoms_by_name):
        g = geoms_by_name[n]
        # Inset toward the interior so generators never coincide where two
        # regions already touch. A region smaller than the inset used to fall
        # straight back to its RAW boundary, which is the degenerate case this
        # is here to avoid -- so try smaller insets first.
        src = None
        for ins in (inset, inset * 0.5, inset * 0.25, 1.0):
            if ins <= 0:
                continue
            cand = _generator_inset(g, ins)
            if cand is not None and not cand.is_empty and cand.is_valid:
                src = cand
                break
        if src is None:
            src = g
        # Keep enough generators that a long thin region is represented along its
        # WHOLE length: if this region is small relative to `spacing` it would
        # otherwise contribute a handful of points and lose its middle to a
        # neighbour that sampled densely. Sample finer until it has a real say.
        edge = src.boundary
        step = spacing
        try:
            if edge.length > 0:
                step = min(spacing, edge.length / 12.0)
        except Exception:
            pass
        for ln in _lines(edge.segmentize(max(step, 1e-6))):
            for x, y in ln.coords:
                pts.append(Point(x, y))
                labels.append(n)
    if len(pts) < 3:
        return None
    tree = STRtree(pts)
    try:
        vor = voronoi_diagram(MultiPoint(pts), envelope=area)
    except Exception:
        try:                                   # nudge onto a fine grid and retry
            vor = voronoi_diagram(set_precision(MultiPoint(pts), 0.001), envelope=area)
        except Exception:
            return None                     # cannot build it: caller falls back
    claims = {n: [] for n in geoms_by_name}
    for cell in vor.geoms:
        c = None
        for attempt in (cell, cell.buffer(0)):
            try:
                c = attempt.intersection(area)
                break
            except Exception:
                c = None
        if c is None or c.is_empty:
            continue
        gi = tree.nearest(cell.centroid)   # convex cell -> centroid inside -> its generator
        claims[labels[gi]].append(c)
    out = {}
    for n, v in claims.items():
        if not v:
            out[n] = Polygon()
            continue
        try:
            out[n] = unary_union(v)
        except Exception:
            out[n] = unary_union([p.buffer(0) for p in v])
    return out


def _thin_free_edges(newg, names, fluff, eps):
    """Strip collinear fluff from each region's FREE edge, pinning the seam.

    _simplify_shared does this through the whole boundary network, which is the
    better way when it works -- but it has to polygonize and hand every face
    back, and a speck-sized region loses its face in that rebuild, so the whole
    thinning is abandoned. `opt` next to the 41 px sAMY.3 therefore came back
    with 3,209 vertices for a 36-vertex region, and the file grew 2.8x.

    This is the fallback: per region, per ring, walk the vertices and keep every
    one that lies in the seam zone EXACTLY as it is, simplifying only the runs
    between them. Those pinned vertices are the ones both regions share, so
    coincidence survives by construction rather than by reconstruction -- there
    is no face assignment to fail. `fluff` is a hundredth of a pixel, so this
    cannot move an outline anywhere a person could see.
    """
    from shapely.prepared import prep

    names = [n for n in names if not newg[n].is_empty]
    if len(names) < 2 or fluff <= 0:
        return newg
    bnd = {}
    for n in names:
        try:
            bnd[n] = newg[n].boundary.buffer(eps)
        except Exception:
            return newg
    seams = []
    for i, a in enumerate(names):
        for b in names[i + 1:]:
            try:
                s = _safe_intersection(bnd[a], bnd[b])
            except Exception:
                continue
            if not s.is_empty:
                seams.append(s)
    if not seams:
        return newg
    try:
        zone = prep(unary_union(seams).buffer(0))
    except Exception:
        return newg

    def thin_ring(coords):
        pts = list(coords)
        if len(pts) < 5:
            return pts
        pinned = [zone.intersects(Point(x, y)) for x, y in pts]
        if all(pinned):
            return pts
        out, run = [], []
        for p, isin in zip(pts, pinned):
            if isin:
                if run:
                    run.append(p)
                    if len(run) > 2:
                        run = list(LineString(run).simplify(fluff).coords)
                    out.extend(run[:-1])
                    run = []
                out.append(p)
            else:
                if not run:
                    run = [out.pop()] if out else []
                run.append(p)
        if run:
            if len(run) > 2:
                run = list(LineString(run).simplify(fluff).coords)
            out.extend(run)
        if len(out) < 4 or out[0] != out[-1]:
            return pts
        return out

    result = {}
    for n in names:
        polys = []
        for p in _polys(newg[n]):
            try:
                q = Polygon(thin_ring(p.exterior.coords),
                            [thin_ring(r.coords) for r in p.interiors])
                if not q.is_valid:
                    q = q.buffer(0)
            except Exception:
                q = p
            if not q.is_empty:
                polys.append(q)
        if not polys:
            return newg
        g = polys[0] if len(polys) == 1 else unary_union(polys).buffer(0)
        if g.is_empty or abs(g.area - newg[n].area) > max(2.0, newg[n].area * 1e-6):
            return newg                       # moved more than fluff should
        result[n] = g
    for i, a in enumerate(names):
        for b in names[i + 1:]:
            try:
                if _safe_intersection(result[a], result[b]).area >                         max(_safe_intersection(newg[a], newg[b]).area, 1e-6) + 0.5:
                    return newg               # opened an overlap: keep the input
            except Exception:
                return newg
    out = dict(newg)
    out.update(result)
    return out


def _simplify_shared(newg, tol, precision, keep_out=None, frozen=None):
    """Thin out vertices on a set of border-sharing polygons WITHOUT breaking
    their coincidence. Node every boundary into one network (a shared border is a
    single edge in it), simplify each edge once -- Douglas-Peucker keeps the
    junction endpoints, so the network stays connected -- then re-polygonize and
    give each face back to the region that covered it. Falls back to the input if
    anything collapses.

    `keep_out` is ground that must stay unclaimed -- another region's territory, or
    a hole a region already had. polygonize() yields a face for every enclosed area
    including those, so without this guard the rebuild quietly fills them in."""
    names = [n for n in newg if not newg[n].is_empty]
    if len(names) < 2 or tol <= 0:
        return newg
    # Where two of these regions' boundaries actually run together -- the seam
    # this operation just built, and the only thing that needs thinning. A
    # region's FREE edge is somebody's traced outline and is left exactly as it
    # is; simplifying the whole network moved 94,240 px of TH's, nowhere near
    # HPF, and cost it 53 of its 133 vertices.
    eps = max(float(tol) * 0.25, 0.5)
    bnd = {n: newg[n].boundary for n in names}
    grown = {}                            # one buffer per region, not per pair
    for n in names:
        try:
            grown[n] = bnd[n].buffer(eps)
        except Exception:
            grown[n] = Polygon()
    seams = []
    for i, a in enumerate(names):
        for b in names[i + 1:]:
            if grown[a].is_empty or grown[b].is_empty:
                continue
            try:
                s = _safe_intersection(grown[a], grown[b])
            except Exception:
                continue
            if not s.is_empty:
                seams.append(s)
    if not seams:
        return newg                       # nothing is shared: nothing to thin
    seam_zone = unary_union(seams).buffer(0)
    # `frozen`: border the regions ALREADY shared before this edit. It is exact
    # already; re-thinning it moved it by up to `tol` into one of them (HPF +
    # dft: 12,024 px2 of HPF's own ground went to dft, on every Share).
    if frozen is not None and not frozen.is_empty:
        seam_zone = _safe_difference(seam_zone, frozen)
    if seam_zone.is_empty:
        return newg
    raw = unary_union(list(bnd.values()))
    # PLAIN line operations. _safe_intersection / _safe_difference open with
    # `a.buffer(0)`, and LineString.buffer(0) is POLYGON EMPTY -- using them here
    # silently returned nothing for every line and made this whole function a
    # no-op. They are for polygons.
    try:
        inside = raw.intersection(seam_zone)
        outside = raw.difference(seam_zone)
    except Exception:
        return newg
    parts = []
    if not inside.is_empty:
        # unary_union noded the coincident linework at every shared vertex, so
        # the seam arrives as a crowd of 2-point segments -- and Douglas-Peucker
        # keeps both endpoints of what it is handed, so a 2-point segment cannot
        # be shortened. Merge them back into continuous arcs first.
        try:
            inside = linemerge(inside)
        except Exception:
            pass
        parts.extend(seg.simplify(tol) for seg in _lines(inside))
    if not outside.is_empty:
        # The free edge keeps its shape to a hundredth of a pixel; only
        # collinear fluff goes. Splitting against the seam zone first means
        # Douglas-Peucker keeps the junction endpoints, so the network stays
        # connected exactly as the seam pieces above do.
        parts.extend(seg.simplify(FREE_EDGE_FLUFF) for seg in _lines(outside))
    if not parts:
        return newg
    net = unary_union(parts)
    # RE-NODE. unary_union returns linework noded at every crossing, but
    # Douglas-Peucker then moves interior vertices and UN-nodes it: a simplified
    # shared arc ends up crossing the simplified outer boundary somewhere that is
    # no longer a shared endpoint. polygonize() does not compute intersections --
    # it needs its input already noded -- so it discards the arc as a dangling edge
    # and returns ONE face for the whole pair. The first region then swallowed the
    # entire territory while the second, having received no face at all, silently
    # kept its old geometry; the two fully overlapped and the cede pass at the end
    # of partition_regions deleted the second region outright. That is what erased
    # thin wrap-around ribbons (fiber tracts, pallidum). Re-noding the simplified
    # network restores the junctions, so every region gets its face back.
    try:
        net = unary_union(net)
    except Exception:
        pass
    faces = list(polygonize(net))
    if not faces:
        return newg
    result = {n: [] for n in names}
    for f in faces:
        rp = f.representative_point()
        owner = next((n for n in names if newg[n].contains(rp)), None)
        if owner is None:
            if keep_out is not None and not keep_out.is_empty:
                try:
                    # by AREA, not by one point: the thinned face for OLF's
                    # 146,324 px2 hole had its representative point just
                    # outside the hole, so the hole was handed to OLF and filled.
                    # And only the part IN keep_out stays out -- skipping the
                    # whole face lost 1,549 px2 of HY + opt's own ground that
                    # shared a face with a strict-mode shadow.
                    if f.intersection(keep_out).area >= 0.5 * f.area:
                        f = _safe_difference(f, keep_out)
                        if f.is_empty or f.area < 1.0:
                            continue       # somebody else's ground / a real hole
                        rp = f.representative_point()
                except Exception:
                    pass
            owner = min(names, key=lambda n: newg[n].distance(rp))
        result[owner].append(f)
    # A region that receives NO face is the tell-tale of a broken network: the
    # rebuild is not a tiling of the same ground. Falling back to newg[n] for just
    # that region (while its neighbour keeps the faces it wrongly won) is worse
    # than not simplifying at all, because the two then overlap. Bail as a set.
    if any(not result[n] for n in names):
        return newg
    out = dict(newg)
    for n in names:
        g = unary_union(result[n]) if result[n] else newg[n]
        # NO precision snap. It ran over the WHOLE region, so the default 1 px
        # grid moved every vertex of a hand-traced outline by up to half a pixel
        # -- on the real TH, 9,402 px of free edge in 67 sub-pixel slivers the
        # entire way round, thousands of pixels from the border being shared.
        # The seam does not need it: polygonize hands both faces the SAME edge,
        # so the two rings are coincident by construction, sub-pixel coordinates
        # and all. `precision` is kept in the signature for the caller.
        g = g.buffer(0)
        if newg[n].area > 0 and g.area < 0.5 * newg[n].area:
            return newg                       # simplification lost the region -> bail
        # Thinning vertices must never hand a region new ground. Growth means the
        # face graph collapsed and somebody absorbed a neighbour, so bail as well.
        if newg[n].area > 0 and g.area > 1.05 * newg[n].area:
            return newg
        out[n] = g
    return out


# ---- multi-part regions -------------------------------------------------------
# A name is legitimately carried by more than one feature: "Ventricles" is two
# lobes of ONE structure, not two regions. _index() keeps only the FIRST feature
# per name, so the second lobe used to be invisible to an edit -- neither a Voronoi
# generator nor protected ground -- and the nearest region simply absorbed it
# (measured: Caudoputamen +22.4 million px^2 while the second "Fiber Tracts"
# polygon was reduced to 8,369 px^2). So every operation that reasons about whole
# regions works on the UNION of a name's features and hands the answer back to them
# afterwards. Only split_region still refuses a duplicate, where "cut THIS region"
# genuinely doesn't say which lobe.


def _index_all(features, id_prop):
    """name -> [feature index, ...] in file order; keeps EVERY feature per name."""
    idx = {}
    for k, f in enumerate(features):
        nm = (f.get("properties") or {}).get(id_prop)
        if nm is not None:
            idx.setdefault(str(nm), []).append(k)
    return idx


def _geom_at(features, k):
    try:
        g = shape(features[k]["geometry"])
    except Exception:
        return Polygon()
    return g if g.is_valid else _valid(g)


def _body(features, ks):
    """A region's whole body: the union of every feature carrying its name."""
    gs = [g for g in (_geom_at(features, k) for k in ks) if not g.is_empty]
    if not gs:
        return Polygon()
    return gs[0] if len(gs) == 1 else unary_union(gs).buffer(0)


def _distribute(body, olds):
    """Hand a recomputed region body back to the features it came from.

    `olds` is [(feature index, its previous geometry), ...]. Each piece of the new
    body goes to the old feature it overlaps most, so two lobes stay two features
    instead of collapsing into one. If that starves a feature -- which happens when
    the edit bridged the lobes into a single piece -- divide the body by nearest
    point instead, which gives every lobe its own neighbourhood and can't empty one.
    Returns {feature index: geometry}; the parts always re-union to exactly `body`.
    """
    if len(olds) == 1:
        return {olds[0][0]: body}
    buckets = {k: [] for k, _ in olds}
    for piece in _polys(body):
        best_k, best_a = None, 0.0
        for k, og in olds:
            if og.is_empty:
                continue
            a = _safe_intersection(piece, og).area
            if a > best_a:
                best_k, best_a = k, a
        if best_k is None:                       # overlaps nobody -> nearest owner
            best_k = min(olds, key=lambda kv: (kv[1].distance(piece)
                                               if not kv[1].is_empty else float("inf")))[0]
        buckets[best_k].append(piece)
    out = {}
    for k, v in buckets.items():
        out[k] = Polygon() if not v else (v[0] if len(v) == 1 else unary_union(v).buffer(0))

    live = {k: og for k, og in olds if not og.is_empty}
    if body.is_empty or len(live) < 2 or not any(out[k].is_empty for k in live):
        return out
    perims = [g.length for g in live.values() if g.length > 0]
    spacing = max(min(40.0, (min(perims) / 24.0) if perims else 40.0), 4.0)
    try:
        share = {k: _safe_intersection(g, body)
                 for k, g in _nearest_partition(body, live, spacing).items()}
    except Exception:
        return out
    # _nearest_partition returns its INPUTS if the Voronoi fails; those overlap, so
    # only accept a share that is a real partition of the body.
    if any(share.get(k, Polygon()).is_empty for k in live):
        return out
    if abs(sum(share[k].area for k in live) - body.area) > 0.02 * max(body.area, 1.0):
        return out
    return {k: share.get(k, Polygon()) for k, _ in olds}


def _write_body(out, features, ks, body):
    """Write a recomputed region body back onto the features that carry its name.

    Refuses to serialize an empty geometry over a feature that had one. Without
    that check a lobe absorbed by a neighbour was written out as
    {"type": "Polygon", "coordinates": []}: the region vanished from the map, an
    empty husk stayed in the file, and the next Save persisted the loss. Every
    whole-region operation (move_border, partition_regions, snap_to_edits) funnels
    through here, and each guarded only the union body -- so a multi-lobe region
    could lose a whole lobe with nothing raised anywhere.

    Raising is right rather than skipping the write: the caller's body no longer
    describes the same set of features, so a partial write would leave the file
    internally inconsistent. Callers already surface ValueError to the user.
    """
    olds = [(k, _geom_at(features, k)) for k in ks]
    shares = _distribute(body, olds)
    prior = dict(olds)
    for k, g in shares.items():
        if (g is None or g.is_empty) and not prior.get(k, Polygon()).is_empty:
            props = (features[k].get("properties") or {})
            name = props.get("name") or props.get("acronym") or f"feature {k}"
            raise ValueError(
                f"that edit would erase {name!r} (one of its parts ends up empty); "
                "nothing was changed")
    for k, g in shares.items():
        out[k]["geometry"] = mapping(g)


def _duplicate_names(features, id_prop, names):
    """Names in `names` that more than one feature carries."""
    counts = {}
    for f in features:
        nm = (f.get("properties") or {}).get(id_prop)
        if nm is not None:
            counts[str(nm)] = counts.get(str(nm), 0) + 1
    return [n for n in dict.fromkeys(str(x) for x in (names or [])) if counts.get(n, 0) > 1]


def _guard_duplicate_names(features, id_prop, names, what):
    """Refuse an operation that needs a name to mean exactly ONE polygon.

    Everything else treats same-named features as one multi-part region (see
    _distribute), so this is only for `split_region`: a cut line drawn across one
    lobe says nothing about what should happen to the other."""
    dups = _duplicate_names(features, id_prop, names)
    if dups:
        listed = ", ".join(f'"{d}"' for d in dups)
        raise ValueError(
            f"{listed} names more than one polygon, so {what} can't tell which one "
            "you mean. Right-click the piece you want and rename it first."
        )


#: Coordinate grid the shared-border result is snapped to. GEOS uses a
#: snap-rounding noder for a fixed precision model and calls that the robust
#: path, and the snap also collapses the sub-pixel needles despike() cannot
#: reach. It has to be far BELOW feature scale to be invisible: the old default
#: of 1.0 moved every vertex of a hand-traced outline by up to half a pixel --
#: 9,341 px of TH's free edge, in 67 slivers the whole way round -- while 0.01
#: costs 65 px and still leaves every seam vertex identical in both regions.
PARTITION_GRID = 0.01


def partition_regions(features, id_prop, names, tol=40.0, precision=PARTITION_GRID,
                      fill_all=False,
                      simplify=None, snap_to=None):
    """Make the named regions tile cleanly: fill small gaps between them, remove
    small overlaps, and give every pair a single shared border. Each point of the
    shared territory goes to its NEAREST region, so borders land on the midline of
    whatever gap/overlap existed.

    `snap_to` puts the seam on ONE region's own edge instead of down the middle:
    that region comes back exactly as it went in, and the rest of the territory
    goes to the others, which makes their new boundary along the join its
    boundary. It is for when one of the two outlines is the one you trust -- the
    midline moves both. With more than two picks the others still split what is
    left between them by midline.

    tol bridges gaps up to ~2*tol; regions farther apart than that stay separate.
    Only the named regions change. A name carried by several features is ONE
    multi-part region: every lobe is a Voronoi generator, and the result is shared
    back out to the individual features at the end. Returns {features, borders}.
    """
    idx = _index_all(features, id_prop)
    # CANONICAL ORDER, so the answer is a property of the regions and not of the
    # clicks. Several passes below settle a tie by list position -- the Voronoi
    # generators are built in this order, and the overlap cede hands the
    # contested ground to whichever came first -- so clicking TH then HPF gave a
    # different file from HPF then TH. Measured on the real set: 15 of the 41
    # pairs the app allows differed between the two orders, dft+RSP by 32,772 px.
    # It is still arbitrary WHICH region wins a tie, but two annotators now get
    # the same answer, which is the part that matters for a shared atlas. The
    # caller's order carries no meaning here: `snap_to` names its region
    # explicitly, and every result is written back by name.
    sel = sorted({str(n) for n in names if str(n) in idx})
    if len(sel) < 2:
        raise ValueError("pick at least two regions to share borders")

    geoms, raw_bodies = {}, {}
    for n in sel:
        # Shave import/boolean hairlines before they can become Voronoi
        # generators. This targets the long skinny spikes that show up as stray
        # edit lines after "Share borders".
        raw_bodies[n] = _body(features, idx[n])
        geoms[n] = _clean_for_partition(raw_bodies[n], tol)
    # ...except the region a seam is snapped to, which has to come back exactly
    # as it arrived. Cleaning moved the real CP by 0.13%, which is not "left
    # alone". Its RAW outline is what the others are fitted to as well, so the
    # shared edge still coincides exactly.
    snap_keep = str(snap_to) if (snap_to is not None and str(snap_to) in geoms) else None
    if snap_keep is not None:
        kept_raw = raw_bodies[snap_keep]
        if not kept_raw.is_empty and not kept_raw.is_valid:
            kept_raw = kept_raw.buffer(0)
        if not kept_raw.is_empty:
            geoms[snap_keep] = kept_raw

    # Territory to tile: morphological close bridges gaps <= ~2*tol and swallows
    # small overlaps, without expanding the outer boundary.
    U = unary_union(list(geoms.values()))
    T = U.buffer(tol, join_style=2).buffer(-tol, join_style=2).buffer(0)
    if T.is_empty:
        T = U
    # A hole a region ALREADY had is deliberate -- a ventricle, a tear -- and the
    # close must not swallow it. A close of radius tol fills anything narrower
    # than 2*tol, so OLF's 220-px-wide hole disappeared at every reach from 150
    # up (and OLF gained 257,073 px at 150, 526,110 at 600), and HY's 120-px one
    # went the same way. Taking them out of the territory keeps them out of
    # everything downstream, because every region is clipped to T.
    _pre_holes = [h for n in sel for h in _holes(geoms[n])]
    _pre_holes = unary_union(_pre_holes).buffer(0) if _pre_holes else Polygon()
    # A hole that ANOTHER PICKED REGION fills is not empty space -- it is that
    # region's ground, and the pair is a nested one. Protecting it took the inner
    # region's own territory away from it: a donut plus the core that fits it
    # exactly lost 26.9% of the pair's area, nearly all of the core, instead of
    # the 2.5% it lost before. Only a hole nobody claims is a deliberate void.
    if not _pre_holes.is_empty:
        _pre_holes = _safe_difference(_pre_holes, U)
    if not _pre_holes.is_empty:
        Th = _safe_difference(T, _pre_holes)
        if not Th.is_empty:
            T = Th
    # The close fills EVERY concavity narrower than 2*tol, including bays of one
    # pick nowhere near the other: HY + PAL.2 (111 px apart, nothing to bridge)
    # gave HY 28,945 px2 in pieces up to 9,557 px from PAL.2, and at reach 500
    # opt + sAMY.1 took 364,000 px2 far from their seam. A close of radius tol
    # bridges a gap up to 2*tol wide, so ground in it lies within 2*tol of BOTH
    # sides; ground further than that from every other pick is a bay of one
    # pick, not a gap between two. Keep only the former. And new
    # ground stays inside the section outline (hemi) -- picks grew up to 902,000
    # px2 past it at reach 5000.
    if tol and tol > 0 and len(sel) >= 2:
        bridged = _safe_difference(T, U)
        if not bridged.is_empty:
            # round, not mitre: a mitred corner reaches sqrt(2) times further
            near = {n: geoms[n].buffer(2.0 * tol, quad_segs=8) for n in sel}
            between = unary_union([_safe_intersection(near[a], near[b])
                                   for a, b in itertools.combinations(sel, 2)] or [Polygon()])
            keep_new = _safe_intersection(bridged, between)
            _cont = section_containers(features, id_prop) - set(sel)
            if _cont:
                cont = unary_union([_body(features, idx[c]) for c in _cont if c in idx]).buffer(0)
                if not cont.is_empty:
                    keep_new = _safe_intersection(keep_new, cont)
            T = unary_union([U, keep_new]).buffer(0)
    # Ground belonging to regions that are NOT being shared, so the bridged fill
    # cannot eat a third region.
    #
    # Except a region that BLANKETS the pair's territory. `hemi` is the
    # whole-hemisphere outline and wraps every other region in these files, so it
    # covers every gap between every pair. Defending it meant no gap could ever be
    # filled: the ground stayed unclaimed and showed as a black wedge between the
    # two regions, and raising the snap reach bridged MORE of hemi's ground and
    # made the wedge bigger. On the real TH + HPF that is 9,347 px at the default
    # reach, 80% of it inside hemi, growing to 122,715 px at reach 300. A
    # container is not a neighbour with ground to defend here -- the same rule
    # clean_lines applies with its `blanket` set.
    blanket_at = 0.98 * T.area if T.area > 0 else 0.0
    other_geoms = []
    for f in features:
        nm = (f.get("properties") or {}).get(id_prop)
        if nm is None or str(nm) in sel:
            continue
        try:
            g = shape(f["geometry"])
            if not g.is_valid:
                g = g.buffer(0)
            if g.is_empty:
                continue
            if blanket_at > 0 and _safe_intersection(g, T).area >= blanket_at:
                continue                       # wraps the pair: nothing to defend
            other_geoms.append(g)
        except Exception:
            pass
    other_union = unary_union(other_geoms).buffer(0) if other_geoms else Polygon()
    protected = other_union.difference(U).buffer(0) if not other_union.is_empty else Polygon()

    def _protect(area):
        if protected.is_empty:
            return area
        # Keep original selected territory as-is, but do not let the bridged/fill
        # area claim another unselected region.
        return unary_union([U, area.difference(protected)]).buffer(0)

    # STRICT (the default): ground in the shadow of a region standing in the
    # gap is left alone as well as that region's own ground -- a pixel is filled
    # only if its line to each pick is clear. Added to `protected` so the
    # territory, the partition AND the void filling all respect it; leaving it
    # out of T alone would let _close_voids hand it straight back. `fill_all`
    # fills every free pixel instead, which is what this always did before.
    if not fill_all and not protected.is_empty:
        sh = _shadows_between(protected, [geoms[n] for n in sel],
                              _safe_difference(T, U), tol)
        if not sh.is_empty:
            protected = unary_union([protected, sh]).buffer(0)
    T = _protect(T)
    # (No territory simplify. It de-densified the WHOLE territory outline, so it
    # reshaped coasts thousands of px from the seam -- 92% of the far-from-seam
    # ground at reach 40 -- filled regions' own holes and grew picks outside
    # hemi. The shared network is still thinned afterwards, by _simplify_shared,
    # which keeps the tiling exact.)

    # Voronoi sampling: coarse (== tol) for large regions so the output stays sparse,
    # but never coarser than ~1/24 of the smallest region's perimeter, so small
    # regions still get enough samples to tile precisely (no slivers of overlap).
    min_perim = min((g.length for g in geoms.values() if not g.is_empty), default=tol)
    # The SMALLEST region in the pair sets the sampling density, which is why a
    # 42-px-perimeter speck makes its 9,024-px-perimeter neighbour come back with
    # thousands of vertices. Driving this from `tol` alone instead is NOT the fix:
    # tried, and `opt` then grew 1039% against sAMY.3 (it was 55%), because a
    # coarse midline next to a tiny region hands it a huge Voronoi cell. The
    # clamp is load-bearing. The vertex count is dealt with where it is safe to
    # deal with it -- see FREE_EDGE_FLUFF in _simplify_shared.
    spacing = max(min(tol, min_perim / 24.0), 4.0)
    if snap_keep is not None:
        keep = snap_keep
        rest = {n: g for n, g in geoms.items() if n != keep}
        # Everything in the territory that is not the kept region. Its edge along
        # the join is therefore exactly the kept region's edge -- and an overlap
        # that used to be shared goes back to the kept region whole.
        avail = _safe_difference(T, geoms[keep])
        if avail.is_empty:
            raise ValueError(f"snapping to {keep} leaves the other region(s) "
                             f"nothing -- {keep} already covers them")
        if len(rest) == 1:
            claimed = {keep: geoms[keep], next(iter(rest)): avail}
        else:
            # Stretch by stretch. The territory beside a kept region comes apart
            # into separate pieces, and Voronoi-ing the lot of them together is
            # both unnecessary and lossy: a piece touching exactly one of the
            # others is that one's, whole. Only a piece between two of them needs
            # a midline, and only between those two.
            parts = {n: [] for n in rest}
            for piece in _polys(avail):
                collar = piece.buffer(max(float(tol) * 0.5, 2.0))
                near = {n: g for n, g in rest.items() if g.intersects(collar)}
                if len(near) == 1:
                    parts[next(iter(near))].append(piece)
                elif not near:
                    # reachable from none of them: nearest one takes it rather
                    # than it being left as a gap
                    parts[min(rest, key=lambda k: rest[k].distance(piece))].append(piece)
                else:
                    for n, got in _nearest_partition(piece, near, spacing).items():
                        got = _safe_intersection(got, piece)
                        if not got.is_empty:
                            parts[n].append(got)
            claimed = {keep: geoms[keep]}
            for n, got in parts.items():
                claimed[n] = unary_union(got).buffer(0) if got else Polygon()
    else:
        # Ground only ONE pick owns stays that pick's. The nearest-region split
        # samples each outline at `spacing` (up to the reach) and insets the
        # sites, so run over the whole territory it handed pieces of a pick's own
        # body to its partner -- 33 of 41 real pairs over 100 px2 at reach 40
        # (TH + lft 48,576), up to 275,814 at 500 -- and every further Share moved
        # the seam again (HY + TH 531,927 px2 on the second pass). Only contested
        # ground -- the bridged gap and any overlap between the picks -- is split.
        excl = {}
        for n in sel:
            others = [geoms[m] for m in sel if m != n]
            excl[n] = _safe_difference(geoms[n], unary_union(others)) if others else geoms[n]
        owned = unary_union([g for g in excl.values() if not g.is_empty] or [Polygon()])
        contested = _safe_difference(T, owned)
        claimed = {n: excl[n] for n in sel}
        if not contested.is_empty and contested.area > 1e-6:
            share = _nearest_partition(contested, geoms, spacing)
            for n in sel:
                got = share.get(n)
                if got is not None and not got.is_empty:
                    got = _safe_intersection(got, contested)
                    claimed[n] = unary_union([claimed[n], got]).buffer(0)

    newg = {}
    for n in sel:
        if n == snap_keep:
            # handed straight back: no clip, no hole-filling, nothing. That is
            # what snapping the seam to this region means.
            newg[n] = claimed[n]
            continue
        # keep the change local: a region only grows/shrinks by ~tol from its outline
        g = claimed[n].intersection(geoms[n].buffer(tol, join_style=2)).buffer(0)
        if not protected.is_empty:
            g = g.difference(protected).buffer(0)
        # NB: do NOT clean per-region here. Adjacent Voronoi cells share EXACT edges;
        # a morphological open + simplify applied to each region on its own moves the
        # two copies of that edge independently, which is what strewed hairline voids
        # AND overlaps along every shared border. The inputs were already de-haired
        # above (before they became Voronoi generators), so nothing needs it here.
        newg[n] = _fill_new_unprotected_holes(g, protected, preserve=geoms[n])

    # Close the tiling. Two things strand empty space, and both are handed to the
    # nearest region so "Share borders" can't leave a gap behind:
    #   * territory inside T that the per-region buffer(tol) clip left unclaimed --
    #     bridged ground further than tol from EVERY original outline;
    #   * space the grown regions ENCIRCLED without filling. Where the two are
    #     further apart than the ~2*tol the close bridges, they meet around the
    #     hollow rather than across it, and that void isn't in T at all.
    # Holes that belong to somebody -- another region's ground, or a hole the
    # region already had -- are left exactly as they are.
    # (_pre_holes is computed with the territory above, because the close itself
    # would otherwise have filled these before anything got here.)

    def _spannable(p):
        """Whether a close at this reach could bridge across `p` -- i.e. it is
        narrower than 2*tol somewhere. Erosion by tol empties exactly those."""
        try:
            return p.buffer(-float(tol)).is_empty
        except Exception:
            return True                   # unmeasurable: behave as before

    def _close_voids(rounds, holes_only=False, skip=None):
        """Hand every leftover empty piece to its nearest region.

        holes_only restricts it to ENCLOSED voids. `skip` is ground to leave
        unclaimed whatever happens -- after simplification that is the ribbon
        along the territory's OUTER edge, because a thinned outline sits a hair
        inside T all the way round and reclaiming that would paste the dense
        pre-simplify edge straight back. An open void in the INTERIOR is a
        different thing: it is a hole between two regions that are supposed to
        tile, which is exactly what must not be left behind."""
        for _ in range(rounds):     # filling one void can expose another
            claimed_all = unary_union([g for g in newg.values() if not g.is_empty]).buffer(0)
            void = [] if holes_only else [
                p for p in _polys(_safe_difference(T, claimed_all)) if p.area > 1.0]
            # An ENCLOSED void only gets absorbed if the reach could actually
            # span it. An open void lies inside T, which the close already built
            # at this reach; a hole has no such limit on it, and that is what
            # made this hand over any amount of ground that happened to be ringed
            # in. A comb of 40-px teeth with 160-px slots grew 327% at the
            # default reach of 40 -- reach 80, slots 160 -- taking 99% of every
            # slot, while the territory itself grew only 20,000 px. Two
            # 350-degree annulus sectors 10 px apart ended up 100% OVERLAPPING,
            # one buried inside the other, because one had absorbed the whole
            # interior; which one depended on click order.
            #
            # Eroding by tol empties anything narrower than 2*tol, which is
            # exactly the reach the slider promises.
            void += [h for h in _holes(claimed_all)
                     if h.area > 1.0 and _spannable(h)]
            if not void:
                return
            orphan = unary_union(void).buffer(0)
            # Subtract what belongs to somebody rather than skipping the whole void:
            # a hollow that is part another region's ground and part empty must still
            # give up its empty part, or the leftover gap survives.
            if not protected.is_empty:
                orphan = _safe_difference(orphan, protected)
            if not _pre_holes.is_empty:
                orphan = _safe_difference(orphan, _pre_holes)
            if skip is not None and not skip.is_empty:
                orphan = _safe_difference(orphan, skip)
            parts = [p for p in _polys(orphan) if p.area > 1.0]
            if not parts:
                return
            orphan = unary_union(parts)
            # Partition against each region's LOCAL outline: same midline answer as
            # the full geometry, without re-sampling megapixel boundaries each round.
            win = orphan.buffer(max(float(tol) * 2.0, 25.0))

            def _near_regions(skip=None):
                out = {}
                for n in sel:
                    if skip is not None and n == skip:
                        continue
                    gl = _safe_intersection(newg[n], win)
                    if not gl.is_empty:
                        out[n] = gl
                return out

            # The snapped region sits this round out so the others take the
            # leftovers and its edge stays put. But if NOTHING else is near a
            # leftover it takes it after all: leaving it unclaimed puts a black
            # void on the map between two regions that are supposed to tile, and
            # handing it to a distant region drops a detached shard on one it has
            # nothing to do with. Both were tried; a void is the worst of the
            # three. What snapping promises is the SEAM -- the border between the
            # picked regions lands on this one's edge -- not that no vertex
            # anywhere on it can change.
            local = _near_regions(skip=snap_keep)
            if not local and snap_keep is not None:
                local = _near_regions()
            if not local:
                return
            if len(local) == 1:
                extra = {next(iter(local)): orphan}
            else:
                extra = _nearest_partition(orphan, local, spacing)
            grew = False
            for n in sel:
                add = extra.get(n)
                if add is None or add.is_empty:
                    continue
                # a _nearest_partition fallback returns its inputs; clipping to the
                # orphan keeps that from silently re-claiming live territory
                add = _safe_intersection(add, orphan)
                if add.is_empty or add.area <= 0:
                    continue
                newg[n] = _snap_polys(unary_union([newg[n], add]).buffer(0))
                grew = True
            if not grew:
                return

    _close_voids(2)

    # Thin the Voronoi output, which is far denser than the source outlines. This
    # MUST run on the shared boundary NETWORK, not per region: simplifying each
    # region on its own moves the two copies of a shared edge apart, which is what
    # used to strew slivers along every border. Run it after the reclaim above --
    # that pastes in fresh Voronoi edges, so thinning first would be undone.
    keep_out = Polygon()
    if not protected.is_empty or not _pre_holes.is_empty:
        keep_out = unary_union([g for g in (protected, _pre_holes) if not g.is_empty]).buffer(0)
    thin = min(max(tol * 0.5, 3.0), MAX_THIN) if simplify is None else float(simplify)
    # Not when a seam is snapped. There is no dense midline to thin -- the seam is
    # the kept region's own outline, and the other's new edge is a copy of it --
    # and thinning the network would move the one outline that has to stay put.
    if thin > 0 and snap_keep is None:
        # border the picks already shared is left exactly where it was
        frozen = Polygon()
        try:
            fz = []
            for i, a in enumerate(sel):
                for b in sel[i + 1:]:
                    c = _safe_intersection(geoms[a].boundary.buffer(0.5),
                                           geoms[b].boundary.buffer(0.5))
                    if not c.is_empty:
                        fz.append(c)
            if fz:
                frozen = unary_union(fz).buffer(0)
        except Exception:
            frozen = Polygon()
        newg = _simplify_shared(newg, thin, precision, keep_out=keep_out, frozen=frozen)
        # Thinning is what used to leave the black wedges: measured over 72
        # configurations it opened an interior void in 10 of them, up to 607 px,
        # while skipping it left 2 of 72 at 1.4 px. Douglas-Peucker moves a
        # vertex by at most `thin`, so anything further than that from the
        # territory's edge is a genuine hole between regions rather than the
        # ribbon the old holes_only pass was protecting.
        try:
            _rim = T.boundary.buffer(max(thin * 1.5, 1.0))
        except Exception:
            _rim = None
        _close_voids(2, skip=_rim)
        # Thinning a seam that runs along the coast can cut a pick's OWN ground
        # (1,549 px2 of HY + opt), and the rim skip above keeps it from coming
        # back. Give such ground back to the pick that owned it -- only that
        # pick, only where it is still attached, so no new part, no stray shard.
        try:
            claimed_now = unary_union([g for g in newg.values() if not g.is_empty]).buffer(0)
            lost_own = _safe_difference(U, claimed_now)
            if not keep_out.is_empty:
                lost_own = _safe_difference(lost_own, keep_out)
            for piece in _polys(lost_own):
                if piece.area < 1.0:
                    continue
                owner = max(sel, key=lambda n: _safe_intersection(geoms[n], piece).area)
                if (_safe_intersection(geoms[owner], piece).area >= 0.5 * piece.area
                        and newg[owner].distance(piece) < 1e-6):
                    joined = unary_union([newg[owner], piece]).buffer(0)
                    if len(_polys(joined)) <= len(_polys(newg[owner])):
                        newg[owner] = joined
        except Exception:
            pass

    # Every pick valid before the overlap pass: the void-closing grid snap can
    # leave a self-touching ring (opt + sAMY.2 at reach 500 raised "side location
    # conflict" in the intersects() below).
    for n in sel:
        if not newg[n].is_empty and not newg[n].is_valid:
            newg[n] = _valid(newg[n])

    # Guarantee a clean tiling: remove any residual sliver overlaps deterministically
    # (later-listed region cedes the overlap to the earlier one, giving an exact
    # shared edge). That is only safe while the overlap really IS a sub-pixel
    # Voronoi artifact. When an upstream step double-claims a region's whole body,
    # an unconditional cede quietly DELETES it -- the ribbon-erasing bug. So a
    # substantial overlap is split down its own midline instead: both regions keep
    # a share, the result is still disjoint, and the damage stays visible-but-small
    # rather than total.
    for i in range(len(sel)):
        a = newg[sel[i]]
        if a.is_empty:
            continue
        for j in range(i + 1, len(sel)):
            b = newg[sel[j]]
            if b.is_empty:
                continue
            try:
                hit = a.intersects(b)
            except Exception:          # GEOS "side location conflict" on near-
                hit = True             # degenerate input: let the robust path decide
            if not hit:
                continue
            try:
                ov = _safe_intersection(a, b)
            except Exception:
                ov = Polygon()
            if ov.is_empty or ov.area <= 0:
                continue
            if snap_keep in (sel[i], sel[j]):
                # Which region cedes is otherwise just which was clicked first.
                # A snapped region cedes nothing and changes nothing, so the
                # other one gives the overlap up however they were ordered.
                other = sel[j] if snap_keep == sel[i] else sel[i]
                trimmed = _safe_difference(newg[other], newg[snap_keep])
                if not trimmed.is_empty:
                    newg[other] = trimmed
                    if other == sel[i]:
                        a = newg[other]
                continue
            if ov.area > 0.05 * min(a.area, b.area):
                # Not an artifact. Give each side the part of the contested ground
                # nearest to the body it does NOT share, so neither is wiped out.
                body = {}
                for nm, g in ((sel[i], a), (sel[j], b)):
                    solo = _safe_difference(g, ov)
                    if not solo.is_empty:
                        body[nm] = solo
                if len(body) == 2:
                    try:
                        share = _nearest_partition(ov, body, spacing)
                        keep_a = _safe_intersection(share.get(sel[i], Polygon()), ov)
                        na = unary_union([_safe_difference(a, ov), keep_a]).buffer(0)
                        nb = _safe_difference(b, na)
                        if not na.is_empty and not nb.is_empty:
                            newg[sel[i]] = na
                            newg[sel[j]] = nb
                            continue
                    except Exception:
                        pass
            nb = _safe_difference(b, a)      # plain cede -- cleaning it here would
            if not nb.is_empty:              # break the shared edge again
                newg[sel[j]] = nb
            else:
                # b has no ground of its own: it ended up wholly inside a.
                # Skipping the write -- which is what used to happen, because
                # `b - a` is empty -- left the two regions 100% overlapping, one
                # buried inside the other, and which one it was came down to
                # click order. a is the container here by definition, so a gives
                # the ground up instead and b keeps its shape.
                na = _safe_difference(a, b)
                if not na.is_empty:
                    newg[sel[i]] = na
                    a = na

    # Needles: rings that double back to within a hair of themselves, enclosing
    # no area. 20 of 86 region-results carried one at the default reach, the
    # longest a 534-px pair of edges 0.004883 px apart, and six pairs produced
    # them at a measured 0.000000 px. They are what Xenium Explorer rejects, and
    # the manual clean-up tool could not always reach them. despike() only
    # deletes vertices that enclose no area and returns its input untouched if
    # the area moves, so this cannot reshape anatomy -- but the pair is checked
    # as a SET afterwards all the same, because a seam has to stay coincident.
    try:
        spiked = dict(newg)
        for n in sel:
            g = newg[n]
            if g.is_empty or n == snap_keep:   # snapped: back exactly as it came
                continue
            # Snap to the precision grid FIRST. A needle narrower than the grid
            # collapses when its two sides land on the same coordinates, which
            # is what snap-rounding is for and what despike cannot reach: a
            # 683 px needle on CP sat 0.004883 px wide and survived despike at
            # every eps up to 0.5. The grid is sub-pixel (see `precision`), so
            # the outline does not visibly move.
            try:
                if precision and float(precision) > 0:
                    sn = set_precision(g, float(precision)).buffer(0)
                    if not sn.is_empty and abs(sn.area - g.area) <= max(
                            1.0, g.area * 1e-5):
                        g = sn
            except Exception:
                pass
            d = despike(g)
            # A needle encloses no area, so taking one off must not move any.
            # The reconstruction is not exact to the last bit, though: CP's cost
            # 2.02 px of 24,979,820. This guard used to allow 1e-9 relative --
            # 0.025 px -- so it REJECTED despike's own successful cleanups, and
            # it rejected them for the whole SET at once, so one region's noise
            # blocked every other region's needle too. despike itself allows
            # area * 1e-4 (2,479 px on CP), which is far too loose to be silent.
            if not d.is_empty and abs(d.area - g.area) <= max(1.0, g.area * 1e-6):
                g = d
            spiked[n] = g
        # Cleaning can leave a sub-pixel overlap where a needle sat ON the seam.
        # This used to demand the cleaned set overlap by <= 1e-6 px -- but the
        # UNCLEANED set already overlaps by up to 0.034 px of ordinary boolean
        # noise, so the test could essentially never pass and every cleanup was
        # discarded. That is why 506 px needles that despike removes in one call
        # were still in the output with the de-spiking supposedly on.
        #
        # So resolve the sliver instead of refusing over it: in canonical order
        # the later region gives it up, which leaves the pair properly disjoint
        # rather than merely no worse than before. A snapped region gives up
        # nothing -- that is its contract -- so its partner cedes instead.
        for i, a in enumerate(sel):
            for b in sel[i + 1:]:
                ov = _safe_intersection(spiked[a], spiked[b])
                if ov.is_empty or ov.area <= 0:
                    continue
                loser, keeper = (a, b) if b == snap_keep else (b, a)
                trimmed = _safe_difference(spiked[loser], spiked[keeper])
                if (not trimmed.is_empty
                        and spiked[loser].area - trimmed.area
                        <= max(1.0, spiked[loser].area * 1e-6)):
                    spiked[loser] = trimmed
        # The trim can fail on its own terms -- a region too small to afford the
        # sliver keeps it -- so the overlap is checked explicitly rather than
        # assumed. Cleaning may never leave the pair overlapping more than it
        # already did; without this gate one pair went from 0.034 px to 3.663.
        def _worst(gs):
            w = 0.0
            for i, x in enumerate(sel):
                for y in sel[i + 1:]:
                    if gs[x].is_empty or gs[y].is_empty:
                        continue
                    w = max(w, _safe_intersection(gs[x], gs[y]).area)
            return w

        base = unary_union([newg[n] for n in sel if not newg[n].is_empty]).buffer(0)
        got = unary_union([g for g in spiked.values() if not g.is_empty]).buffer(0)
        if (not got.is_empty
                and abs(got.area - base.area) <= max(2.0, base.area * 1e-6)
                and _worst(spiked) <= max(_worst(newg), 1e-6) + 0.5
                and all(not spiked[n].is_empty or newg[n].is_empty for n in sel)):
            newg = spiked
    except Exception:
        pass                                  # keep the un-cleaned result

    # Collinear fluff on the free edges, where _simplify_shared could not do it.
    # AFTER the needle removal, not before: thinning first changed the geometry
    # enough that the snap no longer collapsed dft's 506 px needle.
    newg = _thin_free_edges(newg, sel, FREE_EDGE_FLUFF,
                            max(float(tol) * 0.25, 0.5))

    # SNAP: the kept region goes back to exactly the outline it arrived with,
    # and its partner takes everything the snap gives up.
    #
    # Done LAST, on purpose. Every pass above can move a boundary a little --
    # the partition hands out territory, _close_voids hands out leftovers, the
    # thinning rebuilds rings -- and chasing each one separately is how this
    # stayed broken. Restoring the exact outline here makes all of that
    # irrelevant for the snapped region, and because the partner is then
    # `partner - exact`, the seam is the kept region's own boundary by
    # construction rather than by reconstruction, so the two coincide to the
    # last bit.
    #
    # The ground the snap gives up has to go somewhere: left unclaimed it is a
    # black void between two regions that are supposed to tile, which is the
    # complaint that started all of this. It goes to the partner -- or is split
    # between the partners by the same nearest-region rule as everything else,
    # when more than two regions were picked.
    if snap_keep is not None and snap_keep in newg:
        exact = raw_bodies.get(snap_keep)
        if exact is not None and not exact.is_empty:
            if not exact.is_valid:
                exact = exact.buffer(0)
        if exact is not None and not exact.is_empty:
            others = [n for n in sel if n != snap_keep]
            freed = _safe_difference(newg[snap_keep], exact)
            trimmed = {}
            for n in others:
                t = _safe_difference(newg[n], exact)
                # Never erase the partner to honour a snap. It can happen: the
                # partner may lie wholly inside the kept outline when the two
                # overlapped to begin with. Better to refuse the snap than to
                # delete somebody's region, and the caller already reports a
                # ValueError from here in words.
                if t.is_empty and not newg[n].is_empty:
                    raise ValueError(
                        f"snapping the seam onto {snap_keep!r} would erase "
                        f"{n!r}, which lies inside it; nothing was changed")
                trimmed[n] = t
            if not freed.is_empty and others:
                # Only freed ground a partner actually TOUCHES goes to it, and
                # never a hairline. Handing over everything gave CP 24 detached
                # shards and +18 needles / +42 spikes on "Snap to dft" -- the
                # slivers from all round dft's outline, and a 12,224 px2 notch
                # between dft and V3, 11,600 px from the seam. Ground no partner
                # touches stays as it was before the share: unclaimed.
                live = {n: g for n, g in trimmed.items() if not g.is_empty}
                for piece in _polys(freed):
                    if _hairline(piece):
                        continue
                    touch = {n: g for n, g in live.items() if g.distance(piece) <= 0.5}
                    if not touch:
                        continue
                    if len(touch) == 1:
                        share = {next(iter(touch)): piece}
                    else:
                        try:
                            share = _nearest_partition(piece, touch, spacing)
                        except Exception:
                            share = {}
                    for n, g in share.items():
                        if g is not None and not g.is_empty:
                            g = _safe_intersection(g, piece)
                            trimmed[n] = unary_union([trimmed[n], g]).buffer(0)
                            live[n] = trimmed[n]
            # what the partner keeps of its own result, minus the kept region,
            # can be a hairline crumb (a sliver of the kept outline it used to
            # overlap): drop those too
            # -- and a part that is ALL new ground, cut off from the partner by
            # the kept outline, is a shard of bridged territory (opt took 110,795
            # px2 up to 1,915 px away snapping to sAMY.1 at reach 500): it stays
            # unclaimed, as it was
            for n in list(trimmed):
                ps = _polys(trimmed[n])
                if len(ps) > 1:
                    own = raw_bodies.get(n)
                    good = [p for p in ps if not _hairline(p)
                            and (own is None or own.is_empty
                                 or _safe_intersection(p, own).area > 1.0)]
                    if good:
                        trimmed[n] = unary_union(good)
            # where the kept edge meets the partner's own edge at a hair's angle
            # the partner is left a sharp crack (opt beside sAMY.1); trim such
            # vertices along the seam, closing a crack only over empty ground
            try:
                seam_zone = exact.boundary.buffer(2.0)
                taken = unary_union([exact] + ([protected] if not protected.is_empty else []))
                for n in list(trimmed):
                    if not trimmed[n].is_empty:
                        trimmed[n] = _trim_spikes(trimmed[n], seam_zone, taken)
            except Exception:
                pass
            newg[snap_keep] = exact
            for n, g in trimmed.items():
                newg[n] = g

    # FINAL needle sweep, after every pass. Any of them can leave one: in strict
    # mode the shadow carved out of the territory left an 80 px needle, exactly
    # 0.000000 px wide, on opt beside sAMY.4 -- downstream of the de-spike
    # above, which had already run. A needle encloses no area, so removing one
    # cannot move a region; that is checked anyway, as is that no new overlap
    # opens, and the snapped region is left exactly as it is.
    for n in sel:
        if n == snap_keep or newg[n].is_empty:
            continue
        try:
            d = despike(newg[n])
        except Exception:
            continue
        if d.is_empty or abs(d.area - newg[n].area) > max(1.0, newg[n].area * 1e-6):
            continue
        clash = False
        for m in sel:
            if m == n or newg[m].is_empty:
                continue
            try:
                # No new overlap at all, beyond float noise. Allowing half a
                # pixel here took the worst pairwise overlap from 0.000 to 0.308.
                if (_safe_intersection(d, newg[m]).area
                        > _safe_intersection(newg[n], newg[m]).area + 1e-6):
                    clash = True
                    break
            except Exception:
                clash = True
                break
        if not clash:
            newg[n] = d

    # ...and any DEGENERATE part left behind. Taking a needle off can leave its
    # tip as a separate polygon of no real area: opt beside sAMY.4 came out in
    # two parts, the second 0.0 px. Nothing anybody draws is below half a pixel,
    # so a part that small is never anatomy, and removing one takes no ground
    # from anyone. The snapped region is still left exactly as it is.
    for n in sel:
        if n == snap_keep or newg[n].is_empty:
            continue
        parts = _polys(newg[n])
        if len(parts) < 2:
            continue
        keep = [p for p in parts if p.area >= 0.5]
        if keep and len(keep) < len(parts):
            newg[n] = keep[0] if len(keep) == 1 else unary_union(keep).buffer(0)

    # LAST: a sharp tip where the new seam meets a pick's own, exactly kept edge
    # at a hair's angle (opt and sAMY.4 at reach 500). Only where this Share
    # changed the outline, and a crack is closed only over ground nobody holds.
    for n in sel:
        if n == snap_keep or newg[n].is_empty:
            continue
        try:
            zone = newg[n].symmetric_difference(geoms[n]).buffer(2.0)
            if zone.is_empty:
                continue
            others = unary_union([newg[m] for m in sel if m != n and not newg[m].is_empty]
                                 + ([protected] if not protected.is_empty else []))
            t = _trim_spikes(newg[n], zone, others)
            if t is not None and not t.is_empty and t.area >= 0.99 * newg[n].area:
                newg[n] = t
        except Exception:
            pass

    out = json.loads(json.dumps(features))
    for n in sel:
        _write_body(out, features, idx[n], newg[n])

    return {"features": out, "borders": _shared_arcs(newg, sel, spacing)}


def snap_to_edits(before, after, id_prop, moved, tol=40.0, precision=0.0):
    """Authoritative snap: the moved region(s) keep exactly the shape the user
    dragged; every other region conforms. Handles BOTH directions --
      * push (mover grew into a neighbour): the overlap is cut out of the neighbour;
      * pull (mover shrank away, leaving a gap): the vacated strip is handed to the
        nearest neighbour within tol.
    Returns the updated feature list.
    """
    def _index(feats):
        """name -> its WHOLE body (same name twice = one multi-part region)."""
        d = {}
        for k, f in enumerate(feats):
            nm = (f.get("properties") or {}).get(id_prop)
            if nm is None:
                continue
            g = _geom_at(feats, k)
            if g.is_empty:
                continue
            nm = str(nm)
            d[nm] = unary_union([d[nm], g]).buffer(0) if nm in d else g
        return d

    bidx, aidx = _index(before), _index(after)
    moved = [str(m) for m in (moved or []) if str(m) in aidx]
    if not moved:
        return json.loads(json.dumps(after))

    movers_old = unary_union([bidx[m] for m in moved if m in bidx])
    movers_new = unary_union([aidx[m] for m in moved if m in aidx])
    vacated = movers_old.difference(movers_new)              # pulled-away area

    others = [n for n in aidx if n not in moved]
    newg = {}
    for n in others:
        newg[n] = aidx[n].difference(movers_new)             # push: cede overlap

    # pull: split the vacated strip among the neighbours that border it (within
    # tol), each getting the part nearest to it -- so a strip facing two regions
    # is divided along its midline instead of dumped on one.
    if not vacated.is_empty and others:
        near = {n: aidx[n] for n in others if aidx[n].distance(vacated) <= tol + 1e-6}
        if near:
            parts = _nearest_partition(vacated, near, max(tol / 2.0, 4.0))
            for n, part in parts.items():
                if not part.is_empty:
                    newg[n] = unary_union([newg[n], part])

    out = json.loads(json.dumps(after))
    idx = _index_all(after, id_prop)
    for n in others:
        g = newg[n]
        # Only a region the snap actually changed is rewritten. Writing every
        # one back through a 1 px grid moved ISO 13,198 px2 from 5,900 px away,
        # lost ~14,000 px2 of map and added +2,663 px2 of overlap -- with no
        # edit at all. (precision now defaults to 0: no rounding.)
        if g.symmetric_difference(aidx[n]).area <= 1e-9:
            continue
        if precision and not g.is_empty:
            g = set_precision(g, float(precision))
        g = g.buffer(0)
        if n in idx:
            _write_body(out, after, idx[n], g)
    return out


def _index(features, id_prop):
    """name -> the FIRST feature carrying it.

    Only safe where one polygon per name is what's meant (splitting; checking a new
    name isn't taken). Anything that reshapes a whole region wants _index_all, or
    the other lobes of a multi-part region are invisible to it."""
    idx = {}
    for k, f in enumerate(features):
        nm = (f.get("properties") or {}).get(id_prop)
        if nm is not None:
            idx.setdefault(str(nm), k)
    return idx


def _common_base_name(names):
    """'Fiber Tracts 1' + 'Fiber Tracts 2' -> 'Fiber Tracts'; else the first name.

    Damage shapes are the exception. Stripping the number off 'separation.1' and
    'separation.2' left 'separation.', which is not a damage name at all: the
    merged shape stopped counting as damage, and clean_lines -- which skips a
    feature only when damage.is_damage() says so -- then deleted it. Merging
    damage of one designation keeps the LOWEST number, so the result is still a
    damage shape, and still the one the YAML's `voids` already points at.
    """
    names = [str(n) for n in names]
    parsed = [_damage.parse_name(n) for n in names]
    if parsed and all(t for t, _n in parsed) and len({t for t, _n in parsed}) == 1:
        tag = parsed[0][0]
        nums = sorted(n for _t, n in parsed if n is not None)
        return _damage.format_name(tag, nums[0]) if nums else names[0]
    # a dot counts as a separator too: 'PAL.1' + 'PAL.2' used to merge to 'PAL.'
    stripped = [re.sub(r"[\s_\-.]*\d+\s*$", "", n).strip() for n in names]
    if stripped and stripped[0] and all(s == stripped[0] for s in stripped):
        return stripped[0]
    return names[0] if names else "merged"


def _piece_name(name, taken, all_names):
    """The name for the second piece of a split.

    A DAMAGE shape has to stay one. '<name> 2' produced 'separation.7 2', which
    is not a damage name, so the piece stopped counting as damage and
    clean_lines deleted it on the next tidy-up. Damage numbering is global per
    designation, so the new piece takes the next free number for its own.
    """
    tag, _n = _damage.parse_name(name)
    if tag:
        n = _damage.next_number(list(all_names), tag)
        cand = _damage.format_name(tag, n)
        while cand in taken:
            n += 1
            cand = _damage.format_name(tag, n)
        return cand
    i = 2
    while f"{name} {i}" in taken:
        i += 1
    return f"{name} {i}"


def _rename_props(feature, id_prop, name):
    props = dict(feature.get("properties") or {})
    props[id_prop] = name
    if isinstance(props.get("classification"), dict):
        props["classification"] = {**props["classification"], "name": name}
    feature["properties"] = props
    return feature


SRC_PROP = "_srcFile"


def concat_region_files(groups, id_prop, src_prop=SRC_PROP):
    """Several region files' features in one collection, each tagged with the
    file it came from.

    `groups` is [(label, features), ...] in the order they were picked, where
    `label` is the file's name without its extension.

    Names are left exactly as the files have them; the tag is what keeps the
    files apart, so the sidebar can list them under a heading each instead of
    running them together in one heap. Feature ORDER is preserved, because that
    order is the draw order -- grouping the rows by file instead of marking where
    each file starts would misreport what covers what.

    One thing a heading cannot separate: a name used by two of the files. Two
    regions carrying one name are ONE region in this app -- every edit acts on
    all of its parts -- so those really are a single multi-part region however
    they are listed. `shared` names them, so the UI can say so instead of
    implying a separation that is not there.

    Returns {features, sources, shared}.
    """
    feats, sources = [], []
    seen = {}
    for label, features in groups:
        src = str(label or "file")
        sources.append(src)
        for f in (features or []):
            f = json.loads(json.dumps(f))        # never touch the caller's
            props = dict(f.get("properties") or {})
            props[src_prop] = src
            f["properties"] = props
            feats.append(f)
            nm = str(props.get(id_prop) or "").strip()
            if nm:
                seen.setdefault(nm, set()).add(src)
    shared = sorted(nm for nm, srcs in seen.items() if len(srcs) > 1)
    return {"features": feats, "sources": sources, "shared": shared}


#: How far apart two picks can be for a gap between them to be found at all.
#: Generous on purpose: the circle has to be able to show a gap WIDER than the
#: current reach, or it could never tell anyone to raise it.
GAP_SEARCH = 1000.0

#: A gap narrower than this is boolean noise, not something anybody is trying
#: to fill.
MIN_GAP_WIDTH = 2.0

#: The reach readout is the smallest Snap reach whose close covers this share
#: of the gap -- "closes it" in the sense the user sees, not to the last pixel.
GAP_COVER = 0.95


def _reach_to_close(U, piece, hi):
    """Smallest Snap reach whose close of the picks' union U covers GAP_COVER of
    `piece` -- the same mitre close partition_regions uses, so this is the reach
    at which Share borders (Fill all) actually fills the gap. The close only
    grows with its radius, so bisect. A close at radius r is decided by U within
    a few r of a point (mitre joins reach further than r at sharp corners), so U
    is clipped to a window around the piece first. None if even `hi` does not
    cover it."""
    target = piece.area * GAP_COVER
    if target <= 0:
        return None

    def covered(r):
        win = piece.envelope.buffer(6.0 * r + 2.0, join_style=2)
        u = _safe_intersection(U, win)
        if u.is_empty:
            return False
        try:
            c = u.buffer(r, join_style=2).buffer(-r, join_style=2)
        except Exception:
            return False
        return _safe_intersection(c, piece).area >= target

    lo, hi = 0.5, float(hi)
    if not covered(hi):
        return None
    for _ in range(14):
        if hi - lo < 0.5:
            break
        mid = 0.5 * (lo + hi)
        if covered(mid):
            hi = mid
        else:
            lo = mid
    return hi


def gap_between(features, id_prop, names, search=GAP_SEARCH):
    """Where the gap between the picked regions is, and how wide it is there.

    The Snap reach circle used to sit at the midpoint of the closest pair of
    vertices. That is the wrong question as soon as the two regions touch
    anywhere: where they touch they are 0 px apart, so the "closest point" is
    somewhere on the border they already share -- in practice whichever
    zero-distance pair the search met first -- and the gap you are actually
    trying to fill is exactly where they do NOT touch, so the circle could never
    land on it. Measured on HPF + dft: the circle sat on their shared border,
    7,886 px from the only unclaimed gap between them.

    So this finds the gap itself: ground nobody owns, lying between at least
    two of the picks, within reach of a close at `search`. Of those pieces the
    largest is "the gap", and the circle goes at its WIDEST point -- the centre
    of the largest circle that fits inside it. The reach that closes the gap is
    MEASURED (`reach`), not taken as half that width: the widest circle is
    usually pinned by a third region or the section edge, and an open bay needs
    far more than width/2. Assuming width/2 was wrong for 21 of 22 real gaps.

    Same ownership rules as partition_regions: another region's ground is never
    gap, and a region that wraps the picks (`hemi`) is a container rather than a
    neighbour -- its ground is exactly what a gap between two of its children is.

    Returns {kind, centre, width, area, pieces[, reach]}:
      kind "gap"    -- the circle goes on the gap; `width` is the gap's width at
                       its widest; `reach` is the smallest Snap reach that
                       closes it (None if no reach up to `search` does)
      kind "apart"  -- no unclaimed gap, but the picks do not touch; the circle
                       goes where they come closest and `width` is that distance
      None          -- they already touch with nothing visible between them, so
                       there is nothing to bridge and no circle to draw
    """
    from shapely.ops import polylabel
    idx = _index_all(features, id_prop)
    sel = sorted({str(n) for n in names if str(n) in idx})
    if len(sel) < 2:
        return None
    bodies = {}
    for n in sel:
        g = _body(features, idx[n])
        if g is not None and not g.is_empty:
            bodies[n] = g if g.is_valid else g.buffer(0)
    if len(bodies) < 2:
        return None
    U = unary_union(list(bodies.values())).buffer(0)
    R = max(float(search), 1.0)
    try:
        closed = U.buffer(R, join_style=2).buffer(-R, join_style=2).buffer(0)
    except Exception:
        return None
    zone = _safe_difference(closed, U)
    if zone.is_empty:
        return _narrowest_answer(bodies)

    blankets, others = [], []
    for f in features:
        nm = (f.get("properties") or {}).get(id_prop)
        if nm is None or str(nm) in bodies:
            continue
        try:
            g = shape(f["geometry"])
            if not g.is_valid:
                g = g.buffer(0)
        except Exception:
            continue
        if g.is_empty:
            continue
        # wraps the picks: a container, not a neighbour
        if U.area > 0 and _safe_intersection(g, U).area >= 0.98 * U.area:
            blankets.append(g)
        else:
            others.append(g)
    if others:
        zone = _safe_difference(zone, unary_union(others).buffer(0))
    if blankets:
        # ground outside the section is not a gap between two of its regions
        zone = _safe_intersection(zone, unary_union(blankets).buffer(0))
    if zone.is_empty:
        return _narrowest_answer(bodies)

    # Only pieces BETWEEN picks -- each must touch at least two of them -- and
    # only ones a person could see. Boolean noise along a shared border leaves
    # slivers a pixel or two wide all over real files (7 px, 1.7 px across, on
    # HPF + dft), and a circle parked on one of those says nothing.
    names_ = list(bodies)
    found = []
    for p in _polys(zone):
        if p.area <= 1.0:
            continue
        if sum(1 for n in names_ if p.distance(bodies[n]) < 1.0) < 2:
            continue
        try:
            c = polylabel(p, tolerance=max(0.5, (p.area ** 0.5) / 500.0))
        except Exception:
            c = p.representative_point()
        w = 2.0 * c.distance(p.exterior)
        for h in p.interiors:
            w = min(w, 2.0 * c.distance(h))
        if w >= MIN_GAP_WIDTH:
            found.append((p, c, w))
    if found:
        p, c, w = max(found, key=lambda t: t[0].area)
        reach = _reach_to_close(U, p, R)
        return {"kind": "gap", "centre": [float(c.x), float(c.y)], "width": float(w),
                "area": float(p.area), "pieces": len(found),
                "reach": None if reach is None else float(reach)}
    return _narrowest_answer(bodies)


def _narrowest_answer(bodies):
    """No real gap between the picks. If they are apart, the circle goes where
    they come closest -- measured edge to edge, not between vertices, which on
    HY + PAL.2 put the old circle 1,343 px from the true narrowest point. If they
    already touch, there is nothing to bridge, and no circle is the honest
    answer: the old one sat on their shared border, somewhere arbitrary."""
    names_ = list(bodies)
    best = None
    for i, a in enumerate(names_):
        for b in names_[i + 1:]:
            try:
                pa, pb = nearest_points(bodies[a], bodies[b])
            except Exception:
                continue
            d = pa.distance(pb)
            if best is None or d < best[0]:
                best = (d, pa, pb)
    if best is None or best[0] < 1.0:
        return None
    d, pa, pb = best
    return {"kind": "apart", "centre": [(pa.x + pb.x) / 2.0, (pa.y + pb.y) / 2.0],
            "width": float(d), "area": 0.0, "pieces": 0}


def _intruder_shadow(blocker, a, b, zone):
    """The part of `zone` that `blocker` hides from region a or from region b.

    "Only fill ground with nothing in between" means a pixel in the gap is
    filled only if its straight line to EACH of the two regions is clear.
    Beside a region standing in the gap one of those lines runs through it, so
    that ground is in its shadow and is left alone. With nothing in the way the
    strict and fill-all answers are the same; they differ exactly in the strips
    beside an intruder, which fill-all claims and strict does not.

    For each part of the blocker, its shadow toward a region is the hull of the
    part and its projection onto that region -- the strip it sweeps across the
    gap. Exact for a straight gap and an approximation for a curved one, which
    is why it is clipped to `zone`, the bridgeable gap, and never allowed to
    reach out into anybody's own ground.
    """
    hulls = []
    for part in _polys(blocker):
        if len(part.exterior.coords) > 400:      # bound the projection work
            part = part.simplify(1.0)
        ring = list(part.exterior.coords)
        for side in (a, b):
            if side is None or side.is_empty:
                continue
            pts = list(ring)
            for c in ring:
                try:
                    q = nearest_points(Point(c), side)[1]
                    pts.append((q.x, q.y))
                except Exception:
                    pass
            if len(pts) >= 3:
                try:
                    hulls.append(MultiPoint(pts).convex_hull)
                except Exception:
                    pass
    if not hulls:
        return Polygon()
    return _safe_intersection(unary_union(hulls).buffer(0), zone)


def _shadows_between(protected, bodies, gap, reach):
    """Every intruder's shadow across `gap`, for every pair of `bodies`.

    Only protected ground within `reach` of the gap is considered a blocker --
    a region far away cannot stand in it -- and the result is clipped to the
    gap, so this can only ever withhold gap ground, never anyone's own."""
    if protected is None or protected.is_empty or gap is None or gap.is_empty:
        return Polygon()
    try:
        near = _safe_intersection(protected, gap.buffer(max(float(reach), 1.0)))
    except Exception:
        return Polygon()
    if near.is_empty:
        return Polygon()
    # Only a piece that actually STANDS IN the gap casts a shadow. A neighbour
    # merely within reach -- touching the end of a seam, or beside it -- used to
    # shadow it too: strict Merge left up to 74,647 px2 of seam (PAL.1 + sAMY.1)
    # unsealed with nothing standing in it.
    try:
        near = unary_union([q for q in _polys(near) if q.intersection(gap).area > 1.0]
                           or [Polygon()])
    except Exception:
        pass
    if near.is_empty:
        return Polygon()
    out = []
    for i, a in enumerate(bodies):
        for b in bodies[i + 1:]:
            try:
                sh = _intruder_shadow(near, a, b, gap)
            except Exception:
                continue
            if not sh.is_empty:
                out.append(sh)
    return unary_union(out).buffer(0) if out else Polygon()


def merge_regions(features, id_prop, names, new_name=None, seam_tol=40.0,
                  fill_all=False):
    """Combine the named regions into ONE region -- the shared border DISSOLVES.

    A geometric union alone is not enough for that: in these files adjacent
    regions abut without exactly coinciding, so the union keeps the lobes as
    separate parts of a MultiPolygon and the "merged" region still draws the
    old border down its middle. So after the union, the hairline seams between
    the merged bodies are sealed: close over the gap, keep only the sliver
    corridors that touch at least two of the merged bodies (the coastline and
    genuine distance between detached pieces are never bridged), and take them
    in. Regions that truly do not touch stay a MultiPolygon on purpose --
    merging TH with a detached islet must not invent tissue between them.

    Keeps the first region's properties/colour, renamed to `new_name` or a
    common base name. Returns {features, name, parts, sealed}.
    """
    idx = _index_all(features, id_prop)
    sel = [str(n) for n in names if str(n) in idx]
    if len(sel) < 2:
        raise ValueError("pick at least two regions to merge")
    _refuse_container_picks(features, id_prop, sel, "merged into another region")
    # Damage and tissue never merge: [separation.1, TH] turned all of TH into the
    # damage shape, and [TH, separation.1] silently deleted the damage.
    kinds = {bool(_damage.is_damage(n)) for n in sel}
    if len(kinds) > 1:
        raise ValueError("those picks mix damage shapes with regions -- merge damage "
                         "with damage, or regions with regions")
    bodies = [_body(features, idx[n]) for n in sel]
    merged = unary_union(bodies).buffer(0)

    sealed = 0.0
    parts = _polys(merged)
    # Always look for seams. Only a union in several parts or with holes used to
    # be sealed, so a slit open at ONE end -- neither -- stayed open (13,004 px2
    # on the real lft + TH).
    if parts:
        w = max(float(seam_tol) / 2.0, 1.0)
        try:
            closed = merged.buffer(w, join_style=2).buffer(-w, join_style=2).buffer(0)
            raw = _safe_difference(closed, merged)
        except Exception:
            raw = None
        # Ground belonging to regions that are NOT being merged. Sealing a seam
        # must never swallow a region sitting in it: measured, a 14 px region in
        # a 20 px seam was taken 100% into the merged body while its own feature
        # was left exactly where it was, so two regions ended up claiming the
        # same ground. partition_regions has defended against this for a while;
        # this branch never did.
        #
        # Except a region that BLANKETS the merge. `hemi` is the whole-hemisphere
        # outline and contains every seam there is, so defending it would stop
        # any seam ever sealing -- which is the one thing this branch exists to
        # do. Same rule partition_regions applies.
        protected = Polygon()
        try:
            blanket_at = 0.98 * closed.area if closed.area > 0 else 0.0
            keep_out = []
            for f in features:
                nm = (f.get("properties") or {}).get(id_prop)
                if nm is None or str(nm) in sel:
                    continue
                g = shape(f["geometry"])
                if not g.is_valid:
                    g = g.buffer(0)
                if g.is_empty:
                    continue
                if blanket_at > 0 and _safe_intersection(g, closed).area >= blanket_at:
                    continue
                keep_out.append(g)
            if keep_out:
                protected = _safe_difference(unary_union(keep_out).buffer(0), merged)
        except Exception:
            protected = Polygon()

        # STRICT (the default): leave the strips beside an intruder alone too,
        # not just its own ground. `fill_all` seals every free pixel of the seam.
        if not fill_all and not protected.is_empty and raw is not None \
                and not raw.is_empty:
            sh = _shadows_between(protected, bodies, raw, w)
            if not sh.is_empty:
                protected = unary_union([protected, sh]).buffer(0)

        fill = []
        for c0 in (_polys(raw) if raw is not None and not raw.is_empty else []):
            # Another region's ground comes OUT of the corridor first, and what
            # is left is then judged on its own merits. So the clear stretches of
            # a seam seal and the stretch the intruder occupies does not, which
            # is the same answer partition_regions gives for a gap with a region
            # standing in part of it. What must never happen -- and did, taking a
            # 14 px region in a 20 px seam 100% into the merged body while its own
            # feature stayed put -- is the intruder being sealed over.
            #
            # Judging each piece AFTER the trim matters: taking somebody's ground
            # out of a corridor can leave something that is no longer a hairline,
            # or that no longer reaches two of the merged bodies, and neither is a
            # seam to seal.
            pieces = [c0]
            if not protected.is_empty:
                try:
                    pieces = [p for p in _polys(_safe_difference(c0, protected))
                              if p.area > 0]
                except Exception:
                    pieces = []
            for c in pieces:
                if not c.buffer(-w / 2.0).is_empty:
                    continue                  # a fat pocket, not a hairline seam
                collar = c.buffer(1.0)
                if sum(1 for b in bodies if b.intersects(collar)) >= 2:
                    fill.append(c)
        # A seal stays inside the section outline (hemi): TH + V3 at reach 5000
        # sealed 89,523 px2 outside it.
        _cont = section_containers(features, id_prop) - set(sel)
        if fill and _cont:
            cont = unary_union([_body(features, idx[c]) for c in _cont if c in idx]).buffer(0)
            if not cont.is_empty:
                fill = [p for c in fill for p in _polys(_safe_intersection(c, cont))
                        if p.area > 0]
        if fill:
            # Taken in the way Fix a gap takes a gap in: changed only within a
            # pixel of the seal. A grid snap and clean_geom over the WHOLE
            # outline used to re-shape it everywhere (a dense outline went from
            # 3,193 vertices to 13) and dropped any part under 25 px2.
            seal = unary_union(fill).buffer(0)
            merged = _absorb_at(merged, seal, seal.buffer(1.0), protected)
            # LAST: take anybody else's ground back off -- only ground the seal
            # added can be theirs.
            if not protected.is_empty:
                m2 = _safe_difference(merged, protected)
                if not m2.is_empty and m2.area >= 0.5 * merged.area:
                    merged = m2
            sealed = float(seal.area)

    # EVERY merge, not just the sealed ones: a union of outlines that nearly
    # coincide leaves zero-width needles -- the "line going into the region" that
    # had to be dragged out and deleted by hand, and what makes the file valid
    # here but unloadable in Xenium Explorer. The branch above is skipped
    # whenever the union already came back as one piece, which is when they are
    # commonest, so the pass belongs here.
    merged = despike(merged)

    # The LARGEST pick is the base -- its name (unless the picks share a base
    # name), colour and properties -- whatever order they were clicked in; the
    # first click decided before. Anything the base lacks is carried over from
    # the others, and their notes are kept alongside its own: a merge used to
    # drop every property of every pick but the first.
    order = sorted(sel, key=lambda n: -bodies[sel.index(n)].area)
    name = str(new_name).strip() if new_name else _common_base_name(order)
    keep = idx[order[0]][0]
    first = _rename_props(json.loads(json.dumps(features[keep])), id_prop, name)
    props = dict(first.get("properties") or {})
    for n in order[1:]:
        for k in idx[n]:
            for key, val in ((features[k].get("properties") or {}).items()):
                if key in (id_prop, "classification", "color", "colour", "fill") or val in (None, ""):
                    continue
                cur = props.get(key)
                if cur in (None, ""):
                    props[key] = val
                elif (isinstance(cur, str) and isinstance(val, str) and val != cur
                      and any(t in key.lower() for t in ("note", "comment", "descr"))):
                    props[key] = f"{cur} | {val}"
    first["properties"] = props
    first["geometry"] = mapping(merged)
    drop = {k for n in sel for k in idx[n]}     # every lobe of every merged name
    out = []
    for k, f in enumerate(features):
        if k == keep:
            out.append(first)
        elif k in drop:
            continue
        else:
            out.append(json.loads(json.dumps(f)))
    return {"features": out, "name": name,
            "parts": len(_polys(merged)), "sealed": sealed}


def _side_of_path(path_coords, pt):
    """Which side of the drawn cut a point lies on, judged by the NEAREST segment.

    Using the straight chord between the cut's two ends instead scatters pieces
    onto the wrong sides the moment the cut curves or zigzags, which is how one
    region ended up as several disconnected islands."""
    best_d, best_seg = None, None
    for i in range(len(path_coords) - 1):
        (sx, sy), (ex, ey) = path_coords[i], path_coords[i + 1]
        d = LineString([(sx, sy), (ex, ey)]).distance(pt)
        if best_d is None or d < best_d:
            best_d, best_seg = d, ((sx, sy), (ex, ey))
    (sx, sy), (ex, ey) = best_seg
    return 1 if (ex - sx) * (pt.y - sy) - (ey - sy) * (pt.x - sx) >= 0 else -1


def split_region(features, id_prop, name, points, new_name=None, min_piece=100.0):
    """Cut one region into two along the polyline `points`. The line is extended
    past the region so it fully crosses; pieces are grouped onto the two sides of
    the cut. One piece keeps the original name, the other gets `new_name` (or
    '<name> 2'). Returns {features, names:[a, b], areas, parts}."""
    idx = _index(features, id_prop)
    name = str(name)
    if name not in idx:
        raise ValueError(f"region not found: {name!r}")
    _guard_duplicate_names(features, id_prop, [name], "splitting")
    _refuse_container_picks(features, id_prop, [name], "split")
    G = shape(features[idx[name]]["geometry"])
    if not G.is_valid:
        G = G.buffer(0)
    coords = [(float(x), float(y)) for x, y in points]
    if len(coords) < 2:
        raise ValueError("draw a cut line with at least two points")
    if len(coords) > 2 and not LineString(coords).is_simple:
        raise ValueError("that cut line crosses itself — draw a single stroke across the region")

    # The stroke has to reach the edge to cut, so an end that stops short INSIDE
    # the region is carried on -- but only to where it first leaves the region.
    # Extending by the whole diagonal cut regions nobody drew across: a 300 px
    # stroke ending 8,958 px short of dft still cut it in two, and a stroke ending
    # in one arm of dft cut the other arm as well.
    minx, miny, maxx, maxy = G.bounds
    ext = max(((maxx - minx) ** 2 + (maxy - miny) ** 2) ** 0.5, 1000.0)
    if not LineString(coords).intersects(G):
        # A line drawn through the GAP between a region's separate pieces -- the
        # natural way to say "separate that bit" -- crosses none of them, so
        # there is nothing to cut; it divides the pieces instead. A stray
        # triangle of Caudoputamen could not be split off at all before: a line
        # across the triangle cut the triangle, and a line beside it was refused.
        parts = _polys(G)
        if len(parts) > 1:
            (x0, y0), (x1, y1) = coords[0], coords[1]
            (xa, ya), (xb, yb) = coords[-2], coords[-1]

            def _far(px, py, qx, qy):        # carry the end on well past the region
                dx, dy = qx - px, qy - py
                n = (dx * dx + dy * dy) ** 0.5 or 1.0
                return (qx + dx / n * ext, qy + dy / n * ext)
            path = [_far(x1, y1, x0, y0)] + coords + [_far(xa, ya, xb, yb)]
            left = [p for p in parts if _side_of_path(path, p.representative_point()) >= 0]
            right = [p for p in parts if _side_of_path(path, p.representative_point()) < 0]
            if left and right:
                return _split_result(features, idx, id_prop, name, new_name,
                                     unary_union(left), unary_union(right))
        raise ValueError("that cut line doesn't touch the region — draw it across the "
                         "region, or between its separate pieces")
    edge = G.boundary

    def _ext(end, prev):
        if not G.contains(Point(end)):
            return end                         # already at or past the edge
        dx, dy = end[0] - prev[0], end[1] - prev[1]
        n = (dx * dx + dy * dy) ** 0.5 or 1.0
        ux, uy = dx / n, dy / n
        ray = LineString([end, (end[0] + ux * ext, end[1] + uy * ext)])
        hit = ray.intersection(edge)
        pts = []
        for g in ([hit] if hit.geom_type in ("Point", "LineString") else list(getattr(hit, "geoms", []))):
            pts.extend(list(g.coords) if g.geom_type == "LineString" else [(g.x, g.y)])
        pts = [p for p in pts if (p[0] - end[0]) ** 2 + (p[1] - end[1]) ** 2 > 1e-12]
        if not pts:
            return (end[0] + ux * ext, end[1] + uy * ext)
        fx, fy = min(pts, key=lambda p: (p[0] - end[0]) ** 2 + (p[1] - end[1]) ** 2)
        return (fx + ux * 1.0, fy + uy * 1.0)  # a pixel past the edge, so it cuts

    line = LineString([_ext(coords[0], coords[1])] + coords[1:-1] + [_ext(coords[-1], coords[-2])])
    pieces = _polys(split(G, line))
    if len(pieces) < 2:
        raise ValueError("the line didn't cross the region — draw the cut all the way across it")

    # group pieces by which side of the DRAWN PATH they fall on
    path = list(line.coords)
    left, right = [], []
    for p in pieces:
        (left if _side_of_path(path, p.representative_point()) >= 0 else right).append(p)
    gA = unary_union(left) if left else Polygon()
    gB = unary_union(right) if right else Polygon()
    if gA.is_empty or gB.is_empty:
        raise ValueError("the cut left one side empty — draw it across the region")
    small = min(gA.area, gB.area)
    if small < float(min_piece):
        raise ValueError(f"that cut only shaves a {small:.0f} px² sliver off the edge — "
                         "draw it further across the region")
    return _split_result(features, idx, id_prop, name, new_name, gA, gB)


def _split_result(features, idx, id_prop, name, new_name, gA, gB):
    """The feature list with region `name` replaced by gA (keeps the name) and gB."""
    # The LARGER piece keeps the name, whichever way the stroke was drawn: the
    # drawing direction decided before, and HY's main body came out as "HY 2"
    # (over 40 cuts the smaller piece kept the name half the time).
    if gA.area < gB.area:
        gA, gB = gB, gA

    taken = set(idx)
    nameB = str(new_name).strip() if new_name else None
    if not nameB or nameB in taken:
        nameB = _piece_name(name, taken, idx)

    fA = json.loads(json.dumps(features[idx[name]]))
    fA["geometry"] = mapping(gA)
    fB = _rename_props(json.loads(json.dumps(features[idx[name]])), id_prop, nameB)
    fB["geometry"] = mapping(gB)
    out = []
    for k, f in enumerate(features):
        if k == idx[name]:
            out.append(fA)
            out.append(fB)
        else:
            out.append(json.loads(json.dumps(f)))
    return {"features": out, "names": [name, nameB],
            "areas": [float(gA.area), float(gB.area)],
            "parts": [len(_polys(gA)), len(_polys(gB))]}


def split_candidates(features, id_prop, points, exclude=()):
    """Which region(s) a Split line drawn with nothing picked is meant for, most
    likely first -> {names, reason}. `reason` explains an empty list.

    Picking the region in the list before every cut was a click per split. But
    the line alone is ambiguous: its ends lie in the NEIGHBOURS (regions tile the
    section), and split_region carries an end that stops inside a region on to
    that region's far edge -- so asked region by region, every neighbour holding
    an end would "split" too. So, in order:
      1. regions the line crosses all the way as drawn (both ends outside), and
         regions in separate pieces whose gap it runs through (it misses every
         piece but crosses their convex hull);
      2. only if there are none: any region it touches that split_region can cut
         (a line stopping short of an edge), the longest stretch inside first.
    Each is confirmed by actually running split_region. Several names means the
    caller asks; it never picks among them."""
    coords = [(float(x), float(y)) for x, y in points]
    if len(coords) < 2:
        return {"names": [], "reason": "draw a cut line with at least two points"}
    line = LineString(coords)
    ends = [Point(coords[0]), Point(coords[-1])]
    skip = {str(n) for n in (exclude or ())} | set(section_containers(features, id_prop))
    seen, strict, loose = set(), [], []
    reason = None
    for f in features:
        nm = str((f.get("properties") or {}).get(id_prop, ""))
        if not nm or nm in seen or nm in skip:
            continue
        seen.add(nm)
        try:
            G = shape(f["geometry"])
        except Exception:
            continue
        if G.is_empty:
            continue
        if not G.is_valid:
            G = G.buffer(0)
        crosses = line.intersects(G)
        if crosses:
            tier = 1 if not any(G.contains(e) for e in ends) else 2
        elif len(_polys(G)) > 1 and line.intersects(G.convex_hull):
            tier = 1
        else:
            continue
        try:
            split_region(features, id_prop, nm, coords)
        except ValueError as e:
            if crosses and reason is None:
                reason = str(e)          # e.g. a name that means two polygons
            continue
        (strict if tier == 1 else loose).append((line.intersection(G).length, nm))
    pick = strict or loose
    pick.sort(key=lambda t: -t[0])
    names = [nm for _, nm in pick]
    if not names:
        reason = reason or ("that line doesn't cut any region — draw it across a region, "
                            "or through the gap between a region's separate pieces")
    return {"names": names, "reason": None if names else reason}


def delete_part(features, id_prop, name, point, tol=20.0):
    """Delete ONE separate piece of a region -- the piece under `point` (or the
    nearest one within `tol` px) -- and keep the rest of the region.

    For the stray island a region carries after registration (a triangle of
    Caudoputamen off on its own): Delete removes the whole region, Split could
    not separate a piece that was already apart, and "Clean up stray lines" only
    takes hairline-thin pieces. The ground it covered is left empty; Dissolve
    can hand it to the neighbours if it should be filled.
    Returns {features, removed_area, parts_left}."""
    idx = _index(features, id_prop)
    name = str(name)
    if name not in idx:
        raise ValueError(f"region not found: {name!r}")
    _guard_duplicate_names(features, id_prop, [name], "editing")
    G = shape(features[idx[name]]["geometry"])
    if not G.is_valid:
        G = G.buffer(0)
    parts = _polys(G)
    if len(parts) < 2:
        raise ValueError(f"{name} is a single piece — use Delete to remove the whole region")
    P = Point(float(point[0]), float(point[1]))
    dist = [p.distance(P) for p in parts]
    k = min(range(len(parts)), key=lambda i: dist[i])
    if dist[k] > float(tol):
        raise ValueError(f"no piece of {name} there — right-click on the piece itself")
    keep = [p for i, p in enumerate(parts) if i != k]
    f = json.loads(json.dumps(features[idx[name]]))
    f["geometry"] = mapping(unary_union(keep))
    out = [f if i == idx[name] else json.loads(json.dumps(g)) for i, g in enumerate(features)]
    return {"features": out, "removed_area": float(parts[k].area), "parts_left": len(keep)}


# ---- geometry validation ------------------------------------------------------
# Run before writing a file out. Everything here is reported rather than silently
# "fixed", because a self-intersection usually means an edit went wrong and the
# user wants to know, not to have a quietly different shape land on disk.

# severity: "error" blocks an export unless forced; "warning" is informational.
_VALIDATE_ERRORS = {"invalid", "empty", "not_polygon", "duplicate_name", "no_name"}


def validate_features(features, id_prop, sliver_area=25.0, overlap_area=25.0):
    """Check a feature list for the things that make a region file unusable.

    Returns {problems: [{level, kind, region, detail}], counts, ok}."""
    problems = []
    geoms, names = {}, {}
    seen = {}

    for i, f in enumerate(features):
        raw = (f.get("properties") or {}).get(id_prop)
        nm = None if raw is None else str(raw).strip()
        label = nm or f"#{i}"
        names[i] = label
        if not nm:
            problems.append({"level": "error", "kind": "no_name", "region": label,
                             "detail": f"feature {i} has no {id_prop}"})
        else:
            if nm in seen:
                # Not an error: the editor treats same-named features as one
                # multi-part region (two lobes of "Ventricles" really are one
                # structure), so this only needs flagging, not blocking.
                problems.append({"level": "warning", "kind": "duplicate_name", "region": label,
                                 "detail": f"shares its name with feature {seen[nm]} — "
                                           "edited as one multi-part region"})
            seen.setdefault(nm, i)

        geom = f.get("geometry") or {}
        gtype = geom.get("type")
        if gtype not in ("Polygon", "MultiPolygon"):
            problems.append({"level": "error", "kind": "not_polygon", "region": label,
                             "detail": f"geometry is {gtype or 'missing'}, not a polygon"})
            continue
        try:
            g = shape(geom)
        except Exception as e:
            problems.append({"level": "error", "kind": "invalid", "region": label,
                             "detail": f"unreadable geometry: {e}"})
            continue
        if g.is_empty:
            problems.append({"level": "error", "kind": "empty", "region": label,
                             "detail": "geometry is empty"})
            continue
        if not g.is_valid:
            why = "invalid"
            try:
                from shapely.validation import explain_validity
                why = explain_validity(g)
            except Exception:
                pass
            problems.append({"level": "error", "kind": "invalid", "region": label,
                             "detail": str(why)})
            g = g.buffer(0)
            if g.is_empty:
                continue
        geoms[i] = g

        parts = _polys(g)
        if len(parts) > 1:
            crumbs = [p for p in parts if p.area < sliver_area]
            problems.append({
                "level": "warning", "kind": "multipart", "region": label,
                "detail": f"{len(parts)} disconnected pieces"
                          + (f", {len(crumbs)} under {sliver_area:g} px²" if crumbs else "")})
        hs = _holes(g)
        if hs:
            problems.append({"level": "warning", "kind": "holes", "region": label,
                             "detail": f"{len(hs)} hole(s), {sum(h.area for h in hs):,.0f} px² total"})

    # overlaps between regions -- only worth reporting above the numerical floor
    keys = sorted(geoms)
    if len(keys) > 1:
        tree = STRtree([geoms[k] for k in keys])
        for a_pos, i in enumerate(keys):
            for g in tree.query(geoms[i]):
                b_pos = int(g)
                if b_pos <= a_pos:
                    continue
                j = keys[b_pos]
                try:
                    area = geoms[i].intersection(geoms[j]).area
                except Exception:
                    continue
                if area > overlap_area:
                    problems.append({
                        "level": "warning", "kind": "overlap",
                        "region": f"{names[i]} / {names[j]}",
                        "detail": f"overlap {area:,.0f} px²"})

    counts = {"features": len(features),
              "errors": sum(1 for p in problems if p["level"] == "error"),
              "warnings": sum(1 for p in problems if p["level"] == "warning")}
    return {"problems": problems, "counts": counts, "ok": counts["errors"] == 0}


def repair_features(features, id_prop):
    """Make every geometry valid and non-empty, in place on a copy. Returns
    {features, fixed: [names]} -- only geometry is touched, never names."""
    out = json.loads(json.dumps(features))
    fixed = []
    for i, f in enumerate(out):
        geom = f.get("geometry") or {}
        if geom.get("type") not in ("Polygon", "MultiPolygon"):
            continue
        try:
            g = shape(geom)
        except Exception:
            continue
        if g.is_valid and not g.is_empty:
            continue
        healed = _valid(g)                 # keeps every lobe, unlike buffer(0)
        if healed.is_empty or not healed.is_valid:
            continue
        f["geometry"] = mapping(healed)
        raw = (f.get("properties") or {}).get(id_prop)
        fixed.append(str(raw) if raw is not None else f"#{i}")
    return {"features": out, "fixed": fixed}


# ---- dissolve a leftover gap --------------------------------------------------
# Boolean edits (share borders, drag a border) can strand a thin void between
# regions. Rather than chase every op into being gap-free, let the user point at
# a leftover void and hand it back to whatever surrounds it.


def _feature_geoms(features, id_prop):
    """{feature-index-as-string: geometry} + a key -> display-name map. Keyed by
    index, not name, so duplicate region names can't collide."""
    geoms, names = {}, {}
    for i, f in enumerate(features):
        try:
            g = shape(f["geometry"])
        except Exception:
            continue
        if not g.is_valid:
            g = _valid(g)
        if g.is_empty:
            continue
        key = str(i)
        geoms[key] = g
        nm = (f.get("properties") or {}).get(id_prop)
        names[key] = str(nm) if nm is not None else f"#{i}"
    return geoms, names


def _void_at(void, pt, grab=0.0):
    """The connected component of `void` under `pt`, or the nearest one within
    `grab` -- so a click just off a thin sliver still finds it."""
    parts = _polys(void)
    if not parts:
        return None
    for p in parts:
        if p.covers(pt):
            return p
    if grab > 0:
        near = min(parts, key=lambda p: p.distance(pt))
        if near.distance(pt) <= grab:
            return near
    return None


def _touching_regions(geoms, area, pad):
    """Names/keys of the regions that bound `area`."""
    collar = area.buffer(max(1.0, pad))
    return [k for k, g in geoms.items() if g.intersects(collar)]


def find_gap(features, id_prop, point, tol=40.0, grab=None, max_close=None):
    """Locate the void under `point`. Three kinds, tried in order:

      * ENCLOSED -- a hole in the union of every region (the usual leftover);
      * NOTCH    -- a void open to the outside that a morphological close of
                    radius `tol` bridges (e.g. a border dragged back from the
                    outer edge of the pair);
      * BETWEEN  -- a corridor left when one region is dragged away from its
                    neighbour. It is open at BOTH ends, so it is not a hole, and
                    it can be far wider than 2*tol, so the fixed close misses it.
                    Here the bridging radius grows until the void under the click
                    is captured whole.

    Empty background outside the tissue can never match: ENCLOSED and NOTCH can't
    reach beyond the union, and BETWEEN additionally demands that at least TWO
    regions bound the void -- which is true of a corridor between neighbours and
    false of open space beside a single region.
    Returns (gap, kind, geoms, names)."""
    pt = Point(float(point[0]), float(point[1]))
    geoms, names = _feature_geoms(features, id_prop)
    if not geoms:
        raise ValueError("no regions loaded")
    # The section outline (hemi) covers every void between its regions, so with it
    # counted every click answered "that point is inside hemi" and the tool never
    # worked with hemi switched on -- the default. It is a container, not a
    # neighbour: set it aside, and instead use it as the bound -- a gap is ground
    # INSIDE the section, so a click outside it, or a gap reaching past it, is not
    # one (with hemi off, a click near the edge used to hand 320,000 px^2 of
    # background outside the section to V3).
    wrap = section_containers(features, id_prop)
    container = None
    if wrap:
        container = unary_union([g for k, g in geoms.items() if names[k] in wrap]).buffer(0)
        geoms = {k: g for k, g in geoms.items() if names[k] not in wrap}
        names = {k: n for k, n in names.items() if k in geoms}
        if not geoms:
            raise ValueError("no regions loaded")
        if not container.covers(pt):
            raise ValueError(f"that point is outside {sorted(wrap)[0]} -- click a gap "
                             "inside the section")
    inside = [names[k] for k, g in geoms.items() if g.covers(pt)]
    if inside:
        raise ValueError(f"that point is inside {inside[0]} -- click the empty gap itself")

    def _bounded(gp):
        # Clip to the section and keep the piece under the click. Clipping at the
        # outline's edge can leave a zero-width needle where the two boundaries
        # run together, so despike it (needles only -- area-preserving).
        if gp is None or container is None:
            return gp
        gp = _void_at(_safe_intersection(gp, container), pt, grab)
        if gp is None or gp.is_empty:
            return None
        gp = despike(gp)
        return gp if gp is not None and not gp.is_empty and gp.area > 0 else None

    U = unary_union(list(geoms.values())).buffer(0)
    grab = float(tol if grab is None else grab)

    holes = _holes(U)
    gap = _bounded(_void_at(unary_union(holes), pt, grab) if holes else None)
    if gap is not None:
        # A void with ONE region all around it is that region's own hole (OLF's
        # 146,324 px^2, HY's 60,562 on the real file), not ground left between
        # regions. Deliberate holes are kept -- the same rule Share borders follows
        # -- so it is refused here rather than silently filled into the region.
        owners = _touching_regions(geoms, gap, 1.0)
        if len(owners) == 1:
            raise ValueError(f"that is a hole inside {names[owners[0]]} itself, not a gap "
                             "between regions -- holes in a region are kept. If it should "
                             "not be there, remove it from that region in Edit points.")
        return gap, "enclosed", geoms, names

    if tol and tol > 0:
        try:
            closed = U.buffer(tol, join_style=2).buffer(-tol, join_style=2).buffer(0)
            gap = _bounded(_void_at(_safe_difference(closed, U), pt, grab))
        except Exception:
            gap = None
        if gap is not None and not gap.is_empty:
            return gap, "notch", geoms, names

    # BETWEEN: widen the bridge until the corridor closes over. There is no fixed
    # size limit -- the ceiling is the span of the data itself, so however far a
    # region was dragged, the whole corridor is taken. Work on a local window so
    # the buffering stays cheap; if the void runs to the window edge the window
    # was too small, so grow and try again.
    minx, miny, maxx, maxy = U.bounds
    span = max(((maxx - minx) ** 2 + (maxy - miny) ** 2) ** 0.5, tol * 4)
    cap = float(span if max_close is None else max_close)
    r = float(tol) if tol and tol > 0 else 40.0
    rim = False
    while r <= cap:
        window = pt.buffer(r * 6.0 + 50.0)
        local = _safe_intersection(U, window)
        if not local.is_empty:
            try:
                closed = local.buffer(r, join_style=2).buffer(-r, join_style=2).buffer(0)
                comp = _void_at(_safe_difference(closed, U), pt, grab)
            except Exception:
                comp = None
            if comp is not None and comp.area > 0 and comp.within(window):
                comp = _bounded(comp)
                # The margin between the tissue's coast and the section outline is
                # background, not a gap between regions. A corridor between two
                # regions meets the outline, if at all, across a mouth about as wide
                # as itself; ground that meets it along more than 4x the reach (or a
                # fifth of its perimeter) is that margin. Measured on the real file:
                # one click 686 px from the tissue found a 4,692,033 px^2 pocket
                # meeting hemi's edge along 3,944 px, and handed it to seven regions.
                # (Switching the outline off still lets a wide coastal corridor fill.)
                if comp is not None and container is not None:
                    edge = comp.boundary.intersection(container.boundary.buffer(1.0)).length
                    if edge > min(4.0 * float(tol or 40.0), 0.2 * comp.length):
                        rim = True              # a wider bridge only reaches further in
                        break
                if comp is not None and len(_touching_regions(geoms, comp, tol * 0.05)) >= 2:
                    return comp, "between", geoms, names
        r *= 2.0
    if rim:
        raise ValueError(f"that is the margin between the tissue and {sorted(wrap)[0]}'s "
                         "outline, not a gap between regions")

    # NB: no "near both regions" fallback here. Growing two buffers until they
    # overlap eventually matches ANY point, including empty background far from
    # the tissue -- measured at 3.5 billion px^2 in testing. The closing loop
    # above already covers corridors of any width, so the fallback was pure risk.
    raise ValueError("no gap there -- click inside the empty sliver you want removed")


_MIN_GAP_AREA = 1.0     # px2: a "gap" smaller than this is a hairline, not ground


def _hairline(p):
    """The sandbox lint's hairline test: a part (or hole) that all but vanishes
    under a half-pixel erosion."""
    if p.area < _MIN_GAP_AREA:
        return True
    e = p.buffer(-0.5)
    return e.is_empty or e.area < 0.01 * p.area


def _trim_spikes(geom, zone, others=None, max_deg=2.0, min_len=3.0, passes=50):
    """Remove the sharp vertices (edges leaving under `max_deg` apart, both at
    least `min_len` long -- the sandbox lint's spike) that lie in `zone`.
    A tip of the region is cut off, and the sliver stays empty ground; a crack
    INTO the region is closed only if the sliver that fills it overlaps none of
    `others`. Either way the sliver must lie inside `zone`, so nothing away
    from the gap moves."""
    from shapely.geometry.polygon import orient
    if geom is None or geom.is_empty:
        return geom
    reach = zone.buffer(0.5)
    out, any_change = [], False
    for p in _polys(geom):
        p = orient(p, 1.0)               # exterior CCW, holes CW
        rings = [list(p.exterior.coords)[:-1]] + [list(r.coords)[:-1] for r in p.interiors]
        changed = False
        for ri in range(len(rings)):
            pts = rings[ri]
            for _ in range(passes):
                n, drop = len(pts), None
                if n < 4:
                    break
                for i in range(n):
                    p0, p1, p2 = pts[i - 1], pts[i], pts[(i + 1) % n]
                    ax, ay = p0[0] - p1[0], p0[1] - p1[1]
                    bx, by = p2[0] - p1[0], p2[1] - p1[1]
                    la, lb = math.hypot(ax, ay), math.hypot(bx, by)
                    if la < 0.5 or lb < 0.5:
                        continue
                    c = max(-1.0, min(1.0, (ax * bx + ay * by) / (la * lb)))
                    ang = math.degrees(math.acos(c))
                    # the lint's spike: sharp AND long-legged; a near-zero-width
                    # doubling back counts at any length from half a pixel (a
                    # 2.4 px needle on lft escaped the 3 px rule)
                    if not ((la >= min_len and lb >= min_len and ang < max_deg)
                            or ang < 0.5):
                        continue
                    # with this orientation a positive turn is a tip of the region
                    # itself (dropping the vertex takes material away); a negative
                    # one is a crack into it (dropping it adds the sliver)
                    turn = (p1[0] - p0[0]) * (p2[1] - p1[1]) - (p1[1] - p0[1]) * (p2[0] - p1[0])
                    if abs(turn) < 1e-6:
                        # the ring doubles straight back (a zero-area zig-zag the
                        # union leaves at the seam): dropping it changes no ground
                        if reach.covers(Point(p1)):
                            drop = i
                            break
                        continue
                    tri = Polygon([p0, p1, p2])
                    if not tri.is_valid or not tri.within(reach):
                        continue
                    if turn < 0 and others is not None and not others.is_empty \
                            and tri.intersection(others).area > 1e-6:
                        continue
                    drop = i
                    break
                if drop is None:
                    break
                pts = pts[:drop] + pts[drop + 1:]
                changed = True
            rings[ri] = pts
        if changed and len(rings[0]) >= 3:
            q = Polygon(rings[0] + [rings[0][0]],
                        [r + [r[0]] for r in rings[1:] if len(r) >= 3])
            out.append(q if q.is_valid else q.buffer(0))
            any_change = True
        else:
            out.append(p)
    if not any_change:
        return geom
    return out[0] if len(out) == 1 else unary_union(out)


def _zone_flaws(geom, zone):
    """Spikes + needles on the rings of `geom`, counting only rings that reach
    `zone` (cheap: a region's far-away outline is never walked)."""
    import shapely as _sh
    n = 0
    for p in _polys(geom):
        for r in [p.exterior] + list(p.interiors):
            if not r.intersects(zone):
                continue
            cs = list(r.coords)[:-1]
            m = len(cs)
            if m < 3:
                continue
            inz = _sh.dwithin(_sh.points(cs), zone, 0.0)
            for i in (i for i in range(m) if inz[i]):
                # spike at vertex i (the lint's test)
                p0, p1, p2 = cs[i - 1], cs[i], cs[(i + 1) % m]
                ax, ay = p0[0] - p1[0], p0[1] - p1[1]
                bx, by = p2[0] - p1[0], p2[1] - p1[1]
                la, lb = math.hypot(ax, ay), math.hypot(bx, by)
                if la >= 3.0 and lb >= 3.0:
                    c = max(-1.0, min(1.0, (ax * bx + ay * by) / (la * lb)))
                    if math.degrees(math.acos(c)) < 2.0:
                        n += 1
                # needle: edge i running back along one of the next few edges
                ea = LineString([cs[i], cs[(i + 1) % m]])
                if ea.length < 2.0:
                    continue
                for j in range(i + 2, i + 8):
                    q0, q1 = cs[j % m], cs[(j + 1) % m]
                    if {tuple(cs[i]), tuple(cs[(i + 1) % m])} & {tuple(q0), tuple(q1)}:
                        continue
                    eb = LineString([q0, q1])
                    if eb.length < 2.0 or ea.distance(eb) >= 0.01:
                        continue
                    short, other = (ea, eb) if ea.length <= eb.length else (eb, ea)
                    if short.interpolate(0.5, normalized=True).distance(other) < 0.01:
                        n += 1
    return n


def _absorb_at(region, claim, zone, others=None):
    """`region` grown by `claim`, touched only inside `zone` (the gap plus a
    pixel): the union, clipped off `others` (the regions around the gap, so the
    seam cannot overlap them), then the needles, spikes, hairline cracks and
    crumbs the seam leaves -- but only those within `zone`, so the rest of the
    outline keeps every vertex exactly where it was."""
    near = (_safe_intersection(others, zone.buffer(1.0))
            if others is not None and not others.is_empty else Polygon())
    if not near.is_empty:
        claim = _safe_difference(claim, near)
    # A gap and the region it borders can meet only at points to within float
    # noise; the union then keeps the claim as a separate hairline piece and the
    # crumb pass drops it (a 159 px2 sliver along lft came back 0% filled); and
    # a claim corner 0.2 px off the region's edge left a 5 px zig-zag spike on
    # dft. Snap the CLAIM's vertices onto the region's outline within half a
    # pixel so they share edges; the region itself does not move.
    try:
        from shapely import snap as _snap
        claim = _snap(claim, _safe_intersection(region, zone.buffer(1.0)).boundary, 0.5)
        claim = claim if claim.is_valid else claim.buffer(0)
    except Exception:
        pass
    g = unary_union([region, claim]).buffer(0)
    if g.is_empty:
        return g
    # Tidy, then enforce the invariant -- the region gains only ground nobody
    # else holds -- and tidy again what that clip leaves, until it clips nothing.
    for _ in range(4):
        # despike drops a whole needle if its path crosses the zone -- and a long
        # one running in from the gap took dft 2 px2 over CP, 140 px from it.
        # Keep what it did inside the zone only.
        gd = despike(g, within=zone)
        if gd.symmetric_difference(g).difference(zone).area > 1e-9:
            gd = unary_union([_safe_intersection(gd, zone),
                              _safe_difference(g, zone)]).buffer(0)
        g = _trim_spikes(gd, zone, near)
        if near.is_empty:
            break
        taken = _safe_intersection(_safe_difference(g, region), near)
        if taken.area <= 1e-9:
            break
        g = _safe_difference(g, taken)
    else:
        # out of passes with a clip last: its cut can leave a zero-area zig-zag
        # (lft at reach 500). Trimming only removes tips and zig-zags and closes
        # cracks nobody else holds, so it cannot bring the overlap back.
        g = _trim_spikes(g, zone, near)
    keep = []
    for p in _polys(g):
        # a crumb of the seam, not ground -- one that reaches the gap counts,
        # not only one wholly inside it (TH kept a 4-point sliver just past it)
        if p.intersects(zone) and _hairline(p) and _safe_difference(p, region).area >= 0.5 * p.area:
            continue
        holes = []
        for r in p.interiors:
            h = Polygon(r)
            # a hairline crack along the seam is closed -- one that reaches the
            # gap, not only one wholly inside it (dft kept a 3-point sliver hole
            # whose far corner sat 2 px past the gap: a spike) -- provided it is
            # (nearly all) nobody else's ground
            if h.intersects(zone) and _hairline(h) and (
                    h.area < 0.5                # a numeric crumb (TH: 0.006 px2), whoever's
                    or near.is_empty or h.intersection(near).area < 0.5 * h.area):
                continue
            holes.append(list(r.coords))
        q = Polygon(list(p.exterior.coords), holes) if len(holes) != len(p.interiors) else p
        keep.append(q if q.is_valid else q.buffer(0))
    if not keep:
        return Polygon()
    return keep[0] if len(keep) == 1 else unary_union(keep)


def dissolve_gap(features, id_prop, point, tol=40.0, grab=None):
    """Delete the gap under `point`: it is handed to the region(s) surrounding it,
    so the void closes with no new overlap. A single neighbour takes the whole
    gap; several split it by nearest-region, which lands the new border on the
    midline. Returns {features, gap, area, kind, regions}."""
    gap, kind, geoms, names = find_gap(features, id_prop, point, tol=tol, grab=grab)
    if gap.area < _MIN_GAP_AREA:
        raise ValueError("that gap is smaller than a pixel -- nothing to fill. "
                         "Clean up stray lines removes hairlines like this.")

    collar = gap.buffer(max(1.0, float(tol) * 0.05))
    # never a damage shape: it marks damage, it is not ground to grow (one in a
    # gap between A and B was handed 41.7 px2 of it) -- Clean already skips them
    touching = {k: g for k, g in geoms.items()
                if g.intersects(collar) and not _damage.is_damage(names[k])}
    if not touching:
        raise ValueError("that gap isn't next to any region")

    thin = gap.buffer(-1.0).is_empty
    if len(touching) == 1:
        claims = {next(iter(touching)): gap}
    elif thin:
        # Nowhere wider than 2 px: split between several regions it became a
        # string of hairline pieces that could not be joined back cleanly (a
        # third of a 159 px2 sliver was left open). Give the whole sliver to the
        # neighbour it runs along furthest -- at this width the split is moot.
        # Of the neighbours, the one left with no new spike or needle at the gap
        # (a sliver's needle-sharp tip handed to VL.1 became its spike; CP
        # carries the same sliver cleanly); then the longest contact.
        rim = gap.boundary
        zone0 = gap.buffer(1.0)

        def _cost(k):
            rest = unary_union([touching[o] for o in touching if o != k] or [Polygon()])
            g = _absorb_at(touching[k], gap, zone0, rest)
            new = (_zone_flaws(g, zone0) - _zone_flaws(touching[k], zone0)
                   if g is not None and not g.is_empty else 99)
            return (max(new, 0), -rim.intersection(touching[k].buffer(0.5)).length)

        owner = min(sorted(touching), key=_cost)
        claims = {owner: gap}
    else:
        # Partition against each neighbour's LOCAL outline only: same midline
        # answer as the full geometry, without sampling megapixel boundaries.
        win = gap.buffer(max(float(tol) * 2.0, 25.0))
        local = {}
        for k, g in touching.items():
            gl = _safe_intersection(g, win)
            local[k] = g if gl.is_empty else gl
        spacing = max(min(float(tol), gap.length / 24.0), 2.0)
        claims = _nearest_partition(gap, local, spacing)
        # A piece of a claim that does not touch its own region would be a
        # detached crumb of it (opt, 2 px from a hairline gap between HY and lft,
        # was dealt every third stretch of it, and those were dropped as crumbs --
        # leaving a third of the gap open). Hand each such piece to the region it
        # does touch.
        regive = {}
        for k in list(claims):
            c = claims[k]
            if c is None or c.is_empty:
                continue
            stay = []
            for p in _polys(_safe_intersection(c, gap)):
                best = k if p.distance(touching[k]) <= 0.01 else \
                    min(touching, key=lambda o: p.distance(touching[o]))
                (stay if best == k else regive.setdefault(best, [])).append(p)
            claims[k] = unary_union(stay) if stay else Polygon()
        for k, ps in regive.items():
            claims[k] = unary_union([claims.get(k) or Polygon()] + ps)

    out = json.loads(json.dumps(features))
    filled = []
    for k, claim in claims.items():
        if claim is None or claim.is_empty:
            continue
        # Clip to the gap: _nearest_partition falls back to returning the inputs
        # unchanged if the Voronoi fails, and that must not silently no-op.
        claim = _safe_intersection(claim, gap)
        if claim.is_empty or claim.area <= 0:
            continue
        # Only the ground at the gap may change. This used to snap the WHOLE
        # region to a 0.01 grid and run clean_geom over it: a sub-pixel shift
        # all round a 10,000 px outline added 35-140 px2 of overlap with its
        # neighbours and lost ~50 px2 of ground far from the gap, on every click.
        zone = gap.buffer(1.0)
        # everything this region must not take: its neighbours as they were AND
        # the other neighbours' shares of this gap (closing a crack in one region
        # covered 0.6 px2 of the next one's share)
        rest = unary_union([geoms[o] for o in touching if o != k]
                           + [c for o, c in claims.items()
                              if o != k and c is not None and not c.is_empty]
                           or [Polygon()])
        g = _absorb_at(geoms[k], claim, zone, rest)
        if g is None or g.is_empty:
            continue
        out[int(k)]["geometry"] = mapping(g)
        filled.append(names[k])
    if not filled:
        raise ValueError("couldn't hand that gap to a neighbouring region")
    return {"features": out, "gap": mapping(gap), "area": float(gap.area),
            "kind": kind, "regions": filled}


def section_containers(features, id_prop):
    """The whole-section outline(s) -- hemi, whatever it is called: regions that
    wrap every other region, with at least TWO others to wrap. With only one
    other, "wraps everything" is just a nested pair (ISO around SSp), which
    Merge and the border tools must still treat as two ordinary regions."""
    _, names = _feature_geoms(features, id_prop)
    if len(set(names.values())) < 3:
        return set()
    return set(blankets(features, id_prop))


def fold_into_container(features, id_prop, names):
    """Merge regions INTO the whole-section outline -- the explicit, opt-in way to
    merge with hemi. The folded regions' labels go; the outline keeps covering
    their ground, as it always did. The outline keeps its own name, colour,
    notes and draw position whatever order the regions were picked in (a plain
    merge let the first pick win, so [TH, hemi] handed TH the whole hemisphere
    and deleted hemi). Where a folded region poked past the outline, the outline
    grows to cover it, so no ground is lost. Returns {features, name, folded}."""
    picked = [str(n) for n in names]
    cont = section_containers(features, id_prop) & set(picked)
    if len(cont) != 1:
        raise ValueError("pick the section outline and the region(s) to fold into it")
    container = next(iter(cont))
    others = [n for n in dict.fromkeys(picked) if n != container]
    if not others:
        raise ValueError("pick at least one region to fold into the outline")
    idx = _index_all(features, id_prop)
    missing = [n for n in others if n not in idx]
    if missing:
        raise ValueError(f"region not found: {missing[0]!r}")
    outer = _body(features, idx[container])
    folded = unary_union([_body(features, idx[n]) for n in others]).buffer(0)
    out = json.loads(json.dumps(features))
    spill = _safe_difference(folded, outer)
    if not spill.is_empty and spill.area > 1e-6:
        # No clean_geom here: a poke-out joined to the outline only THROUGH the
        # folded region becomes a detached piece of the outline, and clean_geom
        # drops small pieces as shards -- TH's 643 px^2 past hemi vanished,
        # ground no region then owned. Keep every piece; only needles go.
        grown = despike(unary_union([outer, folded]).buffer(0))
        _write_body(out, features, idx[container], grown)
    drop = {k for n in others for k in idx[n]}
    out = [f for k, f in enumerate(out) if k not in drop]
    return {"features": out, "name": container, "folded": others}


def _refuse_container_picks(features, id_prop, names, what):
    """The whole-section outline (hemi, whatever it is called) wraps every other
    region: it is a container, never a peer. Picked as one of the regions an
    edit acts on, it did real damage -- merging it into a child deleted hemi and
    handed the child the whole hemisphere; splitting it left half the regions
    outside it. Refuse plainly instead."""
    wraps = section_containers(features, id_prop) & {str(n) for n in names}
    if wraps:
        nm = sorted(wraps)[0]
        raise ValueError(f"{nm} is the outline that wraps every other region -- "
                         f"it can't be {what}")


def blankets(features, id_prop, keep=None, frac=0.95):
    """Names of regions that wrap essentially every OTHER region -- the
    whole-section outline, whatever it is called. Such a region is never a
    peer: treat it as a neighbour and a border operation will carve the moved
    region's shape straight through it. Names in `keep` are never returned --
    the region the user is actually dragging is the subject, not a bystander.

    Cheap pre-filter first: only a region at least 80% the size of everything
    put together can possibly wrap it, so the exact (expensive) union test
    runs on one or two candidates, not the whole file.
    """
    geoms, names = _feature_geoms(features, id_prop)
    if len(geoms) < 2:
        return []
    hold = {str(n) for n in (keep or [])}
    total = unary_union(list(geoms.values()))
    out = []
    for k, g in geoms.items():
        if names[k] in hold or g.area < 0.8 * total.area:
            continue
        others = unary_union([o for kk, o in geoms.items() if kk != k])
        if others.is_empty:
            continue
        if _safe_intersection(g, others).area >= frac * others.area:
            out.append(names[k])
    return sorted(out)


def clean_lines(features, id_prop, points, width=12.0):
    """Circle the stray hairlines an edit left behind, and they are removed.

    `points` is a loop the user traced around the offending lines. Inside it,
    two kinds of artifact are taken:

      * a region PART that is sliver-thin -- the whole connected piece erodes
        to nothing at width/2. Thinness is judged on the full piece, never on
        what the loop happens to clip, so circling across a healthy region's
        edge cannot shave it: its piece is fat, and fat pieces are never taken.
        A piece qualifies if any of it is inside the loop -- the lines are long
        and circling a stretch of one is enough; the erosion test is what keeps
        that from grabbing anything real.
      * a sliver-thin VOID between regions -- found by the same morphological
        close find_gap uses, so hairline corridors open at both ends are seen
        even though they are not holes. Voids are judged against the union of
        the regions that do NOT blanket the traced loop: a wrap-everything
        outline (hemi) covers every void, and holding it out of the union is
        what makes them visible -- without renaming which region is the
        outline. Fake voids that appear because a covering region was held out
        are fat, so the erosion test discards them.

    The freed ground is then handed out exactly like dissolve_gap: a component
    some remaining region already covers needs nothing (the sliver was lying on
    top of it -- removing it IS the fix); otherwise the touching regions split
    it at the midline. Covering regions never claim, so the outline can never
    eat a hairline that two real regions should share.

    Damage shapes are untouched throughout -- drawn on purpose, often thin.

    Returns {features, removed, deleted, filled, covered, freed, area}.
    """
    if not points or len(points) < 3:
        raise ValueError("trace a loop around the lines you want removed")
    lasso = Polygon([(float(p[0]), float(p[1])) for p in points])
    if not lasso.is_valid:
        lasso = lasso.buffer(0)
    if lasso.is_empty or lasso.area <= 0:
        raise ValueError("that loop has no area")
    w = max(float(width), 2.0)

    geoms, names = _feature_geoms(features, id_prop)
    if not geoms:
        raise ValueError("no regions loaded")
    dmg_keys = {k for k, nm in names.items() if _damage.is_damage(nm)}

    # -- sliver parts of regions ---------------------------------------------
    removed_by, removed_report = {}, []
    for k, g in geoms.items():
        if k in dmg_keys:
            continue
        take = [p for p in _polys(g)
                if p.buffer(-w / 2.0).is_empty
                and not _safe_intersection(p, lasso).is_empty]
        if take:
            removed_by[k] = unary_union(take)
            removed_report.append({"region": names[k],
                                   "area": float(sum(p.area for p in take)),
                                   "parts": len(take)})

    # -- needles on a FAT part -------------------------------------------------
    # A line poking INTO a region belongs to that region's own ring, not to a
    # detached piece, so the erosion test above can never see it: the part it
    # belongs to is the whole healthy region. The void branch below cannot be
    # relied on either -- a loop traced around such a line sits almost entirely
    # inside its own region, which makes that region a `blanket`, holds it out of
    # the void union, and leaves nothing to find. Between them that is why
    # circling one of these lines usually did nothing at all: it only worked if
    # the loop happened to be dragged out across the region's edge.
    #
    # Deleting the needle outright is what was being done by hand anyway, and it
    # encloses no area, so this cannot shave a healthy region. The blanket rule
    # is left alone -- it is there to stop hemi hiding every void.
    despiked, despiked_report = {}, []
    for k, g in geoms.items():
        if k in dmg_keys or k in removed_by:
            continue
        if not g.intersects(lasso):
            continue
        d = despike(g, within=lasso)
        if d.is_empty:
            continue
        # ...and a thin spike WITH a little width (a 30 x 1 px one poking out of
        # a healthy region was refused: "nothing sliver-thin inside that loop").
        # Inside the loop only, only long thin pieces an opening of half the
        # width removes -- a corner chip is short and stays.
        try:
            inside = _safe_intersection(d, lasso)
            opened = d.buffer(-w / 2.0, join_style=2).buffer(w / 2.0, join_style=2)
            thin = _safe_difference(inside, opened)
            cut = []
            for q in _polys(thin):
                if q.area <= 0:
                    continue
                mrr = q.minimum_rotated_rectangle
                xs = list(mrr.exterior.coords)
                sides = [((xs[i][0] - xs[i + 1][0]) ** 2 + (xs[i][1] - xs[i + 1][1]) ** 2) ** 0.5
                         for i in range(2)]
                long_side = max(sides) if sides else 0.0
                if long_side >= 2.0 * w and q.area / max(long_side, 1e-9) < w / 2.0:
                    cut.append(q)
            if cut:
                shaved = _safe_difference(d, unary_union(cut))
                if not shaved.is_empty and shaved.area >= 0.9 * d.area:
                    d = unary_union([p_ for p_ in _polys(shaved) if p_.area >= 1.0]
                                    or [Polygon()])
        except Exception:
            pass
        if d.is_empty:
            continue
        gone = _vertex_count(g) - _vertex_count(d)
        if gone <= 0 and abs(g.area - d.area) < 1e-6:
            continue
        despiked[k] = d
        despiked_report.append({"region": names[k], "vertices": int(max(gone, 0))})
    # the voids below are judged on the cleaned outlines
    for k, d in despiked.items():
        geoms[k] = d

    # -- sliver voids ----------------------------------------------------------
    # The union deliberately leaves out damage shapes and anything that blankets
    # the loop (the outline, or the region being worked inside). The blanket set
    # is remembered: those regions sit the whole operation out -- they hide
    # voids, they must not claim ground, and their covering a hairline says
    # nothing (hemi covers every hairline there is).
    blanket = 0.98 * lasso.area
    blanket_keys = {k for k, g in geoms.items()
                    if k not in dmg_keys
                    and _safe_intersection(g, lasso).area >= blanket}
    u_keys = [k for k in geoms
              if k not in dmg_keys and k not in blanket_keys]
    voids = []
    if u_keys:
        U = unary_union([geoms[k] for k in u_keys]).buffer(0)
        try:
            closed = U.buffer(w, join_style=2).buffer(-w, join_style=2).buffer(0)
            raw = _safe_difference(closed, U)
        except Exception:
            raw = None
        if raw is not None and not raw.is_empty:
            voids = [c for c in _polys(raw)
                     if c.buffer(-w / 2.0).is_empty
                     and not _safe_intersection(c, lasso).is_empty]

    pieces = list(voids)
    for g in removed_by.values():
        pieces.extend(_polys(g))
    if not pieces and not despiked:
        raise ValueError("nothing sliver-thin inside that loop -- it only takes "
                         "lines a few pixels wide, never healthy regions")
    freed = unary_union(pieces).buffer(0) if pieces else Polygon()

    # -- take the slivers out of their donors ---------------------------------
    out = json.loads(json.dumps(features))
    for k, d in despiked.items():        # needles, which free no ground
        out[int(k)]["geometry"] = mapping(d)
    deleted, drop = [], set()
    for k, cut in removed_by.items():
        g = clean_geom(_safe_difference(geoms[k], cut), smooth=False)
        if g.is_empty or g.area <= 0:
            # the whole region was hairline -- exactly the leftover this tool
            # exists for, and the user circled it. Gone, and said out loud.
            deleted.append(names[k])
            drop.add(int(k))
            del geoms[k]
            continue
        geoms[k] = g
        out[int(k)]["geometry"] = mapping(g)

    # -- hand the freed ground to the survivors -------------------------------
    filled_area, filled_names, covered = {}, [], set()
    for comp in _polys(freed):
        collar = comp.buffer(max(1.0, w * 0.1))
        touch = {k: g for k, g in geoms.items()
                 if k not in dmg_keys and k not in blanket_keys
                 and g.intersects(collar)}
        # A PEER that already covers the component means the sliver was lying
        # on its ground -- removing it was the whole fix, nothing to fill.
        # Only peers count: the blanket set covers everything by definition.
        cover = [k for k, g in touch.items() if g.covers(comp.buffer(-0.25))]
        if cover:
            covered.add(names[min(cover, key=lambda k: geoms[k].area)])
            continue
        if not touch:
            continue
        if len(touch) == 1:
            claims = {next(iter(touch)): comp}
        else:
            win = comp.buffer(max(w * 2.0, 25.0))
            local = {}
            for k, g in touch.items():
                gl = _safe_intersection(g, win)
                local[k] = g if gl.is_empty else gl
            spacing = max(min(w, comp.length / 24.0), 2.0)
            claims = _nearest_partition(comp, local, spacing)
        for k, claim in claims.items():
            if claim is None or claim.is_empty:
                continue
            claim = _safe_intersection(claim, comp)
            if claim.is_empty or claim.area <= 0:
                continue
            g = unary_union([geoms[k], claim]).buffer(0)
            g = _snap_polys(g, 0.01)
            g = clean_geom(g, smooth=False)
            if g.is_empty:
                continue
            geoms[k] = g
            out[int(k)]["geometry"] = mapping(g)
            filled_area[k] = filled_area.get(k, 0.0) + float(claim.area)

    filled_names = sorted({names[k] for k in filled_area})
    out = [f for i, f in enumerate(out) if i not in drop]
    return {"features": out,
            "removed": sorted(removed_report, key=lambda r: -r["area"]),
            "despiked": sorted(despiked_report, key=lambda r: -r["vertices"]),
            "deleted": sorted(deleted),
            "filled": filled_names,
            "covered": sorted(covered),
            "freed": mapping(freed), "area": float(freed.area)}


# ---- resampling outlines ------------------------------------------------------


def _vertex_count(geom):
    return sum(len(p.exterior.coords) + sum(len(r.coords) for r in p.interiors)
               for p in _polys(geom))


def _resample_line(line, spacing):
    """The same line with points evenly spaced `spacing` apart. Both endpoints are
    kept exactly, so a noded network stays connected at its junctions."""
    if line.is_empty or line.length <= 0 or spacing <= 0:
        return line
    n = max(1, int(round(line.length / float(spacing))))
    if n + 1 >= len(line.coords):
        return line                      # already sparser than we'd make it
    pts = [line.interpolate(i / n, normalized=True) for i in range(n + 1)]
    out = [(p.x, p.y) for p in pts]
    if line.is_closed:                   # a ring must stay closed
        out[-1] = out[0]
    if len(out) < 2:
        return line
    return LineString(out)


def _even_points(arc, spacing):
    """`arc` re-pointed at even spacing, both ends kept exactly."""
    line = LineString(arc)
    if line.length <= 0:
        return [tuple(p) for p in arc]
    n = max(1, int(round(line.length / float(spacing))))
    pts = [line.interpolate(i / n, normalized=True) for i in range(n + 1)]
    out = [(p.x, p.y) for p in pts]
    out[0] = tuple(arc[0])
    out[-1] = tuple(arc[-1])
    return out


def _shape_points(arc, dev):
    """`arc` with only the points its shape needs: Douglas-Peucker, so no stretch
    moves more than `dev` px from where it was, and the points that remain are
    the arc's OWN points -- none is moved. Both ends kept exactly.

    For "Fewest points". Even spacing spends as many points on a straight run as
    on a corner, and at its coarsest (600 px) still left 45-130 points per
    region on the real files while cutting their corners; this puts the points
    where the outline bends and drops the rest."""
    if len(arc) <= 2:
        return [tuple(p) for p in arc]
    out = [tuple(p) for p in LineString(arc).simplify(float(dev), preserve_topology=False).coords]
    out[0] = tuple(arc[0])
    out[-1] = tuple(arc[-1])
    return out


RESAMPLE_METHODS = ("even", "shape")


def _point_arc(arc, tol, method):
    """Re-point a stretch for Resample points: even spacing `tol` ("even"), or the
    fewest points within `tol` px of it ("shape")."""
    return _shape_points(arc, tol) if method == "shape" else _even_points(arc, tol)


def _swap_ring_span(ring, arc_line, new_pts, eps):
    """Replace the stretch of `ring` that runs along `arc_line` with `new_pts`.

    Editing the rings directly is the only way to get even spacing to survive:
    pushing a resampled arc through move_border re-derives the boundary from
    boolean output, which puts the uneven points straight back (measured: an arc
    went from 39 points to 50, still 8 px in one place and 1,228 px in another).
    Both regions get the SAME list, so they stay exactly coincident.
    """
    pts = list(ring[:-1]) if len(ring) > 1 and ring[0] == ring[-1] else list(ring)
    if len(pts) < 3:
        return None
    on = [Point(p).distance(arc_line) <= eps for p in pts]
    if not any(on):
        return None

    n = len(pts)
    best = None                       # longest contiguous run, wrap-around allowed
    i = 0
    while i < n:
        if not on[i]:
            i += 1
            continue
        j, count = i, 0
        while count < n and on[j % n]:
            j += 1
            count += 1
        if best is None or count > best[1]:
            best = (i, count)
        i = i + count if count else i + 1
    if best is None:
        return None
    startk, count = best
    if count >= n:                    # the whole ring lies on the arc: leave it
        return None

    keep = [pts[(startk + count + k) % n] for k in range(n - count)]
    span = list(new_pts)
    # orient the replacement so it joins the kept part at both ends
    if keep:
        d_fwd = Point(keep[-1]).distance(Point(span[0])) + Point(keep[0]).distance(Point(span[-1]))
        d_rev = Point(keep[-1]).distance(Point(span[-1])) + Point(keep[0]).distance(Point(span[0]))
        if d_rev < d_fwd:
            span = span[::-1]
    out = keep + span
    if out[0] != out[-1]:
        out = out + [out[0]]
    return out


# ---- Resample points on a shared border (Edit shared borders) ---------------------
#
# The border used to come from border_between's corridor arc, which runs ~25 px
# past each junction, and was swapped into both rings with a tolerance of
# tol/4 (37 px at the default 150). Nothing checked the result, so the pair
# pushed into third regions in 34 of 41 real pairs (up to 13,580 px2), spiked at
# fine spacing and depended on click order. Now the border is read off the
# rings themselves, every point a third region touches stays put, and each
# re-pointed stretch is kept only if the pair's combined outline is unchanged
# (so no neighbour gains or loses a pixel), the two do not overlap more, and no
# new spike or needle appears; otherwise it is retried finer, then left alone.

_RS_EPS = 0.5          # px: a vertex this close to the other outline is ON it
_RS_TRIES = 5          # finer spacings tried before a stretch is left as it was


def _ring_flaws(cs, max_deg=2.0, min_len=3.0, needle_tol=0.01, window=8):
    """Spikes + needles in one closed ring -- the same tests as the sandbox's
    geom_lint, so the slider is held to what the sweeps check."""
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
        ea = LineString([ring[k], ring[k + 1]])
        if ea.length < 2.0:
            continue
        for m in range(k + 2, min(k + window, n)):
            if {tuple(ring[k]), tuple(ring[k + 1])} & {tuple(ring[m]), tuple(ring[m + 1])}:
                continue
            eb = LineString([ring[m], ring[m + 1]])
            if eb.length < 2.0 or ea.distance(eb) >= needle_tol:
                continue
            short, other = (ea, eb) if ea.length <= eb.length else (eb, ea)
            if short.interpolate(0.5, normalized=True).distance(other) < needle_tol:
                flaws += 1
    return flaws


def _shared_runs(pts, other_edge, fixed, eps):
    """Stretches of the open ring `pts` that lie along `other_edge`, as lists of
    vertex indices (wrapping allowed), split at every vertex near `fixed` (third
    regions' outlines) so junctions never move. A whole ring along the other
    outline comes back as one closed run, flagged True."""
    import shapely as _sh
    n = len(pts)
    arr = _sh.points(pts)
    on = _sh.dwithin(arr, other_edge, eps)
    mids = _sh.points([((pts[i][0] + pts[(i + 1) % n][0]) / 2,
                        (pts[i][1] + pts[(i + 1) % n][1]) / 2) for i in range(n)])
    edge_ok = [bool(on[i] and on[(i + 1) % n]) for i in range(n)]
    mid_on = _sh.dwithin(mids, other_edge, eps)
    edge_ok = [e and bool(m) for e, m in zip(edge_ok, mid_on)]
    pin = (_sh.dwithin(arr, fixed, eps) if fixed is not None and not fixed.is_empty
           else [False] * n)
    if all(edge_ok):
        starts = [i for i in range(n) if pin[i]]
        if not starts:
            return [(list(range(n)) + [0], True)]
        start = starts[0]
        rot = list(range(start, n)) + list(range(start)) + [start]
        runs, cur = [], [rot[0]]
        for v in rot[1:]:
            cur.append(v)
            if pin[v]:
                runs.append((cur, False))
                cur = [v]
        return runs
    # walk from a vertex that ends a non-shared edge, so no run is cut by the wrap
    s = next(i for i in range(n) if not edge_ok[i])
    runs, cur = [], []
    for step in range(1, n + 1):
        e = (s + step) % n                 # edge e runs vertex e -> e+1
        if edge_ok[e]:
            if not cur:
                cur = [e]
            cur.append((e + 1) % n)
            if pin[(e + 1) % n]:
                runs.append((cur, False))
                cur = [(e + 1) % n]
        else:
            if len(cur) >= 2:
                runs.append((cur, False))
            cur = []
    if len(cur) >= 2:
        runs.append((cur, False))
    return [(r, c) for r, c in runs if len(r) >= 3]


def _splice(pts, run, new_pts, closed):
    """Open ring `pts` with the vertices `run` (in ring order) replaced by
    `new_pts`, which runs from pts[run[0]] to pts[run[-1]]. Returns a closed ring."""
    if closed:
        ring = list(new_pts)
        return ring if tuple(ring[0]) == tuple(ring[-1]) else ring + [ring[0]]
    n = len(pts)
    keep, k = [], (run[-1] + 1) % n
    while k != run[0]:
        keep.append(pts[k])
        k = (k + 1) % n
    ring = keep + list(new_pts)
    return ring + [ring[0]]


def _match_run(pts, old_line, start, end, closed, eps):
    """The same stretch on the OTHER region's open ring `pts`: (run indices in
    ring order, reversed?) or None. Every vertex of it must lie on `old_line`
    and it must be about as long, so a near-miss elsewhere is never taken."""
    import shapely as _sh
    n = len(pts)
    arr = _sh.points(pts)
    on = _sh.dwithin(arr, old_line, eps)
    if closed:
        if all(on) and abs(LineString(list(pts) + [pts[0]]).length - old_line.length) \
                <= 0.01 * old_line.length + 4 * eps:
            return list(range(n)) + [0], False
        return None
    ds = _sh.distance(arr, Point(start))
    de = _sh.distance(arr, Point(end))
    i_s, i_e = int(ds.argmin()), int(de.argmin())
    if ds[i_s] > eps or de[i_e] > eps or i_s == i_e:
        return None
    for rev in (False, True):
        a, b = (i_s, i_e) if not rev else (i_e, i_s)
        run, k = [a], a
        while k != b:
            k = (k + 1) % n
            run.append(k)
        if not all(on[v] for v in run):
            continue
        ln = LineString([pts[v] for v in run]).length
        if abs(ln - old_line.length) <= 0.01 * old_line.length + 4 * eps:
            return run, rev
    return None


def _rings_of(geom):
    """[[exterior, hole, ...] per polygon part], each ring an open point list."""
    out = []
    for p in _polys(geom):
        rs = []
        for r in [p.exterior] + list(p.interiors):
            cs = [(float(x), float(y)) for x, y in r.coords]
            rs.append(cs[:-1] if len(cs) > 1 and cs[0] == cs[-1] else cs)
        out.append(rs)
    return out


def _part_polygon(rings):
    try:
        q = Polygon(list(rings[0]) + [rings[0][0]],
                    [list(h) + [h[0]] for h in rings[1:]])
    except Exception:
        return None
    return q if q.is_valid and not q.is_empty else None


def _resample_pair(out, idx, id_prop, a, b, tol, method="even"):
    """Re-point every stretch of border a and b share, in `out` (edited in
    place). Returns (done, kept): [[old points, new points], ...] per stretch
    changed, and a sentence per stretch left as it was."""
    from shapely.geometry import MultiPolygon, box
    done, kept = [], []
    gb_body = _body(out, idx[b])
    if _body(out, idx[a]).distance(gb_body) > _RS_EPS:
        return done, kept
    near = _body(out, idx[a]).buffer(_RS_EPS * 4).intersection(gb_body.buffer(_RS_EPS * 4))
    thirds = [o for o in idx if o not in (a, b)]
    fixed = unary_union([_body(out, idx[o]).boundary for o in thirds
                         if _body(out, idx[o]).intersects(near)] or [Polygon()])

    # a's rings, feature by feature; each stretch found on them is one job
    for ka in idx[a]:
        a_parts = _rings_of(_geom_at(out, ka))
        for pi in range(len(a_parts)):
            for ri in range(len(a_parts[pi])):
                # Re-pointing one stretch rebuilds the ring, so the indices of
                # the next stretch are stale: find the stretches again after
                # every edit and skip the ones already handled -- known by their
                # end points, which never move.
                handled = set()
                while True:
                    b_edge = _body(out, idx[b]).boundary
                    pts_a = a_parts[pi][ri]
                    todo = None
                    for run, closed in _shared_runs(pts_a, b_edge, fixed, _RS_EPS):
                        key = ("loop",) if closed else (
                            tuple(round(c, 6) for c in pts_a[run[0]]),
                            tuple(round(c, 6) for c in pts_a[run[-1]]))
                        if key not in handled:
                            todo = (run, closed, key)
                            break
                    if todo is None:
                        break
                    run, closed, key = todo
                    handled.add(key)
                    old = [pts_a[v] for v in run]
                    old_line = LineString(old)
                    res = _resample_stretch(out, idx, a, b, ka, a_parts, pi, ri,
                                            run, closed, old, old_line, tol, method)
                    if res is None:
                        kept.append(f"{a} | {b}: a {old_line.length:,.0f} px stretch "
                                    "was left as it was -- re-pointing it would have "
                                    "moved a neighbour's ground or spiked it")
                    elif not res.get("noop"):
                        a_parts = res["a_parts"]
                        done.append([len(old), res["n_new"]])
    return done, kept


def _resample_stretch(out, idx, a, b, ka, a_parts, pi, ri, run, closed, old, old_line, tol,
                      method="even"):
    """One stretch: try `tol`, then finer, keeping the first spacing whose result
    passes every check; write both regions into `out`. None if none passed."""
    from shapely.geometry import MultiPolygon, box
    # the other side of this stretch
    b_hit = None
    for kb in idx[b]:
        b_parts = _rings_of(_geom_at(out, kb))
        for pj, rings in enumerate(b_parts):
            for rj, pts_b in enumerate(rings):
                m = _match_run(pts_b, old_line, old[0], old[-1], closed, _RS_EPS)
                if m is not None:
                    b_hit = (kb, b_parts, pj, rj, m)
                    break
            if b_hit:
                break
        if b_hit:
            break
    if b_hit is None:
        return None
    kb, b_parts, pj, rj, (run_b, rev) = b_hit
    pts_a, pts_b = a_parts[pi][ri], b_parts[pj][rj]

    old_pa = _part_polygon(a_parts[pi])
    old_pb = _part_polygon(b_parts[pj])
    if old_pa is None or old_pb is None:
        return None
    flaws_a = _ring_flaws(list(pts_a) + [pts_a[0]])
    flaws_b = _ring_flaws(list(pts_b) + [pts_b[0]])
    spacing = float(tol)
    for _ in range(_RS_TRIES):
        new = _point_arc(old, spacing, method)
        if len(new) == len(old) and all(
                abs(p[0] - q[0]) < 1e-9 and abs(p[1] - q[1]) < 1e-9 for p, q in zip(new, old)):
            return {"noop": True}
        local = box(*old_line.bounds).buffer(spacing + 4 * _RS_EPS)
        # each side keeps its OWN end points: they sit on third regions' outlines
        if closed:
            new_a, new_b = new, (new[::-1] if rev else new)
        else:
            new_a = [old[0]] + new[1:-1] + [old[-1]]
            nb = [pts_b[run_b[0]]] + (new[::-1] if rev else new)[1:-1] + [pts_b[run_b[-1]]]
            new_b = nb
        ring_a = _splice(pts_a, run, new_a, closed)
        ring_b = _splice(pts_b, run_b, new_b, closed)
        pa_rings = list(a_parts[pi]); pa_rings[ri] = ring_a[:-1]
        pb_rings = list(b_parts[pj]); pb_rings[rj] = ring_b[:-1]
        npa, npb = _part_polygon(pa_rings), _part_polygon(pb_rings)
        ok = npa is not None and npb is not None
        if ok:
            u_old = unary_union([old_pa.intersection(local), old_pb.intersection(local)])
            u_new = unary_union([npa.intersection(local), npb.intersection(local)])
            ok = u_old.symmetric_difference(u_new).area <= 1.0
        if ok:
            ok = (npa.intersection(npb).area
                  <= old_pa.intersection(old_pb).area + 0.5)
        if ok:
            ok = (_ring_flaws(ring_a) <= flaws_a and _ring_flaws(ring_b) <= flaws_b)
        if ok:
            a_new_parts = list(a_parts); a_new_parts[pi] = pa_rings
            b_new_parts = list(b_parts); b_new_parts[pj] = pb_rings

            def _geom(parts):
                polys = [_part_polygon(r) for r in parts]
                polys = [p for p in polys if p is not None]
                return polys[0] if len(polys) == 1 else MultiPolygon(polys)

            out[ka]["geometry"] = mapping(_geom(a_new_parts))
            out[kb]["geometry"] = mapping(_geom(b_new_parts))
            return {"a_parts": a_new_parts, "n_new": len(new)}
        spacing /= 2.0
        if spacing < 2.0:
            break
    return None


def resample_regions(features, id_prop, names, tol=20.0, precision=1.0, method="even"):
    """Even out the points along the SHARED BORDER between two regions.

    Both regions' rings are edited directly with the same point list, so the
    border stays exactly coincident and nothing else about either outline moves:
    the pair's combined outline is checked unchanged, so no neighbour gains or
    loses ground, and points where a third region touches the border stay put.
    `tol` is the spacing you want between points -- or, with method="shape",
    how far the border may move for the fewest points (_shape_points).
    Returns {features, counts, arcs, tol, arcPoints, handles, kept}.
    """
    idx = _index_all(features, id_prop)
    sel = [str(n) for n in (names or []) if str(n) in idx]
    if len(sel) < 2:
        raise ValueError("pick two regions whose shared border you want resampled")
    tol = float(tol)
    if tol <= 0:
        raise ValueError("resample spacing must be greater than zero")
    if method not in RESAMPLE_METHODS:
        raise ValueError(f"unknown resample method {method!r}")

    out = json.loads(json.dumps(features))
    before = {n: _body(features, idx[n]) for n in sel}

    def _arcs_of(feats):
        """The shared arc(s) -- their points ARE the drag handles the user sees,
        which is what 'points' means to them, not ring vertices. Returned so the
        client can DRAW the previewed handles before anything is committed."""
        found = []
        for x in range(len(sel)):
            for y in range(x + 1, len(sel)):
                try:
                    # simplify=0: the handles must BE the ring points we just
                    # spaced evenly, not a Douglas-Peucker version of them
                    for a in border_between(feats, id_prop, sel[x], sel[y], 4.0,
                                            simplify=0) or []:
                        found.append([[float(px), float(py)] for px, py in a])
                except Exception:
                    pass
        return found

    arcs_before = _arcs_of(out)
    handles_before = sum(len(a) for a in arcs_before)
    arcs_done, kept = [], []
    containers = section_containers(out, id_prop)
    # Sorted, so the result never depends on which region was clicked first (it
    # used to: 40 of 41 real pairs came out differently in the two orders).
    order = sorted(sel)
    for i in range(len(order)):
        for j in range(i + 1, len(order)):
            a, b = order[i], order[j]
            if a in containers or b in containers:
                continue                 # hemi's outline is not a border to space out
            done, skipped = _resample_pair(out, idx, id_prop, a, b, tol, method)
            arcs_done.extend(done)
            kept.extend(skipped)

    counts = []
    for n in sel:
        g = _body(out, idx[n])
        counts.append({
            "region": n,
            "before": _vertex_count(before[n]),
            "after": _vertex_count(g),
            "areaDrift": round((g.area - before[n].area) / max(before[n].area, 1e-9) * 100, 3),
        })
    arcs_after = _arcs_of(out)
    return {"features": out, "counts": counts, "arcs": arcs_done, "tol": tol,
            "arcPoints": arcs_after, "kept": kept,
            "handles": {"before": handles_before,
                        "after": sum(len(a) for a in arcs_after)}}


# ---- a region's outline, point by point -----------------------------------------
#
# Resample points (Edit points) and deleting a point both work on one region's
# outline while keeping every border it shares joined. They share these helpers:
# each outline point is labelled by what it lies on, and a stretch shared with a
# neighbour is always re-pointed in BOTH regions with the same point list.


def _near_boundaries(features, idx, name, body, eps):
    """{name: outline} for every other region within reach of `body` -- all lobes
    of a name together."""
    near = body.buffer(eps * 4)
    out = {}
    for other, ks in idx.items():
        if other == name:
            continue
        g = _body(features, ks)
        if not g.is_empty and g.intersects(near):
            out[other] = g.boundary
    return out


def _point_labels(pts, boundaries, eps):
    """For each point, the names of the regions whose outline it lies on (within
    `eps`), as a sorted tuple: () free ground, (N,) one neighbour, (N, M, ...) a
    junction where several meet."""
    import shapely as _sh
    arr = _sh.points(pts)
    labels = [[] for _ in pts]
    for nm in sorted(boundaries):
        for i, hit in enumerate(_sh.dwithin(arr, boundaries[nm], eps)):
            if hit:
                labels[i].append(nm)
    return [tuple(lb) for lb in labels]


def _single_polygon(ring, holes):
    """`ring` (with `holes`) as ONE valid Polygon, or None."""
    try:
        cand = Polygon(ring, holes)
        if not cand.is_valid:
            cand = cand.buffer(0)
    except Exception:
        return None
    return cand if (not cand.is_empty and cand.geom_type == "Polygon") else None


def _area_drift_ok(new, old, max_drift):
    return abs(new.area - old.area) / max(old.area, 1e-9) <= max_drift


def _swap_stretch(out, idx, nm, orig_pts, new_pts, swap_eps, max_drift):
    """Re-point one stretch of region `nm`'s outline, in `out`: its run of points
    along `orig_pts` becomes `new_pts`. Returns an undo record (feature index,
    old geometry), or None if it could not be done cleanly -- and then nothing
    changed.

    Cleanly includes that nothing of the old stretch is left behind: an old point
    still on the stretch (away from its two fixed ends) but off the new line
    would be a bump -- a gap or overlap along the very border this keeps closed."""
    import shapely as _sh
    from shapely.geometry import MultiPolygon
    old_line = LineString(orig_pts)
    new_line = LineString(new_pts)
    for k in idx[nm]:
        parts = _polys(_geom_at(out, k))
        for pi, npoly in enumerate(parts):
            ring = _swap_ring_span(list(npoly.exterior.coords), old_line, new_pts, swap_eps)
            if ring is None:
                continue
            cand = _single_polygon(ring, [list(r.coords) for r in npoly.interiors])
            if cand is None or not _area_drift_ok(cand, npoly, max_drift):
                continue
            if _ring_flaws(list(cand.exterior.coords)) > _ring_flaws(list(npoly.exterior.coords)):
                continue                  # no new spike or needle on the neighbour
            arr = _sh.points(list(cand.exterior.coords))
            along = _sh.line_locate_point(old_line, arr)
            inner = (along > swap_eps) & (along < old_line.length - swap_eps)
            on_old = _sh.dwithin(arr, old_line, swap_eps)
            off_new = ~_sh.dwithin(arr, new_line, 0.5)
            if (inner & on_old & off_new).any():
                continue
            newparts = parts[:pi] + [cand] + parts[pi + 1:]
            ng = newparts[0] if len(newparts) == 1 else MultiPolygon(newparts)
            undo = (k, out[k]["geometry"])
            out[k]["geometry"] = mapping(ng)
            return undo
    return None


def _even_count(pts, count):
    """`pts` re-pointed evenly into exactly `count` points, both ends kept. A
    closed run (first point == last) stays closed."""
    line = LineString(pts)
    if count < 2 or line.length <= 0:
        return [tuple(p) for p in pts]
    return _even_points(pts, line.length / (count - 1))


def resample_region(features, id_prop, name, tol=20.0, eps=0.5, max_drift=0.05,
                    method="even"):
    """Even out the points around ONE region's whole outline without breaking
    any of its borders -- Resample points in Edit points.

    Every point of the outline is labelled by what it lies on: nothing (free
    ground), one other region's edge, or several (a junction, where three
    regions meet). The outline is cut wherever that label changes, and then:

      * a stretch shared with one other region is re-pointed at even `tol`
        spacing in BOTH regions, with the same point list, so the border stays
        exactly coincident. That includes the coast a region shares with
        hemi's outline. Skip this and resampling one region would open a
        hairline gap along every neighbour -- exactly what Share borders fixes;
      * a free stretch is re-pointed in this region only, and joins the shared
        stretches at points that do not move;
      * junction points, and the two ends of every stretch, never move -- so
        regions that met at a point still meet there.

    "Shared" means within `eps` (half a pixel): borders joined by Share borders
    coincide exactly, and a looser tolerance reaches around corners and past
    junctions -- tested, it clipped corners and dropped the point where three
    regions meet. A border that only runs NEAR a neighbour is treated as free,
    as it was never actually joined.

    Conservative by design. A shared stretch is only re-pointed if the same
    stretch is found in the neighbour's outline and replacing it there leaves
    no old point behind; otherwise it is left as it was in both. A part whose
    area would change by more than `max_drift`, or that would stop being one
    valid polygon, is left exactly as it was (and reported in `kept`) -- and its
    neighbours are not touched either. Holes are not resampled.

    method="shape" keeps the fewest points within `tol` px instead of spacing
    them evenly (_shape_points); every check above applies the same.

    Returns {features, counts, tol, handles, kept}: the same shape as
    resample_regions, with `handles` counting this region's own points and
    `counts` listing every region that changed.
    """
    from shapely.geometry import MultiPolygon

    idx = _index_all(features, id_prop)
    name = str(name)
    if name not in idx:
        raise ValueError(f"region not found: {name!r}")
    tol = float(tol)
    if tol <= 0:
        raise ValueError("resample spacing must be greater than zero")
    if method not in RESAMPLE_METHODS:
        raise ValueError(f"unknown resample method {method!r}")
    swap_eps = eps * 1.5

    out = json.loads(json.dumps(features))
    body_before = _body(features, idx[name])
    boundaries = _near_boundaries(features, idx, name, body_before, eps)
    nb_names = sorted(boundaries)
    # Other regions' ground near this one. A free stretch is "free" only to
    # within half a pixel, so at coarse spacing its chords swung across a
    # neighbour lying a few px away (TH took 716 px2 that way at 600): each
    # re-pointed free stretch must take none of it. Containers (hemi) wrap
    # everything and do not count.
    from shapely.geometry import box as _box
    containers = section_containers(features, id_prop)
    near_bodies = {o: g for o, g in ((o, _body(features, idx[o])) for o in idx
                                     if o != name and o not in containers)
                   if not g.is_empty and g.intersects(body_before.buffer(tol + 4))}
    _others_cache = {}

    def _others(partner):
        """Every nearby region's ground except `partner`'s -- the one a shared
        stretch legitimately trades ground with."""
        if partner not in _others_cache:
            _others_cache[partner] = unary_union(
                [g for o, g in near_bodies.items() if o != partner] or [Polygon()])
        return _others_cache[partner]

    def _takes_ground(cand, poly, seg_a, seg_b, partner):
        """px2 of another region's ground `cand` has that `poly` had not, near
        the stretch that changed."""
        local = _box(*LineString(list(seg_a) + list(seg_b)).bounds).buffer(2.0)
        oth = _others(partner)
        if not local.intersects(oth):
            return 0.0
        return (cand.intersection(local).difference(poly).intersection(oth).area)

    def _safe_free(pieces, i, ring_of, holes, poly):
        """Piece `i` re-pointed at the slider's spacing, or finer where that would
        take a third region's ground; its original points if nothing works. A
        shared piece may trade ground with its partner only -- the coast shared
        with hemi took RSP's ground on ISO (1,670 px2 at 150) when it was not
        checked."""
        orig = pieces[i]["orig"]
        partner = pieces[i]["with"]
        spacing = tol
        for _ in range(_RS_TRIES):
            new = _point_arc(orig, spacing, method)
            pieces[i]["new"] = new
            cand = _single_polygon(ring_of(i), holes)
            # too coarse to make a polygon at all is the caller's to judge (it is
            # left as it was and reported in `kept`); only taking a third
            # region's ground is retried finer
            if cand is None or _takes_ground(cand, poly, orig, new, partner) <= 0.5:
                return
            spacing /= 2.0
            if spacing < 2.0:
                break
        pieces[i]["new"] = orig

    kept = []
    handles_before = handles_after = 0

    for k in idx[name]:
        parts = _polys(_geom_at(features, k))
        rebuilt = []
        for poly in parts:
            holes = [list(r.coords) for r in poly.interiors]
            coords = [tuple(c) for c in list(poly.exterior.coords)[:-1]]
            n = len(coords)
            handles_before += n
            if n < 4:
                rebuilt.append(poly)
                handles_after += n
                continue
            labels = _point_labels(coords, boundaries, eps)
            starts = [i for i in range(n) if labels[i] and labels[i] != labels[i - 1]]

            if not starts:
                if any(labels):
                    # the whole outline lies on one other region's edge: there
                    # is no free stretch to even out without moving that one
                    rebuilt.append(poly)
                    handles_after += n
                    continue
                # an island: nothing shared, even out the whole ring. Same rule
                # as every other stretch -- even spacing, which ADDS points to a
                # sparse outline as readily as it thins a dense one (the slider
                # runs fine to coarse). _resample_line would only ever thin.
                closed = coords + [coords[0]]
                isl = [{"orig": closed, "new": closed, "with": None}]
                _safe_free(isl, 0, lambda i: [tuple(c) for c in isl[0]["new"]], holes, poly)
                ring = [tuple(c) for c in isl[0]["new"]]
                if ring[0] != ring[-1]:
                    ring.append(ring[0])
                cand = _single_polygon(ring, holes) if len(ring) >= 4 else None
                if (cand is None or not _area_drift_ok(cand, poly, max_drift)
                        or _ring_flaws(ring) > _ring_flaws(closed)):
                    kept.append(name)
                    rebuilt.append(poly)
                    handles_after += n
                else:
                    rebuilt.append(cand)
                    handles_after += len(ring) - 1
                continue

            # rotate so the outline starts where a shared stretch begins; then
            # plain grouping gives every run, with no wrap-around to merge
            order = list(range(starts[0], n)) + list(range(0, starts[0]))
            groups = []
            for i in order:
                if groups and groups[-1]["label"] == labels[i]:
                    groups[-1]["pts"].append(coords[i])
                else:
                    groups.append({"label": labels[i], "pts": [coords[i]]})

            # pieces: consecutive ones share an end point, so the ring is their
            # concatenation with that point dropped once
            pieces = []
            m = len(groups)
            for gi, g in enumerate(groups):
                nxt = groups[(gi + 1) % m]
                if not g["label"]:
                    seg = [groups[gi - 1]["pts"][-1]] + g["pts"] + [nxt["pts"][0]]
                    pieces.append({"orig": seg, "new": _point_arc(seg, tol, method),
                                   "with": None})
                    continue
                seg = g["pts"]
                shared = g["label"][0] if len(g["label"]) == 1 and len(seg) >= 3 else None
                pieces.append({"orig": seg,
                               "new": _point_arc(seg, tol, method) if shared else seg,
                               "with": shared})
                if nxt["label"]:
                    link = [seg[-1], nxt["pts"][0]]
                    pieces.append({"orig": link, "new": link, "with": None})

            def ring_from(use):
                ring = []
                for pc, u in zip(pieces, use):
                    pts = pc["new"] if u else pc["orig"]
                    ring.extend(pts[1:] if ring else pts)
                if ring[0] != ring[-1]:
                    ring.append(ring[0])
                return ring

            # every stretch tested on its own against third regions' ground (a
            # shared one may trade ground with its partner, which gets the same
            # points, and with nobody else)
            for fi, pc in enumerate(pieces):
                # pieces left as they are (links, junction runs) carry new IS orig
                if pc["new"] is not pc["orig"] and len(pc["orig"]) >= 3:
                    _safe_free(pieces, fi,
                               lambda i: ring_from([j == i for j in range(len(pieces))]),
                               holes, poly)

            use = [True] * len(pieces)
            flaws0 = _ring_flaws(coords + [coords[0]])
            cand = _single_polygon(ring_from(use), holes)
            if (cand is None or not _area_drift_ok(cand, poly, max_drift)
                    or _ring_flaws(ring_from(use)) > flaws0):
                kept.append(name)
                rebuilt.append(poly)
                handles_after += n
                continue

            # shared stretches: re-point the neighbour too, or not at all
            undo = []
            for pi, pc in enumerate(pieces):
                if not pc["with"]:
                    continue
                rec = _swap_stretch(out, idx, pc["with"], pc["orig"], pc["new"],
                                    swap_eps, max_drift)
                if rec is None:
                    use[pi] = False
                else:
                    undo.append(rec)
            ring = ring_from(use)
            cand = _single_polygon(ring, holes)
            if (cand is None or not _area_drift_ok(cand, poly, max_drift)
                    or _ring_flaws(ring) > flaws0):
                for kk, geom in reversed(undo):          # leave everyone as they were
                    out[kk]["geometry"] = geom
                kept.append(name)
                rebuilt.append(poly)
                handles_after += n
                continue
            rebuilt.append(cand)
            handles_after += len(ring) - 1

        if rebuilt:
            ng = rebuilt[0] if len(rebuilt) == 1 else MultiPolygon(rebuilt)
            out[k]["geometry"] = mapping(ng)

    counts = []
    after = _body(out, idx[name])
    counts.append({"region": name,
                   "before": _vertex_count(body_before),
                   "after": _vertex_count(after),
                   "areaDrift": round((after.area - body_before.area)
                                      / max(body_before.area, 1e-9) * 100, 3)})
    for nm in nb_names:
        b0 = _body(features, idx[nm])
        b1 = _body(out, idx[nm])
        if _vertex_count(b0) != _vertex_count(b1) or abs(b1.area - b0.area) > 1e-6:
            counts.append({"region": nm, "before": _vertex_count(b0),
                           "after": _vertex_count(b1),
                           "areaDrift": round((b1.area - b0.area)
                                              / max(b0.area, 1e-9) * 100, 3),
                           "shared": True})
    return {"features": out, "counts": counts, "tol": tol,
            "handles": {"before": handles_before, "after": handles_after},
            "kept": sorted(set(kept))}


# Past "exact", the settings Fewest points falls back through.
_FEWEST_LADDER = (1000, 750, 600, 450, 300, 225, 150, 110, 80, 60, 40, 30, 20, 15, 10,
                  7, 5, 3, 2, 1)
FEWEST_SAFE = 150.0       # beyond this the page warns that the shape will deform
FEWEST_DEFORM_DRIFT = 0.25


def resample_region_fewest(features, id_prop, name, tol, max_drift=0.05, reach=5.0):
    """Fewest points for ONE region at no more than `tol` px: resample_region
    (method="shape") at `tol` and every ladder setting down to tol/`reach`,
    keeping the result with the fewest points. Adds "used": the setting that
    gave it. (The next 3 settings were not enough: at 1000 px TH's best was at
    225 and it came back with 75 points instead of 28.)

    One setting alone is not monotone: a coarser one can fail a guard (a
    stretch that would take a third region's ground is retried finer; a part
    whose area drifts too far is left whole), and on the real file CP came
    back with 28 points at 150 px but 74 at 300 and all 141 at 600. Dragging
    the slider towards "fewest" must never ADD points. Each try is ~0.1 s."""
    tol = float(tol)
    steps = [tol] + [float(v) for v in _FEWEST_LADDER if tol / float(reach) <= v < tol]
    best = last = None
    for t in steps:
        r = resample_region(features, id_prop, name, tol=t, max_drift=max_drift, method="shape")
        last = r
        h = r["handles"]
        if h["after"] >= h["before"]:
            continue                          # changed nothing at this setting
        if best is None or h["after"] < best["handles"]["after"]:
            best, best_t = r, t
    if best is None:
        last["used"] = None
        return last
    best["used"] = best_t
    best["tol"] = float(tol)
    return best


def delete_point(features, id_prop, name, point, respace=True, eps=0.5,
                 max_drift=0.05, grab=2.0):
    """Delete one point from a region's outline without opening any border, and
    re-space the rest of that stretch so no long gap is left where it was.

    What the point lies on decides what happens:
      * where several regions meet (a junction): refused -- deleting it would
        pull them apart;
      * on a border shared with ONE other region: deleted from BOTH, and the
        rest of that shared stretch re-pointed evenly in both, one point fewer;
      * the very end of a shared border (where it stops being shared): refused
        -- removing it would move where the two regions part, and open a gap;
      * on free ground: deleted from this region, and that free stretch --
        between the two fixed points bounding it -- re-pointed evenly.

    "Re-pointed evenly" keeps the stretch's end points and spaces the rest
    along the new outline. If that would change the region's area by more than
    `max_drift` compared with just deleting the point, the point is deleted and
    the others are left where they were. A region never drops below a triangle.

    Returns {features, removedFrom, respaced, stretch} or raises ValueError with
    a sentence saying why nothing changed.
    """
    from shapely.geometry import MultiPolygon

    idx = _index_all(features, id_prop)
    name = str(name)
    if name not in idx:
        raise ValueError(f"region not found: {name!r}")
    target = Point(float(point[0]), float(point[1]))
    swap_eps = eps * 1.5

    # The outline AND its holes: a hole point could not be deleted at all -- the
    # click was refused, or one near an outline point deleted that one instead.
    best = None                      # (distance, feature k, part pi, vertex v, ring ri)
    for k in idx[name]:
        for pi, poly in enumerate(_polys(_geom_at(features, k))):
            rings = [poly.exterior] + list(poly.interiors)
            for ri, ring in enumerate(rings):
                for v, c in enumerate(list(ring.coords)[:-1]):
                    d = target.distance(Point(c))
                    if best is None or d < best[0]:
                        best = (d, k, pi, v, ri)
    if best is None or best[0] > grab:
        raise ValueError(f"no point of {name}'s outline is there")
    _, k, pi, v, ri = best

    if ri > 0:
        parts = _polys(_geom_at(features, k))
        poly = parts[pi]
        hole = [tuple(c) for c in list(poly.interiors[ri - 1].coords)[:-1]]
        if len(hole) <= 3:
            raise ValueError(f"that hole in {name} needs at least 3 points, so that one stays")
        body = _body(features, idx[name])
        bnds = _near_boundaries(features, idx, name, body, eps)
        lab = _point_labels([hole[v]], bnds, eps)[0] if bnds else ()
        if lab:
            raise ValueError(f"that hole point is on {' and '.join(lab)}'s border -- "
                             "delete it from that border in Edit shared borders")
        loop = hole[v + 1:] + hole[:v]
        loop = loop + [loop[0]]
        ring_new = _even_count(loop, len(loop)) if respace else loop
        holes = [list(r.coords) for r in poly.interiors]
        holes[ri - 1] = ring_new
        cand = _single_polygon(list(poly.exterior.coords), holes)
        if cand is None:
            raise ValueError(f"deleting that point would break {name}'s hole -- nothing was changed")
        parts = parts[:pi] + [cand] + parts[pi + 1:]
        out = json.loads(json.dumps(features))
        out[k]["geometry"] = mapping(parts[0] if len(parts) == 1 else MultiPolygon(parts))
        return {"features": out, "removedFrom": [name], "respaced": bool(respace),
                "stretch": "hole"}

    parts = _polys(_geom_at(features, k))
    poly = parts[pi]
    holes = [list(r.coords) for r in poly.interiors]
    coords = [tuple(c) for c in list(poly.exterior.coords)[:-1]]
    n = len(coords)
    if n <= 3:
        raise ValueError(f"{name} needs at least 3 points, so that one stays")

    body = _body(features, idx[name])
    boundaries = _near_boundaries(features, idx, name, body, eps)
    labels = _point_labels(coords, boundaries, eps)
    here = labels[v]
    if len(here) >= 2:
        raise ValueError(f"that point is where {name} meets {' and '.join(here)} -- "
                         "deleting it would pull them apart")
    if here and all(lb == here for lb in labels):
        raise ValueError(f"all of {name}'s outline lies on {here[0]}'s edge -- "
                         "delete the point from that border in Edit shared borders")

    out = json.loads(json.dumps(features))

    def ring_with(pts_first, rest):
        ring = list(pts_first) + list(rest)
        if ring[0] != ring[-1]:
            ring.append(ring[0])
        return ring

    if not any(labels):
        # an island: the whole outline is one free stretch, closed
        loop = coords[v + 1:] + coords[:v]
        loop = loop + [loop[0]]
        deleted = loop
        spaced = _even_count(loop, len(loop)) if respace else loop
        stretch = "outline"
        with_nb, orig = None, None
        ring_del, ring_new = deleted, spaced
    else:
        def run_bounds(label):
            lo = v
            while labels[(lo - 1) % n] == label and (lo - 1) % n != v:
                lo = (lo - 1) % n
            hi = v
            while labels[(hi + 1) % n] == label and (hi + 1) % n != v:
                hi = (hi + 1) % n
            return lo, hi

        def cyc(a, b):
            """indices a..b inclusive, walking forward round the ring"""
            out_i, i = [a], a
            while i != b:
                i = (i + 1) % n
                out_i.append(i)
            return out_i

        lo, hi = run_bounds(here)
        if here:
            if v in (lo, hi):
                raise ValueError(f"that point is where {name}'s border with {here[0]} ends "
                                 "-- drag it instead, or delete one further along")
            span = cyc(lo, hi)                   # the shared run, ends fixed
            with_nb, stretch = here[0], f"border with {here[0]}"
        else:
            a, b = (lo - 1) % n, (hi + 1) % n        # the fixed points bounding the free run
            if a == b:
                # only ONE point of the outline touches anything: the free stretch
                # runs from it all the way round and back to it
                span = [a] + cyc((a + 1) % n, (a - 1) % n) + [a]
            else:
                span = cyc(a, b)                     # free run + its two fixed ends
            with_nb, stretch = None, "free edge"
        orig = [coords[i] for i in span]
        kept_pts = [coords[i] for i in span if i != v]
        spaced = _even_count(kept_pts, len(kept_pts)) if respace and len(kept_pts) >= 3 else kept_pts
        rest = [coords[i] for i in range(n) if i not in set(span)]
        # rest runs forward from just after the span's end back round to its start
        after_end = cyc((span[-1] + 1) % n, (span[0] - 1) % n) if rest else []
        rest = [coords[i] for i in after_end]
        ring_del = ring_with(kept_pts, rest)
        ring_new = ring_with(spaced, rest)

    cand_del = _single_polygon(ring_del, holes)
    if cand_del is None or len(ring_del) < 4:
        raise ValueError("deleting that point would make the outline cross itself -- "
                         "drag it instead")
    cand, respaced = cand_del, False
    pts_for_neighbour = kept_pts if with_nb else None
    if respace:
        cand_new = _single_polygon(ring_new, holes)
        if cand_new is not None and _area_drift_ok(cand_new, cand_del, max_drift):
            cand, respaced = cand_new, True
            if with_nb:
                pts_for_neighbour = spaced

    removed_from = [name]
    if with_nb:
        # the neighbour loses the same point, and gets the same new points, or
        # nothing happens at all -- a border half-edited is a border opened
        rec = _swap_stretch(out, idx, with_nb, orig, pts_for_neighbour, swap_eps, 1.0)
        if rec is None and respaced:
            respaced, cand = False, cand_del
            rec = _swap_stretch(out, idx, with_nb, orig, kept_pts, swap_eps, 1.0)
        if rec is None:
            raise ValueError(f"that point could not be deleted from {with_nb} as well, "
                             "so it stays (their border would open) -- try Share borders "
                             "on the two first")
        removed_from.append(with_nb)

    parts[pi] = cand
    ng = parts[0] if len(parts) == 1 else MultiPolygon(parts)
    out[k]["geometry"] = mapping(ng)
    return {"features": out, "removedFrom": removed_from, "respaced": respaced,
            "stretch": stretch}


# ---- creating regions ---------------------------------------------------------


def _unique_name(taken, base):
    """`base`, or 'base 2', 'base 3'... -- whichever is free."""
    base = str(base or "").strip() or "New region"
    if base not in taken:
        return base
    i = 2
    while f"{base} {i}" in taken:
        i += 1
    return f"{base} {i}"


def _new_feature(features, id_prop, name, geom):
    """A minimal feature carrying just the name. `classification` is included only
    if the rest of the file uses it, and only the name -- inheriting another
    region's colour would be wrong."""
    props = {id_prop: name}
    for f in features:
        if isinstance((f.get("properties") or {}).get("classification"), dict):
            props["classification"] = {"name": name}
            break
    return {"type": "Feature", "properties": props, "geometry": mapping(geom)}


def add_region(features, id_prop, points, name=None, carve=True, min_area=25.0,
               overlap=None, containers=None):
    """Add a brand-new region from a drawn outline.

    `overlap` is the annotator's answer to "on top, underneath, or both?" when
    the new outline lands on ground another region already holds:

      "top"    the existing regions cede the overlap (the historic carve=True).
               The file stays a clean partition.
      "bottom" the NEW region gives up the overlap instead -- it is trimmed to
               what was still free. Nothing already on the map changes.
      "both"   nobody is trimmed; the two genuinely overlap, and draw order
               decides what is seen and clicked. This is the cover convention
               the atlas files already use (hemi wraps everything, ISO covers
               SSp), so a region drawn AROUND another is placed underneath it --
               the container covers its contents and is never carved into a
               donut.

    carve= is kept for callers with no choice to offer (damage sits inside its
    host and never carves); `overlap`, when given, decides.
    Returns {features, name, area, ceded, placedUnder}.
    """
    ring = [(float(x), float(y)) for x, y in points]
    if len(ring) < 3:
        raise ValueError("a region needs at least three points")
    if ring[0] != ring[-1]:
        ring.append(ring[0])

    g = Polygon(ring)
    if not g.is_valid:
        why = ""
        try:
            from shapely.validation import explain_validity
            why = str(explain_validity(g))
        except Exception:
            pass
        if "Self-intersection" in why:
            raise ValueError(f"that outline crosses itself ({why}) -- draw a simple loop")
        g = g.buffer(0)
    g = unary_union(_polys(g)).buffer(0) if _polys(g) else g
    if g.is_empty or g.area < min_area:
        raise ValueError("that outline is too small to be a region")

    if overlap == "top":
        carve = True
    elif overlap in ("bottom", "both"):
        carve = False

    # The whole-section outline is a container: a new region inside the section
    # sits inside it like every other region does. Carving it ("on top") punched
    # a hole in hemi on every draw inside the section, and holding it against the
    # outline ("underneath") left nothing of any region drawn inside it.
    # `containers` comes from a caller that holds regions out first: with only
    # hemi and one region left, hemi no longer "wraps two others" here and was
    # carved like a peer -- so the caller decides from the WHOLE file.
    wrap = section_containers(features, id_prop) if containers is None else set(containers)

    # "underneath": the new region cedes the contested ground rather than
    # taking it, so everything already on the map is left untouched.
    if overlap == "bottom":
        held = []
        for f in features:
            if (f.get("geometry") or {}).get("type") not in ("Polygon", "MultiPolygon"):
                continue
            if str((f.get("properties") or {}).get(id_prop)) in wrap:
                continue
            try:
                other = shape(f["geometry"])
            except Exception:
                continue
            if not other.is_valid:
                other = other.buffer(0)
            if not other.is_empty and other.intersects(g):
                held.append(other)
        if held:
            g = _safe_difference(g, unary_union(held).buffer(0))
            g = unary_union(_polys(g)).buffer(0) if _polys(g) else g
            if g.is_empty or g.area < min_area:
                raise ValueError("underneath leaves nothing of that outline -- "
                                 "it is covered by what is already there. Draw it "
                                 "somewhere freer, or choose on top / overlap.")

    idx = _index(features, id_prop)
    nm = _unique_name(set(idx), name or "New region")

    out = json.loads(json.dumps(features))
    ceded = []
    if carve:
        for k, f in enumerate(out):
            if (f.get("geometry") or {}).get("type") not in ("Polygon", "MultiPolygon"):
                continue
            label = str((f.get("properties") or {}).get(id_prop))
            if label in wrap:
                continue                      # never carve the section outline
            try:
                other = shape(f["geometry"])
            except Exception:
                continue
            if not other.is_valid:
                other = other.buffer(0)
            if other.is_empty or not other.intersects(g):
                continue
            if _safe_intersection(other, g).area <= 0:
                continue
            trimmed = _safe_difference(other, g)
            if trimmed.is_empty or trimmed.area < min_area:
                raise ValueError(f"that outline would erase {label} -- draw a smaller region")
            out[k]["geometry"] = mapping(_snap_polys(trimmed))
            ceded.append(label)

    feat = _new_feature(features, id_prop, nm, g)
    # Draw order is feature order. A region drawn AROUND existing ones has to go
    # BEHIND them or it would hide what it contains; anything else goes on top,
    # which is what "I just drew this" should look like.
    placed_under = []
    at = len(out)
    if overlap == "both":
        for k, f in enumerate(features):
            if (f.get("geometry") or {}).get("type") not in ("Polygon", "MultiPolygon"):
                continue
            try:
                other = shape(f["geometry"])
            except Exception:
                continue
            if not other.is_valid:
                other = other.buffer(0)
            if other.is_empty or other.area <= 0 or not other.intersects(g):
                continue
            if _safe_intersection(other, g).area / other.area >= CONTAINED_FRAC:
                placed_under.append(str((f.get("properties") or {}).get(id_prop)))
                at = min(at, k)
    out.insert(at, feat)
    return {"features": out, "name": nm, "area": float(g.area), "ceded": ceded,
            "placedUnder": placed_under}


def overlap_report(features, id_prop, points, min_area=25.0, containers=None):
    """What an about-to-be-drawn outline lands on, so the annotator can be asked
    "on top, underneath, or overlap?" BEFORE any geometry changes.

    Each hit is {name, relationship, area}: `envelops` (the outline swallows
    that region), `inside` (the outline sits within it), or `partial`.
    """
    ring = [(float(x), float(y)) for x, y in points]
    if len(ring) < 3:
        raise ValueError("a region needs at least three points")
    if ring[0] != ring[-1]:
        ring.append(ring[0])
    g = Polygon(ring)
    if not g.is_valid:
        g = g.buffer(0)
    g = unary_union(_polys(g)).buffer(0) if _polys(g) else g
    if g.is_empty or g.area < min_area:
        raise ValueError("that outline is too small to be a region")

    # Bounding boxes first: _safe_intersection below is the expensive part, and
    # most of a file's regions are nowhere near the outline. Envelopes that only
    # touch still go through -- `inter <= min_area` is what actually filters.
    g_env = g.envelope
    # The section outline is never a peer: every outline inside the section is
    # "inside hemi", so listing it made the prompt appear for every draw and
    # always pre-select Overlap, even for a slight overlap with one neighbour.
    # (`containers`: see add_region.)
    wrap = section_containers(features, id_prop) if containers is None else set(containers)
    hits = []
    for f in features:
        if (f.get("geometry") or {}).get("type") not in ("Polygon", "MultiPolygon"):
            continue
        if str((f.get("properties") or {}).get(id_prop)) in wrap:
            continue
        try:
            other = shape(f["geometry"])
        except Exception:
            continue
        if not other.is_valid:
            other = other.buffer(0)
        if other.is_empty or other.area <= 0 or not g_env.intersects(other.envelope):
            continue
        inter = _safe_intersection(other, g).area
        if inter <= min_area:
            continue
        if inter / other.area >= CONTAINED_FRAC:
            rel = "envelops"
        elif inter / g.area >= CONTAINED_FRAC:
            rel = "inside"
        else:
            rel = "partial"
        hits.append({"name": str((f.get("properties") or {}).get(id_prop)),
                     "relationship": rel, "area": float(inter)})
    hits.sort(key=lambda h: -h["area"])
    return {"overlaps": hits, "area": float(g.area)}


def section_outline(features, id_prop, name="hemi", method="bubble", radius=200.0,
                    exclude=None, min_area=25.0):
    """Build the outline that wraps every region -- the hemisection.

    Two ways, because they fail differently:

    * `bubble` (default) is a morphological CLOSING: grow everything by `radius`,
      union it, shrink back. It bridges the hairline gaps between neighbours and
      swallows small interior voids, while still following the real coastline of
      the section -- concavities and all.
    * `hull` is the convex hull. Cheap and predictable, but a coronal section is
      not convex: the hull cuts straight across every notch and midline dip, so
      it claims ground the tissue does not cover.

    The outline is EXCLUDED from its own input (a rebuild must not wrap the last
    version of itself and creep outwards, run after run), as is anything named in
    `exclude` and every damage shape -- damage sits inside the tissue, so it can
    only pull the outline in.

    Returns {geometry, area, method, replaced, sources}.
    """
    skip = {str(n) for n in (exclude or [])} | {str(name)}
    geoms, used = [], []
    for f in features or []:
        nm = str((f.get("properties") or {}).get(id_prop))
        if nm in skip or _damage.is_damage(nm):
            continue
        if (f.get("geometry") or {}).get("type") not in ("Polygon", "MultiPolygon"):
            continue
        try:
            g = shape(f["geometry"])
        except Exception:
            continue
        if not g.is_valid:
            g = g.buffer(0)
        if g.is_empty or g.area < min_area:
            continue
        geoms.append(g)
        used.append(nm)
    if not geoms:
        raise ValueError("no regions to build an outline from")

    body = unary_union(geoms)
    if method == "hull":
        out = body.convex_hull
    else:
        r = float(radius)
        if r <= 0:
            raise ValueError("the bubble radius must be positive")
        out = body.buffer(r, join_style=1).buffer(-r, join_style=1)
    if not out.is_valid:
        out = out.buffer(0)
    # Only the outer coastline of each part: a closing can leave interior holes
    # where a void was too big to swallow, and an outline with holes is not an
    # outline.
    #
    # EVERY part is kept, not just the biggest. A section that is not one
    # connected blob -- part-way through annotation, a detached piece of tissue,
    # or a region switched off mid-file -- closes into several components, and
    # keeping only the largest silently drops whole regions outside the outline
    # while still reporting that it wrapped them. `parts` is returned so the
    # caller can say so.
    parts = _polys(out)
    if not parts:
        raise ValueError("the outline came out empty -- try a larger radius")
    rings = [Polygon(p.exterior) for p in parts]
    out = rings[0] if len(rings) == 1 else unary_union(rings)
    if not out.is_valid:
        out = out.buffer(0)
    return {"geometry": mapping(_snap_polys(out)), "area": float(out.area),
            "method": ("hull" if method == "hull" else "bubble"),
            "parts": len(rings), "sources": sorted(used)}


def fill_gap_with_region(features, id_prop, point, name=None, tol=40.0, grab=None):
    """Turn the empty void under `point` into a NEW region, rather than handing it
    to the neighbours the way dissolve_gap does.

    The gap geometry is used as found -- its boundary already comes from the
    surrounding regions' outlines, so the new region abuts them with no fresh
    hairline gap -- minus the needles, sharp tips and hairline slivers a void
    between outlines tends to have (3 of 4 real gaps gave a new region with one;
    Xenium Explorer refuses a needle). Returns {features, name, area, kind, gap}.
    """
    gap, kind, geoms, names = find_gap(features, id_prop, point, tol=tol, grab=grab)
    if gap.area < _MIN_GAP_AREA:
        raise ValueError("that gap is smaller than a pixel -- nothing to make a region of. "
                         "Clean up stray lines removes hairlines like this.")
    zone = gap.buffer(1.0)
    around = unary_union([g for g in geoms.values() if g.intersects(zone)] or [Polygon()])
    shape_ = _trim_spikes(despike(gap), zone, around)
    shape_ = _safe_difference(shape_, around) if not around.is_empty else shape_
    parts = [p for p in _polys(shape_) if not _hairline(p)]
    if not parts:
        raise ValueError("that gap is a hairline too thin to be a region -- "
                         "Clean up stray lines removes it")
    region = parts[0] if len(parts) == 1 else unary_union(parts)
    # A sliver whose sharp point cannot be cut without cutting it away (the real
    # CP/dft/VL.1 gap is a 3-point triangle 108 px long, 2 px wide) would be a
    # region that IS a spike. It is not refused silently: Dissolve hands it to
    # the neighbours cleanly.
    if any(_ring_flaws(list(p.exterior.coords)) for p in _polys(region)):
        raise ValueError("that gap is a sliver with a needle-sharp point -- too thin to "
                         "be a region of its own. Dissolve hands it to its neighbours instead.")
    nm = _unique_name({str(v) for v in names.values()}, name or "New region")
    out = json.loads(json.dumps(features))
    out.append(_new_feature(features, id_prop, nm, region))
    return {"features": out, "name": nm, "area": float(region.area),
            "kind": kind, "gap": mapping(gap)}

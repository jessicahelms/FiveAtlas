"""Planar topology for region GeoJSON -- the foundation for shared-border editing.

A border shared by two regions lives in the spaghetti GeoJSON as two independent
(and usually non-coincident) copies. Here we snap coordinates to a grid (so near-
coincident borders become exactly coincident) and intersect region boundaries to
recover the ONE shared arc between each adjacent pair. That shared arc is what the
editor will let you drag so both regions move together.

Fresh build (no dependency on snap_borders); uses only shapely/GEOS geometry.
"""
from __future__ import annotations

import json
import os
import re

from shapely import set_precision
from shapely.geometry import shape, mapping, LineString, Polygon, Point, MultiPoint
from shapely.ops import linemerge, polygonize, split, unary_union, voronoi_diagram
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


def _partition_spike(tol):
    """Small, bounded spike width for share-border partition cleanup."""
    try:
        return max(2.0, min(6.0, float(tol) * 0.10))
    except Exception:
        return 3.0


def _clean_for_partition(geom, tol):
    """Clean needle artifacts before/during tiling without erasing thin regions."""
    if geom is None or geom.is_empty:
        return geom
    g = geom if geom.is_valid else geom.buffer(0)
    area = g.area
    cleaned = clean_geom(g, spike=_partition_spike(tol), smooth=True)
    if area > 0 and (cleaned.is_empty or cleaned.area < area * 0.65):
        cleaned = clean_geom(g, spike=2.0, smooth=True)
    return cleaned


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
    w = max(float(tol), 4.0)
    # The band is the ground BETWEEN the two boundaries: every absorbed point
    # lies within `g` of the inner's edge AND within `g` of the outline. That
    # is what "where the borders are neighbours" means -- a hairline gap
    # between region edge and section outline -- and it is what keeps open
    # interior ground out: an unclaimed pocket may run along the outline OR
    # alongside the inner, but only the true between-sliver is close to both.
    # (The unclaimed ground is one huge connected blob, so membership is by
    # this double proximity, never by connected components.)
    g = max(w / 4.0, 4.0)
    zone = _safe_intersection(_safe_intersection(free, outer.boundary.buffer(g)),
                              inner.buffer(g))
    fill = []
    edge = outer.boundary.buffer(1.0)
    for piece in _polys(zone):
        if piece.area <= 1e-6:
            continue
        if piece.distance(inner) > 0.5:
            continue                       # does not touch the inner
        if not piece.intersects(edge):
            continue                       # does not reach the outline
        fill.append(piece)
    if not fill:
        return {"features": json.loads(json.dumps(features)), "borders": [],
                "sealed": 0.0, "stretches": 0,
                "outer": c["outer"], "inner": c["inner"]}

    new_inner = unary_union([inner] + fill).buffer(0)
    new_inner = _snap_polys(new_inner, 0.01)
    new_inner = clean_geom(new_inner, smooth=False)

    out = json.loads(json.dumps(features))
    _write_body(out, features, idx[c["inner"]], new_inner)

    # the stretches now genuinely shared with the outline, for the drag UI
    arcs = []
    try:
        shared = new_inner.boundary.intersection(outer.boundary.buffer(2.0))
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
    protected = other_union.difference(pair_base).buffer(0) if not other_union.is_empty else Polygon()

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

        # the dragged lobe: the part of the inner whose outline the drag began on
        start_pt = Point(*old_arc[0])
        lobes = _polys(small)
        lobe = min(lobes, key=lambda pg: pg.exterior.distance(start_pt))
        rest = _safe_difference(small, lobe)

        ring = list(new_arc)
        if ring[0] != ring[-1]:
            ring.append(ring[0])
        if len(ring) < 4:
            raise ValueError("the dragged outline collapsed -- try a smaller drag")
        new_lobe = Polygon(ring).buffer(0)
        new_lobe = unary_union(_polys(new_lobe))
        if new_lobe.is_empty or new_lobe.area < 1.0:
            raise ValueError(f"that drag would erase {inner_name} -- try a smaller one")
        if not protected.is_empty:
            new_lobe = _safe_difference(new_lobe, protected)   # never take a neighbour
        new_inner = clean_geom(unary_union([rest, new_lobe]).buffer(0), smooth=False)
        if new_inner.is_empty or new_inner.area < 1.0:
            raise ValueError(f"that drag would erase {inner_name} -- try a smaller one")
        new_outer = clean_geom(unary_union([big, new_inner]).buffer(0), smooth=False)

        out = json.loads(json.dumps(features))
        _write_body(out, features, inner_keys, new_inner)
        _write_body(out, features, outer_keys, new_outer)
        return out

    # The old and new arc share endpoints, so old_arc + reversed(new_arc) closes a
    # thin loop = the area the border swept across. buffer(0) heals any self-touch;
    # if the drag crossed itself we keep every resulting lobe.
    sliver = Polygon(new_arc + old_arc[::-1]).buffer(0)
    sliver = unary_union(_polys(sliver))
    if sliver.is_empty or sliver.area < 1e-6:
        return json.loads(json.dumps(features))       # no real movement

    # Which way did it move? If the swept area sits INSIDE A the border was pushed
    # into A (A shrinks); if it sits outside A, A grew. A owns the dragged arc, so A
    # is authoritative and B conforms.
    frac_in_a = sliver.intersection(A).area / sliver.area
    gA = A.difference(sliver) if frac_in_a > 0.5 else A.union(sliver)
    # Is B right up against the swept sliver? True for a shared border even with the
    # few px of slop these spaghetti GeoJSONs carry, but False across a real gap --
    # so a genuinely-separated B never teleports a slab across the gap to "fill".
    b_adjacent = sliver.distance(B) <= 6.0
    if b_adjacent:
        # The pair's outer extent is fixed -- only the internal divider moves. Close
        # the couple into ONE solid (bridging the spaghetti slop between their non-
        # coincident edges), carve out the authoritative A, and B is exactly what's
        # left: area conserved, no gap, no overlap, whichever way the border went.
        if A.distance(B) <= 0.5:
            U = pair_base
        else:
            U = pair_base.buffer(1.5, join_style=2).buffer(-1.5, join_style=2)
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


def _nearest_partition(area, geoms_by_name, spacing):
    """Split `area` among the named regions: every bit goes to its NEAREST region
    (Voronoi of their densified boundaries). Returns {name: claimed Polygon}.
    Hardened against GEOS overlay/precision failures on messy input."""
    if not area.is_valid:
        area = area.buffer(0)
    # Sample each region's boundary INSET toward its interior. Where two regions
    # already touch, their raw boundary points coincide, which makes the Voronoi
    # degenerate (crashes / slivers). Insetting keeps every generator strictly
    # inside its own region, so the midline still lands on the true shared edge.
    inset = spacing * 0.5
    pts, labels = [], []
    for n, g in geoms_by_name.items():
        src = _generator_inset(g, inset)
        if src is None or src.is_empty or not src.is_valid:
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
        return {n: Polygon() for n in geoms_by_name}
    tree = STRtree(pts)
    try:
        vor = voronoi_diagram(MultiPoint(pts), envelope=area)
    except Exception:
        try:                                   # nudge onto a fine grid and retry
            vor = voronoi_diagram(set_precision(MultiPoint(pts), 0.001), envelope=area)
        except Exception:
            return {n: geoms_by_name[n] for n in geoms_by_name}   # give up: keep originals
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


def _simplify_shared(newg, tol, precision, keep_out=None):
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
    net = unary_union([newg[n].boundary for n in names]).simplify(tol)
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
                    if keep_out.contains(rp):
                        continue           # somebody else's ground / a real hole
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
        if precision and not g.is_empty:
            g = set_precision(g, float(precision))
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
    return g if g.is_valid else g.buffer(0)


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


def partition_regions(features, id_prop, names, tol=40.0, precision=1.0, simplify=None):
    """Make the named regions tile cleanly: fill small gaps between them, remove
    small overlaps, and give every pair a single shared border. Each point of the
    shared territory goes to its NEAREST region, so borders land on the midline of
    whatever gap/overlap existed.

    tol bridges gaps up to ~2*tol; regions farther apart than that stay separate.
    Only the named regions change. A name carried by several features is ONE
    multi-part region: every lobe is a Voronoi generator, and the result is shared
    back out to the individual features at the end. Returns {features, borders}.
    """
    idx = _index_all(features, id_prop)
    sel = [str(n) for n in names if str(n) in idx]
    if len(sel) < 2:
        raise ValueError("pick at least two regions to share borders")

    geoms = {}
    for n in sel:
        # Shave import/boolean hairlines before they can become Voronoi
        # generators. This targets the long skinny spikes that show up as stray
        # edit lines after "Share borders".
        geoms[n] = _clean_for_partition(_body(features, idx[n]), tol)

    # Territory to tile: morphological close bridges gaps <= ~2*tol and swallows
    # small overlaps, without expanding the outer boundary.
    U = unary_union(list(geoms.values()))
    T = U.buffer(tol, join_style=2).buffer(-tol, join_style=2).buffer(0)
    if T.is_empty:
        T = U
    other_geoms = []
    for f in features:
        nm = (f.get("properties") or {}).get(id_prop)
        if nm is None or str(nm) in sel:
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
    protected = other_union.difference(U).buffer(0) if not other_union.is_empty else Polygon()

    def _protect(area):
        if protected.is_empty:
            return area
        # Keep original selected territory as-is, but do not let the bridged/fill
        # area claim another unselected region.
        return unary_union([U, area.difference(protected)]).buffer(0)

    T = _protect(T)
    # De-densify the territory outline BEFORE partitioning: a sparser T gives both
    # sparser free (non-shared) edges and a sparser Voronoi midline, done at the
    # source so the result stays a clean disjoint tiling. Adjacent Voronoi cells
    # share EXACT edges, so we do NOT snap per-region afterwards -- that would break
    # the coincidence and leave sub-pixel slivers of overlap.
    if simplify != 0:
        s = max(tol * 0.5, 3.0) if simplify is None else float(simplify)
        Ts = T.simplify(s)
        if not Ts.is_empty and Ts.is_valid and Ts.area >= 0.98 * T.area:
            T = _protect(Ts.buffer(0))

    # Voronoi sampling: coarse (== tol) for large regions so the output stays sparse,
    # but never coarser than ~1/24 of the smallest region's perimeter, so small
    # regions still get enough samples to tile precisely (no slivers of overlap).
    min_perim = min((g.length for g in geoms.values() if not g.is_empty), default=tol)
    spacing = max(min(tol, min_perim / 24.0), 4.0)
    claimed = _nearest_partition(T, geoms, spacing)

    newg = {}
    for n in sel:
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
    _pre_holes = [h for n in sel for h in _holes(geoms[n])]
    _pre_holes = unary_union(_pre_holes) if _pre_holes else Polygon()

    def _close_voids(rounds, holes_only=False):
        """Hand every leftover empty piece to its nearest region.

        holes_only restricts it to ENCLOSED voids. Use that after simplification:
        a thinned outline sits a hair inside T all the way round, and reclaiming
        that ribbon would paste the dense pre-simplify edge straight back."""
        for _ in range(rounds):     # filling one void can expose another
            claimed_all = unary_union([g for g in newg.values() if not g.is_empty]).buffer(0)
            void = [] if holes_only else [
                p for p in _polys(_safe_difference(T, claimed_all)) if p.area > 1.0]
            void += [h for h in _holes(claimed_all) if h.area > 1.0]
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
            parts = [p for p in _polys(orphan) if p.area > 1.0]
            if not parts:
                return
            orphan = unary_union(parts)
            # Partition against each region's LOCAL outline: same midline answer as
            # the full geometry, without re-sampling megapixel boundaries each round.
            win = orphan.buffer(max(float(tol) * 2.0, 25.0))
            local = {}
            for n in sel:
                gl = _safe_intersection(newg[n], win)
                if not gl.is_empty:
                    local[n] = gl
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
    thin = max(tol * 0.5, 3.0) if simplify is None else float(simplify)
    if thin > 0:
        newg = _simplify_shared(newg, thin, precision, keep_out=keep_out)
        _close_voids(1, holes_only=True)   # the thinned network can strand a sliver

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
            if b.is_empty or not a.intersects(b):
                continue
            try:
                ov = _safe_intersection(a, b)
            except Exception:
                ov = Polygon()
            if ov.is_empty or ov.area <= 0:
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

    out = json.loads(json.dumps(features))
    for n in sel:
        _write_body(out, features, idx[n], newg[n])

    return {"features": out, "borders": _shared_arcs(newg, sel, spacing)}


def snap_to_edits(before, after, id_prop, moved, tol=40.0, precision=1.0):
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
    """'Fiber Tracts 1' + 'Fiber Tracts 2' -> 'Fiber Tracts'; else the first name."""
    stripped = [re.sub(r"[\s_\-]*\d+\s*$", "", str(n)).strip() for n in names]
    if stripped and stripped[0] and all(s == stripped[0] for s in stripped):
        return stripped[0]
    return str(names[0]) if names else "merged"


def _rename_props(feature, id_prop, name):
    props = dict(feature.get("properties") or {})
    props[id_prop] = name
    if isinstance(props.get("classification"), dict):
        props["classification"] = {**props["classification"], "name": name}
    feature["properties"] = props
    return feature


def merge_regions(features, id_prop, names, new_name=None, seam_tol=40.0):
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
    bodies = [_body(features, idx[n]) for n in sel]
    merged = unary_union(bodies).buffer(0)

    sealed = 0.0
    parts = _polys(merged)
    if len(parts) > 1 or any(p.interiors for p in parts):
        w = max(float(seam_tol) / 2.0, 1.0)
        try:
            closed = merged.buffer(w, join_style=2).buffer(-w, join_style=2).buffer(0)
            raw = _safe_difference(closed, merged)
        except Exception:
            raw = None
        fill = []
        for c in (_polys(raw) if raw is not None and not raw.is_empty else []):
            if not c.buffer(-w / 2.0).is_empty:
                continue                      # a fat pocket, not a hairline seam
            collar = c.buffer(1.0)
            if sum(1 for b in bodies if b.intersects(collar)) >= 2:
                fill.append(c)
        if fill:
            merged = unary_union([merged] + fill).buffer(0)
            merged = _snap_polys(merged, 0.01)     # fuse the hairline join
            merged = clean_geom(merged, smooth=False)
            sealed = float(sum(c.area for c in fill))

    name = str(new_name).strip() if new_name else _common_base_name(sel)
    keep = idx[sel[0]][0]
    first = _rename_props(json.loads(json.dumps(features[keep])), id_prop, name)
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
    G = shape(features[idx[name]]["geometry"])
    if not G.is_valid:
        G = G.buffer(0)
    coords = [(float(x), float(y)) for x, y in points]
    if len(coords) < 2:
        raise ValueError("draw a cut line with at least two points")
    if len(coords) > 2 and not LineString(coords).is_simple:
        raise ValueError("that cut line crosses itself — draw a single stroke across the region")

    # Extend by the region's own diagonal, not a fixed distance: a stroke drawn
    # through the middle of a 15,000 px region has to reach both edges to cut it.
    minx, miny, maxx, maxy = G.bounds
    ext = max(((maxx - minx) ** 2 + (maxy - miny) ** 2) ** 0.5, 1000.0)

    def _ext(end, prev):
        dx, dy = end[0] - prev[0], end[1] - prev[1]
        n = (dx * dx + dy * dy) ** 0.5 or 1.0
        return (end[0] + dx / n * ext, end[1] + dy / n * ext)

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

    taken = set(idx)
    nameB = str(new_name).strip() if new_name else None
    if not nameB or nameB in taken:
        i = 2
        while f"{name} {i}" in taken:
            i += 1
        nameB = f"{name} {i}"

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
        healed = g.buffer(0)
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
            g = g.buffer(0)
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
    inside = [names[k] for k, g in geoms.items() if g.covers(pt)]
    if inside:
        raise ValueError(f"that point is inside {inside[0]} -- click the empty gap itself")

    U = unary_union(list(geoms.values())).buffer(0)
    grab = float(tol if grab is None else grab)

    holes = _holes(U)
    gap = _void_at(unary_union(holes), pt, grab) if holes else None
    if gap is not None:
        return gap, "enclosed", geoms, names

    if tol and tol > 0:
        try:
            closed = U.buffer(tol, join_style=2).buffer(-tol, join_style=2).buffer(0)
            gap = _void_at(_safe_difference(closed, U), pt, grab)
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
                if len(_touching_regions(geoms, comp, tol * 0.05)) >= 2:
                    return comp, "between", geoms, names
        r *= 2.0

    # NB: no "near both regions" fallback here. Growing two buffers until they
    # overlap eventually matches ANY point, including empty background far from
    # the tissue -- measured at 3.5 billion px^2 in testing. The closing loop
    # above already covers corridors of any width, so the fallback was pure risk.
    raise ValueError("no gap there -- click inside the empty sliver you want removed")


def dissolve_gap(features, id_prop, point, tol=40.0, grab=None):
    """Delete the gap under `point`: it is handed to the region(s) surrounding it,
    so the void closes with no new overlap. A single neighbour takes the whole
    gap; several split it by nearest-region, which lands the new border on the
    midline. Returns {features, gap, area, kind, regions}."""
    gap, kind, geoms, names = find_gap(features, id_prop, point, tol=tol, grab=grab)
    if gap.area <= 0:
        raise ValueError("that gap has no area")

    collar = gap.buffer(max(1.0, float(tol) * 0.05))
    touching = {k: g for k, g in geoms.items() if g.intersects(collar)}
    if not touching:
        raise ValueError("that gap isn't next to any region")

    if len(touching) == 1:
        claims = {next(iter(touching)): gap}
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
        g = unary_union([geoms[k], claim]).buffer(0)
        g = _snap_polys(g, 0.01)          # fuse the hairline seam the union leaves
        g = clean_geom(g, smooth=False)   # keep thin fill; drop sub-pixel crumbs
        if g.is_empty:
            continue
        out[int(k)]["geometry"] = mapping(g)
        filled.append(names[k])
    if not filled:
        raise ValueError("couldn't hand that gap to a neighbouring region")
    return {"features": out, "gap": mapping(gap), "area": float(gap.area),
            "kind": kind, "regions": filled}


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
    if not pieces:
        raise ValueError("nothing sliver-thin inside that loop -- it only takes "
                         "lines a few pixels wide, never healthy regions")
    freed = unary_union(pieces).buffer(0)

    # -- take the slivers out of their donors ---------------------------------
    out = json.loads(json.dumps(features))
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


def resample_regions(features, id_prop, names, tol=20.0, precision=1.0):
    """Even out the points along the SHARED BORDER between two regions.

    Both regions' rings are edited directly with the same point list, so the
    border stays exactly coincident and nothing else about either outline moves.
    `tol` is the spacing you want between points.
    Returns {features, counts, arcs, tol}.
    """
    idx = _index_all(features, id_prop)
    sel = [str(n) for n in (names or []) if str(n) in idx]
    if len(sel) < 2:
        raise ValueError("pick two regions whose shared border you want resampled")
    tol = float(tol)
    if tol <= 0:
        raise ValueError("resample spacing must be greater than zero")

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
    arcs_done = []
    for i in range(len(sel)):
        for j in range(i + 1, len(sel)):
            a, b = sel[i], sel[j]
            try:
                arcs = border_between(out, id_prop, a, b, 4.0) or []
            except Exception:
                continue
            for arc in arcs:
                if len(arc) < 3:
                    continue
                arc = [(float(x), float(y)) for x, y in arc]
                line = LineString(arc)
                new_pts = _even_points(arc, tol)
                eps = max(4.0, tol * 0.25)
                edited = 0
                for name in (a, b):
                    # a name can carry several lobes; the arc lies on exactly one of
                    # them, and _swap_ring_span leaves the others alone
                    for k in idx[name]:
                        parts, rebuilt, touched = _polys(_geom_at(out, k)), [], False
                        for poly in parts:
                            ring = _swap_ring_span(list(poly.exterior.coords), line, new_pts, eps)
                            if ring is None:
                                rebuilt.append(poly)
                                continue
                            try:
                                cand = Polygon(ring, [list(r.coords) for r in poly.interiors])
                                if not cand.is_valid:
                                    cand = cand.buffer(0)
                                if cand.is_empty or cand.area < poly.area * 0.80:
                                    rebuilt.append(poly)
                                else:
                                    rebuilt.append(cand)
                                    touched = True
                                    edited += 1
                            except Exception:
                                rebuilt.append(poly)
                        if touched and rebuilt:
                            ng = unary_union(rebuilt).buffer(0) if len(rebuilt) > 1 else rebuilt[0]
                            if not ng.is_empty:
                                out[k]["geometry"] = mapping(ng)
                if edited:
                    arcs_done.append([len(arc), len(new_pts)])

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
            "arcPoints": arcs_after,
            "handles": {"before": handles_before,
                        "after": sum(len(a) for a in arcs_after)}}


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


def add_region(features, id_prop, points, name=None, carve=True, min_area=25.0):
    """Add a brand-new region from a drawn outline.

    carve=True keeps the file a clean partition: any existing region the new
    outline covers cedes that ground. A region that would be wiped out entirely
    is refused rather than silently deleted.
    Returns {features, name, area, ceded}.
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

    idx = _index(features, id_prop)
    nm = _unique_name(set(idx), name or "New region")

    out = json.loads(json.dumps(features))
    ceded = []
    if carve:
        for k, f in enumerate(out):
            if (f.get("geometry") or {}).get("type") not in ("Polygon", "MultiPolygon"):
                continue
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
            label = str((f.get("properties") or {}).get(id_prop))
            trimmed = _safe_difference(other, g)
            if trimmed.is_empty or trimmed.area < min_area:
                raise ValueError(f"that outline would erase {label} -- draw a smaller region")
            out[k]["geometry"] = mapping(_snap_polys(trimmed))
            ceded.append(label)

    out.append(_new_feature(features, id_prop, nm, g))
    return {"features": out, "name": nm, "area": float(g.area), "ceded": ceded}


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

    The gap geometry is used exactly as found -- its boundary already comes from
    the surrounding regions' outlines, so the new region abuts them with no fresh
    hairline gap. Returns {features, name, area, kind, gap}.
    """
    gap, kind, geoms, names = find_gap(features, id_prop, point, tol=tol, grab=grab)
    if gap.area <= 0:
        raise ValueError("that gap has no area")
    nm = _unique_name({str(v) for v in names.values()}, name or "New region")
    out = json.loads(json.dumps(features))
    out.append(_new_feature(features, id_prop, nm, gap))
    return {"features": out, "name": nm, "area": float(gap.area),
            "kind": kind, "gap": mapping(gap)}

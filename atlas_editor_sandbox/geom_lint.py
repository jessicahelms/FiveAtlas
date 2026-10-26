"""Geometry lint for test and sweep scripts: needles, spikes and hairline parts.

Xenium Explorer refuses a region file that contains a zero-width needle, and a
thin spike reads as an artifact on screen even when it is technically valid, so
every edit tool's output should come back free of all three:

  needles   -- a ring doubles back on itself: two non-adjacent edges of real
               length run within `tol` px of each other (zero-width, no area)
  spikes    -- a vertex whose two edges leave it at a near-zero angle, both of
               real length: a sharp tip with a little area but almost no width
  hairlines -- a polygon part that disappears under a half-pixel erosion

Import from a sandbox script:  from geom_lint import lint, lint_features
"""
import math

from shapely.geometry import LineString, shape


def _polys(g):
    if g is None or g.is_empty:
        return []
    if g.geom_type == "Polygon":
        return [g]
    if g.geom_type in ("MultiPolygon", "GeometryCollection"):
        out = []
        for p in g.geoms:
            out.extend(_polys(p))
        return out
    return []


def _rings(g):
    for p in _polys(g):
        yield list(p.exterior.coords)
        for h in p.interiors:
            yield list(h.coords)


def ring_needles(cs, min_len=2.0, tol=0.01, window=8):
    """Longest pair of real-length, non-adjacent edges in one ring that run
    ALONGSIDE each other within `tol` px (0 if none) -- a ring doubling back.
    Edges sharing a vertex are skipped (zero apart by definition), and so are
    edges that merely nearly touch end to end: two pieces of one straight run
    split by a sub-pixel step come within `tol` at their ends but are no
    needle, so the shorter edge's MIDPOINT must lie within `tol` of the other."""
    worst = 0.0
    n = len(cs) - 1                      # closed ring: last == first
    for k in range(n):
        a = LineString([cs[k], cs[k + 1]])
        if a.length < min_len:
            continue
        for m in range(k + 2, min(k + window, n)):
            if set(cs[k:k + 2]) & set(cs[m:m + 2]):
                continue
            b = LineString([cs[m], cs[m + 1]])
            if b.length < min_len or a.distance(b) >= tol:
                continue
            short, other = (a, b) if a.length <= b.length else (b, a)
            if short.interpolate(0.5, normalized=True).distance(other) < tol:
                worst = max(worst, min(a.length, b.length))
    return worst


def ring_spikes(cs, max_deg=2.0, min_len=3.0):
    """Vertices where the two edges leave at under `max_deg` degrees from each
    other and both are at least `min_len` px long."""
    pts = cs[:-1] if len(cs) > 1 and cs[0] == cs[-1] else cs
    n = len(pts)
    found = []
    if n < 3:
        return found
    for i in range(n):
        p0, p1, p2 = pts[i - 1], pts[i], pts[(i + 1) % n]
        ax, ay = p0[0] - p1[0], p0[1] - p1[1]
        bx, by = p2[0] - p1[0], p2[1] - p1[1]
        la, lb = math.hypot(ax, ay), math.hypot(bx, by)
        if la < min_len or lb < min_len:
            continue
        cosang = max(-1.0, min(1.0, (ax * bx + ay * by) / (la * lb)))
        if math.degrees(math.acos(cosang)) < max_deg:
            found.append((p1, min(la, lb)))
    return found


def hairline_parts(g, w=0.5):
    """Parts that vanish (or nearly) under an erosion of `w` px."""
    out = []
    for p in _polys(g):
        if p.area <= 0:
            out.append(0.0)
            continue
        e = p.buffer(-w)
        if e.is_empty or e.area < 0.01 * p.area:
            out.append(p.area)
    return out


def lint(g):
    """{'needles': n, 'needle_len': longest, 'spikes': n, 'hairlines': n,
    'valid': bool} for one geometry."""
    needles, needle_len, spikes = 0, 0.0, 0
    for cs in _rings(g):
        nl = ring_needles(cs)
        if nl > 0:
            needles += 1
            needle_len = max(needle_len, nl)
        spikes += len(ring_spikes(cs))
    return {"needles": needles, "needle_len": round(needle_len, 2), "spikes": spikes,
            "hairlines": len(hairline_parts(g)), "valid": bool(g.is_valid)}


def lint_features(features, id_prop="name", only=None):
    """lint() per named region (merging same-named parts is not needed: each
    feature is checked on its own). `only`: restrict to these names."""
    res = {}
    for f in features:
        nm = str((f.get("properties") or {}).get(id_prop))
        if only is not None and nm not in only:
            continue
        try:
            g = shape(f["geometry"])
        except Exception:
            continue
        r = lint(g)
        if nm in res:
            for k in ("needles", "spikes", "hairlines"):
                res[nm][k] += r[k]
            res[nm]["needle_len"] = max(res[nm]["needle_len"], r["needle_len"])
            res[nm]["valid"] = res[nm]["valid"] and r["valid"]
        else:
            res[nm] = r
    return res


def is_clean(r):
    return r["valid"] and r["needles"] == 0 and r["spikes"] == 0 and r["hairlines"] == 0

"""Fix a gap (Dissolve / New region) changes the map ONLY at the gap.

Before the fix Dissolve snapped every touched region's whole outline to a 0.01
grid and ran clean_geom over it: on the real file every click added 35-140 px2
of overlap and lost ~50 px2 of ground far from the gap (the reported "73 px2"),
and on gaps carved along all 41 real borders it added overlap in 84 of 114 runs
(up to 1,704 px2), lost ground in 103 (up to 1,526) and left 41 gaps under 99%
filled. New region kept the gap's needles, spikes and hairlines.

Offline: synthetic gaps with exact answers, then the real voids
(FiveAtlas_handoff_fixtures; skipped if the file is not there).
"""
import itertools
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "backend"))
sys.path.insert(0, HERE)

from shapely.geometry import Polygon, box, shape, mapping   # noqa: E402
from shapely.ops import unary_union                         # noqa: E402
import topology as T                                        # noqa: E402
from geom_lint import lint                                  # noqa: E402

fails = []


def check(label, cond, detail=""):
    print(f"  {'ok  ' if cond else 'FAIL'}  {label}{('  ' + str(detail)) if detail != '' else ''}")
    if not cond:
        fails.append(label)


def feat(name, geom):
    return {"type": "Feature", "properties": {"name": name}, "geometry": mapping(geom)}


def gmap(fs):
    out = {}
    for f in fs:
        n = f["properties"]["name"]
        g = shape(f["geometry"])
        g = g if g.is_valid else g.buffer(0)
        out[n] = unary_union([out[n], g]) if n in out else g
    return out


def new_flaws(after, before):
    la = lint(after)
    lb = lint(before) if before is not None else {"needles": 0, "spikes": 0, "hairlines": 0}
    d = {k: la[k] - lb[k] for k in ("needles", "spikes", "hairlines") if la[k] > lb[k]}
    if not la["valid"]:
        d["invalid"] = 1
    return d


def overlap(G, skip=("hemi",)):
    ks = [k for k in G if k not in skip]
    return sum(G[a].intersection(G[b]).area for a, b in itertools.combinations(ks, 2)
               if G[a].intersects(G[b]))


def far_vertices(g, zone):
    polys = [g] if g.geom_type == "Polygon" else list(g.geoms)
    return {(round(x, 9), round(y, 9)) for p in polys
            for r in [p.exterior] + list(p.interiors) for x, y in r.coords
            if not zone.buffer(2.0).covers(shape({"type": "Point", "coordinates": (x, y)}))}


def judge(tag, fs, res, gap_expect=None):
    G0, G1 = gmap(fs), gmap(res["features"])
    gap = shape(res["gap"])
    zone = gap.buffer(1.0)
    U0 = unary_union([g for k, g in G0.items() if k != "hemi"])
    U1 = unary_union([g for k, g in G1.items() if k != "hemi"])
    check(f"{tag}: no new overlap", overlap(G1) - overlap(G0) < 0.05,
          round(overlap(G1) - overlap(G0), 3))
    check(f"{tag}: no ground lost anywhere", U0.difference(U1).area < 0.05,
          round(U0.difference(U1).area, 3))
    check(f"{tag}: nothing gained outside the gap",
          U1.difference(U0).difference(gap.buffer(0.5)).area < 0.05)
    check(f"{tag}: the gap is filled (>= 99%)",
          U1.difference(U0).area >= 0.99 * gap.area, f"{U1.difference(U0).area:.1f} of {gap.area:.1f}")
    changed = [k for k in G1 if k not in G0 or G1[k].symmetric_difference(G0[k]).area > 1e-9]
    flaws = {k: d for k in changed if (d := new_flaws(G1[k], G0.get(k)))}
    check(f"{tag}: no new needle / spike / hairline", not flaws, flaws)
    moved = [k for k in changed if k in G0 and far_vertices(G1[k], zone) != far_vertices(G0[k], zone)]
    check(f"{tag}: every vertex away from the gap is exactly where it was", not moved, moved)


# ---- synthetic -------------------------------------------------------------------
print("\nsynthetic: a gap cut into A along its border with B; C next door")
A = box(0, 0, 100, 100)
B = box(100, 0, 200, 100)
C = box(0, 100, 200, 160)                 # touches both, must not move at all
notch = Polygon([(80, 30), (100, 30), (100, 70), (80, 70)])
FS = [feat("A", A.difference(notch)), feat("B", B), feat("C", C)]
res = T.dissolve_gap(FS, "name", (90, 50), tol=40)
judge("box notch, Dissolve", FS, res)
check("box notch: C is byte-for-byte unchanged",
      json.dumps([f for f in res["features"] if f["properties"]["name"] == "C"][0]["geometry"])
      == json.dumps(json.loads(json.dumps(FS[2]["geometry"]))))
res = T.fill_gap_with_region(FS, "name", (90, 50), tol=40)
judge("box notch, New region", FS, res)

print("\nsynthetic: a hairline crack (0.4 px) between A and B")
FS2 = [feat("A", box(0, 0, 99.6, 100)), feat("B", B), feat("C", C)]
res = T.dissolve_gap(FS2, "name", (99.8, 50), tol=40)
judge("hairline, Dissolve", FS2, res)
try:
    T.fill_gap_with_region(FS2, "name", (99.8, 50), tol=40)
    check("hairline, New region is refused (too thin to be a region)", False)
except ValueError as e:
    check("hairline, New region is refused (too thin to be a region)", "hairline" in str(e), e)

print("\nsynthetic: a sub-pixel speck is refused, not 'filled'")
speck = Polygon([(99.5, 50), (100, 50), (100, 50.5), (99.5, 50.5)])
FS3 = [feat("A", A.difference(speck)), feat("B", B), feat("C", C)]
for fn in (T.dissolve_gap, T.fill_gap_with_region):
    try:
        fn(FS3, "name", (99.8, 50.2), tol=40)
        check(f"speck, {fn.__name__} refused", False)
    except ValueError as e:
        check(f"speck, {fn.__name__} refused", "smaller than a pixel" in str(e), e)

# ---- the real voids ------------------------------------------------------------------
REAL = r"C:\Users\FIVE\source\repos\FiveAtlas_handoff_fixtures\regions_20261021_112134.geojson"
if not os.path.exists(REAL):
    print("\n(real regions fixture not found -- skipping the real-file checks)")
else:
    F = json.load(open(REAL, encoding="utf-8"))["features"]
    print("\nreal voids, hemi left on")
    for tag, pt in (("lft/PAL/sAMY 305,615 px2", (16583.6, 21176.4)),
                    ("HY/lft/opt 159 px2 hairline", (13651.4, 23751.8)),
                    ("CP/dft/VL.1 81 px2 sliver", (15721.7, 12031.5))):
        for tol in (5, 40, 500):
            judge(f"{tag} reach {tol}", F, T.dissolve_gap(F, "name", pt, tol=tol))
    judge("lft/PAL/sAMY New region", F, T.fill_gap_with_region(F, "name", (16583.6, 21176.4), tol=40))
    try:
        T.fill_gap_with_region(F, "name", (15721.7, 12031.5), tol=40)
        check("CP/dft/VL.1 sliver: New region refused (it would be a spike)", False)
    except ValueError as e:
        check("CP/dft/VL.1 sliver: New region refused (it would be a spike)", "needle-sharp" in str(e), e)

    print("\nreal borders: a 6 px and a 25 px gap cut along five shared borders")
    for a, b in (("lft", "TH"), ("HPF", "TH"), ("CP", "dft"), ("HY", "lft"), ("OLF", "sAMY.1")):
        S = T.partition_regions(F, "name", [a, b], tol=40)["features"]
        G = gmap(S)
        edge = G[a].boundary.intersection(G[b].boundary)
        c = edge.interpolate(0.5, normalized=True)
        for w in (6.0, 25.0):
            carve = G[b].buffer(w).intersection(c.buffer(80)).intersection(G[a])
            S2 = json.loads(json.dumps(S))
            for f in S2:
                if f["properties"]["name"] == a:
                    g = shape(f["geometry"])
                    g = g if g.is_valid else g.buffer(0)
                    f["geometry"] = mapping(g.difference(carve))
            # click ground nobody holds (ISO is drawn over SSp, so part of the cut
            # can still be ISO's)
            G2 = gmap(S2)
            free = carve.difference(unary_union([g for k, g in G2.items() if k != "hemi"]))
            if free.area < 1.0:
                print(f"  (skip {a}|{b} {w:g} px: another region covers the cut)")
                continue
            pt = max(T._polys(free), key=lambda p: p.area).representative_point()
            judge(f"{a}|{b} {w:g} px gap", S2, T.dissolve_gap(S2, "name", (pt.x, pt.y), tol=40))

print()
if fails:
    print(f"{len(fails)} FAILED")
    sys.exit(1)
print("all passed")

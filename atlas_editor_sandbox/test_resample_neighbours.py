"""Resample points never pushes into a region you did not pick.

Both sliders: the one under Edit shared borders (resample_regions) and the one
under Edit points (resample_region). Before the fix the shared-border slider
took up to 13,580 px2 from third regions on 34 of 41 real pairs, spiked at fine
spacing and depended on click order; the Edit-points one let TH take 716 px2.

Offline: a synthetic wavy border with a region pinned to its middle and one
across its end, where the right answer is exact, then the real pairs that
failed (FiveAtlas_handoff_fixtures; skipped if the file is not there).
"""
import itertools
import json
import math
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "backend"))
sys.path.insert(0, HERE)

from shapely.geometry import Polygon, shape, mapping   # noqa: E402
from shapely.ops import unary_union                    # noqa: E402
import topology as T                                   # noqa: E402
from geom_lint import lint                             # noqa: E402

fails = []


def check(label, cond, detail=""):
    print(f"  {'ok  ' if cond else 'FAIL'}  {label}{('  ' + str(detail)) if detail != '' else ''}")
    if not cond:
        fails.append(label)


def feat(name, geom):
    return {"type": "Feature", "properties": {"name": name}, "geometry": mapping(geom)}


def geoms(fs):
    out = {}
    for f in fs:
        n = f["properties"]["name"]
        g = shape(f["geometry"])
        g = g if g.is_valid else g.buffer(0)
        out[n] = unary_union([out[n], g]) if n in out else g
    return out


def vset(g):
    polys = [g] if g.geom_type == "Polygon" else list(g.geoms)
    return {(round(x, 6), round(y, 6)) for p in polys for x, y in p.exterior.coords}


def new_flaws(after, before):
    la, lb = lint(after), lint(before)
    d = {k: la[k] - lb[k] for k in ("needles", "spikes", "hairlines") if la[k] > lb[k]}
    if not la["valid"]:
        d["invalid"] = 1
    return d


# ---- synthetic: a wavy A|B border, D pinned to its middle, C across its top ----
def wx(y):
    return 100.0 + 15.0 * math.sin(y / 25.0)


curve = [(wx(y), float(y)) for y in range(0, 301)]
D = Polygon([(60.0, 130.0)] + [(wx(y), float(y)) for y in range(130, 171)] + [(60.0, 170.0)])
A = Polygon([(0.0, 0.0)] + curve + [(0.0, 300.0)]).difference(D)
B = Polygon(curve + [(200.0, 300.0), (200.0, 0.0)])
C = Polygon([(0.0, 300.0), (200.0, 300.0), (200.0, 340.0), (0.0, 340.0)])
FS = [feat("A", A), feat("B", B), feat("C", C), feat("D", D)]
S = geoms(FS)
junctions = [(round(wx(130), 6), 130.0), (round(wx(170), 6), 170.0), (round(wx(300), 6), 300.0)]

print("\nshared-border slider, synthetic: D sits on the border, C across its end")
for tol in (20, 60, 150, 600):
    r1 = T.resample_regions(FS, "name", ["A", "B"], tol=tol)
    r2 = T.resample_regions(FS, "name", ["B", "A"], tol=tol)
    G, G2 = geoms(r1["features"]), geoms(r2["features"])
    check(f"tol {tol}: C and D untouched",
          G["C"].symmetric_difference(S["C"]).area < 1e-6
          and G["D"].symmetric_difference(S["D"]).area < 1e-6)
    check(f"tol {tol}: A+B together cover exactly what they did",
          unary_union([G["A"], G["B"]]).symmetric_difference(
              unary_union([S["A"], S["B"]])).area < 1.0)
    check(f"tol {tol}: A and B do not overlap", G["A"].intersection(G["B"]).area < 0.5,
          round(G["A"].intersection(G["B"]).area, 3))
    check(f"tol {tol}: the same result in either click order",
          max(G[n].symmetric_difference(G2[n]).area for n in ("A", "B")) < 1e-6)
    check(f"tol {tol}: no new needle / spike / hairline",
          not new_flaws(G["A"], S["A"]) and not new_flaws(G["B"], S["B"]),
          {n: new_flaws(G[n], S[n]) for n in ("A", "B")})
    va, vb = vset(G["A"]), vset(G["B"])
    check(f"tol {tol}: the three junction points are still on both outlines",
          all(any(abs(p[0] - q[0]) < 1e-3 and abs(p[1] - q[1]) < 1e-3 for q in va)
              and any(abs(p[0] - q[0]) < 1e-3 and abs(p[1] - q[1]) < 1e-3 for q in vb)
              for p in junctions))
    if tol == 60:
        check("tol 60: the border really was re-pointed",
              r1["handles"]["after"] != r1["handles"]["before"], r1["handles"])

# ---- the real pairs that failed ----------------------------------------------
REAL = r"C:\Users\FIVE\source\repos\FiveAtlas_handoff_fixtures\regions_20261021_112134.geojson"
if not os.path.exists(REAL):
    print("\n(real regions fixture not found -- skipping the real-file checks)")
else:
    F = json.load(open(REAL, encoding="utf-8"))["features"]
    B0 = geoms(F)
    names = [n for n in B0 if n != "hemi"]

    print("\nshared-border slider, real pairs that pushed into neighbours")
    for a, b in (("lft", "TH"), ("OLF", "sAMY.1"), ("SSp", "dft"), ("HY", "lft"), ("dft", "ISO")):
        shared = T.partition_regions(F, "name", [a, b], tol=40)["features"]
        Sx = geoms(shared)
        for tol in (20, 150, 600):
            G = geoms(T.resample_regions(shared, "name", [a, b], tol=tol)["features"])
            G2 = geoms(T.resample_regions(shared, "name", [b, a], tol=tol)["features"])
            # NET per third region: a moved border only hands over overlap the two
            # picks already had with a peer (ISO is drawn over RSP and SSp here)
            net = max(sum(G[p].intersection(Sx[o]).area - Sx[p].intersection(Sx[o]).area
                          for p in (a, b)) for o in names if o not in (a, b))
            moved = max(G[o].symmetric_difference(Sx[o]).area for o in names if o not in (a, b))
            check(f"{a}+{b} tol {tol}: takes nothing from a third region, moves no one else",
                  net <= 1.0 and moved < 1e-6, f"net {net:.1f} px2, others moved {moved:.1f}")
            check(f"{a}+{b} tol {tol}: order-independent, no new lint",
                  max(G[n].symmetric_difference(G2[n]).area for n in (a, b)) < 1e-6
                  and not any(new_flaws(G[n], Sx[n]) for n in (a, b)))

    print("\nEdit-points slider, real regions")

    def overlap_total(G):
        return sum(G[x].intersection(G[y]).area for x, y in itertools.combinations(names, 2)
                   if G[x].intersects(G[y]))

    ov0 = overlap_total(B0)
    for nm, tol in (("TH", 600), ("TH", 150), ("ISO", 150), ("ISO", 600), ("HPF", 600)):
        r = T.resample_region(F, "name", nm, tol=tol)
        G = geoms(r["features"])
        d = overlap_total(G) - ov0
        check(f"{nm} tol {tol}: no new overlap with any region", d <= 1.0, f"{d:+.1f} px2")
        check(f"{nm} tol {tol}: no new lint on any region",
              not any(new_flaws(G[m], B0[m]) for m in names))

print()
if fails:
    print(f"{len(fails)} FAILED")
    sys.exit(1)
print("all passed")

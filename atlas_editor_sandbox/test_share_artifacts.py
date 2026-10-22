"""Share borders must not invent geometry: the artifact cases from the
adversarial sweep, as regression tests.

Entirely offline and entirely synthetic -- every shape is built here, there is no
server, no port and no dataset folder. Each case states the figure the old code
produced, so a regression is recognisable rather than just a failed assert.

Usage:  python test_share_artifacts.py
"""
import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend"))

from shapely.geometry import LineString, Point, Polygon, box, mapping, shape  # noqa: E402
from shapely.ops import unary_union                                          # noqa: E402

import topology as T                                                         # noqa: E402

fails = []


def check(label, cond, detail=""):
    print(f"  {'ok  ' if cond else 'FAIL'}  {label}{('  ' + detail) if detail else ''}")
    if not cond:
        fails.append(label)


def section(t):
    print(f"\n{t}")


def feat(n, g):
    return {"type": "Feature", "properties": {"name": n}, "geometry": mapping(g)}


def run(fs, names, **kw):
    r = T.partition_regions(fs, "name", names, **kw)
    out = {}
    for n in names:
        gs = [shape(f["geometry"]) for f in r["features"]
              if f["properties"].get("name") == n]
        out[n] = unary_union(gs) if gs else Polygon()
    return out


def nv(g):
    return sum(len(p.exterior.coords) + sum(len(r.coords) for r in p.interiors)
               for p in T._polys(g))


def needle(g, minlen=20.0):
    """Longest pair of real-length edges in one ring running within 0.01 px.

    Segments that SHARE a vertex are skipped. They are zero apart by
    definition, and in a ring of fewer than ten points the first and last
    segments are inside the comparison window -- so a plain square read as a
    5,000 px needle. Every real region here has 36 vertices or more, so this
    never affected the measurements on the dataset, only small fixtures.
    """
    worst = 0.0
    for p in T._polys(g):
        cs = list(p.exterior.coords)
        for k in range(len(cs) - 1):
            la = LineString([cs[k], cs[k + 1]])
            if la.length < minlen:
                continue
            for m in range(k + 2, min(k + 8, len(cs) - 1)):
                if set(cs[k:k + 2]) & set(cs[m:m + 2]):
                    continue                   # share a vertex: not a needle
                lb = LineString([cs[m], cs[m + 1]])
                if lb.length >= minlen and la.distance(lb) < 0.01:
                    worst = max(worst, min(la.length, lb.length))
    return worst


def sector(r_out, r_in, a0, a1, steps=180):
    o = [(r_out * math.cos(math.radians(a0 + (a1 - a0) * i / steps)),
          r_out * math.sin(math.radians(a0 + (a1 - a0) * i / steps)))
         for i in range(steps + 1)]
    n = [(r_in * math.cos(math.radians(a0 + (a1 - a0) * i / steps)),
          r_in * math.sin(math.radians(a0 + (a1 - a0) * i / steps)))
         for i in range(steps, -1, -1)]
    p = Polygon(o + n)
    return p if p.is_valid else p.buffer(0)


# ---------------------------------------------------------------------------
section("two regions must never come back overlapping, whatever the click order")
# 350-degree annulus sectors, 10 px apart radially, no overlap to begin with.
# Old behaviour: at tol=150 and 600 one region ended up WHOLLY INSIDE the other
# -- overlap 1,097,566 px, 100% of S2 -- and reversing the order moved all of it
# to the other region (order symdiff 1,097,566 px).
S1, S2 = sector(800, 600, 0, 350), sector(590, 400, 0, 350)
fs = [feat("S1", S1), feat("S2", S2)]
check("the inputs do not overlap", T._safe_intersection(S1, S2).area == 0)
for tol in (150, 600):
    fwd, rev = run(fs, ["S1", "S2"], tol=float(tol)), run(fs, ["S2", "S1"], tol=float(tol))
    ov = T._safe_intersection(fwd["S1"], fwd["S2"]).area
    sym = fwd["S1"].symmetric_difference(rev["S1"]).area
    check(f"reach {tol}: the results are disjoint", ov <= 1.0,
          f"overlap {ov:,.1f} px (was 1,097,566 = 100% of S2)")
    check(f"reach {tol}: click order does not change the answer", sym <= 1.0,
          f"symdiff {sym:,.1f} px (was 1,097,566)")

# ---------------------------------------------------------------------------
section("a void is only absorbed if the reach could actually bridge it")
# A comb of 40-px teeth. The slots are 160 px -- four times the reach at the
# default tol=40, which bridges 2*40 = 80 px. Old behaviour: the comb grew
# +327% and took 99% of every slot, because an ENCLOSED void had no size limit.
teeth = [box(i * 200, 0, i * 200 + 40, 400) for i in range(10)]
cm = unary_union(teeth + [box(0, 0, 1840, 40)])
fs = [feat("cm", cm), feat("sl", box(0, 400, 1840, 700))]
wide = run(fs, ["cm", "sl"], tol=40.0)
slot = box(40, 40, 200, 400)
owns = T._safe_intersection(wide["cm"], slot).area / slot.area
check("a 160-px slot is left alone at reach 40 (bridges 80 px)", owns <= 0.05,
      f"the comb owns {owns:.1%} of it (was 99%)")
check("...and the comb does not balloon", wide["cm"].area / cm.area - 1 <= 0.05,
      f"{wide['cm'].area / cm.area - 1:+.1%} (was +327%)")

# The limit must not simply switch void filling off: the same comb with 60-px
# slots is inside the reach at tol=40 and must still be filled.
teeth2 = [box(i * 100, 0, i * 100 + 40, 400) for i in range(10)]
cm2 = unary_union(teeth2 + [box(0, 0, 940, 40)])
fs2 = [feat("cm", cm2), feat("sl", box(0, 400, 940, 700))]
narrow = run(fs2, ["cm", "sl"], tol=40.0)
slot2 = box(40, 40, 100, 400)
claimed = unary_union([narrow["cm"], narrow["sl"]])
got = T._safe_intersection(claimed, slot2).area / slot2.area
check("a 60-px slot IS still bridged at reach 40", got >= 0.90,
      f"{got:.1%} of it ends up claimed")

# ---------------------------------------------------------------------------
section("a hole is protected only when it is really empty")
# A hole the OTHER PICKED REGION fills is that region's ground, not a void.
# Protecting it erased the inner region: the donut + its exact core lost 26.9%
# of the pair's area instead of 2.5%.
o, i = Point(0, 0).buffer(600, quad_segs=48), Point(0, 0).buffer(300, quad_segs=48)
D = Polygon(o.exterior.coords, [i.exterior.coords])
core = Point(0, 0).buffer(300, quad_segs=48)
fs = [feat("D", D), feat("core", core)]
before = unary_union([D, core]).area
for tol in (40, 150):
    got = run(fs, ["D", "core"], tol=float(tol))
    after = unary_union([got["D"], got["core"]]).area
    check(f"reach {tol}: the nested core survives",
          got["core"].area >= 0.95 * core.area,
          f"{got['core'].area:,.0f} of {core.area:,.0f} px")
    check(f"reach {tol}: the pair keeps its area",
          abs(after - before) <= 0.05 * before,
          f"{(after - before) / before:+.2%} (was -26.90% while holes were protected)")

# A hole nobody fills is deliberate -- a ventricle, a tear -- and a close of
# radius tol must not swallow it. 220 px wide, so reach 150 (bridging 300)
# would have closed it.
ring = Polygon(box(0, 0, 2000, 2000).exterior.coords,
               [box(890, 890, 1110, 1110).exterior.coords])
fs = [feat("ring", ring), feat("nb", box(2000, 0, 3000, 2000))]
for tol in (40, 150, 600):
    got = run(fs, ["ring", "nb"], tol=float(tol))
    holes = sum(len(p.interiors) for p in T._polys(got["ring"]))
    check(f"reach {tol}: a 220-px hole nobody claims is kept", holes >= 1,
          f"{holes} hole(s) (OLF's real one vanished at every reach from 150 up)")

# ---------------------------------------------------------------------------
section("the result stays clean enough for another tool to read")
# A small region beside a large one is the vertex-explosion case: the smallest
# region sets the Voronoi sampling density, so the big one used to come back
# with thousands of all-but-collinear vertices (x89.8 measured on real data,
# and one click grew the region file 2.79x).
big = box(0, 0, 9000, 6000)
small = box(9010, 2990, 9050, 3030)              # 40x40, 10 px away
fs = [feat("big", big), feat("small", small)]
got = run(fs, ["big", "small"], tol=40.0)
ratio = nv(got["big"]) / nv(big)
check("a big region keeps a sane vertex count next to a small one", ratio <= 8.0,
      f"{nv(big)} -> {nv(got['big'])} verts (x{ratio:.1f})")
check("the free edge is not displaced by the fluff removal",
      big.symmetric_difference(got["big"]).difference(small.buffer(200)).area <= 50.0,
      f"{big.symmetric_difference(got['big']).difference(small.buffer(200)).area:,.1f} px")

for label, fs2, names in (
    ("two offset boxes", [feat("A", box(0, 0, 1000, 1000)),
                          feat("B", box(800, 0, 1800, 1000))], ["A", "B"]),
    ("an L and a block", [feat("L", unary_union([box(0, 0, 200, 900),
                                                 box(0, 0, 900, 200)])),
                          feat("K", box(250, 250, 900, 900))], ["L", "K"]),
    ("a ribbon and a blob", [feat("R", box(0, 0, 2000, 30)),
                             feat("Bl", Point(1000, 300).buffer(260))], ["R", "Bl"]),
):
    got = run(fs2, names, tol=40.0)
    worst = max(needle(got[n]) for n in names)
    check(f"{label}: no zero-width needle", worst == 0.0, f"longest {worst:.1f} px")
    ov = T._safe_intersection(got[names[0]], got[names[1]]).area
    check(f"{label}: disjoint", ov <= 1.0, f"overlap {ov:.3f} px")
    for n in names:
        check(f"{label}: {n} is valid", got[n].is_valid)

# ---------------------------------------------------------------------------
section("the answer must not depend on which region was clicked first")

# Several passes used to settle a tie by list position, so clicking a then b
# gave a different file from b then a. Measured on the real set before the fix:
# 15 of the 41 pairs the app allows differed, dft+RSP by 32,772 px. On two plain
# overlapping boxes it was 448,554 px.
ORDER_CASES = (
    ("two offset boxes", {"A": box(0, 0, 1000, 1000), "B": box(800, 0, 1800, 1000)}),
    ("a wide and a narrow one", {"W": box(0, 0, 10000, 1000),
                                 "N": box(9800, 0, 10800, 1000)}),
    ("an L and a block", {"L": unary_union([box(0, 0, 200, 900), box(0, 0, 900, 200)]),
                          "K": box(250, 250, 900, 900)}),
    ("two annulus sectors", {"S1": sector(800, 600, 0, 350),
                             "S2": sector(590, 400, 0, 350)}),
    ("a speck and a slab", {"big": box(0, 0, 9000, 6000),
                            "tiny": box(9010, 2990, 9050, 3030)}),
)
for label, geoms in ORDER_CASES:
    fs = [feat(n, g) for n, g in geoms.items()]
    ns = list(geoms)
    for tol in (40, 150):
        f = run(fs, ns, tol=float(tol))
        r = run(fs, list(reversed(ns)), tol=float(tol))
        sym = max(f[n].symmetric_difference(r[n]).area for n in ns)
        check(f"{label}, reach {tol}: same answer either way", sym <= 1.0,
              f"symdiff {sym:,.1f} px")

# Three regions: every ordering has to agree, not just the two-region swap.
import itertools                                                      # noqa: E402
tri = {"P": box(0, 0, 1000, 1000), "Q": box(900, 0, 1900, 1000),
       "R": box(0, 900, 1900, 1900)}
fs = [feat(n, g) for n, g in tri.items()]
base = run(fs, ["P", "Q", "R"], tol=40.0)
worst = 0.0
for perm in itertools.permutations(["P", "Q", "R"]):
    got = run(fs, list(perm), tol=40.0)
    worst = max(worst, max(base[n].symmetric_difference(got[n]).area for n in tri))
check("all six orderings of three regions agree", worst <= 1.0,
      f"worst symdiff {worst:,.1f} px")

section("a snapped region comes back as it went in")
# The docstring promises it; the audit measured it changing in 554 of 2,078
# snapped runs. Cleaning now skips the snapped region entirely.
for label, geoms in (("two offset boxes",
                      {"A": box(0, 0, 1000, 1000), "B": box(1100, 0, 2100, 1000)}),
                     ("an L and a block",
                      {"L": unary_union([box(0, 0, 200, 900), box(0, 0, 900, 200)]),
                       "K": box(250, 250, 900, 900)})):
    fs = [feat(n, g) for n, g in geoms.items()]
    ns = list(geoms)
    for keep in ns:
        got = run(fs, ns, tol=40.0, snap_to=keep)
        moved = got[keep].symmetric_difference(geoms[keep]).area
        check(f"{label}: snapping to {keep} leaves it alone", moved <= 1.0,
              f"moved {moved:,.1f} px")

section("a speck next to a big region does not inflate it")
# The smallest region sets the Voronoi sampling density, so a 40x40 speck used
# to make its neighbour come back with thousands of near-collinear vertices
# (x89.6 on the real opt). What matters is absolute movement, not percentage:
# a 31 px region growing by 27 px reads as "+90%" and means nothing.
big, tiny = box(0, 0, 9000, 6000), box(9010, 2990, 9050, 3030)
fs = [feat("big", big), feat("tiny", tiny)]
got = run(fs, ["big", "tiny"], tol=40.0)
check("the big region keeps a sane vertex count",
      nv(got["big"]) / nv(big) <= 8.0,
      f"{nv(big)} -> {nv(got['big'])} verts")
check("the speck is not erased", got["tiny"].area >= 0.5 * tiny.area,
      f"{tiny.area:,.0f} -> {got['tiny'].area:,.0f} px")
check("...nor does it balloon", got["tiny"].area <= tiny.area + 4000.0,
      f"{tiny.area:,.0f} -> {got['tiny'].area:,.0f} px")
check("the pair is still disjoint",
      T._safe_intersection(got["big"], got["tiny"]).area <= 1.0)

# ---------------------------------------------------------------------------
section("the outline handed in is not eroded")

# The input cleaning was a morphological open whose only guard let it lose 35%
# of a region before falling back, so it quietly ate the outline it was given --
# 3,938 px of CP against sAMY.1, 1,323 px of TH against HPF, and that erosion
# was essentially ALL of the ground a pair ended up losing. A hairline that has
# real area is anatomy, however thin; only a needle enclosing NO area is an
# artifact, and that is what despike() removes.
spiky = unary_union([
    box(0, 0, 1000, 1000),
    box(1000, 499.75, 1400, 500.25),          # 400 x 0.5 px hairline, 200 px
])
cleaned = T._clean_for_partition(spiky, 40.0)
check("a 0.5 px hairline with real area is kept",
      abs(cleaned.area - spiky.area) <= 1.0,
      f"{spiky.area:,.1f} -> {cleaned.area:,.1f} px")

# A needle is still removed. It has to be built with a hair of width or
# buffer(0) collapses it before anything under test sees it -- the real ones
# measure 0.004883 px across, so this is 0.004 over 600 px on a region the size
# of CP, which is the case that mattered.
needled = Polygon([(0, 0), (5000, 0), (5000, 5000), (2500.004, 5000),
                   (2500.004, 4400), (2500, 4400), (2500, 5000), (0, 5000)])
check("the needle fixture is valid and really has a needle",
      needled.is_valid and needle(needled, minlen=100.0) > 0,
      f"valid {needled.is_valid}, needle {needle(needled, minlen=100.0):.1f} px")
nc = T._clean_for_partition(needled, 40.0)
check("a needle is still removed", needle(nc, minlen=100.0) == 0.0,
      f"{nv(needled)} -> {nv(nc)} verts, needle "
      f"{needle(needled, minlen=100.0):.1f} -> {needle(nc, minlen=100.0):.1f} px")
check("...without moving the region", abs(nc.area - needled.area) <= 5.0,
      f"{needled.area:,.1f} -> {nc.area:,.1f} px")

# End to end: the pair must not quietly lose its own ground.
for label, geoms in (
    ("two hairlined squares", {
        "H1": unary_union([box(0, 0, 1000, 1000),
                           box(1000, 199.8, 1300, 200.2)]),
        "H2": unary_union([box(1060, 0, 2060, 1000),
                           box(760, 799.8, 1060, 800.2)])}),
    ("a ribbon and a slab", {"R": box(0, 0, 4000, 30),
                             "S": box(0, 60, 4000, 900)}),
    ("two offset boxes", {"A": box(0, 0, 1000, 1000),
                          "B": box(800, 0, 1800, 1000)}),
):
    fs = [feat(n, g) for n, g in geoms.items()]
    ns = list(geoms)
    got = run(fs, ns, tol=40.0)
    before = unary_union(list(geoms.values()))
    after = unary_union([got[n] for n in ns])
    lost = T._safe_difference(before, after).area
    check(f"{label}: the pair keeps its own ground", lost <= 50.0,
          f"lost {lost:,.1f} px")

# A genuinely thin region survives whole -- the reason the old 35% guard was
# there in the first place.
fs = [feat("rib", box(0, 0, 4000, 6)), feat("nb", box(0, 20, 4000, 900))]
got = run(fs, ["rib", "nb"], tol=40.0)
check("a 6 px ribbon is not erased", got["rib"].area >= 24000.0,
      f"24,000 -> {got['rib'].area:,.0f} px")
check("...and stays in one piece", len(T._polys(got["rib"])) == 1,
      f"{len(T._polys(got['rib']))} parts")

# ---------------------------------------------------------------------------
print()
if fails:
    print(f"{len(fails)} FAILED: " + "; ".join(fails))
    sys.exit(1)
print("all checks passed")

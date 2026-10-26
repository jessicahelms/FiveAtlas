"""topology.gap_between: where the Snap reach circle should sit.

The circle used to be placed at the midpoint of the closest pair of vertices
between the two picks. That is the wrong question once the regions touch
anywhere -- where they touch they are 0 px apart, so the "closest point" is on
the border they already share, not on the gap being filled (measured 7,886 px
off on HPF + dft). gap_between finds the gap itself: unclaimed ground lying
between at least two picks, the largest piece, and the circle goes at its
widest point so a reach of width/2 closes it.

Entirely offline and entirely synthetic -- every shape is built here, there is
no server, no port and no dataset folder.

Usage:  python test_gap.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend"))

from shapely.geometry import box, mapping, shape   # noqa: E402
from shapely.ops import unary_union                # noqa: E402

import topology as T                         # noqa: E402

fails = []


def check(label, cond, detail=""):
    print(f"  {'ok  ' if cond else 'FAIL'}  {label}{('  ' + detail) if detail else ''}")
    if not cond:
        fails.append(label)


def section(t):
    print(f"\n{t}")


def feat(n, g):
    return {"type": "Feature", "properties": {"name": n}, "geometry": mapping(g)}


def gap(fs, names, **kw):
    return T.gap_between(fs, "name", names, **kw)


# Two 100x100 boxes, 50 px apart horizontally: A on the left, B on the right,
# the gap the strip x in [100, 150], y in [0, 100] -- 50 px wide.
A = box(0, 0, 100, 100)
B = box(150, 0, 250, 100)


# ---------------------------------------------------------------------------
section("they already touch -> no circle (None)")
# Boxes sharing an edge exactly: 0 px apart, nothing between to bridge.
touch = [feat("A", box(0, 0, 100, 100)), feat("B", box(100, 0, 200, 100))]
r = gap(touch, ["A", "B"])
check("touching pair returns None", r is None, repr(r))


# ---------------------------------------------------------------------------
section("a real unclaimed gap -> kind 'gap', circle at its widest point")
r = gap([feat("A", A), feat("B", B)], ["A", "B"])
check("kind is 'gap'", r is not None and r.get("kind") == "gap", repr(r))
if r and r.get("kind") == "gap":
    cx, cy = r["centre"]
    # The circle sits inside the 50-wide strip between the boxes...
    check("centre x is inside the gap strip", 100.0 <= cx <= 150.0, f"cx={cx:.1f}")
    check("centre y is within the boxes", 0.0 <= cy <= 100.0, f"cy={cy:.1f}")
    # ...and the width at that point is the 50 px gap (a reach of 25 closes it).
    check("width ~= 50 px (the gap)", 45.0 <= r["width"] <= 55.0, f"width={r['width']:.1f}")
    # The reach is MEASURED with the close Share borders uses, not assumed to be
    # width/2 -- on the real file width/2 was wrong for 21 of 22 gaps. For this
    # straight slot the two agree: a close of 25 bridges 50.
    rr = r.get("reach")
    check("reach is measured (~25 for a 50 px slot)",
          rr is not None and 24.0 <= rr <= 26.5, f"reach={rr}")
    if rr:
        res = T.partition_regions([feat("A", A), feat("B", B)], "name", ["A", "B"],
                                  tol=rr, fill_all=True)
        covered = sum(shape(f["geometry"]).area for f in res["features"])
        check("Share borders at that reach really fills the gap (>= 95%)",
              covered >= 20000 + 0.95 * 5000, f"covered={covered:.0f} of 25000")


# ---------------------------------------------------------------------------
section("the reach accounts for an open bay, where width/2 under-promises")
# B is a U-shaped cup around A, open at the top: the ground between them is a
# 40 px moat on three sides that runs out of the cup at the open end. Its widest
# point is 40 px across, but the part at the open mouth only closes once the
# close spans the mouth -- the measured reach must cover the piece, so it can be
# no smaller than half the width and must actually fill it at Share borders.
cupA = box(40, 0, 160, 160)
cupB = unary_union([box(-40, -40, 240, 0),       # floor A sits on
                    box(-40, 0, 0, 200),          # left arm
                    box(200, 0, 240, 200)])       # right arm
r = gap(feat_list := [feat("A", cupA), feat("B", cupB)], ["A", "B"])
if r and r.get("kind") == "gap":
    rr = r.get("reach")
    check("bay: reach measured and >= width/2", rr is not None and rr >= r["width"] / 2 - 0.5,
          f"width={r['width']:.1f} reach={rr}")
    if rr:
        res = T.partition_regions(feat_list, "name", ["A", "B"], tol=rr, fill_all=True)
        got = unary_union([shape(f["geometry"]) for f in res["features"]])
        piece_area = r["area"]
        before = unary_union([cupA, cupB]).area
        check("bay: Share borders at the measured reach fills >= 90% of the piece",
              got.area - before >= 0.90 * piece_area,
              f"filled={got.area - before:.0f} of piece {piece_area:.0f}")
else:
    check("bay: a gap is found", False, repr(r))


# ---------------------------------------------------------------------------
section("gap wider than the search reach -> kind 'apart', measured edge to edge")
# search=10 closes only up to 20 px, so the 50 px gap is not bridged: no gap to
# draw, but the picks do not touch, so report how far apart they are.
r = gap([feat("A", A), feat("B", B)], ["A", "B"], search=10)
check("kind is 'apart'", r is not None and r.get("kind") == "apart", repr(r))
if r and r.get("kind") == "apart":
    check("width ~= 50 px (nearest approach)", 48.0 <= r["width"] <= 52.0, f"width={r['width']:.1f}")
    cx, _ = r["centre"]
    check("centre is midway between the picks", 120.0 <= cx <= 130.0, f"cx={cx:.1f}")


# ---------------------------------------------------------------------------
section("a container wrapping both picks (hemi) is not treated as a neighbour")
# `hemi` covers everything, including the gap. If it were subtracted like any
# other region the gap would vanish; as a container its ground is exactly what a
# gap between two of its children is, so the gap must still be found.
hemi = box(-20, -20, 270, 120)
fs = [feat("A", A), feat("B", B), feat("hemi", hemi)]
r = gap(fs, ["A", "B"])
check("gap still found through a container", r is not None and r.get("kind") == "gap", repr(r))
if r and r.get("kind") == "gap":
    check("width ~= 50 px through the container", 45.0 <= r["width"] <= 55.0, f"width={r['width']:.1f}")


# ---------------------------------------------------------------------------
section("a region standing in the gap is never counted as gap")
# C sits in the middle of the gap. Its ground is another region's, so it is
# subtracted; the two strips left beside it each touch only one pick, so there
# is no clean gap between A and B -- the honest answer is 'apart', not a circle
# parked on the intruder.
C = box(115, 0, 135, 100)
fs = [feat("A", A), feat("B", B), feat("C", C)]
r = gap(fs, ["A", "B"])
check("intruder splits the gap -> not kind 'gap'",
      r is None or r.get("kind") != "gap", repr(r))


# ---------------------------------------------------------------------------
section("fewer than two valid picks -> None")
check("one pick returns None", gap([feat("A", A), feat("B", B)], ["A"]) is None)
check("unknown names return None", gap([feat("A", A), feat("B", B)], ["X", "Y"]) is None)


# ---------------------------------------------------------------------------
print()
if fails:
    print(f"{len(fails)} FAILED: " + "; ".join(fails))
    sys.exit(1)
print("all checks passed")

"""move_border -- a drag may extend past the section outline.

The pair's outer boundary used to be a wall: the swept ground outside A|B was
clipped, and hemi counted as a protecting neighbour on top of that, so a border
near the coast could never move past the coast. Offline, pure topology.
"""
import sys

sys.path.insert(0, r"C:\Users\FIVE\source\repos\Jess\atlas_editor\backend")

from shapely.geometry import Point, box, shape, mapping   # noqa: E402
import topology as T                                      # noqa: E402

fails = []


def check(label, cond, detail=""):
    print(f"  {'ok  ' if cond else 'FAIL'}  {label}{('  ' + detail) if detail else ''}")
    if not cond:
        fails.append(label)


def feat(name, geom):
    return {"type": "Feature", "properties": {"name": name},
            "geometry": mapping(geom)}


def geom_of(out, name):
    return shape(next(f for f in out if f["properties"]["name"] == name)["geometry"])


A = box(0, 0, 100, 100)
B = box(100, 0, 200, 100)
ARC = [(100.0, 0.0), (100.0, 100.0)]
# the drag bulges the divider PAST the pair's top edge: outside both regions
BULGE = [(100.0, 0.0), (130.0, 60.0), (120.0, 130.0), (100.0, 100.0)]

print("\npast the pair's own outline, empty ground beyond")
out = T.move_border([feat("A", A), feat("B", B)], "name", "A", "B",
                    BULGE, drag_start=ARC)
gA, gB = geom_of(out, "A"), geom_of(out, "B")
check("the bulge above the old coast is KEPT", gA.covers(Point(115, 110)),
      f"A bounds {tuple(round(v,1) for v in gA.bounds)}")
check("it belongs to the dragged region", not gB.covers(Point(115, 110)))
check("no overlap between the pair", gA.intersection(gB).area < 0.5)
check("B is intact where the drag did not reach", gB.covers(Point(180, 50)))

print("\nwith hemi wrapping everything -- the section outline itself")
hemi = box(-20, -20, 220, 120)
out = T.move_border([feat("hemi", hemi), feat("A", A), feat("B", B)],
                    "name", "A", "B", BULGE, drag_start=ARC)
gA = geom_of(out, "A")
check("hemi does not wall the drag in", gA.covers(Point(115, 110)),
      f"A bounds {tuple(round(v,1) for v in gA.bounds)}")
check("hemi itself is untouched",
      abs(geom_of(out, "hemi").area - 240 * 140) < 0.01)
check("...even where the bulge crossed PAST hemi's own outline",
      gA.covers(Point(118, 125)) or gA.bounds[3] > 120.0,
      f"top {gA.bounds[3]:.1f} vs hemi top 120")

print("\na real neighbour is still protected")
C = box(0, 100, 200, 140)          # sits exactly where the bulge wants to go
out = T.move_border([feat("A", A), feat("B", B), feat("C", C)],
                    "name", "A", "B", BULGE, drag_start=ARC)
gA, gC = geom_of(out, "A"), geom_of(out, "C")
check("the bulge cannot take C's ground", gA.intersection(gC).area < 0.5)
check("C is whole", abs(gC.area - 200 * 40) < 1.0, f"{gC.area:.1f}")

print("\nordinary internal drags are unchanged")
push = [(100.0, 0.0), (80.0, 50.0), (100.0, 100.0)]     # push INTO A
out = T.move_border([feat("A", A), feat("B", B)], "name", "A", "B",
                    push, drag_start=ARC)
gA, gB = geom_of(out, "A"), geom_of(out, "B")
check("area is conserved on an internal drag",
      abs(gA.area + gB.area - 20000) < 1.0, f"{gA.area + gB.area:.1f}")
check("B took what A ceded", gB.covers(Point(95, 50)))

print("\n" + (f"{len(fails)} FAILED: " + "; ".join(fails) if fails else "all checks passed"))
sys.exit(1 if fails else 0)

import json
import numpy as np
from shapely.geometry import shape, LineString, Point
from shapely.ops import unary_union, polygonize

import datasets as ds
import scan
import topology

d = ds.get_dataset("DNMT3A_002_27_38_Het_F")
fc = json.load(open(scan.primary_regions_path(d), encoding="utf-8"))
feats = fc["features"]
arcs = [a for a in topology.shared_borders(feats, "name", 4.0) if set(a["regions"]) == {"dft", "ISO"}]
arc = max(arcs, key=lambda a: a["length"])
pts = np.array(arc["points"], float)
new = pts.copy(); new[1:-1, 0] += 350


def geom(name):
    for f in feats:
        if str(f["properties"].get("name")) == name:
            return shape(f["geometry"])


A = geom("dft"); B = geom("ISO")
print("dft:", A.geom_type, "n", len(A.geoms) if A.geom_type == "MultiPolygon" else 1, "area", round(A.area))
print("ISO:", B.geom_type, "n", len(B.geoms) if B.geom_type == "MultiPolygon" else 1, "area", round(B.area))
U = unary_union([A.buffer(0), B.buffer(0)])
print("U:", U.geom_type, "n", len(U.geoms) if U.geom_type == "MultiPolygon" else 1, "area", round(U.area),
      "n_interior_rings", sum(len(p.interiors) for p in (U.geoms if U.geom_type == "MultiPolygon" else [U])))
bnd = U.boundary
c = [tuple(map(float, p)) for p in new]
print("arc endpoint0:", [round(v) for v in c[0]], "dist to U.bnd:", round(Point(c[0]).distance(bnd), 1))
print("arc endpoint1:", [round(v) for v in c[-1]], "dist to U.bnd:", round(Point(c[-1]).distance(bnd), 1))
c[0] = bnd.interpolate(bnd.project(Point(c[0]))).coords[0]
c[-1] = bnd.interpolate(bnd.project(Point(c[-1]))).coords[0]
line = LineString(c)
print("snapped ep0 dist:", round(Point(c[0]).distance(bnd), 3), " ep1 dist:", round(Point(c[-1]).distance(bnd), 3))
print("line intersects bnd:", line.intersects(bnd), " within U:", line.within(U))
merged = unary_union([bnd, line])
pieces = list(polygonize(merged))
inside = [p for p in pieces if U.contains(p.representative_point())]
print("polygonize pieces:", len(pieces), " inside U:", len(inside), " areas:", sorted(round(p.area) for p in inside)[:8])

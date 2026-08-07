"""The hemisection outline, and switching regions off.

Run against an isolated backend, never the one serving the user's workdir:

    ATLAS_WORKDIR=<scratch>/wd python -m uvicorn app:app --app-dir backend --port 8060

Both features exist for the same reason: `hemi` wraps all 22 other regions, so
with it in play nothing is ever outside a region and no gap can be found.
"""
import json
import os
import sys
import urllib.error
import urllib.request

sys.path.insert(0, r"C:\Users\FIVE\source\repos\Jess\atlas_editor\backend")

from shapely.geometry import shape           # noqa: E402
from shapely.ops import unary_union          # noqa: E402
import damage as D                           # noqa: E402
import topology as T                         # noqa: E402

BASE = os.environ.get("ATLAS_TEST_BASE", "http://127.0.0.1:8060")
if ":8050" in BASE or ":8000" in BASE:
    raise SystemExit("refusing to run against the live backend")

fails = []


def check(label, cond, detail=""):
    print(f"  {'ok  ' if cond else 'FAIL'}  {label}{('  ' + detail) if detail else ''}")
    if not cond:
        fails.append(label)


def get(path):
    with urllib.request.urlopen(BASE + path, timeout=120) as r:
        return json.load(r)


def post(path, body):
    req = urllib.request.Request(BASE + path, data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json"},
                                 method="POST")
    try:
        with urllib.request.urlopen(req, timeout=300) as r:
            return r.status, json.load(r)
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode()[:300]


def geom(f):
    g = shape(f["geometry"])
    return g if g.is_valid else g.buffer(0)


dsets = get("/api/datasets")
ds_id = fc0 = None
for cand in dsets:
    try:
        got = get(f"/api/datasets/{cand['id']}/regions")
    except Exception:
        continue
    if len(got.get("features") or []) >= 10:
        ds_id, fc0 = cand["id"], got
        break
if ds_id is None:
    raise SystemExit(f"no atlas-sized dataset on {BASE} (saw {[d['id'] for d in dsets]})")
info = get(f"/api/datasets/{ds_id}/info")
id_prop = info.get("idProp", "name")
names = [(f.get("properties") or {}).get(id_prop) for f in fc0["features"]]
print(f"dataset {ds_id} — {len(names)} features")

anat, _dmg = D.split_features(fc0["features"], id_prop)
inner = [f for f in anat if (f["properties"] or {})[id_prop] != "hemi"]
inner_union = unary_union([geom(f) for f in inner])
hemi0 = next(geom(f) for f in anat if (f["properties"] or {})[id_prop] == "hemi")

# --- building the outline --------------------------------------------------------
print("\nbuilding the outline")
code, pv = post(f"/api/datasets/{ds_id}/regions/outline", {"fc": fc0})
check("a preview comes back", code == 200 and "geometry" in pv, str(pv)[:120])
check("it knows hemi already exists", pv.get("exists") is True)
check("it is built from the other regions, hemi excluded",
      "hemi" not in pv["sources"] and len(pv["sources"]) == len(inner),
      f"{len(pv['sources'])} sources")
check("preview changes nothing",
      get(f"/api/datasets/{ds_id}/regions")["features"] == fc0["features"])

built = shape(pv["geometry"])
check("the outline contains every region it was built from",
      built.buffer(1).contains(inner_union),
      f"{inner_union.difference(built).area:,.0f} px² left outside")
check("it has no holes — an outline with holes is not an outline",
      len(built.interiors) == 0 if built.geom_type == "Polygon" else False,
      built.geom_type)
check("it is close to the real hand-drawn hemi, not a wild over-estimate",
      0.9 < built.area / hemi0.area < 1.35, f"{built.area / hemi0.area:.3f}× hemi")

code, hull = post(f"/api/datasets/{ds_id}/regions/outline",
                  {"fc": fc0, "method": "hull"})
hull_g = shape(hull["geometry"])
check("the convex hull is a hull", hull_g.equals(hull_g.convex_hull))
check("...and it claims more ground than the bubble, which is why it is not the "
      "default", hull_g.area > built.area,
      f"hull {hull_g.area:,.0f} vs bubble {built.area:,.0f}")

small = shape(post(f"/api/datasets/{ds_id}/regions/outline",
                   {"fc": fc0, "radius": 40})[1]["geometry"])
big = shape(post(f"/api/datasets/{ds_id}/regions/outline",
                 {"fc": fc0, "radius": 600})[1]["geometry"])
check("a bigger radius gives a looser outline", big.area > small.area,
      f"{small.area:,.0f} → {big.area:,.0f}")
code, bad = post(f"/api/datasets/{ds_id}/regions/outline", {"fc": fc0, "radius": 0})
check("a zero radius is refused, not silently ignored", code == 422, str(code))

# --- applying it -----------------------------------------------------------------
print("\napplying it")
code, ap = post(f"/api/datasets/{ds_id}/regions/outline", {"fc": fc0, "apply": True})
check("apply replaces the existing hemi rather than adding a second",
      code == 200 and ap["replaced"] is True
      and [n for n in [(f.get("properties") or {}).get(id_prop)
                       for f in ap["features"]]].count("hemi") == 1)
check("no other region is touched",
      len(ap["features"]) == len(fc0["features"]))
check("the outline is drawn first, so it sits under what it wraps",
      (ap["features"][0].get("properties") or {}).get(id_prop) == "hemi")
check("the trail records it", any(e.get("action") == "section-outline"
                                  for e in ap.get("_provenance") or []))

# rebuilding from the rebuilt file must not creep outwards
code, again = post(f"/api/datasets/{ds_id}/regions/outline", {"fc": ap, "apply": True})
a1 = next(geom(f) for f in ap["features"]
          if (f["properties"] or {})[id_prop] == "hemi")
a2 = next(geom(f) for f in again["features"]
          if (f["properties"] or {})[id_prop] == "hemi")
check("rebuilding twice gives the same outline — it never uses itself as input",
      abs(a2.area - a1.area) < 1.0, f"{a2.area - a1.area:+,.1f} px²")

# a file with no outline at all
no_hemi = {**fc0, "features": [f for f in fc0["features"]
                               if (f.get("properties") or {}).get(id_prop) != "hemi"]}
code, made = post(f"/api/datasets/{ds_id}/regions/outline",
                  {"fc": no_hemi, "apply": True})
check("it can create one where there is none",
      code == 200 and made["replaced"] is False
      and len(made["features"]) == len(no_hemi["features"]) + 1)

# damage never pulls the outline in
sq = built.representative_point()
dmg_fc = {**fc0, "features": fc0["features"] + [{
    "type": "Feature", "properties": {id_prop: "separation.1"},
    "geometry": {"type": "Polygon", "coordinates": [[
        [sq.x - 50, sq.y - 50], [sq.x + 50, sq.y - 50],
        [sq.x + 50, sq.y + 50], [sq.x - 50, sq.y + 50], [sq.x - 50, sq.y - 50]]]}}]}
code, with_dmg = post(f"/api/datasets/{ds_id}/regions/outline", {"fc": dmg_fc})
check("damage shapes are left out of the outline",
      "separation.1" not in with_dmg["sources"]
      and abs(shape(with_dmg["geometry"]).area - built.area) < 1.0)

# --- switching regions off -------------------------------------------------------
print("\nswitching regions off")
code, full = post(f"/api/datasets/{ds_id}/regions/validate", {"fc": fc0})
code, less = post(f"/api/datasets/{ds_id}/regions/validate",
                  {"fc": fc0, "exclude": ["hemi"]})
overlaps_full = [p for p in full["problems"] if "overlap" in str(p).lower()]
overlaps_less = [p for p in less["problems"] if "overlap" in str(p).lower()]
check("Check geometry reports the outline's overlaps while it is on",
      len(overlaps_full) > 0, f"{len(overlaps_full)} overlap problems")
check("switching it off removes them", len(overlaps_less) < len(overlaps_full),
      f"{len(overlaps_full)} → {len(overlaps_less)}")
check("the excluded region is named in the report", less.get("excluded") == ["hemi"],
      str(less.get("excluded")))

# A gap can only be found with the outline switched off. The point has to be in a
# REAL gap — inside hemi, outside every other region — not just inside hemi, which
# is true of the whole section.
# It has to be an ENCLOSED void — a hole in the tissue with regions all around it
# — which is what find_gap looks for. The ground between the outline and the
# tissue is not a gap, it is background.
from shapely.geometry import Polygon                            # noqa: E402

holes = []
for poly in getattr(inner_union, "geoms", [inner_union]):
    for ring in poly.interiors:
        h = Polygon(ring)
        if h.area > 500:
            holes.append(h)
holes.sort(key=lambda h: -h.area)
if not holes:
    print("  (no enclosed void between the regions in this file — "
          "the gap check below is skipped)")
    code_on = code_off = None
else:
    gp = holes[0].representative_point()
    gap_pt = [float(gp.x), float(gp.y)]
    print(f"  (largest enclosed void: {holes[0].area:,.0f} px² at "
          f"{gap_pt[0]:.0f},{gap_pt[1]:.0f})")
    code_on, on = post(f"/api/datasets/{ds_id}/regions/dissolve-gap",
                       {"fc": fc0, "point": gap_pt})
    code_off, offres = post(f"/api/datasets/{ds_id}/regions/dissolve-gap",
                            {"fc": fc0, "point": gap_pt, "exclude": ["hemi"]})
    check("with the outline on, the point counts as inside a region, not a gap",
          code_on == 422, str(code_on))
if code_off == 200:
    check("with it off, the gap under the same point is found",
          offres["area"] > 0, f"{offres['area']:,.0f} px²")
    kept = [(f.get("properties") or {}).get(id_prop) for f in offres["features"]]
    check("and the switched-off region comes back untouched, in place",
          kept.count("hemi") == 1 and kept.index("hemi") == names.index("hemi"),
          f"index {kept.index('hemi') if 'hemi' in kept else '-'} vs {names.index('hemi')}")
    back = next(geom(f) for f in offres["features"]
                if (f["properties"] or {})[id_prop] == "hemi")
    check("...with its geometry byte-for-byte unchanged",
          abs(back.area - hemi0.area) < 1e-6, f"{back.area - hemi0.area:+.6f}")
elif code_off is not None:
    check("with it off, the gap under the same point is found", False,
          f"{code_off} {str(offres)[:120]}")

print("\n" + (f"{len(fails)} FAILED: " + "; ".join(fails) if fails else "all checks passed"))
sys.exit(1 if fails else 0)

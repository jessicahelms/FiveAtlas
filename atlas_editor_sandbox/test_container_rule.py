"""The whole-section outline (hemi) is a container, never a peer -- in every tool.

The 2026-10-23 feature audit found it treated as an ordinary region in five
places: Merge deleted it into a child, Split cut it in two, Draw "on top" holed
it on every draw inside the section, a border drag with hemi picked protected
no neighbour, and Fix a gap refused every click while hemi was on. Each case
here is one of those, plus a needle/spike/hairline lint on every output.

Entirely offline and synthetic.   Usage:  python test_container_rule.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from shapely.geometry import box, mapping, shape   # noqa: E402

import topology as T                               # noqa: E402
from geom_lint import lint, is_clean                # noqa: E402

fails = []


def check(label, cond, detail=""):
    print(f"  {'ok  ' if cond else 'FAIL'}  {label}{('  ' + detail) if detail else ''}")
    if not cond:
        fails.append(label)


def section(t):
    print(f"\n{t}")


def feat(n, g):
    return {"type": "Feature", "properties": {"name": n}, "geometry": mapping(g)}


def by_name(feats):
    out = {}
    for f in feats:
        out.setdefault(f["properties"]["name"], []).append(shape(f["geometry"]))
    return {k: v[0] if len(v) == 1 else v for k, v in out.items()}


def refuses(fn, *a, **k):
    try:
        fn(*a, **k)
    except ValueError as e:
        return str(e)
    return None


HEMI = box(0, 0, 1000, 1000)
A = box(100, 100, 400, 900)
B = box(420, 100, 700, 900)        # 20 px corridor between A and B
C = box(750, 100, 900, 900)
FS = [feat("hemi", HEMI), feat("A", A), feat("B", B), feat("C", C)]


def clean_changed(before, after, names):
    """No NEW needle/spike/hairline in the named regions."""
    bad = []
    for n in names:
        if n not in after:
            continue
        ra, rb = lint(after[n]), lint(before[n]) if n in before else None
        if not ra["valid"] or (rb and (ra["needles"] > rb["needles"] or ra["spikes"] > rb["spikes"]
                                       or ra["hairlines"] > rb["hairlines"])) or (not rb and not is_clean(ra)):
            bad.append((n, ra))
    return bad


# ---------------------------------------------------------------------------
section("blankets() names the section outline")
check("hemi is the container", T.blankets(FS, "name") == ["hemi"], repr(T.blankets(FS, "name")))

# ---------------------------------------------------------------------------
section("Merge: hemi is never merged into a child")
for picks in (["A", "hemi"], ["hemi", "A"], ["A", "B", "hemi"]):
    msg = refuses(T.merge_regions, FS, "name", picks)
    check(f"merge {picks} refused", msg is not None and "hemi" in msg, msg or "accepted")
r = T.merge_regions(FS, "name", ["A", "B"])
g = by_name(r["features"])
check("merging two children still works; hemi untouched",
      "hemi" in g and g["hemi"].equals(HEMI) and "B" not in g, f"names={sorted(g)}")

# ---------------------------------------------------------------------------
section("Merging WITH hemi is an explicit fold: the region joins hemi, hemi is kept")
for picks in (["A", "hemi"], ["hemi", "A"]):
    r = T.fold_into_container(FS, "name", picks)
    g = by_name(r["features"])
    check(f"fold {picks}: A's label is gone, hemi kept as it was",
          "A" not in g and "hemi" in g and g["hemi"].equals(HEMI) and r["name"] == "hemi",
          f"names={sorted(g)} name={r['name']}")
    hemi_props = [f["properties"] for f in r["features"] if f["properties"]["name"] == "hemi"]
    check(f"fold {picks}: hemi keeps its own properties", hemi_props == [{"name": "hemi"}])
POKE = box(950, 400, 1080, 600)                       # pokes 80 px past hemi's edge
FS3 = FS + [feat("P", POKE)]
r = T.fold_into_container(FS3, "name", ["hemi", "P"])
g = by_name(r["features"])
check("folding a region that pokes past hemi grows hemi to cover it (no ground lost)",
      g["hemi"].covers(POKE) and abs(g["hemi"].area - HEMI.union(POKE).area) < 1,
      f"hemi {HEMI.area:.0f} -> {g['hemi'].area:.0f}")
check("...and the grown outline is lint-clean", is_clean(lint(g["hemi"])), repr(lint(g["hemi"])))
msg = refuses(T.fold_into_container, FS, "name", ["A", "B"])
check("folding without the outline among the picks is refused", msg is not None, msg or "accepted")

# ---------------------------------------------------------------------------
section("Split: hemi is never split; children still are")
msg = refuses(T.split_region, FS, "name", "hemi", [(500, -50), (500, 1050)])
check("split hemi refused", msg is not None and "hemi" in msg, msg or "accepted")
r = T.split_region(FS, "name", "A", [(250, 0), (250, 1000)])
g = by_name(r["features"])
check("split a child still works; hemi untouched",
      "hemi" in g and g["hemi"].equals(HEMI) and len(r["names"]) == 2, repr(r["names"]))

# ---------------------------------------------------------------------------
section("Draw: hemi is not a stacking hit and is never carved or held against")
rep = T.overlap_report(FS, "name", [(150, 920), (350, 920), (350, 980), (150, 980)])
check("outline inside hemi only -> no hits (no prompt)", rep["overlaps"] == [], repr(rep["overlaps"]))
rep = T.overlap_report(FS, "name", [(350, 400), (450, 400), (450, 500), (350, 500)])
names = sorted(h["name"] for h in rep["overlaps"])
check("partial over A and B -> hits are A and B, not hemi", names == ["A", "B"], repr(names))

r = T.add_region(FS, "name", [(350, 400), (450, 400), (450, 500), (350, 500)], overlap="top")
g = by_name(r["features"])
check("on top: hemi geometry unchanged (no hole punched)", g["hemi"].equals(HEMI),
      f"hemi area {g['hemi'].area:.0f}, holes {len(g['hemi'].interiors)}")
check("on top: A and B are trimmed", g["A"].area < A.area and g["B"].area < B.area)
bad = clean_changed(by_name(FS), g, ["A", "B", r["name"]])
check("on top: no new needle/spike/hairline", not bad, repr(bad))

r = T.add_region(FS, "name", [(150, 920), (350, 920), (350, 980), (150, 980)], overlap="bottom")
g = by_name(r["features"])
check("underneath, inside hemi: the region is kept whole (hemi not held against it)",
      abs(g[r["name"]].area - 200 * 60) < 1, f"area {g[r['name']].area:.0f}")

# ---------------------------------------------------------------------------
section("Edit shared borders: with hemi picked, real neighbours are still protected")
ring = list(A.exterior.coords)
moved = [(460.0 if abs(x - 400) < 1e-9 else x, y) for x, y in ring]   # push A's right edge 60 px into B
out = T.move_border(FS, "name", "hemi", "A", moved, drag_start=ring)
g = by_name(out)
check("B untouched (WKB-equal)", g["B"].equals(B), f"B area {g['B'].area:.0f} vs {B.area:.0f}")
check("C untouched", g["C"].equals(C))
check("A grew only into the free corridor (<= 20 px wide)",
      A.area < g["A"].area <= A.area + 20 * 800 + 1, f"A {A.area:.0f} -> {g['A'].area:.0f}")
check("A and B do not overlap", g["A"].intersection(g["B"]).area < 1e-6)
bad = clean_changed(by_name(FS), g, ["A", "hemi"])
check("no new needle/spike/hairline", not bad, repr(bad))

# ---------------------------------------------------------------------------
section("Fix a gap: works with hemi on, stays inside the section")
gp, kind, geoms, nms = T.find_gap(FS, "name", (410, 500), tol=40)
check("click in the corridor finds the gap with hemi present", gp is not None and gp.area > 0,
      f"kind={kind} area={gp.area:.0f}")
check("the gap lies inside hemi", gp.difference(HEMI).area < 1e-6)
check("hemi is not one of the regions it hands the gap to", "hemi" not in nms.values())
r = T.dissolve_gap(FS, "name", (410, 500), tol=40)
g = by_name(r["features"])
check("dissolve fills the corridor between A and B", sorted(r["regions"]) == ["A", "B"], repr(r["regions"]))
check("hemi and C untouched", g["hemi"].equals(HEMI) and g["C"].equals(C))
bad = clean_changed(by_name(FS), g, ["A", "B"])
check("no new needle/spike/hairline", not bad, repr(bad))
msg = refuses(T.find_gap, FS, "name", (1100, 500), tol=40)
check("a click outside hemi is refused", msg is not None and "outside" in msg, msg or "accepted")
msg = refuses(T.find_gap, FS, "name", (200, 500), tol=40)
check("a click inside a child is still refused", msg is not None and "inside A" in msg, msg or "accepted")

# ---------------------------------------------------------------------------
section("Fix a gap: a region's own hole and the margin to the outline are not gaps")
# On the real file, setting hemi aside made OLF's 146k px^2 hole fillable and a
# click in the unlabelled margin found a 4.7M px^2 'gap'. Deliberate holes are
# kept; the margin between the tissue and the outline is background.
A_holed = A.difference(box(200, 400, 300, 500))
FS2 = [feat("hemi", HEMI), feat("A", A_holed), feat("B", B), feat("C", C)]
msg = refuses(T.find_gap, FS2, "name", (250, 450), tol=40)
check("a hole inside A itself is refused (holes are kept)",
      msg is not None and "hole inside A" in msg, msg or "accepted")
msg = refuses(T.find_gap, FS, "name", (500, 950), tol=40)
check("the margin between the tissue and hemi's outline is refused",
      msg is not None and ("margin" in msg or "no gap" in msg), msg or "accepted")
gp, kind, _, _ = T.find_gap(FS, "name", (725, 500), tol=40)
check("a real corridor between B and C is still found", gp is not None and gp.area > 0,
      f"kind={kind} area={gp.area:.0f}")

# ---------------------------------------------------------------------------
print()
if fails:
    print(f"{len(fails)} FAILED: " + "; ".join(fails))
    sys.exit(1)
print("all checks passed")

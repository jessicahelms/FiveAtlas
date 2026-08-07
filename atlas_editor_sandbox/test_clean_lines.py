"""clean_lines -- circle stray hairlines, and only stray hairlines go.

Offline: pure topology, no server, nothing written anywhere.
"""
import sys

sys.path.insert(0, r"C:\Users\FIVE\source\repos\Jess\atlas_editor\backend")

from shapely.geometry import box, shape, mapping   # noqa: E402
import topology as T                               # noqa: E402

fails = []


def check(label, cond, detail=""):
    print(f"  {'ok  ' if cond else 'FAIL'}  {label}{('  ' + detail) if detail else ''}")
    if not cond:
        fails.append(label)


def feat(name, geom):
    return {"type": "Feature", "properties": {"name": name},
            "geometry": mapping(geom)}


def area_of(res, name):
    return sum(shape(f["geometry"]).area for f in res["features"]
               if f["properties"]["name"] == name)


def names_of(res):
    return [f["properties"]["name"] for f in res["features"]]


loop = [(80, -10), (220, -10), (220, 110), (80, 110)]   # circles the border zone

# ---------------------------------------------------------------------------
print("\na stray sliver region between two healthy ones")
# ISO | 3px sliver | TH -- the classic leftover from a border operation.
regions = [feat("ISO", box(0, 0, 100, 100)),
           feat("sliv", box(100, 0, 103, 100)),
           feat("TH", box(103, 0, 200, 100))]
res = T.clean_lines(regions, "name", loop)
check("the sliver region is deleted outright", res["deleted"] == ["sliv"],
      str(res["deleted"]))
check("its ground goes to the neighbours", set(res["filled"]) == {"ISO", "TH"},
      str(res["filled"]))
check("no ground is lost", abs(area_of(res, "ISO") + area_of(res, "TH")
                               - 200 * 100) < 1.0,
      f"{area_of(res, 'ISO') + area_of(res, 'TH'):.1f}")
check("the healthy regions were never eroded",
      area_of(res, "ISO") > 100 * 100 - 1 and area_of(res, "TH") > 97 * 100 - 1)

print("\na hairline GAP between two regions")
regions = [feat("ISO", box(0, 0, 100, 100)), feat("TH", box(102.5, 0, 200, 100))]
res = T.clean_lines(regions, "name", loop)
check("the gap is found and filled", set(res["filled"]) == {"ISO", "TH"},
      str(res["filled"]))
check("nothing was deleted", res["deleted"] == [])
check("the plane is whole again", abs(area_of(res, "ISO") + area_of(res, "TH")
                                      - 200 * 100) < 5.0,
      f"{area_of(res, 'ISO') + area_of(res, 'TH'):.1f}")

print("\nhemi wraps everything and must not interfere")
regions = [feat("hemi", box(-20, -20, 220, 120)),
           feat("ISO", box(0, 0, 100, 100)), feat("TH", box(102.5, 0, 200, 100))]
res = T.clean_lines(regions, "name", loop)
check("the gap is still found with hemi ON",
      set(res["filled"]) == {"ISO", "TH"}, str(res["filled"]))
check("hemi claims none of it", "hemi" not in res["filled"])
check("hemi is byte-identical", abs(area_of(res, "hemi") - 240 * 140) < 0.01)

print("\na sliver PART of a real region")
# TH grew a detached 2px hairline on ISO's side of the border.
th = box(103, 0, 200, 100).union(box(98, 20, 100, 80))
regions = [feat("ISO", box(0, 0, 100, 100).difference(box(98, 20, 100, 80))),
           feat("TH", th)]
res = T.clean_lines(regions, "name", loop)
check("the stray part is removed from TH",
      res["removed"] and res["removed"][0]["region"] == "TH",
      str(res["removed"]))
check("TH itself survives", "TH" in names_of(res) and res["deleted"] == [])
check("the hole it left is filled -- the plane tiles whole again",
      abs(area_of(res, "ISO") + area_of(res, "TH") - 200 * 100) < 5.0,
      f"{area_of(res, 'ISO') + area_of(res, 'TH'):.1f}")

print("\nwhat it must NEVER touch")
regions = [feat("ISO", box(0, 0, 100, 100)), feat("TH", box(100, 0, 200, 100))]
try:
    T.clean_lines(regions, "name", loop)
    check("a clean file raises rather than inventing work", False)
except ValueError as e:
    check("a clean file raises rather than inventing work", True, str(e)[:60])

# the lasso clips only a thin strip of ISO's edge -- induced thinness
thin_loop = [(95, -10), (107, -10), (107, 110), (95, 110)]
regions = [feat("ISO", box(0, 0, 100, 100)), feat("TH", box(103, 0, 200, 100))]
res = T.clean_lines(regions, "name", thin_loop)
# ISO may legitimately GAIN (it takes its half of the hairline gap the loop
# crossed); what it must never do is LOSE ground or appear in `removed`.
check("a healthy region clipped thin by the loop is not shaved",
      area_of(res, "ISO") >= 100 * 100 - 0.01
      and not any(r["region"] == "ISO" for r in res["removed"]),
      f"{area_of(res, 'ISO'):.1f}")

# a drawn damage shape is thin on purpose
regions = [feat("ISO", box(0, 0, 100, 100)), feat("TH", box(103, 0, 200, 100)),
           feat("separation.1", box(110, 10, 113, 90))]
res = T.clean_lines(regions, "name", loop)
check("a thin damage shape is never taken",
      "separation.1" in names_of(res)
      and abs(area_of(res, "separation.1") - 3 * 80) < 0.01
      and not any(r["region"] == "separation.1" for r in res["removed"]))
check("...and never receives ground", "separation.1" not in res["filled"])

print("\nreporting")
regions = [feat("ISO", box(0, 0, 100, 100)),
           feat("sliv", box(100, 0, 103, 100)),
           feat("TH", box(103, 0, 200, 100))]
res = T.clean_lines(regions, "name", loop)
check("the freed ground is returned for the preview",
      shape(res["freed"]).area > 0 and res["area"] > 0)
check("removed entries carry region, area and parts",
      all(set(r) == {"region", "area", "parts"} for r in res["removed"]))

print("\n" + (f"{len(fails)} FAILED: " + "; ".join(fails) if fails else "all checks passed"))
sys.exit(1 if fails else 0)

"""Several region files picked together keep their regions SEPARATE.

/regions/load-multi used to concatenate the files' features, and two regions
carrying one name are ONE region in this app -- every edit acts on all of its
parts. So ticking merged.regions AND merged.regions_edited, which hold the same
23 names, gave 23 two-part regions instead of 46 separate ones: they listed as
pairs and every edit hit both files' copies at once.

Offline: concat_region_files is pure, so none of this needs a server.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "backend"))

from shapely.geometry import box, mapping   # noqa: E402

import topology as T                        # noqa: E402

fails = []


def check(label, cond, detail=""):
    print(f"  {'ok  ' if cond else 'FAIL'}  {label}{('  ' + detail) if detail else ''}")
    if not cond:
        fails.append(label)


def feat(name, geom, **extra):
    props = {"name": name}
    props.update(extra)
    return {"type": "Feature", "properties": props, "geometry": mapping(geom)}


def names_of(res):
    return [f["properties"]["name"] for f in res["features"]]


# two takes on the same section, the way the checklist offers them
FILE_A = [feat("ISO", box(0, 0, 100, 100)), feat("TH", box(100, 0, 200, 100))]
FILE_B = [feat("ISO", box(0, 0, 100, 100)), feat("TH", box(100, 0, 200, 100))]

print("\none file alone is untouched")
res = T.concat_region_files([("merged.regions", FILE_A)], "name")
check("every name is kept as it was", names_of(res) == ["ISO", "TH"], str(names_of(res)))
check("nothing is reported as renamed", res["renames"] == [])

print("\ntwo files that share every name")
res = T.concat_region_files([("merged.regions", FILE_A),
                             ("merged.regions_edited", FILE_B)], "name")
got = names_of(res)
check("all four regions are there", len(got) == 4, str(got))
check("every name is now distinct", len(set(got)) == 4, str(got))
check("the FIRST file keeps its names untouched", got[:2] == ["ISO", "TH"], str(got[:2]))
check("the second says which file it came from",
      got[2:] == ["ISO (merged.regions_edited)", "TH (merged.regions_edited)"],
      str(got[2:]))
check("the renames are reported", len(res["renames"]) == 2, str(res["renames"]))

print("\n...so nothing is a multi-part region by accident")
# This is the behaviour that was actually wrong: one name over two features is
# one body, and every operation acts on the whole of it.
parts = {}
for n in got:
    parts[n] = parts.get(n, 0) + 1
check("no name is carried by two features", max(parts.values()) == 1, str(parts))
idx = T._index_all(res["features"], "name")
check("...and the topology index agrees",
      all(len(v) == 1 for v in idx.values()), str({k: len(v) for k, v in idx.items()}))
body = T._body(res["features"], idx["ISO"])
check("editing ISO reaches one 100x100 region, not both files' copies",
      abs(body.area - 100 * 100) < 1e-6, f"{body.area:.1f}")

print("\ndamage keeps its numbering convention instead")
# A damage shape is `<designation>.<n>`, so a collision takes the next free
# number rather than gaining a filename.
A = [feat("separation.1", box(0, 0, 10, 10))]
B = [feat("separation.1", box(50, 50, 60, 60))]
res = T.concat_region_files([("one", A), ("two", B)], "name")
got = names_of(res)
check("the second separation is renumbered, not suffixed",
      got == ["separation.1", "separation.2"], str(got))

print("\nthree files, and a name that collides twice")
C = [feat("ISO", box(0, 0, 1, 1))]
res = T.concat_region_files([("a", [feat("ISO", box(0, 0, 1, 1))]),
                            ("b", [feat("ISO", box(0, 0, 1, 1))]),
                            ("c", C)], "name")
got = names_of(res)
check("each gets its own name", len(set(got)) == 3, str(got))
check("named after the file each came from",
      got == ["ISO", "ISO (b)", "ISO (c)"], str(got))

# ...and two files with the SAME stem cannot collide either
res = T.concat_region_files([("a", [feat("ISO", box(0, 0, 1, 1))]),
                            ("dup", [feat("ISO", box(0, 0, 1, 1))]),
                            ("dup", [feat("ISO", box(0, 0, 1, 1))])], "name")
got = names_of(res)
check("two files with one stem still end up distinct", len(set(got)) == 3, str(got))

print("\nthe other properties survive the rename")
A = [feat("ISO", box(0, 0, 10, 10), classification={"name": "ISO", "colorRGB": -16711936})]
B = [feat("ISO", box(0, 0, 10, 10), classification={"name": "ISO", "colorRGB": -65536})]
res = T.concat_region_files([("a", A), ("b", B)], "name")
second = res["features"][1]["properties"]
check("the colour is kept", second["classification"]["colorRGB"] == -65536,
      str(second["classification"]))
check("...and classification.name is renamed with it, so the two agree",
      second["classification"]["name"] == second["name"] == "ISO (b)",
      str(second["classification"]["name"]))

print("\nthe inputs are not mutated")
orig = [feat("ISO", box(0, 0, 10, 10))]
before = orig[0]["properties"]["name"]
T.concat_region_files([("a", [feat("X", box(0, 0, 1, 1))]), ("b", orig)], "name")
check("the caller's features are left alone",
      orig[0]["properties"]["name"] == before, orig[0]["properties"]["name"])

print("\n" + (f"{len(fails)} FAILED: " + "; ".join(fails) if fails else "all checks passed"))
sys.exit(1 if fails else 0)

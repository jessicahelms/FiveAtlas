"""Region files picked together are tagged with the file they came from, so the
sidebar can list them under a heading each instead of running them together.

Names are left exactly as the files have them. The tag is FiveAtlas's own
bookkeeping, so it must never reach a file the app hands out -- that is checked
here too, because a `_srcFile` turning up in an exported GeoJSON would be in
somebody's data for good.

One thing a heading cannot separate, and the last section pins it rather than
pretending otherwise: a name used by two of the files is still ONE region here,
because every edit acts on all the parts of a name. `shared` reports those so
the UI can say so.

Offline: concat_region_files and strip_internal_props are both pure.
"""
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "backend"))

# config fixes WORKDIR the moment it loads and geo pulls it in, so this has to be
# set BEFORE the first backend import or the real working folder is the one used.
_SCRATCH = tempfile.mkdtemp(prefix="multiload_")
os.environ["ATLAS_WORKDIR"] = _SCRATCH

import config                                   # noqa: E402

if str(config.WORKDIR) != _SCRATCH:
    raise SystemExit(f"refusing to run: WORKDIR is {config.WORKDIR}, not the scratch dir")

import geo                                      # noqa: E402
import topology as T                            # noqa: E402

from shapely.geometry import box, mapping       # noqa: E402

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


def srcs_of(res):
    return [(f["properties"] or {}).get("_srcFile") for f in res["features"]]


FILE_A = [feat("ISO", box(0, 0, 100, 100)), feat("TH", box(100, 0, 200, 100))]
FILE_B = [feat("ISO", box(0, 0, 100, 100)), feat("TH", box(100, 0, 200, 100))]
FILE_C = [feat("CP", box(0, 200, 50, 250))]        # a name neither other file has

print("\none file")
res = T.concat_region_files([("merged.regions", FILE_A)], "name")
check("names are untouched", names_of(res) == ["ISO", "TH"], str(names_of(res)))
check("each feature says which file it came from",
      srcs_of(res) == ["merged.regions"] * 2, str(srcs_of(res)))
check("the file is listed once", res["sources"] == ["merged.regions"], str(res["sources"]))
check("no name is shared", res["shared"] == [])

print("\ntwo files whose regions do not overlap by name")
res = T.concat_region_files([("a", FILE_A), ("c", FILE_C)], "name")
check("nothing is renamed", names_of(res) == ["ISO", "TH", "CP"], str(names_of(res)))
check("the tags separate them", srcs_of(res) == ["a", "a", "c"], str(srcs_of(res)))
check("still no name is shared", res["shared"] == [], str(res["shared"]))

print("\ntwo files that share every name")
res = T.concat_region_files([("merged.regions", FILE_A),
                             ("merged.regions_edited", FILE_B)], "name")
check("names are left exactly as the files have them",
      names_of(res) == ["ISO", "TH", "ISO", "TH"], str(names_of(res)))
check("the tags are what tells them apart",
      srcs_of(res) == ["merged.regions", "merged.regions",
                       "merged.regions_edited", "merged.regions_edited"],
      str(srcs_of(res)))
check("both files are listed, in the order picked",
      res["sources"] == ["merged.regions", "merged.regions_edited"], str(res["sources"]))
check("the shared names are reported", res["shared"] == ["ISO", "TH"], str(res["shared"]))

print("\nfeature ORDER is preserved, because it is the draw order")
res = T.concat_region_files([("a", FILE_A), ("c", FILE_C), ("b", FILE_B)], "name")
check("the files stay in the order they were picked",
      srcs_of(res) == ["a", "a", "c", "b", "b"], str(srcs_of(res)))

print("\nwhat a heading cannot fix, and does not pretend to")
# Pinned deliberately: a name over two features is ONE body here, so an edit to
# ISO reaches both files' copies. The row in the sidebar says so.
res = T.concat_region_files([("a", FILE_A), ("b", FILE_B)], "name")
idx = T._index_all(res["features"], "name")
check("a shared name is still one region in two parts",
      len(idx["ISO"]) == 2, str({k: len(v) for k, v in idx.items()}))
check("...and that is exactly what `shared` warned about",
      set(res["shared"]) == {nm for nm, ks in idx.items() if len(ks) > 1},
      str(res["shared"]))

print("\nthe caller's features are not mutated")
mine = [feat("ISO", box(0, 0, 10, 10))]
T.concat_region_files([("a", mine)], "name")
check("no tag is left on the input", "_srcFile" not in mine[0]["properties"],
      str(mine[0]["properties"]))

print("\nand the tag never reaches a file the app hands out")
res = T.concat_region_files([("a", FILE_A), ("b", FILE_B)], "name")
fc = {"type": "FeatureCollection", "features": res["features"],
      "_orientation": {"rot": 90, "flipH": False, "flipV": False}}
clean = geo.strip_internal_props(fc)
check("_srcFile is gone from every feature",
      all("_srcFile" not in (f["properties"] or {}) for f in clean["features"]),
      str([list((f["properties"] or {}).keys()) for f in clean["features"][:1]]))
check("the names are still there", names_of(clean) == ["ISO", "TH", "ISO", "TH"],
      str(names_of(clean)))
check("collection-level members are kept",
      clean["_orientation"]["rot"] == 90 and clean["type"] == "FeatureCollection")
check("the collection it was given is untouched",
      all("_srcFile" in (f["properties"] or {}) for f in fc["features"]))

# a file that never had the tag must come through byte-identical
plain = {"type": "FeatureCollection",
         "features": [feat("ISO", box(0, 0, 10, 10),
                           classification={"name": "ISO", "colorRGB": -65536})]}
out = geo.strip_internal_props(plain)
check("an untagged collection is unchanged", out == plain, str(out))

print("\n" + (f"{len(fails)} FAILED: " + "; ".join(fails) if fails else "all checks passed"))
sys.exit(1 if fails else 0)

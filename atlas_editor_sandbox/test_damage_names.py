"""A damage shape must stay a damage shape through a split and a merge.

Damage is recognised by NAME. Two tools skip a feature only when
damage.is_damage() says so -- clean_lines and section_outline -- so a shape whose
name stops resolving stops being protected, and the next tidy-up deletes
something somebody drew on purpose.

The app used to make exactly such names itself: split_region called the second
piece '<name> 2', so splitting separation.7 gave 'separation.7 2', and merging
separation.1 with separation.2 gave 'separation.'. Neither resolved.

Entirely offline and entirely synthetic: every shape is built here, there is no
server, no port and no dataset folder. The region names used as negative cases
are the real 23 from the DNMT3A set, as strings -- nothing is read from the share.

Usage:  python test_damage_names.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend"))

import damage as DMG                                 # noqa: E402
import topology as T                                 # noqa: E402

fails = []


def check(label, cond, detail=""):
    print(f"  {'ok  ' if cond else 'FAIL'}  {label}{('  ' + detail) if detail else ''}")
    if not cond:
        fails.append(label)


def section(t):
    print(f"\n{t}")


def box(nm, x0, y0, x1, y1):
    return {"type": "Feature", "properties": {"name": nm},
            "geometry": {"type": "Polygon", "coordinates": [[
                [x0, y0], [x1, y0], [x1, y1], [x0, y1], [x0, y0]]]}}


# ---------------------------------------------------------------------------
section("the names the app makes for itself still resolve")

# What a split and a merge used to produce. The number is not recoverable from
# a doubly-numbered name, so None is right -- canonicalise() hands out a free one.
for nm, want in (("separation.7", ("separation", 7)),
                 ("separation.7 2", ("separation", None)),
                 ("separation.", ("separation", None)),
                 ("separation.7.2", ("separation", None)),
                 ("separation.7 2 3", ("separation", None)),
                 ("Separation 2", ("separation", 2)),
                 ("separation_3", ("separation", 3)),
                 ("VoidSmall.2", ("voidsmall", 2)),
                 ("voidsmall", ("voidsmall", None)),
                 ("Small void.4", ("voidsmall", 4))):
    got = DMG.parse_name(nm)
    check(f"{nm!r} -> {want}", got == want, f"got {got}")

section("ordinary anatomy is still left alone")

# The real 23 of the DNMT3A set, plus the shapes the audit checked for
# false positives. A trailing number is NOT a damage marker.
ANATOMY = ["hemi", "CP", "CTXsp", "dft", "HPF", "HY", "ISO", "lft", "OLF", "opt",
           "PAL.2", "RSP", "SSp", "TH", "V3", "VL.1", "VL.2", "PAL.1", "PAL.3",
           "sAMY.1", "sAMY.2", "sAMY.3", "sAMY.4",
           "Fiber Tracts 2", "TH 4", "RSP-1", "ISO2", "hemi.1", "separations.1",
           "CP.3", "SSp.1", "", "   "]
wrong = [n for n in ANATOMY if DMG.is_damage(n)]
check("none of the 23 real region names reads as damage", not wrong, repr(wrong))
check("nor do the near-misses the audit checked", not wrong)

# ---------------------------------------------------------------------------
section("splitting a damage shape")

fs = [box("hemi", 0, 0, 4000, 4000), box("separation.7", 1000, 1000, 1400, 1400)]
r = T.split_region(fs, "name", "separation.7", [[1200, 900], [1200, 1500]])
check("both pieces are still damage",
      all(DMG.is_damage(n) for n in r["names"]), repr(r["names"]))
check("the new piece keeps the designation",
      DMG.parse_name(r["names"][1])[0] == "separation", repr(r["names"][1]))
check("and has a number of its own",
      DMG.parse_name(r["names"][1])[1] is not None, repr(r["names"][1]))

# Numbering is global per designation, so taken numbers are skipped.
fs = [box("hemi", 0, 0, 4000, 4000),
      box("separation.1", 200, 200, 400, 400),
      box("separation.2", 500, 200, 700, 400),
      box("separation.7", 1000, 1000, 1400, 1400)]
r = T.split_region(fs, "name", "separation.7", [[1200, 900], [1200, 1500]])
check("a number already in the file is not reused",
      r["names"][1] not in ("separation.1", "separation.2", "separation.7"),
      repr(r["names"][1]))
check("...and the piece is still damage", DMG.is_damage(r["names"][1]))

# An ordinary region is unchanged: it still gets '<name> 2'.
fs = [box("hemi", 0, 0, 4000, 4000), box("TH", 1000, 1000, 1400, 1400)]
r = T.split_region(fs, "name", "TH", [[1200, 900], [1200, 1500]])
check("an ordinary region still splits to '<name> 2'",
      r["names"] == ["TH", "TH 2"], repr(r["names"]))

# ---------------------------------------------------------------------------
section("merging damage shapes")

fs = [box("hemi", 0, 0, 4000, 4000), box("separation.4", 1000, 1000, 1400, 1400),
      box("separation.2", 1380, 1000, 1800, 1400)]
r = T.merge_regions(fs, "name", ["separation.4", "separation.2"])
check("the merged shape is still damage", DMG.is_damage(r["name"]), repr(r["name"]))
check("and keeps the lowest number, which the YAML already points at",
      r["name"] == "separation.2", repr(r["name"]))

# Two designations: no common damage name, so the first one is kept -- still damage.
fs = [box("hemi", 0, 0, 4000, 4000), box("separation.1", 1000, 1000, 1400, 1400),
      box("bubble.3", 1380, 1000, 1800, 1400)]
r = T.merge_regions(fs, "name", ["separation.1", "bubble.3"])
check("merging two designations still leaves a damage name",
      DMG.is_damage(r["name"]), repr(r["name"]))

# Ordinary anatomy keeps the behaviour it had.
fs = [box("hemi", 0, 0, 4000, 4000), box("Fiber Tracts 1", 1000, 1000, 1400, 1400),
      box("Fiber Tracts 2", 1380, 1000, 1800, 1400)]
r = T.merge_regions(fs, "name", ["Fiber Tracts 1", "Fiber Tracts 2"])
check("ordinary anatomy still merges to its base name",
      r["name"] == "Fiber Tracts", repr(r["name"]))
check("...and does not become damage", not DMG.is_damage(r["name"]))

# ---------------------------------------------------------------------------
section("the clean-up tools no longer delete them")

LOOP = [[900, 900], [1300, 900], [1300, 1100], [900, 1100]]
for nm in ("separation.1", "separation.7 2", "separation.", "separation.7.2"):
    fs = [box("hemi", 0, 0, 4000, 4000), box(nm, 1000, 1000, 1200, 1004)]
    try:
        res = T.clean_lines(fs, "name", LOOP, width=20.0)
        removed = [d.get("region") for d in (res.get("removed") or [])]
    except ValueError:
        removed = []                       # refused outright: nothing to clean
    check(f"clean_lines leaves {nm!r} alone", nm not in removed, repr(removed))

# ...but a genuinely thin ORDINARY region is still cleanable, so this has not
# just turned the tool off.
fs = [box("hemi", 0, 0, 4000, 4000), box("PAL.1", 1000, 1000, 1200, 1004)]
try:
    res = T.clean_lines(fs, "name", LOOP, width=20.0)
    removed = [d.get("region") for d in (res.get("removed") or [])]
except ValueError:
    removed = []
check("a thin ordinary region is still removed", "PAL.1" in removed, repr(removed))

# The section outline must not swallow damage either.
for nm in ("separation.7 2", "separation.", "separation.7.2"):
    fs = [box("hemi", 0, 0, 4000, 4000), box("TH", 500, 500, 1500, 1500),
          box(nm, 3000, 3000, 3200, 3200)]
    try:
        res = T.section_outline(fs, "name", name="outline", radius=50.0)
        srcs = res.get("sources") or []
    except Exception as e:
        srcs = [f"error: {e}"]
    check(f"section_outline leaves {nm!r} out", nm not in srcs, repr(srcs))

# ---------------------------------------------------------------------------
section("names already saved in people's files can be repaired")

fs = [box(n, 0, 0, 90, 90) for n in ("separation.7", "separation.7 2", "separation.")]
res = DMG.canonicalise(fs, "name")
got = [f["properties"]["name"] for f in res["features"]]
check("canonicalise gives every shape a proper id",
      all(DMG.parse_name(n)[1] is not None for n in got), repr(got))
check("the ids are distinct", len(set(got)) == 3, repr(got))
check("and the renames are reported, so `voids` can follow",
      {r["from"] for r in res["renames"]} == {"separation.7 2", "separation."},
      repr(res["renames"]))
check("the already-canonical one keeps its number",
      got[0] == "separation.7", repr(got[0]))

# ---------------------------------------------------------------------------
print()
if fails:
    print(f"{len(fails)} FAILED: " + "; ".join(fails))
    sys.exit(1)
print("all checks passed")

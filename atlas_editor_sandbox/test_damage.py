"""damage.py — name parsing, overlap assignment, TSV. Runs entirely offline.

No live backend: these are pure functions, and a test in this project has already
damaged the real working copy once by pointing at the running server.
"""
import sys
sys.path.insert(0, r"C:\Users\FIVE\source\repos\Jess\atlas_editor\backend")

from shapely.geometry import box, mapping   # noqa: E402
import damage as D                          # noqa: E402

fails = []


def check(label, cond, detail=""):
    print(f"  {'ok  ' if cond else 'FAIL'}  {label}{('  ' + detail) if detail else ''}")
    if not cond:
        fails.append(label)


def feat(name, geom):
    return {"type": "Feature", "properties": {"name": name}, "geometry": mapping(geom)}


# --- names ----------------------------------------------------------------------
check("plain tag + number", D.parse_name("separation.7") == ("separation", 7))
check("display name resolves", D.parse_name("Small void.2") == ("voidsmall", 2))
check("spaces / case / underscores all fold",
      D.parse_name("VoidSmall_3") == ("voidsmall", 3)
      and D.parse_name("small void 4") == ("voidsmall", 4)
      and D.parse_name("Internal Fold.1") == ("foldinternal", 1))
check("an ordinary region is NOT damage",
      D.parse_name("Isocortex") == (None, None) and not D.is_damage("hemi"))
check("a region that merely ends in a number is not damage",
      D.parse_name("Fiber Tracts 2") == (None, None))
check("every designation round-trips through its own display name",
      all(D.parse_name(f"{lbl}.1")[0] == tag for tag, (lbl, _) in D.DESIGNATIONS.items()))

# --- numbering is global per designation ----------------------------------------
existing = ["separation.1", "separation.3", "voidlarge.1", "Isocortex"]
check("next number fills the first gap", D.next_number(existing, "separation") == 2)
check("unused designation starts at 1", D.next_number(existing, "bubble") == 1)
check("numbering ignores other designations", D.next_number(existing, "voidlarge") == 2)

# --- splitting ------------------------------------------------------------------
feats = [feat("ISO", box(0, 0, 100, 100)), feat("separation.1", box(10, 10, 20, 20)),
         feat("TH", box(100, 0, 200, 100))]
regs, dmg = D.split_features(feats)
check("damage shapes split out from anatomy",
      [f["properties"]["name"] for f in regs] == ["ISO", "TH"]
      and [f["properties"]["name"] for f in dmg] == ["separation.1"])

# --- assignment -----------------------------------------------------------------
regions = [feat("ISO", box(0, 0, 100, 100)), feat("TH", box(100, 0, 200, 100))]
shapes = [
    feat("separation.1", box(10, 10, 30, 30)),      # wholly in ISO
    feat("voidlarge.1", box(90, 40, 130, 60)),      # straddles ISO and TH
    # grazes ISO's top edge by 0.1 of its 300-tall body, i.e. 0.03% of the shape,
    # and reaches neither region otherwise -- the sliver-touch case
    feat("bubble.1", box(10, 99.9, 20, 400)),
    feat("separation.2", box(500, 500, 510, 510)),  # nowhere
]
a = D.assign(regions, shapes)
by = {s["name"]: s for s in a["shapes"]}
check("shape inside one region -> that region", by["separation.1"]["regions"] == ["ISO"])
check("straddling shape goes in BOTH regions (per the SOP)",
      sorted(by["voidlarge.1"]["regions"]) == ["ISO", "TH"], str(by["voidlarge.1"]["regions"]))
check("straddling shape still names a dominant one",
      by["voidlarge.1"]["dominant"] in ("ISO", "TH") and by["voidlarge.1"]["spans"])
check("hairline graze is not assigned, but IS reported",
      by["bubble.1"]["regions"] == [] and by["bubble.1"]["marginal"],
      f"marginal={by['bubble.1']['marginal']}")
check("shape overlapping nothing is flagged, not dropped",
      [s["name"] for s in a["unassigned"]] == ["bubble.1", "separation.2"],
      str([s["name"] for s in a["unassigned"]]))
check("region damage tags are the designations that reached it",
      a["regions"]["ISO"]["damage"] == ["separation", "voidlarge"], str(a["regions"]["ISO"]))
check("region voids list the shape ids",
      a["regions"]["ISO"]["voids"] == ["separation.1", "voidlarge.1"])
check("TH only gets the shape that reaches it",
      a["regions"]["TH"] == {"damage": ["voidlarge"], "voids": ["voidlarge.1"]})

# --- TSV ------------------------------------------------------------------------
notes = {
    "ISO": {"region": "Isocortex", "annotator": "Emma Jones",
            "notes": "line of separations\tacross the section", "enclaves": ""},
    "TH": {"region": "Thalamus", "annotator": "Jane-Valeriane Kim Boua"},
}
# transcripts is never drawn, so it can only arrive as a per-region tag
out = D.tsv(a, notes, region_order=["ISO", "TH"], extra_damage={"TH": ["transcripts"]})
lines = out.split("\n")
cells = [ln.split("\t") for ln in lines]
check("header row", cells[0] == D.TSV_COLUMNS)
check("one row per region", len(lines) == 3)
check("every row has the same column count",
      all(len(c) == len(D.TSV_COLUMNS) for c in cells))
check("drawn damage reaches the row", cells[1][3] == "separation,voidlarge")
check("undrawn designation merges in with the drawn ones",
      cells[2][3] == "transcripts,voidlarge", cells[2][3])
check("annotator and full name carry through from the notes",
      cells[1][1] == "Isocortex" and cells[1][2] == "Emma Jones")
check("a tab inside a note cannot break the row apart",
      "\t" not in cells[1][6] and cells[1][6].startswith("line of separations"))
check("a missing notes entry leaves blanks, not 'None'",
      cells[2][6] == "" and cells[2][5] == "")

# --- canonicalising names -------------------------------------------------------
messy = [
    feat("separation.1", box(0, 0, 1, 1)),      # already canonical -> untouched
    feat("Separation 2", box(1, 0, 2, 1)),      # typed by hand -> separation.2
    feat("voidsmall", box(2, 0, 3, 1)),         # no number at all -> voidsmall.1
    feat("Small Void_1", box(3, 0, 4, 1)),      # wants 1, which the line above took
    feat("Isocortex", box(4, 0, 5, 1)),         # not damage -> never renamed
]
c = D.canonicalise(messy)
got = [f["properties"]["name"] for f in c["features"]]
check("already-canonical names are left alone", got[0] == "separation.1")
check("a hand-typed name is canonicalised", got[1] == "separation.2", got[1])
check("a bare designation gets a number", got[2] == "voidsmall.1", got[2])
check("a clashing number is moved, not overwritten", got[3] == "voidsmall.2", got[3])
check("a collision is reported, not silent",
      [x["name"] for x in c["collisions"]] == ["Small Void_1"], str(c["collisions"]))
check("an ordinary region is never renamed", got[4] == "Isocortex")
check("the rename map covers exactly what moved",
      [(r["from"], r["to"]) for r in c["renames"]]
      == [("Separation 2", "separation.2"), ("voidsmall", "voidsmall.1"),
          ("Small Void_1", "voidsmall.2")], str(c["renames"]))
check("canonicalising twice changes nothing more",
      [f["properties"]["name"] for f in D.canonicalise(c["features"])["features"]] == got)
check("voids are rewritten through the rename map so the YAML still matches",
      D.apply_renames(["Separation 2", "separation.1", "voidsmall"], c["renames"])
      == ["separation.2", "separation.1", "voidsmall.1"])

print()
print("FAILED:", fails if fails else "none")
sys.exit(1 if fails else 0)

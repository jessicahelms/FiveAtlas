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
# mode="all" is the literal SOP reading; mode="dominant" is what the lab chose on
# 2026-08-07. Both are tested because both are supported and they write different
# YAML -- see the dominant block below.
a = D.assign(regions, shapes, mode="all")
by = {s["name"]: s for s in a["shapes"]}
check("shape inside one region -> that region", by["separation.1"]["regions"] == ["ISO"])
check("straddling shape goes in BOTH regions (per the SOP)",
      sorted(by["voidlarge.1"]["regions"]) == ["ISO", "TH"], str(by["voidlarge.1"]["regions"]))
check("straddling shape still names a dominant one",
      by["voidlarge.1"]["dominant"] in ("ISO", "TH") and by["voidlarge.1"]["spans"])
check("mode=all never asks -- every overlapping region is recorded",
      a["needsChoice"] == [])
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

# --- dominant, and the question it raises ---------------------------------------
# The lab's call: a shape belongs to the ONE region holding most of it, and one
# that reaches into a second is a question for the annotator, not for geometry.
dom = D.assign(regions, shapes, mode="dominant")
byd = {s["name"]: s for s in dom["shapes"]}
check("dominant is the default", D.assign(regions, shapes)["mode"] == "dominant")
check("a shape inside one region is unchanged by the mode",
      byd["separation.1"]["regions"] == ["ISO"])
check("a straddling shape goes to the region holding most of it",
      byd["voidlarge.1"]["regions"] == ["TH"], str(byd["voidlarge.1"]["regions"]))
check("...and it is flagged for the annotator rather than settled quietly",
      byd["voidlarge.1"]["needsChoice"]
      and [s["name"] for s in dom["needsChoice"]] == ["voidlarge.1"])
check("both regions are offered, most-of-it first",
      byd["voidlarge.1"]["candidates"] == ["TH", "ISO"], str(byd["voidlarge.1"]["candidates"]))
check("a shape in one region is never a question",
      not byd["separation.1"]["needsChoice"] and not byd["separation.1"]["spans"])

# The answer rides on the shape, so it survives a save and the next annotator.
answered = [dict(f, properties={**f["properties"], D.CHOICE_PROP: ["ISO", "TH"]})
            if f["properties"]["name"] == "voidlarge.1" else f for f in shapes]
ch = D.choices_from(answered)
check("the answer is read back off the shape", ch == {"voidlarge.1": ["ISO", "TH"]})
ans = D.assign(regions, answered, mode="dominant", choices=ch)
bya = {s["name"]: s for s in ans["shapes"]}
check("an answered shape is recorded where the annotator said",
      sorted(bya["voidlarge.1"]["regions"]) == ["ISO", "TH"])
check("...and is not asked about again", ans["needsChoice"] == [])
check("the answer reaches both regions' voids",
      "voidlarge.1" in ans["regions"]["ISO"]["voids"]
      and "voidlarge.1" in ans["regions"]["TH"]["voids"])
one = D.assign(regions, answered, mode="dominant", choices={"voidlarge.1": ["ISO"]})
check("choosing the smaller side overrides the geometry",
      {s["name"]: s["regions"] for s in one["shapes"]}["voidlarge.1"] == ["ISO"])
stale = D.assign(regions, shapes, mode="dominant", choices={"separation.1": ["TH"]})
bys = {s["name"]: s for s in stale["shapes"]}
check("an answer naming a region the shape no longer reaches is dropped, not written",
      bys["separation.1"]["regions"] == ["ISO"] and bys["separation.1"]["staleChoice"] == ["TH"])

# --- containment: the more specific region wins ---------------------------------
# `hemi` wraps every region, so it holds 100% of every shape and would be offered
# against the region actually drawn in. But containment is a RELATION, not a label
# on a region: on the real file ISO contains SSp and RSP while still being an
# ordinary region people draw in, so it can only be set aside for a shape that
# lands in SSp or RSP too.
whole = feat("hemi", box(-10, -10, 210, 110))
inner = feat("SSp", box(0, 0, 50, 100))          # 50% of ISO, wholly inside it
with_hemi = [whole] + regions
check("a region that swallows another is spotted",
      D.containment(with_hemi) == {"hemi": ["ISO", "TH"]}, str(D.containment(with_hemi)))
check("side-by-side neighbours contain nothing", D.containment(regions) == {})
check("two coincident copies of one region do not swallow each other",
      D.containment([feat("ISO", box(0, 0, 100, 100)),
                     feat("ISO2", box(0, 0, 100, 100))]) == {})
hemi_a = D.assign(with_hemi, shapes, mode="dominant")
byh = {s["name"]: s for s in hemi_a["shapes"]}
check("the containment relation is reported, not just a flat list",
      hemi_a["containment"] == {"hemi": ["ISO", "TH"]} and hemi_a["containers"] == ["hemi"])
check("a shape drawn in ISO is recorded in ISO, not the region enclosing it",
      byh["separation.1"]["regions"] == ["ISO"], str(byh["separation.1"]["regions"]))
# It keeps what nothing more specific claimed — bubble.1 only grazes ISO (0.03%,
# below the threshold) and otherwise sits in hemi's own ground. Recording it
# against the outline says something true; calling it homeless does not.
check("the enclosing region keeps only what no more specific region claimed",
      hemi_a["regions"]["hemi"]["voids"] == ["bubble.1"],
      str(hemi_a["regions"]["hemi"]["voids"]))
check("the shapes inside real regions do NOT also land in it",
      "separation.1" not in hemi_a["regions"]["hemi"]["voids"]
      and "voidlarge.1" not in hemi_a["regions"]["hemi"]["voids"])
check("the shape says which region was set aside for it",
      byh["separation.1"]["enclosing"] == ["hemi"])
check("an enclosing region does not turn every shape into a question",
      not byh["separation.1"]["needsChoice"])

# The part a blanket "skip containers" rule would get wrong: a shape in ISO's own
# ground, nowhere near SSp, must still be recorded against ISO.
nested = [whole, feat("ISO", box(0, 0, 100, 100)), inner, feat("TH", box(100, 0, 200, 100))]
own = [feat("separation.9", box(70, 10, 90, 30))]     # in ISO, outside SSp
nest_a = D.assign(nested, own, mode="dominant")
check("ISO contains SSp and is still an ordinary region",
      D.containment(nested).get("ISO") == ["SSp"], str(D.containment(nested)))
check("a shape in the containing region's OWN ground still lands there",
      nest_a["shapes"][0]["regions"] == ["ISO"], str(nest_a["shapes"][0]["regions"]))
check("...while a shape inside the nested region goes to the nested one",
      D.assign(nested, [feat("separation.8", box(10, 10, 30, 30))], mode="dominant")
      ["shapes"][0]["regions"] == ["SSp"])
check("no shape is left with nowhere to go", nest_a["unassigned"] == [])

# What the rule is actually for. The tie-break alone stops the enclosing region
# winning outright — both hold 100% of the shape, and the smaller takes the tie —
# but it does nothing about it being a *candidate*, which turns every shape into a
# question and hands it every void in mode="all".
raw = D.assign(with_hemi, shapes, mode="dominant", specific_wins=False)
byr = {s["name"]: s for s in raw["shapes"]}
check("a tie on fraction goes to the more specific region, never the one enclosing it",
      byr["separation.1"]["dominant"] == "ISO", str(byr["separation.1"]["candidates"]))
check("without the rule every shape becomes a question",
      byr["separation.1"]["needsChoice"] and len(raw["needsChoice"]) == 2,
      str([s["name"] for s in raw["needsChoice"]]))
check("without the rule the enclosing region collects every void — including the "
      "shape that reaches no real region at all",
      sorted(D.assign(with_hemi, shapes, mode="all", specific_wins=False)
             ["regions"]["hemi"]["voids"]) == ["bubble.1", "separation.1", "voidlarge.1"])
check("a shape outside every region, outline included, is still flagged",
      [s["name"] for s in hemi_a["unassigned"]] == ["separation.2"],
      str([s["name"] for s in hemi_a["unassigned"]]))

# --- the SmartSheet cell --------------------------------------------------------
# The sheet's Damage column is a multi-select dropdown: one cell, several chips,
# by their DISPLAY names. `Done` rides in the same cell without being damage.
print()
extras = {"ISO": ["cutoff", "transcripts"], "TH": ["separation"]}
done = {"ISO": True}
cs = {c["region"]: c for c in D.cells(a, extras, done)}
check("a region with damage gets a box", set(cs) == {"ISO", "TH"}, str(sorted(cs)))
check("Done comes first, then the designations",
      cs["ISO"]["values"][0] == D.DONE_LABEL, str(cs["ISO"]["values"]))
check("the chips are display names, not tags",
      "Small void" not in cs["ISO"]["values"] and "Cutoff" in cs["ISO"]["values"]
      and "cutoff" not in cs["ISO"]["values"], str(cs["ISO"]["values"]))
check("drawn and hand-ticked damage merge into one cell",
      cs["ISO"]["values"] == ["Done", "Separation", "Large void", "Cutoff",
                              "Low/no transcripts"], str(cs["ISO"]["values"]))
check("...but the box still says which came from a shape",
      cs["ISO"]["drawn"] == ["separation", "voidlarge"]
      and cs["ISO"]["typed"] == ["cutoff", "transcripts"])
check("a region with no Done tick has no Done chip",
      D.DONE_LABEL not in cs["TH"]["values"], str(cs["TH"]["values"]))
check("the same designation drawn AND ticked appears once",
      cs["TH"]["values"].count("Separation") == 1, str(cs["TH"]["values"]))
check("a region with nothing is left out unless asked for",
      "CP" not in {c["region"] for c in D.cells(a, {}, {})}
      and "hemi" in {c["region"] for c in D.cells(
          D.assign([whole] + regions, shapes), {}, {}, include_empty=True)})
# A region with no damage at all still needs a box once it is ticked off — that
# tick is the annotator saying they went through it and found nothing.
ticked = {c["region"]: c for c in D.cells(nest_a, {}, {"TH": True})}
check("ticking Done alone is enough to get a box, with no damage in it",
      "TH" in ticked and ticked["TH"]["tags"] == []
      and ticked["TH"]["values"] == [D.DONE_LABEL], str(ticked.get("TH", {}).get("values")))

check("the default format quotes the cell, so a grid keeps it in one",
      cs["ISO"]["text"].startswith('"') and cs["ISO"]["text"].endswith('"')
      and cs["ISO"]["text"].count("\n") == len(cs["ISO"]["values"]) - 1)
check("lines format is the same values, unquoted",
      D.cell_text(["Done", "Bubble"], "lines") == "Done\nBubble")
check("comma format for a sheet that wants one",
      D.cell_text(["Done", "Bubble"], "comma") == "Done, Bubble")
check("an unknown format falls back rather than failing",
      D.cell_text(["Done"], "nonsense") == D.cell_text(["Done"], D.DEFAULT_SEPARATOR))
check("every designation's chip matches its dropdown label",
      all(D.label_of(t) == lab for t, (lab, _) in D.DESIGNATIONS.items()))

# read back off the features, the way the routes do
feats = [dict(feat("ISO", box(0, 0, 100, 100)))]
feats[0]["properties"] = {"name": "ISO", D.EXTRA_PROP: ["Cutoff", "cutoff", "nonsense"],
                          D.DONE_PROP: True}
check("hand-ticked types are read off the region, tolerating display spellings",
      D.extras_from(feats) == {"ISO": ["cutoff"]}, str(D.extras_from(feats)))
check("the Done tick is read off the region", D.done_from(feats) == {"ISO": True})
check("a region with neither is simply absent",
      D.extras_from([feat("TH", box(0, 0, 1, 1))]) == {}
      and D.done_from([feat("TH", box(0, 0, 1, 1))]) == {})

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

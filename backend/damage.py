"""Damage designations, assigning damage shapes to regions, and the SmartSheet TSV.

The lab's workflow (LabArchives, "Damage Tracking"): damage is DRAWN as shapes in a
`damage.geojson` shared per sample, each named `<designation>.<n>`. Afterwards the
annotator fills two fields per region in `annotation.notes.yaml`:

    damage:  every designation affecting the region, drawn or not
    voids:   the ids of the drawn shapes that OVERLAP the region

and ticks the same damage types by hand in a SmartSheet dropdown. This module does
the geometry and the bookkeeping so that becomes one paste instead.

Deliberately dependency-free beyond shapely, and pure: no HTTP, no file writing, no
app state. That keeps it testable offline, which matters here -- a test that points
at the running server has already damaged this project's real working copy once.
"""
from __future__ import annotations

import re

from shapely.geometry import shape
from shapely.ops import unary_union

# tag -> (display name, is it expected to be drawn?)
#
# "Drawn" comes straight from the SOP's "this damage should be annotated". It
# decides whether a designation can ever appear in `voids`: half of these can only
# ever be a tag in `damage`, because there is nothing to draw.
DESIGNATIONS = {
    "missing":      ("Missing", False),
    "voidsmall":    ("Small void", None),      # None = optional, annotator's call
    "separation":   ("Separation", True),
    "voidlarge":    ("Large void", True),
    "bubble":       ("Bubble", True),
    "foldsmall":    ("Small external fold", False),
    "foldlarge":    ("Large external fold", False),
    "foldinternal": ("Internal fold", True),
    "cutoff":       ("Cutoff", False),
    "overlap":      ("Overlap", False),
    "removal":      ("Removal", False),
    "distortion":   ("Distortion", False),
    "transcripts":  ("Low/no transcripts", False),
}

# An enclave is not damage -- it is one region fully inside another, recorded in its
# own YAML field. Kept out of DESIGNATIONS so it can never be offered as a shape.
ENCLAVE = "enclave"


def _key(s) -> str:
    """Fold a written name to its tag: case, spaces, hyphens and underscores all
    ignored, so 'Small Void', 'small_void' and 'VoidSmall' are one thing."""
    return re.sub(r"[\s_\-]+", "", str(s or "")).lower()


# Both the tag and the display name resolve to the tag.
_ALIASES = {}
for _tag, (_label, _drawn) in DESIGNATIONS.items():
    _ALIASES[_key(_tag)] = _tag
    _ALIASES[_key(_label)] = _tag
_ALIASES[_key("void small")] = "voidsmall"
_ALIASES[_key("void large")] = "voidlarge"
_ALIASES[_key("fold internal")] = "foldinternal"
_ALIASES[_key("internal fold")] = "foldinternal"
_ALIASES[_key("no transcripts")] = "transcripts"
_ALIASES[_key("low transcripts")] = "transcripts"


def alias_map():
    """Every written form that resolves to a tag, keyed by its folded spelling.

    Handed to the client so its labelling agrees with `parse_name` rather than
    re-deriving the vocabulary and drifting from it.
    """
    return dict(_ALIASES)


def parse_name(name):
    """`'separation.7'` -> `('separation', 7)`. Not a designation -> `(None, None)`.

    Tolerates what people actually type: 'Separation 7', 'separation_7',
    'VoidSmall.2'. A name that does not resolve is an ordinary region and must be
    left alone -- never guess.
    """
    raw = str(name or "").strip()
    m = re.match(r"^(.*?)[.\s_\-]*(\d+)\s*$", raw)
    stem, num = (m.group(1), int(m.group(2))) if m else (raw, None)
    tag = _ALIASES.get(_key(stem))
    return (tag, num) if tag else (None, None)


def is_damage(name) -> bool:
    return parse_name(name)[0] is not None


def format_name(tag: str, n: int) -> str:
    return f"{tag}.{n}"


def next_number(existing_names, tag: str) -> int:
    """The next free number for `tag`.

    Numbering is GLOBAL per designation across the whole damage file, not per
    region -- the lab's own data proves it (separation.4/.5/.6 in HY, .7 in lft).
    So this must be given every name in the merged damage file, not one region's.
    """
    used = {n for nm in (existing_names or [])
            for t, n in [parse_name(nm)] if t == tag and n is not None}
    n = 1
    while n in used:
        n += 1
    return n


def canonicalise(damage_features, id_prop="name"):
    """Rename damage shapes to `<tag>.<n>`.

    Names are typed by hand in Xenium Explorer, so a real file arrives with
    'Separation 2', 'separation_3' and a bare 'voidsmall' side by side. Tidying
    them is safe only if the caller updates `voids` in the same breath — the YAML
    lists shapes BY NAME, so a rename that isn't mirrored there silently breaks the
    link between the two files. Hence the rename map comes back rather than just
    the features.

    Shapes already spelled canonically keep their numbers, so a mostly-tidy file
    isn't reshuffled; only the odd ones move. A number already claimed is reported
    in `collisions` and the shape gets the next free one.
    """
    feats = [dict(f) for f in (damage_features or [])]
    parsed = []
    for f in feats:
        nm = str((f.get("properties") or {}).get(id_prop) or "")
        tag, num = parse_name(nm)
        parsed.append((f, nm, tag, num))

    taken = {}
    for _f, nm, tag, num in parsed:
        if tag and num is not None and nm == format_name(tag, num):
            taken.setdefault(tag, set()).add(num)

    renames, collisions = [], []
    for f, nm, tag, num in parsed:
        if not tag:
            continue                                   # not damage; leave alone
        used = taken.setdefault(tag, set())
        if num is not None and nm == format_name(tag, num):
            continue                                   # already canonical
        if num is None or num in used:
            if num is not None:
                collisions.append({"name": nm, "wanted": format_name(tag, num)})
            num = 1
            while num in used:
                num += 1
        used.add(num)
        new = format_name(tag, num)
        props = dict(f.get("properties") or {})
        props[id_prop] = new
        f["properties"] = props
        renames.append({"from": nm, "to": new})
    return {"features": feats, "renames": renames, "collisions": collisions}


def apply_renames(voids, renames):
    """Rewrite a `voids` list through a rename map, so the YAML keeps pointing at
    shapes that still exist."""
    m = {r["from"]: r["to"] for r in (renames or [])}
    return [m.get(v, v) for v in (voids or [])]


def _geom(feature):
    try:
        g = shape(feature["geometry"])
    except Exception:
        return None
    if not g.is_valid:
        g = g.buffer(0)
    return None if g.is_empty else g


def split_features(features, id_prop="name"):
    """Separate a feature list into (anatomical regions, damage shapes).

    Damage shapes sit INSIDE regions on purpose, so they break every assumption
    that the file is a clean partition. Anything that consumes regions -- share
    borders, validation, export of anatomy -- has to split them out first.
    """
    regions, dmg = [], []
    for f in features or []:
        nm = (f.get("properties") or {}).get(id_prop)
        (dmg if is_damage(nm) else regions).append(f)
    return regions, dmg


# Where the annotator's answer to "which region does this shape belong to?" is
# kept: on the shape itself, so it survives a save, an email and a reload. The
# file is the record here, the same reasoning as `_provenance`.
CHOICE_PROP = "_damage_regions"


def choices_from(damage_features, id_prop="name"):
    """{shape name: [regions]} — the choices already recorded on the shapes."""
    out = {}
    for f in damage_features or []:
        props = f.get("properties") or {}
        nm = props.get(id_prop)
        picked = props.get(CHOICE_PROP)
        if nm is None or not isinstance(picked, (list, tuple)):
            continue
        vals = [str(x) for x in picked if str(x).strip()]
        if vals:
            out[str(nm)] = vals
    return out


def _regions_by_name(region_features, id_prop="name"):
    """name -> geometry, with a multi-part region unioned into one body."""
    rgeo = {}
    for f in region_features or []:
        nm = (f.get("properties") or {}).get(id_prop)
        g = _geom(f)
        if nm is None or g is None:
            continue
        nm = str(nm)
        rgeo[nm] = unary_union([rgeo[nm], g]) if nm in rgeo else g
    return rgeo


def containment(region_features, id_prop="name", frac=0.90, margin=1.05):
    """{region: [the regions it swallows whole]}.

    `hemi` is the outline of the entire hemisphere and all 22 other regions sit
    inside it, so EVERY damage shape drawn anywhere is also "inside hemi". Left
    alone, hemi's `voids` becomes a copy of the whole damage file -- and under
    mode="dominant" it is worse than noise: hemi holds 100% of the shape and gets
    offered against the region the annotator actually drew in, so every single
    shape becomes a question.

    This is a RELATION, not a label on a region, and the difference matters. On
    the real file `ISO` contains `SSp` (97%) and `RSP` (94%), and `dft` contains
    `VL.2` -- but ISO and dft are ordinary regions an annotator owns and draws in.
    Striking them out wholesale would strand every shape drawn in ISO proper. So
    a container is only set aside for a shape that ALSO lands in something it
    contains: the more specific region wins, and nothing else changes.

    The 0.90 threshold is `topology.containment()`'s, picked from real data where
    genuine side-by-side neighbours overlap 0% of each other and a container
    swallows 94-100%. `margin` keeps two coincident copies of one region from
    swallowing each other and emptying the assignment.
    """
    rgeo = _regions_by_name(region_features, id_prop)
    out = {}
    for a, ga in rgeo.items():
        for b, gb in rgeo.items():
            if a == b or gb.area <= 0 or ga.area < gb.area * margin:
                continue
            if not ga.intersects(gb):
                continue
            try:
                if ga.intersection(gb).area / gb.area >= frac:
                    out.setdefault(a, []).append(b)
            except Exception:
                continue
    return {k: sorted(v) for k, v in out.items()}


# How much of a container's own share a contained region must account for before
# the container is set aside for that shape. 1.0 would demand exactness that
# floating-point intersections never give; well below ~0.9 and an ordinary
# straddle at a nested border starts being mistaken for containment.
SPECIFIC_SHARE = 0.9


def assign(region_features, damage_features, id_prop="name", min_overlap=0.01,
           mode="dominant", choices=None, specific_wins=True):
    """Which regions does each damage shape belong to?

    `mode="dominant"` (the lab's call, 2026-08-07): a shape is recorded against
    the ONE region holding most of it. A shape that also reaches into a second
    region is flagged `needsChoice` so the annotator is ASKED rather than guessed
    at -- keep the dominant one, move it to the other, or record both. Their
    answer rides on the shape (`CHOICE_PROP`) and is honoured verbatim from then
    on, including across annotators.

    `mode="all"` is the literal SOP reading -- "all selections that OVERLAP your
    region", so a straddling shape belongs to both. Kept because it is what the
    written procedure says, and because the two write different YAML.

    `min_overlap` is the fraction OF THE SHAPE that must lie inside a region, so a
    hairline touch along a shared border does not list a shape in a neighbour it
    barely grazes. Marginal ones are still reported, flagged, never dropped silently.

    Returns {shapes, regions: {name: {damage, voids}}, unassigned, needsChoice,
    containers, mode}.
    """
    rgeo = _regions_by_name(region_features, id_prop)
    encloses = containment(region_features, id_prop) if specific_wins else {}

    shapes, unassigned, asks = [], [], []
    per_region = {nm: {"damage": set(), "voids": set()} for nm in rgeo}

    for f in damage_features or []:
        nm = str((f.get("properties") or {}).get(id_prop))
        tag, num = parse_name(nm)
        g = _geom(f)
        if g is None or not tag:
            continue
        area = g.area or 1.0
        hits = []
        for rname, rg in rgeo.items():
            if not g.intersects(rg):
                continue
            try:
                ov = g.intersection(rg).area
            except Exception:
                continue
            if ov <= 0:
                continue
            hits.append({"region": rname, "frac": ov / area, "area": ov})
        # Most of the shape first; ties go to the SMALLER region, so the specific
        # one wins over the one that merely encloses it.
        hits.sort(key=lambda h: (-h["frac"], rgeo[h["region"]].area))
        kept = [h for h in hits if h["frac"] >= min_overlap]

        # Where a shape lands in both a region and something that region
        # contains, only the contained one is recorded -- see containment().
        #
        # But "also lands in" is not enough on its own. ISO contains SSp, and a
        # shape drawn in ISO near their long shared border can clip SSp by a few
        # per cent while lying ENTIRELY in ISO. Setting ISO aside for that would
        # file the shape under SSp alone -- 95% outside it -- leave ISO's record
        # empty, and never ask, because one candidate is not a straddle. So the
        # container is only set aside when what it contains accounts for
        # essentially all of what the container was holding.
        #
        # That has to be the COMBINED share of the contained regions, not the
        # largest one. A shape straddling TH and ISO is held 100% by hemi and
        # ~60/40 by the two, so no single region ever clears the bar -- asking
        # region by region would keep hemi for every straddling shape there is,
        # and offer the whole-hemisphere outline as a candidate against the two
        # real regions. Summed, the pair account for all of hemi's share and it
        # is set aside; SSp's lone 5% still does not account for ISO's, so ISO
        # stays. A shape that merely grazes a contained region below
        # min_overlap contributes nothing here, so the container keeps it --
        # recording it against the outline says something true.
        fracs = {h["region"]: h["frac"] for h in kept}
        enclosing = set()
        for h in kept:
            inside = sum(fracs.get(inner, 0.0)
                         for inner in encloses.get(h["region"], [])
                         if inner != h["region"])
            if inside >= h["frac"] * SPECIFIC_SHARE:
                enclosing.add(h["region"])
        for h in kept:
            h["enclosing"] = h["region"] in enclosing
        cand = [h["region"] for h in kept if h["region"] not in enclosing]
        dominant = cand[0] if cand else None

        # A stored answer wins -- but only for regions the shape still reaches. If
        # it has been dragged off one since, that part of the answer is stale and
        # is reported rather than written on.
        asked = [str(x) for x in (choices or {}).get(nm, [])]
        picked = [c for c in asked if c in cand]
        stale = [c for c in asked if c not in cand]

        if picked:
            recorded = picked
        elif mode == "all":
            recorded = list(cand)
        else:
            recorded = [dominant] if dominant else []
        needs = mode == "dominant" and len(cand) > 1 and not picked

        entry = {
            "name": nm, "designation": tag, "number": num,
            "area": float(area),
            "regions": recorded,
            "candidates": cand,
            "dominant": dominant,
            "overlaps": hits,
            # things a person needs to look at rather than trust
            "marginal": [h["region"] for h in hits if h["frac"] < min_overlap],
            # set aside because a region they contain took the shape instead
            "enclosing": sorted(enclosing),
            "spans": len(cand) > 1,
            "chosen": picked or None,
            "staleChoice": stale,
            "needsChoice": needs,
        }
        shapes.append(entry)
        if not cand:
            unassigned.append(entry)
        if needs:
            asks.append(entry)
        for rname in recorded:
            per_region[rname]["damage"].add(tag)
            per_region[rname]["voids"].add(nm)

    def _sorted_voids(v):
        return sorted(v, key=lambda s: (parse_name(s)[0] or "", parse_name(s)[1] or 0))

    return {
        "shapes": shapes,
        "unassigned": unassigned,
        "needsChoice": asks,
        # {region: [regions it contains]} — reported so the UI can say why a
        # region ended up with nothing rather than leaving a blank row.
        "containment": encloses,
        "containers": sorted(encloses),
        "mode": mode,
        "regions": {nm: {"damage": sorted(d["damage"]),
                         "voids": _sorted_voids(d["voids"])}
                    for nm, d in per_region.items()},
    }


# --- the SmartSheet cell --------------------------------------------------------
#
# The tracking sheet's Damage column is a MULTI-SELECT dropdown: one cell holds
# several chips (`Done ×  Bubble ×  Cutoff ×`). So the useful export is not one
# long row -- it is one CELL per region, holding the DISPLAY names, which is what
# the dropdown's options are.
#
# `Done` sits in the same cell as the damage: it is the annotator's "I have been
# through this region" tick, not a designation, so it is kept out of DESIGNATIONS
# and out of the YAML, and only ever added here.
DONE_LABEL = "Done"

# Per-region annotation, stored on the REGION feature the way a shape stores its
# own choice -- it is a judgement that has to survive a save and a reload.
EXTRA_PROP = "_damage_extra"     # designations nobody can draw: cutoff, missing...
DONE_PROP = "_damage_done"       # the Done tick

# How the values reach the clipboard. Pasting multi-line text into a grid usually
# splits it across ROWS; a quoted field is the convention that keeps it in one
# cell. Which one SmartSheet wants is a five-second experiment, so all three are
# offered rather than guessed at.
SEPARATORS = {
    "cell": lambda vals: '"' + "\n".join(vals) + '"',
    "lines": lambda vals: "\n".join(vals),
    "comma": lambda vals: ", ".join(vals),
}
DEFAULT_SEPARATOR = "cell"


def label_of(tag) -> str:
    d = DESIGNATIONS.get(tag)
    return d[0] if d else str(tag)


def _prop_of(features, id_prop, prop, cast):
    out = {}
    for f in features or []:
        props = f.get("properties") or {}
        nm = props.get(id_prop)
        if nm is None or prop not in props:
            continue
        v = cast(props.get(prop))
        if v is not None:
            out[str(nm)] = v
    return out


def extras_from(region_features, id_prop="name"):
    """{region: [tags]} — designations ticked by hand, off the region itself.

    Half the vocabulary can never be drawn (`missing`, `cutoff`, `transcripts`
    and the rest), so without this it could not reach the sheet at all.
    """
    def clean(v):
        if not isinstance(v, (list, tuple)):
            return None
        tags = []
        for x in v:
            t = parse_name(x)[0]
            if t and t not in tags:
                tags.append(t)
        return tags
    return _prop_of(region_features, id_prop, EXTRA_PROP, clean)


def done_from(region_features, id_prop="name"):
    """{region: bool} — which regions the annotator has ticked off."""
    return _prop_of(region_features, id_prop, DONE_PROP, lambda v: bool(v))


def cell_values(tags, done=False) -> list:
    """The chips for one cell: Done first, then the designations in SOP order."""
    order = list(DESIGNATIONS)
    seen = sorted({t for t in (tags or []) if t in DESIGNATIONS},
                  key=order.index)
    return ([DONE_LABEL] if done else []) + [label_of(t) for t in seen]


def cell_text(values, sep=DEFAULT_SEPARATOR) -> str:
    return SEPARATORS.get(sep, SEPARATORS[DEFAULT_SEPARATOR])(list(values))


def cells(assignment, extras=None, done=None, sep=DEFAULT_SEPARATOR,
          include_empty=False) -> list:
    """One box per region, ready to paste into the dropdown cell.

    Drawn damage and hand-ticked damage are the same thing by the time they reach
    the sheet, so they merge here; `sources` keeps them apart for the UI, because
    "there is a shape for this" and "someone said so" are not equally checkable.
    """
    extras, done = extras or {}, done or {}
    out = []
    for region in sorted(assignment.get("regions", {})):
        a = assignment["regions"][region]
        drawn = list(a["damage"])
        typed = [t for t in extras.get(region, []) if t in DESIGNATIONS]
        tags = sorted(set(drawn) | set(typed), key=list(DESIGNATIONS).index)
        is_done = bool(done.get(region))
        if not tags and not is_done and not include_empty:
            continue
        values = cell_values(tags, is_done)
        out.append({
            "region": region,
            "done": is_done,
            "tags": tags,
            "labels": [label_of(t) for t in tags],
            "drawn": drawn,
            "typed": typed,
            "voids": list(a["voids"]),
            "values": values,
            "text": cell_text(values, sep),
        })
    return out


TSV_COLUMNS = ["Region", "Full name", "Annotator", "Damage", "Voids", "Enclaves", "Notes"]


def _cell(v) -> str:
    """One TSV cell. Tabs and newlines inside a value would break the row apart when
    pasted into a sheet, so they become spaces; nothing else is touched."""
    if v is None:
        return ""
    if isinstance(v, (list, tuple, set)):
        v = ",".join(str(x) for x in v)
    return re.sub(r"[\t\r\n]+", " ", str(v)).strip()


def tsv(assignment, notes=None, region_order=None, extra_damage=None) -> str:
    """A TSV block to paste straight into SmartSheet, one row per region.

    `notes` is the parsed annotation.notes.yaml, so annotator/full name/free text
    carry through. `extra_damage` is {region: [tags]} for the designations that are
    never drawn -- missing, cutoff, transcripts and the rest -- which have no shape
    to find and so can only come from the person.
    """
    notes = notes or {}
    extra = extra_damage or {}
    rows = [TSV_COLUMNS]
    names = region_order or sorted(assignment.get("regions", {}))
    for nm in names:
        a = assignment.get("regions", {}).get(nm, {"damage": [], "voids": []})
        n = notes.get(nm) if isinstance(notes.get(nm), dict) else {}
        tags = sorted(set(a["damage"]) | {t for t in extra.get(nm, []) if t in DESIGNATIONS})
        rows.append([
            _cell(nm),
            _cell(n.get("region")),
            _cell(n.get("annotator")),
            _cell(tags),
            _cell(a["voids"]),
            _cell(n.get("enclaves")),
            _cell(n.get("notes")),
        ])
    return "\n".join("\t".join(r) for r in rows)

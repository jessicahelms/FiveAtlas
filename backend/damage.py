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


def assign(region_features, damage_features, id_prop="name", min_overlap=0.01):
    """Which regions does each damage shape overlap?

    The SOP says the `voids` field lists "all selections in the damage GeoJSON file
    that OVERLAP your region" -- so a shape straddling two regions belongs to both,
    which is what falls out when each annotator records their own region.

    `min_overlap` is the fraction OF THE SHAPE that must lie inside a region, so a
    hairline touch along a shared border does not list a shape in a neighbour it
    barely grazes. Marginal ones are still reported, flagged, never dropped silently.

    Returns {shapes: [...], regions: {name: {damage, voids}}, unassigned: [...]}
    """
    rgeo = {}
    for f in region_features or []:
        nm = (f.get("properties") or {}).get(id_prop)
        g = _geom(f)
        if nm is None or g is None:
            continue
        nm = str(nm)
        rgeo[nm] = unary_union([rgeo[nm], g]) if nm in rgeo else g

    shapes, unassigned = [], []
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
        hits.sort(key=lambda h: -h["frac"])
        kept = [h for h in hits if h["frac"] >= min_overlap]
        entry = {
            "name": nm, "designation": tag, "number": num,
            "area": float(area),
            "regions": [h["region"] for h in kept],
            "dominant": kept[0]["region"] if kept else None,
            "overlaps": hits,
            # things a person needs to look at rather than trust
            "marginal": [h["region"] for h in hits if h["frac"] < min_overlap],
            "spans": len(kept) > 1,
        }
        shapes.append(entry)
        if not kept:
            unassigned.append(entry)
        for h in kept:
            per_region[h["region"]]["damage"].add(tag)
            per_region[h["region"]]["voids"].add(nm)

    def _sorted_voids(v):
        return sorted(v, key=lambda s: (parse_name(s)[0] or "", parse_name(s)[1] or 0))

    return {
        "shapes": shapes,
        "unassigned": unassigned,
        "regions": {nm: {"damage": sorted(d["damage"]),
                         "voids": _sorted_voids(d["voids"])}
                    for nm, d in per_region.items()},
    }


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

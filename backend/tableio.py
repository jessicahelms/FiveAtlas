"""Regions from a CSV/TSV of vertices -- the import path for people whose
region outlines live in a spreadsheet rather than a GeoJSON.

Expected shape: one row per vertex, in drawing order, grouped by region name.

    name,x,y            ISO,1200,3400 ...
    region,x_px,y_px    (any of the recognised aliases works)
    name,part,x,y       `part` (or ring/polygon/piece) separates the lobes of
                        a multi-part region; within a part, row order is
                        vertex order.

Units are decided by the COLUMN NAMES, never guessed from the numbers:
`x`/`x_px`/`vertex_x`/`px_x` are full-resolution pixels; `x_location`/`x_um`/
`x_micron(s)`/`x_centroid` are microns (Xenium's own column names) and are
converted with the dataset's pixel size. Ambiguity is an error, not a guess.
"""
from __future__ import annotations

import csv
from pathlib import Path

_NAME_COLS = ("name", "region", "region_name", "id", "label", "roi")
_PART_COLS = ("part", "ring", "polygon", "piece", "group", "lobe")
_PX_X = ("x", "x_px", "vertex_x", "px_x", "x_pixel", "x_pixels")
_PX_Y = ("y", "y_px", "vertex_y", "px_y", "y_pixel", "y_pixels")
_UM_X = ("x_location", "x_um", "x_micron", "x_microns", "x_centroid")
_UM_Y = ("y_location", "y_um", "y_micron", "y_microns", "y_centroid")


def _find(header, wanted):
    for i, h in enumerate(header):
        if h in wanted:
            return i
    return None


def read_regions_table(path, id_prop="name", pixel_size_um=None):
    """-> (FeatureCollection, notes:list[str]). Raises ValueError with a
    message a person can act on -- naming columns and rows, never guessing."""
    p = Path(path)
    text = p.read_text(encoding="utf-8-sig", errors="replace")
    lines = [ln for ln in text.splitlines() if ln.strip()]
    if len(lines) < 4:
        raise ValueError("the table has fewer than 4 data lines -- a region "
                         "needs a name and at least 3 vertices")

    # Delimiter: sniff comma/semicolon/tab from the header line.
    head = lines[0]
    delim = max(",;\t", key=head.count)
    rows = list(csv.reader(lines, delimiter=delim))
    header = [h.strip().lower().replace(" ", "_") for h in rows[0]]

    ni = _find(header, _NAME_COLS)
    if ni is None:
        raise ValueError(f"no region-name column. Recognised names: "
                         f"{', '.join(_NAME_COLS)}. Header was: {header}")
    pxi, pyi = _find(header, _PX_X), _find(header, _PX_Y)
    uxi, uyi = _find(header, _UM_X), _find(header, _UM_Y)
    if pxi is not None and pyi is not None:
        xi, yi, unit = pxi, pyi, "px"
    elif uxi is not None and uyi is not None:
        if not pixel_size_um:
            raise ValueError(
                f"columns {header[uxi]}/{header[uyi]} are in microns, but this "
                "dataset has no pixel size to convert with")
        xi, yi, unit = uxi, uyi, "um"
    else:
        raise ValueError(
            "no coordinate columns. Pixels: x/y (or x_px, vertex_x...); "
            f"microns: x_location/y_location (or x_um...). Header was: {header}")
    gi = _find(header, _PART_COLS)

    scale = (1.0 / float(pixel_size_um)) if unit == "um" else 1.0

    order: list = []                     # (name, part) in first-seen order
    rings: dict = {}
    bad = 0
    for r, row in enumerate(rows[1:], start=2):
        if len(row) <= max(xi, yi, ni):
            bad += 1
            continue
        nm = row[ni].strip()
        if not nm:
            bad += 1
            continue
        try:
            x = float(row[xi]) * scale
            y = float(row[yi]) * scale
        except ValueError:
            raise ValueError(f"row {r}: '{row[xi]}'/'{row[yi]}' is not a number")
        part = row[gi].strip() if gi is not None and len(row) > gi else ""
        key = (nm, part)
        if key not in rings:
            rings[key] = []
            order.append(key)
        rings[key].append([x, y])

    too_small = [f"{nm}{f' (part {pt})' if pt else ''}: {len(v)} vertices"
                 for (nm, pt), v in rings.items() if len(v) < 3]
    if too_small:
        raise ValueError("every region needs at least 3 vertices -- "
                         + "; ".join(too_small[:5]))

    by_name: dict = {}
    name_order: list = []
    for (nm, pt) in order:
        ring = rings[(nm, pt)]
        if ring[0] != ring[-1]:
            ring = ring + [ring[0]]
        by_name.setdefault(nm, [])
        if nm not in name_order:
            name_order.append(nm)
        by_name[nm].append([ring])

    features = []
    for nm in name_order:
        parts = by_name[nm]
        geom = ({"type": "Polygon", "coordinates": parts[0]} if len(parts) == 1
                else {"type": "MultiPolygon", "coordinates": parts})
        features.append({"type": "Feature", "properties": {id_prop: nm},
                         "geometry": geom})

    notes = [f"{len(features)} region(s), {sum(len(v) for v in rings.values())} "
             f"vertices, coordinates read as "
             + ("pixels" if unit == "px"
                else f"microns (converted at {pixel_size_um} um/px)")]
    if bad:
        notes.append(f"{bad} short/blank row(s) skipped")
    return {"type": "FeatureCollection", "features": features}, notes

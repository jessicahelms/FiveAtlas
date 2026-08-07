# Wiring up the SmartSheet TSV

`backend/damage.py` is built and tested (`atlas_editor_sandbox/test_damage.py`, 26
checks, all offline). It is pure — no HTTP, no file writes — so it could be written
while `app.py` was held by another session. This is the remaining glue.

## 1. The route (paste into `app.py` when it is free)

```python
import damage as DMG

@app.post("/api/datasets/{ds_id}/regions/smartsheet.tsv")
async def smartsheet_tsv(ds_id: str, request: Request):
    """One TSV row per region, ready to paste into the tracking sheet.
    Body: {fc?, damageFc?, notes?, extraDamage?, minOverlap?}"""
    try:
        d = ds.get_dataset(ds_id)
    except KeyError:
        raise HTTPException(404, "unknown dataset")
    body = await request.json() or {}
    fc = body.get("fc") or geo.load_regions(ds_id)[0]
    regions, inline = DMG.split_features(fc.get("features", []), d["id_prop"])
    dmg = (body.get("damageFc") or {}).get("features", []) or inline
    res = DMG.assign(regions, dmg, d["id_prop"],
                     float(body.get("minOverlap", 0.01)))
    text = DMG.tsv(res, body.get("notes"), extra_damage=body.get("extraDamage"))
    return {"tsv": text, "shapes": res["shapes"], "unassigned": res["unassigned"],
            "regions": res["regions"]}
```

Returns the assignment alongside the text so the UI can show the review table
rather than handing over a block of TSV no one can check.

## 2. Frontend

- **Copy for SmartSheet** button in the Export section. Fetch, then
  `navigator.clipboard.writeText(res.tsv)`. Clipboard writes need a user gesture, so
  it must be the click handler itself, not an async continuation.
- Show a small review table first: shape, designation, regions, % overlap. Sort
  `unassigned` and anything `marginal` to the top — those are the rows a person has
  to judge. Region cells editable; a shape that `spans` shows both regions.
- A "damage present but not drawn" control per region (the undrawn designations:
  `missing`, `cutoff`, `overlap`, `removal`, `distortion`, `transcripts`,
  `foldsmall`, `foldlarge`) feeding `extraDamage` — without it, half the vocabulary
  is unreachable.

## 3. Loading `damage.geojson`

`scan.py` should surface `damage.geojson` as its own source, and the app should draw
it as a separate layer. Until it does, the route falls back to damage shapes found
by name inside the region file, which is what `split_features` is for.

**Import must MERGE, not replace.** The file is shared per sample and accumulates
across annotators. Merge by name; report same-name-different-geometry rather than
silently picking one. And renumber on merge — `next_number()` must be given every
name in the merged file, since two annotators can otherwise both mint
`separation.8`.

## 4. Keep damage out of the geometry engine

Damage shapes sit inside regions on purpose, so they violate the partition
assumption everywhere:

- `partition` / `move-border` / `shared-border` — pass only the anatomical half of
  `split_features()`. `topology.containment()` would refuse them anyway, since a
  shape 100% inside a region is exactly what that guard catches.
- `validate_features` — a damage shape overlapping its host is not an error.
- Region export — anatomy only; damage goes to `damage.geojson`.

## Open question that changes the output

The user asked for **dominant region by default, with a prompt offering both**. The
SOP says **all overlapping regions**. `assign()` currently implements the SOP and
also reports `dominant` and `spans`, so either behaviour is one line at the caller —
but the two produce different YAML, so it needs deciding before this ships.

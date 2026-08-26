"""Auto-detect a Xenium-style dataset by scanning one folder.

Anchored on `experiment.xenium` (the Xenium manifest, which names the morphology,
morphology_focus, and transcripts files) with filename heuristics as a fallback.
Returns a descriptor:

    {
      id, label, root, pixel_size_um, id_prop,
      sources: {
        manifest, morphology, morphology_focus, genes, regions[], transcripts
      }
    }

Everything here is cheap: it reads small JSON/GeoJSON files and zip central
directories, and derives channel names from filenames -- it never decodes the
big JPEG2000 pixel data.
"""
from __future__ import annotations

import json
import re
import zipfile
from pathlib import Path

TIFF_EXTS = (".ome.tif", ".ome.tiff")


def _slug(name: str) -> str:
    s = re.sub(r"[^A-Za-z0-9_.-]+", "_", name).strip("_")
    return s or "dataset"


# A stock Xenium export names the four stain files morphology_focus_0000..0003
# with no stain name in the filename; what each index IS is fixed by the
# platform (multimodal cell segmentation kit), so name them here -- the names
# also drive the default colours (DAPI blue, boundary magenta, ...).
_XENIUM_MF_NAMES = {0: "DAPI", 1: "ATP1A1 / CD45 / E-Cadherin",
                    2: "18S", 3: "alphaSMA / Vimentin"}


def _channels_from_filenames(names) -> list:
    """morphology_focus channels: 'ch0002_18s.ome.tif' -> {index:2, name:'18s'};
    a stock export's 'morphology_focus_0002.ome.tif' -> {index:2, name:'18S'}."""
    chans = []
    for n in names:
        base = Path(n).name
        m = re.match(r"ch(\d+)[_-](.+?)\.ome\.tiff?$", base, re.I)
        if m:
            chans.append({"index": int(m.group(1)),
                          "name": m.group(2).replace("_", " ")})
            continue
        m = re.match(r"morphology_focus_(\d+)\.ome\.tiff?$", base, re.I)
        if m:
            i = int(m.group(1))
            chans.append({"index": i,
                          "name": _XENIUM_MF_NAMES.get(i, f"channel {i}")})
    chans.sort(key=lambda c: c["index"])
    return chans


def _geojson_space(path: Path):
    """Classify a region GeoJSON by coordinate magnitude: full-res vs 640-canvas."""
    try:
        d = json.load(open(path, encoding="utf-8"))
    except Exception:
        return "unknown", 0
    feats = d.get("features", [])
    mx = 0.0

    def walk(c):
        nonlocal mx
        if isinstance(c, list):
            if c and isinstance(c[0], (int, float)):
                mx = max(mx, abs(c[0]), abs(c[1]))
            else:
                for x in c:
                    walk(x)

    for f in feats[:100]:
        walk((f.get("geometry") or {}).get("coordinates") or [])
    space = "fullres" if mx > 2000 else ("canvas640" if mx <= 800 else "unknown")
    return space, len(feats)


def scan_folder(path: str) -> dict:
    root = Path(path)
    if not root.is_dir():
        raise NotADirectoryError(path)

    sources: dict = {}
    pixel_size = None
    label = root.name

    # --- manifest anchor -----------------------------------------------------
    manifest = root / "experiment.xenium"
    man = None
    if manifest.exists():
        try:
            man = json.load(open(manifest, encoding="utf-8"))
        except Exception:
            man = None
    if man:
        sources["manifest"] = str(manifest)
        pixel_size = man.get("pixel_size")
        if man.get("region_name"):
            label = f"{root.name} ({man['region_name']})"

    images = (man or {}).get("images") or {}
    xef = (man or {}).get("xenium_explorer_files") or {}

    # --- morphology pyramid (DAPI reference) ---------------------------------
    morph = None
    if images.get("morphology_filepath"):
        cand = root / images["morphology_filepath"]
        if cand.exists():
            morph = cand
    if morph is None and (root / "morphology.ome.tif").exists():
        morph = root / "morphology.ome.tif"
    if morph is None:
        omes = [p for p in sorted(root.glob("*.ome.tif"))
                if not p.name.startswith("morphology_focus")]
        if omes:
            morph = omes[0]
    if morph:
        sources["morphology"] = {"path": str(morph), "kind": "ome_pyramid"}

    # --- morphology_focus (multichannel stains): folder or zip ---------------
    mf_dir = root / "morphology_focus"
    mf_zip = root / "morphology_focus.zip"
    if mf_dir.is_dir():
        # rglob: unzipping the bundle can nest as morphology_focus/morphology_focus/
        files = sorted(str(p) for p in mf_dir.rglob("*.ome.tif*"))
        sources["morphology_focus"] = {
            "path": str(mf_dir), "kind": "folder", "files": files,
            "channels": _channels_from_filenames(files),
        }
    elif mf_zip.exists():
        members = []
        try:
            with zipfile.ZipFile(mf_zip) as zf:
                members = [i.filename for i in zf.infolist()
                           if not i.is_dir()
                           and i.filename.lower().endswith(TIFF_EXTS)]
        except Exception:
            pass
        sources["morphology_focus"] = {
            "path": str(mf_zip), "kind": "zip", "members": sorted(members),
            "channels": _channels_from_filenames(members),
        }

    # --- gene rasters (640-canvas transcript renders) ------------------------
    gm = root / "_grid_meta.json"
    if gm.exists():
        try:
            meta = json.load(open(gm, encoding="utf-8"))
        except Exception:
            meta = {}
        req = meta.get("genes_requested") or []
        present = [g for g in req if (root / f"{g}.tif").exists()]
        if not present:
            present = [p.stem for p in sorted(root.glob("*.tif"))
                       if not p.stem.startswith(("morphology", "transcripts"))]
        if present:
            sources["genes"] = {"dir": str(root), "grid_meta": str(gm),
                                "names": present}
            if pixel_size is None:
                pixel_size = meta.get("pixel_size_um")

    # --- region GeoJSONs -----------------------------------------------------
    regions = []
    for p in sorted(root.glob("*.geojson")):
        space, n = _geojson_space(p)
        regions.append({"path": str(p), "name": p.stem, "space": space, "features": n})
    if regions:
        sources["regions"] = regions

    # --- transcripts zarr ----------------------------------------------------
    tz = None
    if xef.get("transcripts_zarr_filepath"):
        cand = root / xef["transcripts_zarr_filepath"]
        if cand.exists():
            tz = cand
    if tz is None:
        for cand in (root / "transcripts.zarr.zip", root / "transcripts.zarr"):
            if cand.exists():
                tz = cand
                break
    if tz:
        sources["transcripts"] = {"path": str(tz)}

    return {
        "id": _slug(root.name),
        "label": label,
        "root": str(root),
        "pixel_size_um": pixel_size,
        "id_prop": "name",
        "sources": sources,
    }


def primary_regions_path(desc: dict):
    """The region GeoJSON to edit by default: full-res 'merged.regions' if present."""
    regs = desc.get("sources", {}).get("regions") or []
    pref = [r for r in regs if r["space"] == "fullres" and "merged.regions" in r["name"]]
    pref = pref or [r for r in regs if r["space"] == "fullres"]
    pref = pref or regs
    return pref[0]["path"] if pref else None

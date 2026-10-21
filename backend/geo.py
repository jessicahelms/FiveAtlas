"""GeoJSON I/O and topology operations.

The heavy lifting (shared-border snapping, neighbour reconstruction, overlap
resolution) is delegated to the user's existing engine:
    snap_borders/fix_geojson_borders_v2.py
so the webapp's edits are identical to their batch pipeline.
"""
from __future__ import annotations

import json
import os
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Optional
from uuid import uuid4

import datasets as ds
import topology


def load_regions(ds_id: str):
    """Return (FeatureCollection, source_path). Prefers the edited working copy.
    A dataset with no region GeoJSON yet returns an empty FeatureCollection."""
    d = ds.get_dataset(ds_id)
    ep = ds.edited_regions_path(ds_id)
    if ep.exists():
        path = ep
    else:
        rp = ds.regions_path(d)
        if rp is None:
            return {"type": "FeatureCollection", "features": []}, None
        path = rp
    with open(path, encoding="utf-8") as f:
        fc = json.load(f)
    try:
        fc["features"] = topology.clean_features(fc.get("features", []), d["id_prop"], spike=3.0)
    except Exception:
        pass
    return fc, str(path)


def load_original(ds_id: str) -> dict:
    d = ds.get_dataset(ds_id)
    with open(ds.regions_path(d), encoding="utf-8") as f:
        return json.load(f)


def _write_atomic(path, text: str) -> None:
    """Write through a temp file and a rename, so the target is never half-written.

    The working copy used to be written with a plain open(path, "w"), which
    truncates first: the live file drops to 0 bytes and grows back in place.
    Measured, 54,923 size samples taken during one 36 MB save caught the
    zero-byte state. A drive dropping out, a full disk or a kill in that window
    leaves the file truncated -- and because load_regions() prefers the working
    copy whenever it merely EXISTS, a 0-byte file wedges the dataset behind a
    bare 500 on EVERY read (/regions, /info, history, validate, export,
    notes/preview), with nothing naming the file or pointing at versions/.

    The temp name is unique per call. One fixed name lets two concurrent writers
    to the same target share the temp path, so one can rename the other's
    half-written file into place. It does not end in .geojson, so a folder scan
    never mistakes it for a region source.
    """
    p = Path(path)
    tmp = p.with_name(f"{p.name}.{os.getpid()}.{uuid4().hex[:8]}.fiveatlas-tmp")
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            f.write(text)
        last = None
        for attempt in range(40):             # ~2 s in all
            try:
                os.replace(tmp, p)
                return
            except PermissionError as e:
                # Windows refuses the rename while ANOTHER handle is open on the
                # destination -- antivirus, a sync client (OneDrive, Dropbox,
                # Box), Explorer's preview pane, a second FiveAtlas. Those
                # handles are nearly always transient, and the new contents are
                # already written in full, so waiting beats failing a save.
                # Measured: a reader doing back-to-back reads of the working
                # copy made a bare os.replace fail with WinError 5 within a
                # handful of saves.
                last = e
                time.sleep(0.05)
        raise last
    except Exception:
        try:
            os.unlink(tmp)
        except Exception:
            pass
        raise


def save_edited(ds_id: str, fc: dict) -> dict:
    """Save the working copy AND keep an immutable timestamped snapshot.

    Save used to overwrite one file, so the previous state was gone the moment you
    saved again. Now every save also writes versions/regions_<stamp>.geojson, which
    is never touched afterwards -- an earlier state can always be recovered.

    The dataset folder itself is still never written to: originals stay original.
    """
    payload = json.dumps(fc)
    ep = ds.edited_regions_path(ds_id)
    _write_atomic(ep, payload)

    vdir = ds.workdir_for(ds_id) / "versions"
    vdir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    vp = vdir / f"regions_{stamp}.geojson"
    n = 1
    while vp.exists():                       # more than one save in the same second
        n += 1
        vp = vdir / f"regions_{stamp}_{n}.geojson"
    _write_atomic(vp, payload)

    return {"saved": str(ep), "version": str(vp),
            "versions": len(list(vdir.glob("regions_*.geojson")))}


# ---- "Save as": the file the annotator chose to keep their regions in --------
#
# Remembered per dataset in the app's own folder, so every later Save updates
# that same file. `written` is every file the app has EVER written this way.
# It matters because the scanner reads any *.geojson in a dataset folder as a
# region source: a file saved next to the originals would look like an
# original the next time the dataset opens, and the originals guard would then
# refuse the annotator's own file.

def _target_record(ds_id: str) -> Path:
    return ds.workdir_for(ds_id) / "save_target.json"


def get_save_target(ds_id: str) -> dict:
    try:
        rec = json.load(open(_target_record(ds_id), encoding="utf-8"))
    except Exception:
        rec = {}
    written = rec.get("written") if isinstance(rec.get("written"), list) else []
    return {"path": rec.get("path") or None, "written": [str(w) for w in written]}


def set_save_target(ds_id: str, path: str) -> dict:
    rec = get_save_target(ds_id)
    rec["path"] = str(path)
    if str(path) not in rec["written"]:
        rec["written"].append(str(path))
    with open(_target_record(ds_id), "w", encoding="utf-8") as f:
        json.dump(rec, f, indent=1)
    return rec


def clear_save_target(ds_id: str) -> dict:
    rec = get_save_target(ds_id)
    rec["path"] = None                   # `written` is kept: those stay ours
    with open(_target_record(ds_id), "w", encoding="utf-8") as f:
        json.dump(rec, f, indent=1)
    return rec


#: per-feature keys FiveAtlas keeps for itself and never writes to a user file
INTERNAL_FEATURE_PROPS = ("_srcFile",)


def strip_internal_props(fc: dict) -> dict:
    """A copy with FiveAtlas's own per-feature bookkeeping taken out.

    `_srcFile` records which region file a feature came from, so the sidebar can
    list several files' regions under a heading each instead of running them
    together. It belongs to the editing session, not to anybody's data, so every
    file this app hands out goes through here first: both Save paths (below) and
    the export route. The working copy keeps it, which is how the grouping
    survives reopening the dataset.
    """
    feats = []
    for f in (fc.get("features") or []):
        props = f.get("properties")
        if isinstance(props, dict) and any(k in props for k in INTERNAL_FEATURE_PROPS):
            f = {**f, "properties": {k: v for k, v in props.items()
                                     if k not in INTERNAL_FEATURE_PROPS}}
        feats.append(f)
    return {**fc, "features": feats}


def write_geojson_atomic(path, fc: dict) -> None:
    """Write via a temp file and a rename, so a network drive dropping out mid-
    write leaves the previous file intact rather than a half-written one. The
    temp name does not end in .geojson, so a scan never mistakes it for regions.
    """
    # NB: this one STRIPS the app's internal properties -- it is the file the
    # annotator keeps. The working copy must not be written through here: it
    # needs _srcFile to survive, or the sidebar stops grouping by file.
    _write_atomic(path, json.dumps(strip_internal_props(fc)))


def restore_original(ds_id: str) -> dict:
    """Throw the working copy away and go back to the dataset's own GeoJSON.

    The working copy is snapshotted into versions/ first, so this is never a
    one-way door: whatever you were looking at is still on disk afterwards.
    The dataset's file is only ever READ here.
    """
    d = ds.get_dataset(ds_id)
    rp = ds.regions_path(d)
    if rp is None or not Path(rp).exists():
        raise FileNotFoundError("this dataset has no original regions file to go back to")

    ep = ds.edited_regions_path(ds_id)
    backup = None
    if ep.exists():
        vdir = ds.workdir_for(ds_id) / "versions"
        vdir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        bp = vdir / f"regions_{stamp}_before_restore.geojson"
        n = 1
        while bp.exists():
            n += 1
            bp = vdir / f"regions_{stamp}_before_restore_{n}.geojson"
        bp.write_text(ep.read_text(encoding="utf-8"), encoding="utf-8")
        backup = str(bp)

    with open(rp, encoding="utf-8") as f:
        fc = json.load(f)
    try:
        fc["features"] = topology.clean_features(fc.get("features", []), d["id_prop"], spike=3.0)
    except Exception:
        pass
    with open(ep, "w", encoding="utf-8") as f:
        json.dump(fc, f)
    return {"fc": fc, "source": str(rp), "backup": backup,
            "features": len(fc.get("features", []))}


def list_versions(ds_id: str) -> list:
    """Every saved snapshot, newest first."""
    vdir = ds.workdir_for(ds_id) / "versions"
    if not vdir.exists():
        return []
    out = []
    for p in vdir.glob("regions_*.geojson"):
        try:
            st = p.stat()
            out.append({"path": str(p), "name": p.name, "bytes": st.st_size,
                        "saved": datetime.fromtimestamp(st.st_mtime).isoformat(timespec="seconds")})
        except OSError:
            pass
    return sorted(out, key=lambda v: v["saved"], reverse=True)


def run_snap(ds_id: str, before_fc: dict, after_fc: dict,
             moved: Optional[list] = None, tol: float = 40.0) -> dict:
    """Snap neighbours to the moved region's new border.

    `before_fc` is the state the client last loaded; `after_fc` is that state
    with the moved region(s) edited. The moved region is authoritative -- every
    neighbour conforms, filling area it pulled away from and ceding area it
    pushed into. Stateless: the client passes both, so iterative edits stay
    correct.
    """
    d = ds.get_dataset(ds_id)
    moved = moved or []
    out_feats = topology.snap_to_edits(
        before_fc["features"], after_fc["features"],
        id_prop=d["id_prop"], moved=moved, tol=tol,
    )
    return {
        "type": "FeatureCollection",
        "features": out_feats,
        "_movers": sorted(str(m) for m in moved),
        "_notes": [],
    }


def load_regions_file(ds_id: str, path: str) -> dict:
    """Load a region set from disk and make it this dataset's working copy."""
    fc = read_regions_file(ds_id, path)
    save_edited(ds_id, fc)
    return fc


def read_regions_file(ds_id: str, path: str) -> dict:
    """Read a region set from disk WITHOUT touching the working copy -- so the
    annotator can be asked "add these, or replace what is on the map?" before
    anything changes.

    GeoJSON as before; a .csv/.tsv/.txt is read as a vertex table (one row per
    vertex, grouped by region name -- see tableio for the columns it accepts,
    including Xenium's micron column names)."""
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(path)
    d = ds.get_dataset(ds_id)
    if p.suffix.lower() in (".csv", ".tsv", ".txt"):
        import tableio
        fc, notes = tableio.read_regions_table(
            p, id_prop=d["id_prop"], pixel_size_um=d.get("pixel_size_um"))
        fc["_import"] = {"from": str(p), "notes": notes}
    else:
        with open(p, encoding="utf-8") as f:
            fc = json.load(f)
        if not isinstance(fc, dict) or fc.get("type") != "FeatureCollection":
            raise ValueError("not a GeoJSON FeatureCollection")
    # smooth away spikes / heal invalid polygons on load
    fc["features"] = topology.clean_features(fc.get("features", []), d["id_prop"], spike=3.0)
    return fc

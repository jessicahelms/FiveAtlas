"""GeoJSON I/O and topology operations.

The heavy lifting (shared-border snapping, neighbour reconstruction, overlap
resolution) is delegated to the user's existing engine:
    snap_borders/fix_geojson_borders_v2.py
so the webapp's edits are identical to their batch pipeline.
"""
from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Optional

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


def save_edited(ds_id: str, fc: dict) -> dict:
    """Save the working copy AND keep an immutable timestamped snapshot.

    Save used to overwrite one file, so the previous state was gone the moment you
    saved again. Now every save also writes versions/regions_<stamp>.geojson, which
    is never touched afterwards -- an earlier state can always be recovered.

    The dataset folder itself is still never written to: originals stay original.
    """
    payload = json.dumps(fc)
    ep = ds.edited_regions_path(ds_id)
    with open(ep, "w", encoding="utf-8") as f:
        f.write(payload)

    vdir = ds.workdir_for(ds_id) / "versions"
    vdir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    vp = vdir / f"regions_{stamp}.geojson"
    n = 1
    while vp.exists():                       # more than one save in the same second
        n += 1
        vp = vdir / f"regions_{stamp}_{n}.geojson"
    with open(vp, "w", encoding="utf-8") as f:
        f.write(payload)

    return {"saved": str(ep), "version": str(vp),
            "versions": len(list(vdir.glob("regions_*.geojson")))}


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
    """Load an arbitrary GeoJSON from disk and make it this dataset's working
    copy (so any region set can be opened for editing, not just the folder's)."""
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(path)
    with open(p, encoding="utf-8") as f:
        fc = json.load(f)
    if not isinstance(fc, dict) or fc.get("type") != "FeatureCollection":
        raise ValueError("not a GeoJSON FeatureCollection")
    # smooth away spikes / heal invalid polygons on load
    d = ds.get_dataset(ds_id)
    fc["features"] = topology.clean_features(fc.get("features", []), d["id_prop"], spike=3.0)
    save_edited(ds_id, fc)
    return fc

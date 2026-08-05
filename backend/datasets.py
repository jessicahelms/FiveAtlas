"""Dynamic dataset registry.

Nothing dataset-specific is hard-coded. The app remembers folders the user has
opened in a workdir file (opened_datasets.json -- user state, not code); on
startup it re-scans them. A fresh install starts empty: the user opens a folder
via /api/browse + /api/datasets/open.
"""
from __future__ import annotations

import json
from pathlib import Path

import scan
from config import WORKDIR

_REGISTRY: dict[str, dict] = {}
_OPENED_FILE = WORKDIR / "opened_datasets.json"


def _read_opened() -> list:
    try:
        data = json.load(open(_OPENED_FILE, encoding="utf-8"))
        return data if isinstance(data, list) else []
    except Exception:
        return []


def _write_opened(paths):
    try:
        seen, out = set(), []
        for p in paths:
            if p and p not in seen:
                seen.add(p); out.append(p)
        json.dump(out, open(_OPENED_FILE, "w", encoding="utf-8"), indent=2)
    except Exception as e:
        print(f"[datasets] could not persist opened list: {e}")


def register(desc: dict) -> dict:
    _REGISTRY[desc["id"]] = desc
    return desc


def open_path(path: str) -> dict:
    desc = register(scan.scan_folder(path))
    opened = _read_opened()
    if path not in opened:
        opened.append(path)
        _write_opened(opened)
    return desc


def forget(ds_id: str):
    """Remove a dataset from the registry and the persisted opened list."""
    d = _REGISTRY.pop(ds_id, None)
    if d:
        _write_opened([p for p in _read_opened() if p != d["root"]])


def get_dataset(ds_id: str) -> dict:
    if ds_id not in _REGISTRY:
        raise KeyError(ds_id)
    return _REGISTRY[ds_id]


def all_datasets() -> list:
    return [{"id": k, "label": v["label"]} for k, v in _REGISTRY.items()]


def image_path(d: dict):
    m = d["sources"].get("morphology")
    return Path(m["path"]) if m else None


def regions_path(d: dict):
    p = scan.primary_regions_path(d)
    return Path(p) if p else None


def workdir_for(ds_id: str) -> Path:
    w = WORKDIR / ds_id
    w.mkdir(parents=True, exist_ok=True)
    return w


def edited_regions_path(ds_id: str) -> Path:
    return workdir_for(ds_id) / "regions_edited.geojson"


def _stub(path: str) -> dict:
    """A minimal descriptor for a folder we can't scan right now (e.g. its network
    drive is offline). Keeps the dataset usable for GeoJSON region editing -- the
    edited working copy still lives in the local workdir -- while imagery/genes are
    simply unavailable until the folder comes back."""
    p = Path(path)
    return {
        "id": p.name, "label": f"{p.name} (imagery offline)", "root": str(p),
        "id_prop": "name", "pixel_size_um": None, "sources": {}, "offline": True,
    }


def _load_opened():
    for path in _read_opened():
        try:
            register(scan.scan_folder(path))
        except Exception as e:  # a moved/offline folder must not break startup...
            print(f"[datasets] {path} not scannable ({e}); registering offline stub")
            try:
                register(_stub(path))     # ...keep it usable for region editing
            except Exception as e2:
                print(f"[datasets] could not stub {path}: {e2}")


_load_opened()

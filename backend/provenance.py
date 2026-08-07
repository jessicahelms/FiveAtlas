"""Who did what to a region file, recorded in the file itself.

A region file gets handed along a chain of people. If the edit history lived in
one person's workdir it would be lost the moment the file was emailed on, so the
trail is a `_provenance` member ON the FeatureCollection. GeoJSON allows foreign
members there, so it survives save, export, and being opened by the next person.

Each entry records BOTH a display name and the Windows account it was made from:
a typed name on its own is unverifiable, and the point of a chain of custody is
that you can tell who actually made the edit.
"""
from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path

import config

# Entries are small (~150 bytes). Cap the trail so a file that has been round-
# tripped for months can't grow without bound; the oldest are dropped first and
# a marker records that it happened, so the count is never silently wrong.
MAX_ENTRIES = 2000
_TRIM_NOTE = "…earlier history trimmed"


def _identity_file() -> Path:
    return Path(config.WORKDIR) / "identity.json"


def display_name() -> str:
    """The name to attribute edits to. Whatever the user set, else the Windows
    account, else 'unknown'."""
    try:
        p = _identity_file()
        if p.exists():
            nm = (json.load(open(p, encoding="utf-8")) or {}).get("name")
            if nm and str(nm).strip():
                return str(nm).strip()
    except Exception:
        pass
    return account_name()


def account_name() -> str:
    for var in ("USERNAME", "USER", "LOGNAME"):
        v = os.environ.get(var)
        if v:
            return v
    return "unknown"


def set_display_name(name: str) -> str:
    name = str(name or "").strip()
    p = _identity_file()
    p.parent.mkdir(parents=True, exist_ok=True)
    with open(p, "w", encoding="utf-8") as f:
        json.dump({"name": name}, f)
    return display_name()


def trail(fc: dict) -> list:
    """The entries already on a FeatureCollection, tolerating junk."""
    v = (fc or {}).get("_provenance")
    return [e for e in v if isinstance(e, dict)] if isinstance(v, list) else []


def entry(action: str, detail=None, regions=None) -> dict:
    e = {
        "t": datetime.now().isoformat(timespec="seconds"),
        "who": display_name(),
        "account": account_name(),
        "app": config.VERSION,
        "action": str(action),
    }
    if detail:
        e["detail"] = str(detail)
    if regions:
        e["regions"] = [str(r) for r in regions]
    return e


def stamp(fc: dict, action: str, detail=None, regions=None) -> dict:
    """Append one entry and return the SAME FeatureCollection.

    Call this on the collection an endpoint is about to hand back, so the trail
    reaches the client already attached and cannot be lost by a client that
    rebuilds {type, features} by hand.
    """
    if not isinstance(fc, dict):
        return fc
    log = trail(fc)
    log.append(entry(action, detail, regions))
    if len(log) > MAX_ENTRIES:
        dropped = len(log) - MAX_ENTRIES
        log = log[-MAX_ENTRIES:]
        log[0] = {**log[0], "trimmed": f"{dropped} {_TRIM_NOTE}"}
    fc["_provenance"] = log
    return fc


def foreign(fc: dict) -> dict:
    """Every FeatureCollection member that is not `type` or `features`.

    These belong to the FILE, not to any one edit: the provenance trail, and
    `_orientation` (which frame the coordinates are in). An endpoint that returns
    a new feature list has to carry them across or they are silently lost -- edit
    a rotated dataset and the file stops saying it is rotated.
    """
    return {k: v for k, v in (fc or {}).items() if k not in ("type", "features")}


def stamped(features: list, source_fc: dict, action: str, detail=None, regions=None) -> dict:
    """Build the response FeatureCollection for an endpoint that returns a new
    feature list: carries the incoming file-level members across and adds this
    action to the trail."""
    out = {**foreign(source_fc), "type": "FeatureCollection", "features": features}
    existing = trail(source_fc)
    out["_provenance"] = [dict(e) for e in existing] if existing else []
    if not out["_provenance"]:
        out.pop("_provenance")
    return stamp(out, action, detail, regions)


def carry(dst: dict, src: dict) -> dict:
    """Copy the file-level members onto a collection without adding an entry."""
    if not isinstance(dst, dict):
        return dst
    for k, v in foreign(src).items():
        dst.setdefault(k, v)
    log = trail(src)
    if log:
        dst["_provenance"] = [dict(e) for e in log]
    return dst


def summary(fc: dict) -> dict:
    """Counts for the sidebar: how many edits, by whom, and when it started."""
    log = trail(fc)
    people, actions = {}, {}
    for e in log:
        w = e.get("who") or "unknown"
        people[w] = people.get(w, 0) + 1
        a = e.get("action") or "?"
        actions[a] = actions.get(a, 0) + 1
    return {
        "count": len(log),
        "people": [{"who": k, "edits": v}
                   for k, v in sorted(people.items(), key=lambda kv: -kv[1])],
        "actions": [{"action": k, "n": v}
                    for k, v in sorted(actions.items(), key=lambda kv: -kv[1])],
        "first": log[0].get("t") if log else None,
        "last": log[-1].get("t") if log else None,
        "me": display_name(),
        "account": account_name(),
    }

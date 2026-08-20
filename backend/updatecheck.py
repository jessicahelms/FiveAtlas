"""Is a newer FiveAtlas on the Releases page? Asked politely, remembered briefly.

No auto-download and no self-replacement -- an unsigned app cannot swap its own
bundle under Gatekeeper, and silently replacing a tool mid-annotation is not a
behaviour this lab wants anyway. The app just says "a newer one exists" and
hands over the download page; the person updates when they choose to.
"""
from __future__ import annotations

import json
import threading
import time
import urllib.request

REPO = "jessicahelms/FiveAtlas"
_TTL = 6 * 3600            # ask GitHub at most a few times a day
_lock = threading.Lock()
_cache = {"at": 0.0, "result": None}


def _parse(v):
    out = []
    for part in str(v).lstrip("v").split("."):
        num = ""
        for ch in part:
            if ch.isdigit():
                num += ch
            else:
                break
        out.append(int(num) if num else 0)
    return tuple((out + [0, 0, 0])[:3])


def check(current: str):
    """-> {current, latest, url, newer} | {current, error}. Never raises, never
    blocks long: one 4-second request, cached for hours, offline is fine."""
    with _lock:
        now = time.time()
        if _cache["result"] is not None and now - _cache["at"] < _TTL:
            cached = dict(_cache["result"])
            cached["current"] = current
            cached["newer"] = ("latest" in cached
                               and _parse(cached["latest"]) > _parse(current))
            return cached
    try:
        req = urllib.request.Request(
            f"https://api.github.com/repos/{REPO}/releases/latest",
            headers={"Accept": "application/vnd.github+json",
                     "User-Agent": f"FiveAtlas/{current}"})
        with urllib.request.urlopen(req, timeout=4) as r:
            data = json.loads(r.read().decode())
        latest = str(data.get("tag_name") or "").lstrip("v")
        result = {"latest": latest,
                  "url": data.get("html_url")
                  or f"https://github.com/{REPO}/releases/latest"}
    except Exception as e:
        # offline / rate-limited / air-gapped lab machine: not an event
        return {"current": current, "error": type(e).__name__}
    with _lock:
        _cache["at"] = time.time()
        _cache["result"] = result
    out = dict(result)
    out["current"] = current
    out["newer"] = _parse(out["latest"]) > _parse(current)
    return out

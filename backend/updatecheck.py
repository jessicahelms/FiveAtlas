"""Is a newer FiveAtlas on the Releases page? Asked politely, remembered briefly.

This only finds out. Fetching and installing the newer version is
selfupdate.py's job, and it never replaces the app mid-annotation: the person
restarts when they choose to (or the switch happens the next time FiveAtlas
is opened). Where it cannot update itself, the page links to the download
page instead.
"""
from __future__ import annotations

import json
import os
import re
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


def _headline(body):
    """The release's first "What's new" title, for the banner to say WHY to
    update ("FiveAtlas 1.3.2 is out -- Faster on laptops").

    CI writes the release body as "# What's new in FiveAtlas vX" followed by
    the CHANGELOG section with its ### titles printed as "## ". Only a title
    under that heading counts: a release with no CHANGELOG section carries the
    install instructions alone, and their headings are not news.
    """
    inside = False
    for line in str(body or "").splitlines():
        s = line.strip()
        if s.startswith("# "):
            inside = s[2:].lower().replace("’", "'").startswith("what's new")
            continue
        if inside and s.startswith("## "):
            title = re.sub(r"[*_`]", "", s[3:]).strip().rstrip(".!:;")
            return title[:100] or None
    return None


def check(current: str):
    """-> {current, latest, headline, url, assets, newer} | {current, error}. Never raises, never
    blocks long: one 4-second request, cached for hours, offline is fine."""
    with _lock:
        now = time.time()
        if _cache["result"] is not None and now - _cache["at"] < _TTL:
            cached = dict(_cache["result"])
            cached["current"] = current
            cached["newer"] = ("latest" in cached
                               and _parse(cached["latest"]) > _parse(current))
            return cached
    # ATLAS_UPDATE_API: a stand-in for GitHub's "latest release" answer, for the
    # self-update tests (tests/self_update_e2e.py). Never set in normal use.
    api = (os.environ.get("ATLAS_UPDATE_API")
           or f"https://api.github.com/repos/{REPO}/releases/latest")
    try:
        req = urllib.request.Request(
            api,
            headers={"Accept": "application/vnd.github+json",
                     "User-Agent": f"FiveAtlas/{current}"})
        with urllib.request.urlopen(req, timeout=4) as r:
            data = json.loads(r.read().decode())
        latest = str(data.get("tag_name") or "").lstrip("v")
        result = {"latest": latest,
                  "headline": _headline(data.get("body")),
                  "url": data.get("html_url")
                  or f"https://github.com/{REPO}/releases/latest",
                  # file name -> download link, for selfupdate.py
                  "assets": {a.get("name"): a.get("browser_download_url")
                             for a in (data.get("assets") or [])
                             if a.get("name") and a.get("browser_download_url")}}
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

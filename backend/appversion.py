"""Which FiveAtlas is running, and which one is installed.

Why this exists: people updated and kept seeing the old version.
  * On a Mac, Finder happily replaces FiveAtlas.app while the old copy is still
    running -- it has no window and no Dock icon, so nobody can see it is up --
    and opening the app again then only wakes the OLD process: macOS matches a
    running app by its bundle and never starts the new copy. Reading the version
    out of the bundle on disk is how the running app finds out it has been
    updated underneath itself (see the watchdog in launcher.py and /api/version).
  * On both platforms a new copy used to see "a FiveAtlas is already up" and hand
    the browser to it, whatever version it was (launcher.py now compares).
"""
from __future__ import annotations

import os
import plistlib
import sys
from pathlib import Path


def version_key(v) -> tuple:
    """(major, minor, patch) of "1.3.1", "v1.3.1" or "1.2.2-live-trial"; a part
    that does not start with a digit counts as 0, and missing parts are 0."""
    out = []
    for part in str(v or "").strip().lstrip("v").split("."):
        num = ""
        for ch in part:
            if not ch.isdigit():
                break
            num += ch
        out.append(int(num) if num else 0)
    return tuple((out + [0, 0, 0])[:3])


def bundle_dir():
    """This process's FiveAtlas.app, or None when it is not the packaged Mac app
    (running from source, or on Windows)."""
    if not getattr(sys, "frozen", False) or sys.platform != "darwin":
        return None
    exe = Path(sys.executable)            # .../FiveAtlas.app/Contents/MacOS/FiveAtlas
    parents = exe.parents
    if len(parents) >= 3 and parents[2].suffix == ".app":
        return parents[2]
    return None


def installed_version():
    """The version now installed where this process was started from, or None
    when that cannot be told. ATLAS_INSTALLED_PLIST points at a plist to read
    instead -- for tests and for trying the update banner off a Mac."""
    override = os.environ.get("ATLAS_INSTALLED_PLIST")
    if override:
        plist = Path(override)
    else:
        app = bundle_dir()
        if app is None:
            return None
        plist = app / "Contents" / "Info.plist"
    try:
        with open(plist, "rb") as fh:
            v = plistlib.load(fh).get("CFBundleShortVersionString")
        return str(v) if v else None
    except Exception:
        return None


def replaced_on_disk(running: str):
    """The installed version when it differs from the running one, else None."""
    inst = installed_version()
    return inst if inst and inst != str(running) else None

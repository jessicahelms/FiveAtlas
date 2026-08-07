"""Paths and app-wide config for the FiveAtlas backend.

Works both from source (dev: Vite on :5173 proxying here) and inside a PyInstaller
bundle (packaged: this server serves the built UI too -- one process, one port).
"""
import os
import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent
APP_DIR = BACKEND_DIR.parent                 # atlas_editor/

# Stamped into every provenance entry, so a region file records which build made
# each edit. build_release.ps1 exports FIVEATLAS_VERSION (FiveAtlas.spec reads the
# same variable for the bundle version), so a build from source agrees with the
# filename; the literal is the fallback for a plain `python -m uvicorn` run.
VERSION = os.environ.get("FIVEATLAS_VERSION") or "0.4.1"

# PyInstaller unpacks bundled data to sys._MEIPASS; from source it's the app dir.
FROZEN = bool(getattr(sys, "frozen", False))
BUNDLE_DIR = Path(getattr(sys, "_MEIPASS", str(APP_DIR)))

# The built frontend. Packaged, it ships inside the bundle as `frontend_dist`.
FRONTEND_DIST = (BUNDLE_DIR / "frontend_dist") if FROZEN else (APP_DIR / "frontend" / "dist")


def _default_workdir() -> Path:
    """Where edited GeoJSON and the opened-dataset list live.

    Packaged this MUST be outside the bundle: on Windows the install folder is
    read-only, and on macOS the .app is code-signed so writing inside it breaks
    the signature (and Gatekeeper then refuses to launch it). From source we keep
    the historical backend/workdir, so edits already on disk stay where they are.

    Per-platform, packaged:
      Windows  %LOCALAPPDATA%\\FiveAtlas
      macOS    ~/Library/Application Support/FiveAtlas
      Linux    $XDG_DATA_HOME/FiveAtlas  (else ~/.local/share/FiveAtlas)
    """
    override = os.environ.get("ATLAS_WORKDIR")
    if override:
        return Path(override)
    if FROZEN:
        if sys.platform == "win32":
            base = os.environ.get("LOCALAPPDATA") or os.environ.get("APPDATA")
            return (Path(base) if base else Path.home()) / "FiveAtlas"
        if sys.platform == "darwin":
            return Path.home() / "Library" / "Application Support" / "FiveAtlas"
        base = os.environ.get("XDG_DATA_HOME")
        return (Path(base) if base else Path.home() / ".local" / "share") / "FiveAtlas"
    return BACKEND_DIR / "workdir"


# Local working directory for edited GeoJSON. We NEVER overwrite the user's
# original files on disk; edits land here instead.
WORKDIR = _default_workdir()
WORKDIR.mkdir(parents=True, exist_ok=True)

# Native tile size of the morphology OME-TIFF pyramid (verified: 1024x1024).
TILE_SIZE = 1024

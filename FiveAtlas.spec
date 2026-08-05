# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for FiveAtlas. Builds on Windows and macOS from one file.

One-FOLDER build on purpose, not --onefile: onefile re-extracts a few hundred MB
to %TEMP% on every launch (15-30 s startup, and the antivirus rescans it each
time). The folder build starts in a couple of seconds and zips just as well.
On macOS the same folder is wrapped in a .app bundle, which is the only shape
Finder, Gatekeeper and codesigning all understand.

Build with:  pyinstaller --noconfirm FiveAtlas.spec
(run `npm run build` in frontend/ first -- the built UI is bundled in)

macOS note: PyInstaller cannot cross-compile, and it cannot make a universal2
build unless every wheel is universal2 (imagecodecs is not). Build on the arch
you are shipping to -- an arm64 Mac produces an Apple Silicon app, an Intel Mac
or a macos-13 CI runner produces an Intel one.
"""
import sys
from pathlib import Path

from PyInstaller.utils.hooks import collect_dynamic_libs, collect_submodules

APP_DIR = Path(SPECPATH).resolve()
BACKEND = APP_DIR / "backend"
DIST_UI = APP_DIR / "frontend" / "dist"

MACOS = sys.platform == "darwin"
VERSION = "0.1.0"

if not (DIST_UI / "index.html").exists():
    raise SystemExit(
        f"frontend not built: {DIST_UI / 'index.html'} is missing.\n"
        "Run `npm run build` in atlas_editor/frontend first."
    )

# The backend imports its modules by bare name (import datasets, import scan...),
# so they have to be collected explicitly rather than discovered from launcher.py.
BACKEND_MODULES = [
    "app", "config", "datasets", "geo", "genes", "nativedialog",
    "scan", "stains", "tiles", "topology", "transcripts",
]

# uvicorn resolves its loop/protocol implementations at runtime by string.
UVICORN_HIDDEN = collect_submodules("uvicorn")

hiddenimports = (
    BACKEND_MODULES
    + UVICORN_HIDDEN
    + collect_submodules("imagecodecs")     # JPEG2000 decode for the morphology pyramid
    + [
        "anyio", "click", "h11",
        "shapely", "shapely.geometry", "shapely.ops", "shapely.strtree",
        "shapely.validation", "shapely._geos",
        "tifffile", "zarr", "numcodecs",
        "PIL.Image", "PIL.ImageDraw",
    ]
)

# The tkinter picker is only a fallback, and on macOS it is never reached (see
# nativedialog._pick_mac). Homebrew/pyenv Pythons often ship without Tk, so ask
# for it only when it actually imports -- otherwise the build fails on a module
# the app does not need.
try:
    import tkinter  # noqa: F401
    hiddenimports += ["tkinter", "tkinter.filedialog"]
except Exception:
    pass

# shapely ships GEOS as bundled DLLs/dylibs; imagecodecs ships a pile of codecs.
binaries = collect_dynamic_libs("shapely") + collect_dynamic_libs("imagecodecs")

a = Analysis(
    [str(APP_DIR / "launcher.py")],
    pathex=[str(BACKEND), str(APP_DIR)],
    binaries=binaries,
    datas=[(str(DIST_UI), "frontend_dist")],
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["matplotlib", "scipy", "pandas", "IPython", "notebook", "pytest"],
    noarchive=False,
    optimize=0,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="FiveAtlas",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    # Windows: console stays ON, so colleagues get the URL, the workdir path and
    # any error text in the window instead of a silent failure.
    # macOS: a .app has no console, so it goes off and launcher.py mirrors the
    # same text to FiveAtlas.log plus a native alert on a fatal error.
    console=not MACOS,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,          # host arch; see the module docstring
    codesign_identity=None,    # PyInstaller ad-hoc signs on arm64, which is enough to run
    entitlements_file=None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="FiveAtlas",
)

if MACOS:
    app = BUNDLE(
        coll,
        name="FiveAtlas.app",
        icon=None,
        bundle_identifier="com.fivelab.fiveatlas",
        version=VERSION,
        info_plist={
            "CFBundleName": "FiveAtlas",
            "CFBundleDisplayName": "FiveAtlas",
            "CFBundleShortVersionString": VERSION,
            "CFBundleVersion": VERSION,
            "NSHighResolutionCapable": True,
            "LSMinimumSystemVersion": "11.0",
            # The app opens the user's browser and reads data folders they pick;
            # it never needs to be the frontmost app on its own.
            "LSBackgroundOnly": False,
            "NSHumanReadableCopyright": "Five Lab",
        },
    )

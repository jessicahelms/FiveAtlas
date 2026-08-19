#!/usr/bin/env bash
#
# Build the shippable FiveAtlas for macOS. The counterpart to build_release.ps1.
#
# Produces, in <out-root>/release/ :
#   FiveAtlas.app                        the app (drag to /Applications)
#   FiveAtlas-<ver>-macos-<arch>.dmg     what you send people
#   FiveAtlas-<ver>-macos-<arch>.zip     same thing, for people who prefer a zip
#
# Everything the build writes goes to <out-root> (~1 GB of scratch), which
# defaults to ~/Library/Caches/FiveAtlas_build -- outside the repo, so build
# trees never show up in git status.
#
# PyInstaller cannot cross-compile: this must run ON a Mac, and the app it makes
# is for the arch of that Mac. Apple Silicon Mac -> arm64 app. (Intel is not
# built or shipped; every Mac sold since 2021 is Apple Silicon.)
#
# Usage:
#     ./build_release_mac.sh
#     ./build_release_mac.sh --version 0.2.0
#     ./build_release_mac.sh --out-root /Volumes/Scratch/builds --skip-frontend
#
set -euo pipefail

# Default: the git tag this tree is at, so a local build is labelled the same
# way CI labels it. --version overrides; 0.0.0-dev if there is no tag at all.
VERSION="$(git -C "$(dirname "${BASH_SOURCE[0]}")" describe --tags --dirty --always 2>/dev/null | sed 's/^v//')"
VERSION="${VERSION:-0.0.0-dev}"
OUT_ROOT="${HOME}/Library/Caches/FiveAtlas_build"
SKIP_FRONTEND=0

while [[ $# -gt 0 ]]; do
    case "$1" in
        --version)       VERSION="$2"; shift 2 ;;
        --out-root)      OUT_ROOT="$2"; shift 2 ;;
        --skip-frontend) SKIP_FRONTEND=1; shift ;;
        -h|--help)       sed -n '2,26p' "$0"; exit 0 ;;
        *) echo "unknown option: $1" >&2; exit 2 ;;
    esac
done

APP="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO="$(dirname "$APP")"
ARCH="$(uname -m)"

# Same convention as the Windows script: reuse the repo venv if there is one,
# otherwise fall back to whatever python3 is on PATH.
if [[ -x "$REPO/.venv/bin/python" ]]; then
    PY="$REPO/.venv/bin/python"
else
    PY="$(command -v python3 || true)"
    [[ -n "$PY" ]] || { echo "no python found - create $REPO/.venv first" >&2; exit 1; }
    echo "   note: $REPO/.venv not found, using $PY"
fi

WORK="$OUT_ROOT/work"
DIST_DIR="$OUT_ROOT/dist"
RELEASE="$OUT_ROOT/release"
SCRATCH="$OUT_ROOT/temp"        # launch-check log + throwaway workdir
mkdir -p "$OUT_ROOT" "$WORK" "$DIST_DIR" "$RELEASE" "$SCRATCH"

echo "== FiveAtlas $VERSION (macOS $ARCH) =="
echo "   build output -> $OUT_ROOT"
echo "   python       -> $PY"

# 1. the UI ------------------------------------------------------------------
if [[ "$SKIP_FRONTEND" -eq 0 ]]; then
    echo "[1/5] building frontend..."
    ( cd "$APP/frontend" && npm run build )
else
    echo "[1/5] skipping frontend build"
fi
[[ -f "$APP/frontend/dist/index.html" ]] || {
    echo "frontend/dist/index.html missing - the UI did not build" >&2; exit 1; }

# 2. the app -----------------------------------------------------------------
echo "[2/5] running PyInstaller (a few minutes)..."
# A running copy holds its files open and confuses the rebuild.
pkill -f "FiveAtlas.app/Contents/MacOS/FiveAtlas" 2>/dev/null || true
# The spec reads this, so CFBundleVersion matches the .dmg filename and the tag.
export FIVEATLAS_VERSION="$VERSION"
( cd "$APP" && "$PY" -m PyInstaller --noconfirm --clean \
      --workpath "$WORK" --distpath "$DIST_DIR" FiveAtlas.spec )

BUILT="$DIST_DIR/FiveAtlas.app"
[[ -d "$BUILT" ]] || { echo "FiveAtlas.app was not produced" >&2; exit 1; }

# 3. sign --------------------------------------------------------------------
# On Apple Silicon an unsigned binary will not execute at all -- but PyInstaller
# has ALREADY ad-hoc signed every Mach-O it produced, so there is nothing to do
# in the default case.
#
# Do NOT "just re-sign to be safe" with `codesign --force --deep --sign -`: on a
# PyInstaller bundle that clobbers the per-binary signatures PyInstaller made,
# and the kernel then SIGKILLs the app the moment it loads. It fails silently
# too -- `codesign --verify` still passes, because the signature is structurally
# valid, it just no longer matches what the loader wants. That mistake cost this
# build one CI round; the app packaged fine and died instantly on launch.
#
# Set CODESIGN_ID to a "Developer ID Application: ..." identity to produce
# something colleagues can open without the quarantine dance. Even then, no
# --deep: it is deprecated, and the inner code is already signed.
echo "[3/5] signing..."
if [[ -n "${CODESIGN_ID:-}" ]]; then
    codesign --force --sign "$CODESIGN_ID" --options runtime --timestamp "$BUILT"
    echo "      signed with: $CODESIGN_ID"
else
    echo "      leaving PyInstaller's ad-hoc signature alone"
fi
# Under `set -e`, `cmd && echo` does NOT stop the script when cmd fails, so a
# broken signature used to be printed past rather than acted on.
if codesign --verify --strict "$BUILT"; then
    echo "      signature verifies"
else
    echo "the bundle signature does not verify -- refusing to package it" >&2
    exit 1
fi

# Verifying is not the same as running: a bundle whose inner signatures have been
# clobbered verifies fine and is SIGKILLed the moment the kernel loads it. So
# actually start the thing and see whether it is still alive a few seconds later.
#
# Start it as the server, NOT with `--pick`: --pick opens a real modal file
# dialog, which on a machine with nobody to answer it blocks for the picker's
# full timeout. That mistake hung a CI build until the job timed out.
echo "      launch check..."
LAUNCH_LOG="$SCRATCH/launch-check.log"
ATLAS_NO_BROWSER=1 ATLAS_PORT=8062 ATLAS_WORKDIR="$SCRATCH/launch-check-workdir" \
    "$BUILT/Contents/MacOS/FiveAtlas" >"$LAUNCH_LOG" 2>&1 &
LPID=$!
sleep 6
if kill -0 "$LPID" 2>/dev/null; then
    echo "      launches OK"
    kill "$LPID" 2>/dev/null || true
    wait "$LPID" 2>/dev/null || true
else
    rc=0; wait "$LPID" || rc=$?
    echo "the app exits immediately (code $rc) -- it will not run for anyone" >&2
    [[ $rc -eq 137 || $rc -eq 9 ]] && \
        echo "  137/9 = SIGKILL: the kernel is rejecting the bundle's signature" >&2
    echo "---- app output ----" >&2
    cat "$LAUNCH_LOG" >&2 2>/dev/null || true
    # Under Actions, also emit it as a check-run annotation: workflow logs need
    # admin rights to fetch through the API, but annotations are public, so this
    # is the only copy of the error readable from outside the browser.
    if [[ -n "${GITHUB_ACTIONS:-}" ]]; then
        body=$(head -c 4000 "$LAUNCH_LOG" 2>/dev/null | python3 -c \
          "import sys;print(sys.stdin.read().replace('%','%25').replace('\r','%0D').replace('\n','%0A'))")
        echo "::error title=launch-check (exit $rc)::${body}"
    fi
    exit 1
fi

STAGED="$RELEASE/FiveAtlas.app"
rm -rf "$STAGED"
# ditto, not cp: it preserves symlinks, resource forks and the signature.
ditto "$BUILT" "$STAGED"

cat > "$RELEASE/README.txt" <<EOF
FiveAtlas $VERSION  (macOS, Apple Silicon / $ARCH)

WHAT IT RUNS ON
  An Apple Silicon Mac (M1, M2, M3, M4...) on macOS 11 Big Sur or newer.
  Check: Apple menu -> About This Mac -> "Chip". If it says Intel, this build
  will not open -- there is no Intel build.
  Download the .dmg ON THE MAC ITSELF. Passing it through a Windows computer or
  a shared drive can strip the parts macOS needs and you will get "damaged".

INSTALL
  Double-click the .dmg, drag FiveAtlas onto the Applications folder in that
  window, then eject the disk image. Always start it from Applications.

FIRST LAUNCH -- macOS will refuse it once
  It will say FiveAtlas "is damaged and can't be opened", or "Apple could not
  verify FiveAtlas is free of malware". That is Gatekeeper reacting to an app
  that did not come through the App Store, not a fault in the app. The fix
  that always works, once:
     1. Press Cmd+Space, type  Terminal  and press Return.
     2. Paste this one line exactly and press Return:
            xattr -dr com.apple.quarantine /Applications/FiveAtlas.app
        (nothing is printed when it worked)
     3. Open FiveAtlas again.
  On macOS 15 Sequoia you can instead open System Settings -> Privacy &
  Security, scroll down to where it says FiveAtlas was blocked, and press
  "Open Anyway". Right-click -> Open no longer works there.

RUNNING IT
  FiveAtlas has no window of its own. Open it and, after a few seconds (up to a
  minute the very first time, while macOS checks it), a tab opens in your
  browser at  http://127.0.0.1:8050  -- if no tab appears, type that address
  into your browser yourself. It keeps serving while that tab is open.
  To stop it: press "Quit FiveAtlas" at the bottom of the sidebar. It also
  stops by itself about ten minutes after the last browser tab is closed.
  (Quit FiveAtlas from the Dock does nothing -- it is not a windowed app.)

YOUR DATA
  Connect to the lab share in Finder first (Go -> Connect to Server). Then in
  FiveAtlas press Open Folder and pick the experiment folder under Locations.
  Pasting a path works too: it must look like /Volumes/... (not smb://...).

WHERE THINGS GO
  Your original files are never modified. Edits, the dated backups of every
  Save, and the log (FiveAtlas.log) live in
     ~/Library/Application Support/FiveAtlas
  (Finder: Shift+Cmd+G and paste that path.) If FiveAtlas will not start, that
  log is what to send. Use Export in the sidebar to write GeoJSON out.
EOF

# 4. dmg ---------------------------------------------------------------------
echo "[4/5] building dmg..."
DMG="$RELEASE/FiveAtlas-$VERSION-macos-$ARCH.dmg"
rm -f "$DMG"
STAGE="$(mktemp -d)"
trap 'rm -rf "$STAGE"' EXIT
ditto "$STAGED" "$STAGE/FiveAtlas.app"
cp "$RELEASE/README.txt" "$STAGE/README.txt"
ln -s /Applications "$STAGE/Applications"      # the familiar drag-to-install layout
hdiutil create -volname "FiveAtlas $VERSION" -srcfolder "$STAGE" \
    -ov -format UDZO "$DMG" >/dev/null
hdiutil verify "$DMG" >/dev/null || { echo "dmg does not verify" >&2; exit 1; }

# 5. zip ---------------------------------------------------------------------
# The zip carries the README too: the .dmg had it and the .zip did not, so
# whoever took the zip got no install or Gatekeeper instructions at all. A
# parent folder, so it unzips tidily rather than spraying into Downloads.
echo "[5/5] zipping..."
ZIP="$RELEASE/FiveAtlas-$VERSION-macos-$ARCH.zip"
rm -f "$ZIP"
ZSTAGE="$(mktemp -d)/FiveAtlas-$VERSION"
mkdir -p "$ZSTAGE"
ditto "$STAGED" "$ZSTAGE/FiveAtlas.app"
cp "$RELEASE/README.txt" "$ZSTAGE/README.txt"
ditto -c -k --sequesterRsrc --keepParent "$ZSTAGE" "$ZIP"
rm -rf "$(dirname "$ZSTAGE")"

# checksums, so a download can be checked against what CI produced
( cd "$RELEASE" && shasum -a 256 "$(basename "$DMG")" "$(basename "$ZIP")" > SHA256SUMS )

echo
echo "Done. In $RELEASE :"
du -sh "$RELEASE"/* 2>/dev/null | sed 's/^/  /'

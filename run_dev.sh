#!/usr/bin/env bash
#
# Run FiveAtlas from source on macOS or Linux -- the equivalent of run.bat.
#
# Creates ../.venv on first run, installs the backend deps, builds the UI if it
# has not been built, then starts the app exactly the way the packaged build
# does: one process serving both the API and the UI, browser opened on it.
#
# Usage:
#     ./run_dev.sh              start it
#     ./run_dev.sh --vite       dev mode instead: Vite on :5173 with hot reload,
#                               proxying the API to the backend on :8050
#
set -euo pipefail

APP="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO="$(dirname "$APP")"
VENV="$REPO/.venv"
VITE=0
[[ "${1:-}" == "--vite" ]] && VITE=1

# 1. python env --------------------------------------------------------------
if [[ ! -x "$VENV/bin/python" ]]; then
    echo "== creating $VENV (first run, a few minutes) =="
    PY="$(command -v python3.12 || command -v python3.11 || command -v python3)"
    [[ -n "$PY" ]] || { echo "python3 not found - install it from python.org" >&2; exit 1; }
    "$PY" -m venv "$VENV"
    "$VENV/bin/python" -m pip install --upgrade pip
    # The top-level file is the pinned one; backend/requirements.txt is the loose
    # list and pulls in rasterio, which nothing in the app actually imports.
    "$VENV/bin/python" -m pip install -r "$APP/requirements.txt"
fi
PY="$VENV/bin/python"

# 2. the UI ------------------------------------------------------------------
# Packaged mode serves frontend/dist, so it has to exist. Vite mode does not
# need it -- it serves the sources itself.
if [[ "$VITE" -eq 0 && ! -f "$APP/frontend/dist/index.html" ]]; then
    echo "== building the UI (needs Node.js) =="
    command -v npm >/dev/null || {
        echo "npm not found - install Node.js from nodejs.org, or use --vite" >&2; exit 1; }
    ( cd "$APP/frontend" && npm install && npm run build )
fi

# 3. go ----------------------------------------------------------------------
if [[ "$VITE" -eq 1 ]]; then
    echo "== backend on :8050, Vite on :5173 =="
    command -v npm >/dev/null || { echo "npm not found - install Node.js" >&2; exit 1; }
    [[ -d "$APP/frontend/node_modules" ]] || ( cd "$APP/frontend" && npm install )
    # Backend in the background, and make sure it dies with this script rather
    # than surviving as an orphan holding :8050.
    ATLAS_NO_BROWSER=1 ATLAS_PORT=8050 "$PY" "$APP/launcher.py" &
    BACKEND=$!
    trap 'kill "$BACKEND" 2>/dev/null || true' EXIT INT TERM
    # vite.config.js proxies /api to :8000 unless told otherwise, and the
    # launcher defaults to :8050 -- point it at the one we just started.
    ( cd "$APP/frontend" && ATLAS_API_TARGET="http://127.0.0.1:8050" npm run dev )
else
    exec "$PY" "$APP/launcher.py"
fi

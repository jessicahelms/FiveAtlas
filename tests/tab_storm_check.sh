#!/usr/bin/env bash
#
# Regression check for the browser tab storm.
#
# v0.1.5 shipped an app that, on at least one Mac, opened browser tabs faster
# than the user could force-quit it. The root cause was never reproduced here,
# so launcher.py carries a circuit breaker instead: whatever starts the app
# repeatedly, the tabs must stay bounded. This asserts that property against the
# REAL built bundle, because that is where it failed -- not from source.
#
# Python's webbrowser honours $BROWSER, so we point it at a stub that records a
# line per "tab" rather than opening anything.
#
# Usage:
#     tests/tab_storm_check.sh <path-to-executable> [launches] [max-tabs]
#
# e.g. tests/tab_storm_check.sh dist/FiveAtlas.app/Contents/MacOS/FiveAtlas
#
set -uo pipefail

APP="${1:?usage: tab_storm_check.sh <executable> [launches] [max-tabs]}"
LAUNCHES="${2:-10}"
MAX_TABS="${3:-3}"

[[ -x "$APP" ]] || { echo "not executable: $APP" >&2; exit 2; }

TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

TALLY="$TMP/opens.txt"
: > "$TALLY"

cat > "$TMP/stub.sh" <<STUB
#!/bin/sh
echo "\$@" >> "$TALLY"
STUB
chmod +x "$TMP/stub.sh"

export BROWSER="$TMP/stub.sh"
export ATLAS_WORKDIR="$TMP/workdir"
export ATLAS_PORT="8058"
unset ATLAS_NO_BROWSER

echo "launching $LAUNCHES times in quick succession..."
for _ in $(seq 1 "$LAUNCHES"); do
    "$APP" >/dev/null 2>&1 &
    sleep 0.5
done

# Give the last starter time to reach its browser-open point before counting.
sleep 8
pkill -f "$(basename "$APP")" 2>/dev/null || true
sleep 1

N=$(wc -l < "$TALLY" 2>/dev/null | tr -d '[:space:]')
N="${N:-0}"
echo "$LAUNCHES launches opened $N browser tab(s); ceiling is $MAX_TABS"

if [[ "$N" -gt "$MAX_TABS" ]]; then
    echo "::error title=tab-storm::$LAUNCHES launches opened $N browser tabs (max $MAX_TABS). The circuit breaker in launcher.py is not holding."
    echo "FAIL: the tab storm can happen again" >&2
    exit 1
fi

# Zero is not a pass: it means the app never got as far as opening a browser,
# so this run proved nothing about the breaker.
if [[ "$N" -eq 0 ]]; then
    echo "::error title=tab-storm::no browser opens recorded at all -- the app never started, so this check was vacuous"
    echo "FAIL: expected at least one tab; the check did not actually exercise anything" >&2
    exit 1
fi

echo "OK"

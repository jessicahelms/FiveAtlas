"""Entry point for the packaged FiveAtlas.

One process: FastAPI serves both the API and the built UI, on one free port, and
the default browser is opened on it. Also serves as the app's own file-picker
helper -- see `--pick` below.

Usage:
    FiveAtlas                         start the app
    FiveAtlas --pick folder DIR       show the folder picker, print the choice
    FiveAtlas --pick file   DIR       show the file picker, print the choice

The --pick mode exists because a modal dialog must not run inside the server
process, and once frozen `sys.executable` is this exe rather than a Python
interpreter, so the backend re-launches *itself* to put a picker on screen.

Windows ships as a console app: the window IS the status display, and closing it
stops the server. macOS ships as a .app bundle, which has no console at all --
there `sys.stdout` can even be None -- so everything that would have been printed
is mirrored to FiveAtlas.log in the workdir, and a fatal startup error is raised
as a native alert instead of dying silently in the Dock.
"""
from __future__ import annotations

import os
import socket
import sys
import threading
import time
import webbrowser
from pathlib import Path

# From source, the backend modules are a sibling package dir; frozen, PyInstaller
# has already put them at the top level.
_HERE = Path(__file__).resolve().parent
if not getattr(sys, "frozen", False):
    sys.path.insert(0, str(_HERE / "backend"))


class _Tee:
    """Write to a log file and, when there is one, the real stream.

    In a windowed (no-console) bundle PyInstaller sets sys.stdout to None, so a
    bare print() would raise AttributeError. This stands in for it either way.
    """

    def __init__(self, stream, handle):
        self._stream = stream
        self._handle = handle

    def write(self, text):
        for target in (self._stream, self._handle):
            try:
                if target is not None:
                    target.write(text)
                    # Flush both, not just the log: a stream that is not a tty
                    # (output redirected to a file, or the app launched from a
                    # wrapper) is block-buffered, so the banner would otherwise
                    # sit in the buffer until exit and look like a hang.
                    target.flush()
            except Exception:
                pass
        return len(text)

    def flush(self):
        for target in (self._stream, self._handle):
            try:
                if target is not None:
                    target.flush()
            except Exception:
                pass

    def isatty(self):
        try:
            return bool(self._stream is not None and self._stream.isatty())
        except Exception:
            return False

    # Libraries poke at these on sys.stdout (click/uvicorn read .encoding,
    # subprocess asks for .fileno() when stdout is passed through). A missing
    # attribute there is an AttributeError deep inside someone else's code.
    encoding = "utf-8"
    errors = "replace"

    def fileno(self):
        if self._stream is None:
            raise OSError("no underlying stream")
        return self._stream.fileno()


def _start_logging(workdir: Path):
    """Mirror stdout/stderr into <workdir>/FiveAtlas.log.

    The streams are ALWAYS wrapped, even when the log cannot be opened: in a
    windowed bundle sys.stdout is None, and uvicorn calls sys.stdout.isatty()
    while building its log formatter -- so "could not open the log" used to
    turn into an AttributeError that killed the server before it served a
    request. The log is best effort; a usable stdout is not.
    """
    handle = None
    try:
        workdir.mkdir(parents=True, exist_ok=True)
        path = workdir / "FiveAtlas.log"
        # Keep it from growing without bound: past ~5 MB, start over and keep
        # the previous one beside it.
        try:
            if path.exists() and path.stat().st_size > 5 * 1024 * 1024:
                path.replace(path.with_suffix(".log.1"))
        except Exception:
            pass
        handle = open(path, "a", encoding="utf-8", buffering=1)
    except Exception:
        handle = None
    sys.stdout = _Tee(sys.stdout, handle)
    sys.stderr = _Tee(sys.stderr, handle)


def _alert(message: str):
    """Surface a fatal error where a GUI-launched app has no console to print to.
    No-op on Windows, where the console window is already showing the traceback.

    A modal alert is a trap on any machine with nobody in front of it: the dialog
    waits forever, the process stays alive holding it, and from the outside the
    app looks like it started and then hung. So it is skipped whenever we are
    clearly running unattended, and capped at a few seconds even when we are not.
    ATLAS_NO_BROWSER is the same flag the smoke tests and CI already set to mean
    "no one is watching this".
    """
    if sys.platform != "darwin":
        return
    if os.environ.get("ATLAS_NO_BROWSER") or os.environ.get("CI"):
        return
    try:
        import subprocess
        # Newlines MUST become the two-character escape \n. An AppleScript string
        # literal cannot span lines, and osascript treats a real newline inside
        # -e as a script line break -- so the whole thing failed to compile and
        # exited 1 without ever drawing a dialog. Both callers pass a message
        # containing "\n\n", so this alert had never once appeared: every fatal
        # macOS startup error vanished in exactly the silence it exists to avoid.
        body = (message.replace("\\", "\\\\")
                       .replace('"', '\\"')
                       .replace("\r", "")
                       .replace("\n", "\\n"))
        # `activate` first so the alert is not behind the browser; "giving up
        # after" keeps it on screen long enough to be read (the old 15 s
        # timeout killed osascript -- and the dialog -- before most people had
        # found it), while still guaranteeing the process cannot hang on it.
        res = subprocess.run(
            ["osascript", "-e", "activate", "-e",
             f'display alert "FiveAtlas could not start" message "{body}" '
             f'as critical giving up after 300'],
            capture_output=True, text=True, timeout=310)
        if res.returncode != 0:
            print(f"[alert] osascript failed ({res.returncode}): "
                  f"{(res.stderr or '').strip()}", file=sys.stderr)
    except Exception as e:
        print(f"[alert] could not display the alert: {e}", file=sys.stderr)


def _run_picker_mode() -> bool:
    """Handle a picker request and return True if this process was one.

    Two channels, because getting this wrong is expensive: miss the request and
    the process falls through to main(), starting a second server and opening
    another browser tab. argv is the primary one; the environment is the backstop
    for a windowed macOS .app, where argv is the least reliable part of the
    bootloader. See app._picker_cmd, which sets both.
    """
    if len(sys.argv) >= 3 and sys.argv[1] == "--pick":
        kind = sys.argv[2]
        initial = sys.argv[3] if len(sys.argv) > 3 else None
    elif os.environ.get("ATLAS_PICK_KIND"):
        kind = os.environ["ATLAS_PICK_KIND"]
        initial = os.environ.get("ATLAS_PICK_INITIAL") or None
    else:
        return False

    import nativedialog
    # Anything that is not "folder" used to fall through to the *file* picker,
    # so a typo silently opened a modal dialog and blocked until someone closed
    # it -- which on a headless machine is never. Refuse instead.
    if kind not in ("folder", "file"):
        print(f"[picker] unknown kind {kind!r}; expected 'folder' or 'file'",
              file=sys.stderr)
        return True
    title = "Select dataset folder" if kind == "folder" else "Select a GeoJSON region file"
    try:
        print(nativedialog.pick(kind, initial or None, title))
    except Exception as e:
        print("", file=sys.stdout)
        print(f"[picker] {e}", file=sys.stderr)
    return True


def _free_port(preferred: int = 8050) -> int:
    """The preferred port if it's free, otherwise whatever the OS hands out --
    a colleague's machine may well have something else on 8050."""
    for candidate in (preferred, 0):
        s = socket.socket()
        try:
            s.bind(("127.0.0.1", candidate))
            return s.getsockname()[1]
        except OSError:
            continue
        finally:
            s.close()
    return preferred


def _open_when_up(url: str, timeout: float = 30.0):
    """Open the browser once the server actually answers, so the first paint
    isn't a connection error."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        s = socket.socket()
        s.settimeout(0.4)
        try:
            s.connect(("127.0.0.1", int(url.rsplit(":", 1)[1])))
            break
        except OSError:
            time.sleep(0.2)
        finally:
            s.close()
    opened = False
    try:
        opened = bool(webbrowser.open(url))
    except Exception:
        opened = False
    if not opened and sys.platform == "darwin":
        # webbrowser on macOS goes through osascript; if that is blocked (MDM,
        # a missing default browser entry) fall back to the system opener.
        try:
            import subprocess
            subprocess.run(["open", url], check=False, timeout=10)
            opened = True
        except Exception:
            pass
    print(f"[browser] {'opened' if opened else 'could NOT open'} {url}"
          + ("" if opened else " -- open it by hand"))


def _already_running(port: int) -> bool:
    """True if a FiveAtlas is already serving on this port.

    Second line of defence against the tab storm: even if something does manage
    to start us twice, the second one hands the browser to the instance that is
    already up instead of racing it for a port and opening another tab.
    """
    import json
    import urllib.request
    try:
        # 4 s, not 1: an instance busy decoding tiles can take longer than a
        # second to answer, and a miss here means a second server + second tab.
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/health", timeout=4.0) as r:
            return bool(json.loads(r.read().decode()).get("ok"))
    except Exception:
        return False


def _claim_browser_open(workdir: Path, window: float = 60.0, burst: int = 3) -> bool:
    """Cross-process circuit breaker on opening the browser.

    A backstop, not a fix for any particular cause. Whatever makes the app start
    more than once -- a relaunch loop, an impatient double-click, some quirk of
    how a .app gets launched -- the cost must never be an unbounded pile of
    browser tabs. That failure mode is genuinely awful: it outruns force-quit,
    because by the time you have killed one process the next tab is already open,
    and you cannot reach the UI to stop it.

    Deliberately NOT a flat cooldown. Quitting and relaunching within a few
    seconds is a perfectly normal thing to do, and on macOS the app has no
    console, so a suppressed tab means the user sees nothing happen at all.
    Instead: allow the first few opens in a window, then trip. A human never
    relaunches four times in a minute; a spawn loop does it in under a second.

    Fails open. An app that never shows itself is worse than one extra tab.
    """
    stamp = workdir / ".browser-opens"
    now = time.time()
    started, count = now, 0
    # The read-modify-write below is what the breaker IS, so N processes racing
    # through it could all read "0" and all open a tab. Hold an advisory lock
    # across it where the OS has one (the storm was POSIX-only to begin with);
    # if locking fails for any reason, carry on unlocked -- fail open.
    lock_fh = None
    try:
        import fcntl
        lock_fh = open(workdir / ".browser-opens.lock", "a+")
        fcntl.flock(lock_fh.fileno(), fcntl.LOCK_EX)
    except Exception:
        lock_fh = None
    try:
        try:
            if stamp.exists():
                parts = stamp.read_text(encoding="utf-8").split()
                if len(parts) == 2:
                    started, count = float(parts[0]), int(parts[1])
                    if now - started > window:          # old burst, start a fresh one
                        started, count = now, 0
        except Exception:
            started, count = now, 0

        if count >= burst:
            print(f"[browser] {count} launches in {int(now - started)}s -- not opening "
                  f"another tab. Open the address above by hand if you need it.",
                  file=sys.stderr)
            return False

        try:
            stamp.write_text(f"{started} {count + 1}", encoding="utf-8")
        except Exception:
            pass
        return True
    finally:
        if lock_fh is not None:
            try:
                import fcntl
                fcntl.flock(lock_fh.fileno(), fcntl.LOCK_UN)
                lock_fh.close()
            except Exception:
                pass


def _watchdog(backend, every: float = 15.0, silence: float = 600.0):
    """Stop the server once the UI has been gone for a long while.

    The packaged Mac app has no window, no console and -- because the process
    has no Cocoa event loop -- nothing the Dock can quit. If the user simply
    closes the browser tab, the server would otherwise live until logout, and
    double-clicking the app again does nothing visible (LaunchServices sends a
    reopen event to the running process instead of starting a new one). So the
    UI sends a heartbeat, and when it has been silent for `silence` seconds the
    server exits, which lets the next double-click start a fresh one.

    Two things make this safe rather than trigger-happy:
      * it does not start counting until a UI has connected at least once, so
        starting the app and not opening the tab is not a death sentence, and
        CI's headless smoke tests are untouched;
      * `silence` is generous (10 min) and is measured in consecutive MISSED
        TICKS, not wall clock -- browsers throttle a background tab's timers to
        about once a minute, which still resets the count every tick or two,
        and a laptop asleep neither ticks nor counts.
    Quitting on purpose is the Quit button in the sidebar (POST /api/quit).
    """
    import signal
    ticks_needed = max(1, int(silence / every))
    missed = 0
    while True:
        time.sleep(every)
        last = getattr(backend, "LAST_PING", None)
        if last is None:
            continue                          # no UI has ever connected
        if time.monotonic() - last < every * 2:
            missed = 0
        else:
            missed += 1
        if missed >= ticks_needed:
            print(f"[watchdog] no UI heartbeat for ~{int(silence)}s -- stopping.")
            try:
                os.kill(os.getpid(), signal.SIGTERM)
            except Exception:
                os._exit(0)
            return


def main():
    if _run_picker_mode():
        return

    # A process spawned as a helper must never become a server, even if it failed
    # to parse what kind of helper it was meant to be.
    if os.environ.get("ATLAS_CHILD"):
        print("[picker] helper process with no recognised picker mode; exiting",
              file=sys.stderr)
        return

    # Allowlist argv: serve only for arguments we actually recognise.
    #
    # This is the storm. A frozen app has no importable __main__, so CPython
    # re-executes sys.executable to build helper processes -- and
    # multiprocessing.resource_tracker does it as:
    #     [FiveAtlas, '-c', 'from multiprocessing.resource_tracker import main;main(N)']
    # freeze_support() does NOT intercept that: it only claims argv starting
    # '--multiprocessing-fork'. So that child reached main(), started its own
    # server, opened its own browser tab, and could spawn another the same way.
    # Every generation is an independent process, which is why killing one did
    # not help -- the next was already running.
    #
    # resource_tracker is POSIX-only and macOS defaults to the "spawn" start
    # method, so this could only ever fire on the Mac. Windows never had it.
    # Any dependency touching a multiprocessing Lock/Semaphore/Queue is enough.
    #
    # Such a child cannot do its job here anyway (the bootloader ignores -c and
    # runs the bundled script), so exiting is the whole fix. The cost is that
    # POSIX semaphores may not be cleaned up at exit; that is a leak of a few
    # bytes, against an app that could not be shut down.
    argv1 = sys.argv[1] if len(sys.argv) > 1 else ""
    # (-psn_0_... is the process serial number old LaunchServices passed to
    # every app it started. Modern macOS no longer does, but refusing to serve
    # on it would be refusing a Finder launch, so it is the one dash exception.)
    if argv1.startswith("-") and not argv1.startswith("-psn_"):
        print(f"[launcher] not serving for argv {sys.argv[1:]!r} -- this process "
              "was spawned by interpreter machinery, not by the user.",
              file=sys.stderr)
        return

    import uvicorn
    import config

    # Logging goes up before the backend import, so an import-time failure (a
    # missing codec dylib, say) still lands in the log rather than nowhere.
    _start_logging(config.WORKDIR)

    preferred = int(os.environ.get("ATLAS_PORT", "8050"))

    # If a FiveAtlas is already up, hand the browser to it and get out of the
    # way. Starting a second server would give the user two half-states over the
    # same workdir, and -- much worse -- a second browser tab every time.
    if _already_running(preferred):
        url = f"http://127.0.0.1:{preferred}"
        print(f"FiveAtlas is already running at {url} -- opening that instead.")
        if not os.environ.get("ATLAS_NO_BROWSER") and \
                _claim_browser_open(config.WORKDIR):
            try:
                webbrowser.open(url)
            except Exception:
                pass
        return

    try:
        import app as backend
    except Exception as e:
        print(f"[fatal] backend failed to import: {e}", file=sys.stderr)
        _alert(f"{type(e).__name__}: {e}\n\nSee {config.WORKDIR / 'FiveAtlas.log'}")
        raise

    port = _free_port(preferred)
    url = f"http://127.0.0.1:{port}"

    # There is no console window to close on macOS -- say how to stop it there.
    stop = ("Stop it with the Quit button at the bottom of the sidebar "
            "(it also stops by itself about ten minutes after the tab is closed)."
            if sys.platform == "darwin"
            else "Keep this window open while you use the app; close it to stop.")

    print("=" * 62)
    print("  FiveAtlas  -  Five Lab region editor")
    print(f"  {url}")
    print(f"  edits are saved in: {config.WORKDIR}")
    print(f"  {stop}")
    print("=" * 62)

    if not os.environ.get("ATLAS_NO_BROWSER") and _claim_browser_open(config.WORKDIR):
        threading.Thread(target=_open_when_up, args=(url,), daemon=True).start()
    else:
        print("  (not opening a browser -- go to the address above)")
    if sys.platform == "darwin":
        threading.Thread(target=_watchdog, args=(backend,), daemon=True).start()
    try:
        uvicorn.run(backend.app, host="127.0.0.1", port=port, log_level="warning")
    except SystemExit as e:
        # uvicorn does NOT raise on a failed bind: Server.startup catches the
        # OSError, logs one line, and calls sys.exit(1). SystemExit derives from
        # BaseException, so the `except Exception` below never saw the single
        # most likely way for this server to die, and the user got no message at
        # all -- on macOS, no console either.
        code = e.code if isinstance(e.code, int) else 1
        if code:
            msg = (f"the server could not start on port {port} "
                   "(most likely something else is already using it)")
            print(f"[fatal] {msg}", file=sys.stderr)
            _alert(f"{msg}\n\nSee {config.WORKDIR / 'FiveAtlas.log'}")
        raise
    except Exception as e:
        print(f"[fatal] server stopped: {e}", file=sys.stderr)
        _alert(f"{type(e).__name__}: {e}\n\nSee {config.WORKDIR / 'FiveAtlas.log'}")
        raise


if __name__ == "__main__":
    # MUST be the first thing that runs, before any other import or side effect.
    #
    # A frozen app has no importable __main__ module, so when anything spawns a
    # process (macOS and Windows both default to "spawn", not fork) the child
    # re-executes THIS FILE from the top instead of resuming inside the worker.
    # Without freeze_support() that child then runs main() itself: another
    # server, another browser tab, and another round of spawning. It compounds
    # -- the observed failure was an unstoppable storm of browser tabs that
    # outran force-quit, because every process killed had already started more.
    #
    # freeze_support() makes the child recognise itself as a worker and return
    # instead. On a non-frozen run it is a documented no-op.
    import multiprocessing
    multiprocessing.freeze_support()
    main()

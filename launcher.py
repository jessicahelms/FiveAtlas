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


def _start_logging(workdir: Path):
    """Mirror stdout/stderr into <workdir>/FiveAtlas.log. Best effort: if the log
    cannot be opened we carry on with whatever streams we already had."""
    try:
        workdir.mkdir(parents=True, exist_ok=True)
        handle = open(workdir / "FiveAtlas.log", "a", encoding="utf-8", buffering=1)
    except Exception:
        return
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
        body = message.replace("\\", "\\\\").replace('"', '\\"')
        subprocess.run(
            ["osascript", "-e",
             f'display alert "FiveAtlas could not start" message "{body}" as critical'],
            timeout=15)
    except Exception:
        pass


def _run_picker_mode() -> bool:
    """Handle `--pick <folder|file> [initial]` and return True if we did."""
    if len(sys.argv) < 3 or sys.argv[1] != "--pick":
        return False
    import nativedialog
    kind = sys.argv[2]
    # Anything that is not "folder" used to fall through to the *file* picker,
    # so a typo silently opened a modal dialog and blocked until someone closed
    # it -- which on a headless machine is never. Refuse instead.
    if kind not in ("folder", "file"):
        print(f"[picker] unknown kind {kind!r}; expected 'folder' or 'file'",
              file=sys.stderr)
        return True
    initial = sys.argv[3] if len(sys.argv) > 3 else None
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
    try:
        webbrowser.open(url)
    except Exception:
        pass


def main():
    if _run_picker_mode():
        return

    import uvicorn
    import config

    # Logging goes up before the backend import, so an import-time failure (a
    # missing codec dylib, say) still lands in the log rather than nowhere.
    _start_logging(config.WORKDIR)

    try:
        import app as backend
    except Exception as e:
        print(f"[fatal] backend failed to import: {e}", file=sys.stderr)
        _alert(f"{type(e).__name__}: {e}\n\nSee {config.WORKDIR / 'FiveAtlas.log'}")
        raise

    port = _free_port(int(os.environ.get("ATLAS_PORT", "8050")))
    url = f"http://127.0.0.1:{port}"

    # There is no console window to close on macOS -- say how to stop it there.
    stop = ("Quit FiveAtlas from the Dock to stop the app."
            if sys.platform == "darwin"
            else "Keep this window open while you use the app; close it to stop.")

    print("=" * 62)
    print("  FiveAtlas  -  Five Lab region editor")
    print(f"  {url}")
    print(f"  edits are saved in: {config.WORKDIR}")
    print(f"  {stop}")
    print("=" * 62)

    if not os.environ.get("ATLAS_NO_BROWSER"):
        threading.Thread(target=_open_when_up, args=(url,), daemon=True).start()
    try:
        uvicorn.run(backend.app, host="127.0.0.1", port=port, log_level="warning")
    except Exception as e:
        print(f"[fatal] server stopped: {e}", file=sys.stderr)
        _alert(f"{type(e).__name__}: {e}\n\nSee {config.WORKDIR / 'FiveAtlas.log'}")
        raise


if __name__ == "__main__":
    main()

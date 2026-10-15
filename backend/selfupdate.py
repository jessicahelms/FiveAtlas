"""One-click updates for the packaged app.

When updatecheck finds a newer release, the new version is downloaded in the
background, checked against the release's SHA256SUMS, and unpacked beside the
installed copy. The page then offers "Restart to update"; if nobody clicks it,
the next time FiveAtlas is opened it switches over before it starts.

Nothing is ever replaced while the app is running: the swap is done by a tiny
helper script that waits for this process to exit, moves the old copy aside,
moves the new one into its place and starts it. If any step fails the old copy
is put back and started again, and the page says the update could not be
installed and links to the download page instead. Unsaved edits are guarded by
the page (it asks before restarting), exactly like the Mac "installed over me"
restart in appversion/launcher.

Where it applies:
  * Windows -- the unzipped FiveAtlas folder FiveAtlas.exe runs from.
  * macOS   -- FiveAtlas.app. Extracted with ditto (zipfile would drop the
    bundle's symlinks), and downloaded by the app itself, so it carries no
    quarantine flag and Gatekeeper does not stop the new copy.
It does not apply from source, when the install location is not writable
(e.g. a non-admin Mac account and /Applications), or when macOS is running the
app from a translocated read-only copy; the page then links to the download
page as before. ATLAS_NO_SELF_UPDATE=1 turns it off.
"""
from __future__ import annotations

import hashlib
import json
import os
import platform
import shutil
import subprocess
import sys
import threading
import time
import urllib.request
import zipfile
from pathlib import Path

import config
from appversion import version_key

UPDATES = Path(config.WORKDIR) / "updates"
READY = UPDATES / "ready.json"        # a downloaded, verified, unpacked update
RESULT = UPDATES / "last-result.txt"  # what the helper reported: ok / failed
ATTEMPT = UPDATES / "attempt.txt"     # the version the last switch-over aimed at

_lock = threading.Lock()
_state = {"state": "idle", "version": None, "error": None, "got": 0, "total": 0}
_thread = None


# ---- where we are installed -------------------------------------------------
def install_target():
    """The installed copy this process runs from -- the FiveAtlas folder on
    Windows, FiveAtlas.app on a Mac -- or None (from source, other platforms)."""
    if not getattr(sys, "frozen", False):
        return None
    exe = Path(sys.executable).resolve()
    if sys.platform == "win32":
        folder = exe.parent
        return folder if (folder / "_internal").is_dir() else None
    if sys.platform == "darwin":
        parents = exe.parents          # .../FiveAtlas.app/Contents/MacOS/FiveAtlas
        if len(parents) >= 3 and parents[2].suffix == ".app":
            return parents[2]
    return None


_writable_cache: dict = {}


def _can_write(folder: Path) -> bool:
    """Really try: on Windows os.access ignores folder permissions, so a copy
    under Program Files would look writable and then fail half-way."""
    if folder not in _writable_cache:
        probe = folder / f".fiveatlas-write-test-{os.getpid()}"
        try:
            probe.write_bytes(b"")
            probe.unlink()
            _writable_cache[folder] = True
        except Exception:
            _writable_cache[folder] = False
    return _writable_cache[folder]


def why_not():
    """None when this copy can update itself, else a short reason why not."""
    if os.environ.get("ATLAS_NO_SELF_UPDATE"):
        return "automatic updates are turned off"
    target = install_target()
    if target is None:
        return "only the installed app updates itself"
    if "AppTranslocation" in str(target):
        return ("macOS is running FiveAtlas from a read-only copy -- move it into "
                "Applications")
    if not (_can_write(target.parent) and _can_write(target)):
        return f"no permission to replace {target}"
    return None


def _last_attempt_failed():
    """The version the last switch-over failed to install, or None."""
    try:
        if RESULT.read_text(encoding="utf-8").strip() == "failed":
            return ATTEMPT.read_text(encoding="utf-8").strip() or "?"
    except Exception:
        pass
    return None


def asset_names(version: str):
    """(release file to download, checksum file that lists it)."""
    if sys.platform == "win32":
        return f"FiveAtlas-{version}-win64.zip", "SHA256SUMS-windows.txt"
    return f"FiveAtlas-{version}-macos-{platform.machine()}.zip", "SHA256SUMS"


def _staged_path(target: Path, version: str) -> Path:
    # Beside the installed copy, so the final move is a rename on one volume.
    if sys.platform == "darwin":
        return target.parent / f".FiveAtlas-update-{version}.app"
    return target.parent / f"FiveAtlas-update-{version}"


def _old_path(target: Path, version: str) -> Path:
    if sys.platform == "darwin":
        return target.parent / f".FiveAtlas-old-{version}.app"
    return target.parent / f"FiveAtlas.old-{version}"


# ---- state ------------------------------------------------------------------
def _set(**kw):
    with _lock:
        _state.update(kw)


def _read_ready():
    try:
        return json.loads(READY.read_text(encoding="utf-8"))
    except Exception:
        return None


def status():
    """What the page shows: {state, version, error, progress, canApply, reason}.
    state: unsupported | idle | downloading | ready | failed."""
    reason = why_not()
    with _lock:
        out = dict(_state)
    if reason:
        out.update(state="unsupported", reason=reason)
    elif out["state"] in ("idle", "failed"):
        ready = _read_ready()
        if ready and version_key(ready.get("version")) > version_key(config.VERSION) \
                and Path(ready.get("staged", "")).exists():
            out.update(state="ready", version=ready["version"], error=None)
    failed = _last_attempt_failed()
    if failed and out["state"] not in ("downloading", "ready", "unsupported"):
        out.update(state="failed", version=failed, error=(
            f"FiveAtlas {failed} could not be installed automatically -- this is "
            f"still {config.VERSION}"))
    total = out.pop("total", 0) or 0
    got = out.pop("got", 0) or 0
    out["progress"] = round(got / total, 3) if total else None
    out["canApply"] = out["state"] == "ready"
    return out


# ---- download ---------------------------------------------------------------
def prepare(check: dict):
    """Start downloading the release updatecheck found, if it is newer and this
    copy can update itself. Returns at once; status() reports progress."""
    global _thread
    latest = (check or {}).get("latest")
    if not latest or not check.get("newer") or why_not():
        return
    if version_key(latest) <= version_key(config.VERSION):
        return
    if _last_attempt_failed() == latest:
        return            # it failed once: offer the download page, don't loop
    ready = _read_ready()
    if ready and ready.get("version") == latest and Path(ready.get("staged", "")).exists():
        return
    zip_name, sums_name = asset_names(latest)
    assets = check.get("assets") or {}
    if zip_name not in assets or sums_name not in assets:
        _set(state="failed", version=latest,
             error=f"the {latest} release has no {zip_name} to update from")
        return
    with _lock:
        if _thread is not None and _thread.is_alive():
            return
        _state.update(state="downloading", version=latest, error=None, got=0, total=0)
        _thread = threading.Thread(target=_download, daemon=True,
                                   args=(latest, assets[zip_name], assets[sums_name], zip_name))
        _thread.start()


def _fetch(url: str, timeout: float = 30.0):
    req = urllib.request.Request(url, headers={"User-Agent": f"FiveAtlas/{config.VERSION}"})
    return urllib.request.urlopen(req, timeout=timeout)


def _expected_sha(sums_text: str, name: str):
    for line in sums_text.splitlines():
        parts = line.strip().split()
        if len(parts) >= 2 and parts[-1].lstrip("*") == name:
            return parts[0].lower()
    return None


def _download(version, zip_url, sums_url, zip_name):
    target = install_target()
    work = UPDATES / version
    try:
        _clean_downloads(keep=version)
        work.mkdir(parents=True, exist_ok=True)
        with _fetch(sums_url) as r:
            expected = _expected_sha(r.read().decode("utf-8", "replace"), zip_name)
        if not expected:
            raise RuntimeError(f"{zip_name} is not in the release's checksum list")

        part = work / (zip_name + ".part")
        h = hashlib.sha256()
        with _fetch(zip_url, timeout=60.0) as r, open(part, "wb") as out:
            _set(total=int(r.headers.get("Content-Length") or 0))
            got = 0
            while True:
                chunk = r.read(1 << 20)
                if not chunk:
                    break
                out.write(chunk)
                h.update(chunk)
                got += len(chunk)
                _set(got=got)
        if h.hexdigest().lower() != expected:
            raise RuntimeError("the download did not match its checksum")

        staged = _staged_path(target, version)
        _remove(staged)
        if sys.platform == "darwin":
            _unpack_mac(part, work / "unpacked", staged, version)
        else:
            _unpack_windows(part, target.parent / f".FiveAtlas-unpack-{version}", staged)
        part.unlink(missing_ok=True)

        UPDATES.mkdir(parents=True, exist_ok=True)
        READY.write_text(json.dumps({"version": version, "staged": str(staged),
                                     "target": str(target)}), encoding="utf-8")
        RESULT.unlink(missing_ok=True)
        _set(state="ready", error=None)
        print(f"[update] FiveAtlas {version} is downloaded and ready -- "
              "restart to switch to it")
    except Exception as e:
        print(f"[update] could not prepare {version}: {type(e).__name__}: {e}",
              file=sys.stderr)
        _set(state="failed", error=f"could not download {version}: {e}")


def _unpack_windows(zip_path: Path, tmp: Path, staged: Path):
    _remove(tmp)
    tmp.mkdir(parents=True)
    root = tmp.resolve()
    with zipfile.ZipFile(zip_path) as z:
        for name in z.namelist():                   # never write outside tmp
            dest = (tmp / name.replace("\\", "/")).resolve()
            if root not in dest.parents and dest != root:
                raise RuntimeError(f"unsafe path in the download: {name}")
        z.extractall(tmp)
    exes = [p for p in tmp.rglob("FiveAtlas.exe") if (p.parent / "_internal").is_dir()]
    if len(exes) != 1:
        raise RuntimeError("the download does not contain one FiveAtlas.exe")
    exes[0].parent.rename(staged)
    _remove(tmp)


def _unpack_mac(zip_path: Path, tmp: Path, staged: Path, version: str):
    _remove(tmp)
    tmp.mkdir(parents=True)
    # ditto keeps the symlinks and code signature inside the bundle
    subprocess.run(["/usr/bin/ditto", "-x", "-k", str(zip_path), str(tmp)],
                   check=True, capture_output=True, timeout=600)
    apps = [p for p in tmp.rglob("FiveAtlas.app") if (p / "Contents" / "Info.plist").exists()]
    if len(apps) != 1:
        raise RuntimeError("the download does not contain one FiveAtlas.app")
    import plistlib
    with open(apps[0] / "Contents" / "Info.plist", "rb") as fh:
        got = str(plistlib.load(fh).get("CFBundleShortVersionString") or "")
    if version_key(got) != version_key(version):
        raise RuntimeError(f"the download says it is {got or 'no version'}, not {version}")
    subprocess.run(["/usr/bin/xattr", "-dr", "com.apple.quarantine", str(apps[0])],
                   capture_output=True, timeout=120)
    subprocess.run(["/bin/mv", str(apps[0]), str(staged)], check=True,
                   capture_output=True, timeout=600)
    _remove(tmp)


def _remove(path: Path):
    try:
        if path.is_dir() and not path.is_symlink():
            shutil.rmtree(path, ignore_errors=True)
        elif path.exists() or path.is_symlink():
            path.unlink()
    except Exception:
        pass


def _clean_downloads(keep=None):
    """Drop downloads and unpacked copies of any version other than `keep`."""
    target = install_target()
    if UPDATES.is_dir():
        for p in UPDATES.iterdir():
            if p.is_dir() and p.name != keep:
                _remove(p)
    if target is not None:
        for p in target.parent.glob(".FiveAtlas-unpack-*"):
            _remove(p)
        kept = _staged_path(target, keep) if keep else None
        pattern = ".FiveAtlas-update-*" if sys.platform == "darwin" else "FiveAtlas-update-*"
        for p in target.parent.glob(pattern):
            if p != kept:
                _remove(p)


# ---- switching over ---------------------------------------------------------
_WIN_HELPER = r"""@echo off
setlocal
set /a N=0
:wait
tasklist /FI "PID eq %FA_PID%" /NH 2>NUL | find " %FA_PID% " >NUL
if errorlevel 1 goto gone
set /a N+=1
if %N% GEQ 120 goto fail
ping -n 2 127.0.0.1 >NUL
goto wait
:gone
ping -n 2 127.0.0.1 >NUL
set /a M=0
:moveold
move "%FA_TARGET%" "%FA_OLD%" >NUL 2>&1
if not errorlevel 1 goto moved
set /a M+=1
if %M% GEQ 20 goto fail
ping -n 2 127.0.0.1 >NUL
goto moveold
:moved
move "%FA_STAGED%" "%FA_TARGET%" >NUL 2>&1
if errorlevel 1 goto restore
echo ok> "%FA_RESULT%"
start "" /D "%FA_TARGET%" "%FA_TARGET%\FiveAtlas.exe"
exit /b 0
:restore
move "%FA_OLD%" "%FA_TARGET%" >NUL 2>&1
:fail
echo failed> "%FA_RESULT%"
start "" /D "%FA_TARGET%" "%FA_TARGET%\FiveAtlas.exe"
exit /b 1
"""

_MAC_HELPER = r"""n=0
while kill -0 "$FA_PID" 2>/dev/null; do
  n=$((n+1)); [ "$n" -ge 240 ] && break; sleep 0.5
done
sleep 1
if /bin/mv "$FA_TARGET" "$FA_OLD"; then
  if /bin/mv "$FA_STAGED" "$FA_TARGET"; then
    echo ok > "$FA_RESULT"; /usr/bin/open -n "$FA_TARGET"; exit 0
  fi
  /bin/mv "$FA_OLD" "$FA_TARGET"
fi
echo failed > "$FA_RESULT"
/usr/bin/open -n "$FA_TARGET"
exit 1
"""


def apply(pid=None) -> str:
    """Hand the switch-over to a helper that waits for `pid` (this process) to
    exit. Returns the version being switched to; the caller then exits. Raises
    RuntimeError when there is nothing ready or this copy cannot update."""
    reason = why_not()
    if reason:
        raise RuntimeError(reason)
    ready = _read_ready()
    target = install_target()
    if not ready or version_key(ready.get("version")) <= version_key(config.VERSION):
        raise RuntimeError("no newer version has been downloaded")
    staged = Path(ready.get("staged", ""))
    if not staged.exists() or Path(ready.get("target", "")) != target:
        READY.unlink(missing_ok=True)
        raise RuntimeError("the downloaded update is missing -- it will be fetched again")
    version = ready["version"]
    old = _old_path(target, config.VERSION)
    _remove(old)
    # Taken off the list BEFORE the helper runs, so a swap that fails can never
    # be retried on every launch in a loop; the next update check fetches again.
    READY.unlink(missing_ok=True)
    RESULT.unlink(missing_ok=True)
    ATTEMPT.write_text(version, encoding="utf-8")
    env = dict(os.environ, FA_PID=str(pid or os.getpid()), FA_TARGET=str(target),
               FA_STAGED=str(staged), FA_OLD=str(old), FA_RESULT=str(RESULT))
    UPDATES.mkdir(parents=True, exist_ok=True)
    if sys.platform == "win32":
        script = UPDATES / "apply-update.cmd"
        script.write_text(_WIN_HELPER.replace("\n", "\r\n"), encoding="ascii")
        # A hidden console of its own (not DETACHED_PROCESS: then every
        # tasklist/ping it runs would pop up a console window of its own), in
        # its own group so it outlives this process; `start` then gives the new
        # FiveAtlas.exe its normal visible window.
        flags = subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP
        args = ["cmd.exe", "/d", "/c", str(script)]
        try:
            subprocess.Popen(args, env=env, cwd=str(UPDATES), close_fds=True,
                             creationflags=flags | subprocess.CREATE_BREAKAWAY_FROM_JOB)
        except OSError:          # a job that forbids breakaway: still detached
            subprocess.Popen(args, env=env, cwd=str(UPDATES), close_fds=True,
                             creationflags=flags)
    else:
        script = UPDATES / "apply-update.sh"
        script.write_text(_MAC_HELPER, encoding="utf-8")
        subprocess.Popen(["/bin/sh", str(script)], env=env, cwd=str(UPDATES),
                         stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL, start_new_session=True)
    print(f"[update] switching {config.VERSION} -> {version}; the new version "
          "opens by itself in a few seconds")
    return version


def apply_on_launch() -> bool:
    """At start-up: when a newer version is downloaded and waiting, switch to it
    instead of starting this one. True means this process should exit now."""
    if why_not():
        return False
    ready = _read_ready()
    if not ready or version_key(ready.get("version")) <= version_key(config.VERSION):
        return False
    try:
        apply()
        return True
    except Exception as e:
        print(f"[update] not switching at start-up: {e}", file=sys.stderr)
        return False


def cleanup_after_update():
    """At start-up: remove the copy an update moved aside, and stale downloads.

    A Windows FiveAtlas folder is removed only when it holds nothing but the app
    -- if someone kept their own files in it, it is left where it is."""
    target = install_target()
    if target is None:
        return
    try:
        if RESULT.read_text(encoding="utf-8").strip() == "ok":
            RESULT.unlink(missing_ok=True)
            ATTEMPT.unlink(missing_ok=True)
    except Exception:
        pass
    pattern = ".FiveAtlas-old-*.app" if sys.platform == "darwin" else "FiveAtlas.old-*"
    for old in target.parent.glob(pattern):
        if sys.platform == "win32":
            extras = [p for p in old.iterdir()
                      if p.name not in ("FiveAtlas.exe", "README.txt", "_internal")]
            if extras:
                print(f"[update] leaving {old}: it holds files that are not part of "
                      f"FiveAtlas ({extras[0].name}...)", file=sys.stderr)
                continue
        _remove(old)
    ready = _read_ready()
    if not ready or version_key(ready.get("version")) <= version_key(config.VERSION):
        READY.unlink(missing_ok=True)       # nothing newer waiting: tidy up
        _clean_downloads(keep=None)

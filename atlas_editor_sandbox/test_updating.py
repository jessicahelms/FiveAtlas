"""Updating opens the NEW version.

People downloaded 1.3.0 and kept seeing the old app. Three causes, one test each:
  * the new copy found an old FiveAtlas still running, handed the browser to it
    and exited (launcher: _running_version / _reuse_running);
  * the page came out of the browser's cache -- every version lives at the same
    http://127.0.0.1:8050/ (launcher: ?v= on the address; app: no-cache page);
  * on a Mac, opening the app again only wakes the old process, so the running
    app must notice it was replaced on disk (appversion, the watchdog, and
    /api/version + /api/restart for the page's Restart button).

Offline: fake "FiveAtlas" servers on free ports, a fake Mac bundle, the app's
routes called directly, all under a scratch folder. Nothing touches the real
app, its port or its working folder.

    python test_updating.py [scratch_dir]
"""
import json
import os
import plistlib
import shutil
import socket
import sys
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.join(HERE, "..")
sys.path.insert(0, os.path.join(REPO, "backend"))
sys.path.insert(0, REPO)

scratch = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(tempfile.mkdtemp(prefix="updating_"))
if scratch.exists():
    shutil.rmtree(scratch, ignore_errors=True)
scratch.mkdir(parents=True)
# BEFORE any backend import: config fixes WORKDIR the moment it loads
os.environ["ATLAS_WORKDIR"] = str(scratch / "wd")
os.environ.pop("ATLAS_INSTALLED_PLIST", None)

import config  # noqa: E402
if Path(config.WORKDIR).resolve() != (scratch / "wd").resolve():
    raise SystemExit(f"refusing to run: WORKDIR is {config.WORKDIR}, not the scratch folder")

import appversion  # noqa: E402
import launcher  # noqa: E402

fails = []


def check(label, cond, detail=""):
    print(f"  {'ok  ' if cond else 'FAIL'}  {label}{('  ' + str(detail)) if detail != '' else ''}")
    if not cond:
        fails.append(label)


def free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


def fake_server(routes):
    """A tiny server answering GET path -> JSON (or 404 when not in routes)."""
    class H(BaseHTTPRequestHandler):
        def do_GET(self):
            body = routes.get(self.path)
            if body is None:
                self.send_response(404)
                self.end_headers()
                return
            data = json.dumps(body).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def log_message(self, *a):
            pass
    srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, srv.server_address[1]


def write_plist(path, version):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "wb") as fh:
        plistlib.dump({"CFBundleShortVersionString": version, "CFBundleName": "FiveAtlas"}, fh)


print("\nwhich FiveAtlas is running on a port")
servers = {
    "a 1.3.1+ (says so at /api/version)": ({"/api/health": {"ok": True}, "/api/version": {"version": "1.3.1"}}, "1.3.1"),
    "a 1.3.0 (only /openapi.json knows)": ({"/api/health": {"ok": True},
                                            "/openapi.json": {"info": {"title": "FiveAtlas", "version": "1.3.0"}}}, "1.3.0"),
    "a very old one (no version anywhere)": ({"/api/health": {"ok": True}}, ""),
    "something that is not FiveAtlas": ({"/other": {"x": 1}}, None),
}
started = []
for label, (routes, want) in servers.items():
    srv, port = fake_server(routes)
    started.append(srv)
    got = launcher._running_version(port)
    check(label, got == want, repr(got))
check("nothing on the port at all", launcher._running_version(free_port()) is None)

print("\nopen the running one, or start this one?")
cases = [
    (None, "1.3.1", False, "nothing running -> start"),
    ("", "1.3.1", False, "a FiveAtlas too old to say -> start beside it"),
    ("1.3.0", "1.3.1", False, "an OLDER one running -> start beside it (the bug)"),
    ("1.2.2", "1.3.1", False, "much older -> start beside it"),
    ("1.3.1", "1.3.1", True, "the same version -> open that one (tab-storm guard kept)"),
    ("1.4.0", "1.3.1", True, "a NEWER one running -> open that, never downgrade"),
    ("1.3.1-live-trial", "1.3.1", True, "same numbers with a suffix -> same version"),
]
for running, mine, want, label in cases:
    check(label, launcher._reuse_running(running, mine) is want)

print("\nthe address the browser is sent to")
page = launcher._page_url(8050, "1.3.1")
check("carries the version, so no cached older page can answer it",
      page == "http://127.0.0.1:8050/?v=1.3.1", page)
check("the port is still read from it", launcher._port_of(page) == 8050)
check("and from a bare address", launcher._port_of("http://127.0.0.1:8123") == 8123)

opened = []
real_open = launcher.webbrowser.open
launcher.webbrowser.open = lambda url, *a, **k: opened.append(url) or True
try:
    srv, port = fake_server({"/api/health": {"ok": True}})
    started.append(srv)
    target = launcher._page_url(port, "1.3.1")
    t0 = time.time()
    launcher._open_when_up(target, timeout=5)
    check("the browser is opened on that exact address once the server is up",
          opened == [target], opened)
    check("without waiting out the timeout", time.time() - t0 < 3, f"{time.time() - t0:.1f}s")
finally:
    launcher.webbrowser.open = real_open

print("\nversions compare by number")
check("1.3.10 is newer than 1.3.9", appversion.version_key("1.3.10") > appversion.version_key("1.3.9"))
check("v-prefix and suffix are ignored",
      appversion.version_key("v1.3.1") == appversion.version_key("1.3.1-live-trial") == (1, 3, 1))
check("missing parts are 0", appversion.version_key("1.4") == (1, 4, 0))
check("garbage is 0.0.0, not a crash", appversion.version_key("dev") == (0, 0, 0))

print("\nthe Mac app notices it was replaced on disk")
real = (getattr(sys, "frozen", None), sys.platform, sys.executable)
bundle = scratch / "Applications" / "FiveAtlas.app"
write_plist(bundle / "Contents" / "Info.plist", "1.3.2")
exe = bundle / "Contents" / "MacOS" / "FiveAtlas"
exe.parent.mkdir(parents=True, exist_ok=True)
exe.write_bytes(b"")
try:
    sys.frozen, sys.platform, sys.executable = True, "darwin", str(exe)
    check("finds its own FiveAtlas.app", appversion.bundle_dir() == bundle, appversion.bundle_dir())
    check("reads the version installed there", appversion.installed_version() == "1.3.2")
    check("a different version on disk = replaced", appversion.replaced_on_disk("1.3.1") == "1.3.2")
    check("the same version on disk = not replaced", appversion.replaced_on_disk("1.3.2") is None)
    write_plist(bundle / "Contents" / "Info.plist", "1.3.3")
    check("an update dragged in later is seen too", appversion.replaced_on_disk("1.3.1") == "1.3.3")
finally:
    if real[0] is None:
        del sys.frozen
    else:
        sys.frozen = real[0]
    sys.platform, sys.executable = real[1], real[2]
check("not the Mac app -> no bundle, no claim", appversion.bundle_dir() is None
      and appversion.installed_version() is None)

print("\nthe watchdog steps aside only when no tab is using it")
newer_plist = scratch / "newer.plist"
write_plist(newer_plist, "99.0.0")
same_plist = scratch / "same.plist"
write_plist(same_plist, config.VERSION)


class Backend:
    LAST_PING = None


def watch(plist, last_ping, wait=0.6, grace=45.0):
    os.environ["ATLAS_INSTALLED_PLIST"] = str(plist)
    stopped = threading.Event()
    b = Backend()
    b.LAST_PING = last_ping
    threading.Thread(target=launcher._watchdog, args=(b,),
                     kwargs=dict(every=0.05, silence=600, stale_grace=grace, stop=stopped.set),
                     daemon=True).start()
    return stopped.wait(wait)


check("replaced, and no tab ever opened -> stops", watch(newer_plist, None))
check("replaced, and the tab went quiet -> stops", watch(newer_plist, time.monotonic() - 100))
check("replaced, but a tab is open -> keeps running (the page offers Restart)",
      not watch(newer_plist, time.monotonic() + 3600))
check("not replaced -> keeps running", not watch(same_plist, None))
os.environ.pop("ATLAS_INSTALLED_PLIST", None)

print("\nthe app's routes")
import app as A  # noqa: E402   (WORKDIR is the scratch folder -- checked at the top)
from fastapi import HTTPException  # noqa: E402

os.environ["ATLAS_INSTALLED_PLIST"] = str(newer_plist)
v = A.version_info()
check("/api/version names the running version", v["version"] == config.VERSION, v)
check("... and the one installed in its place", v["installed"] == "99.0.0", v)
check("... and says restarting by itself is Mac-only", v["canRestart"] is False, v)
os.environ.pop("ATLAS_INSTALLED_PLIST", None)
check("off a Mac nothing is 'installed over' it", A.version_info()["installed"] is None)
try:
    A.restart_app()
    code = 200
except HTTPException as e:
    code = e.status_code
check("/api/restart off the Mac app refuses (409) and says what to do", code == 409, code)

if getattr(A, "_spa_index", None):
    r = A._spa_index()
    check("the page is served no-cache", r.headers.get("cache-control") == "no-cache",
          r.headers.get("cache-control"))
    r2 = A._spa_fallback("some/view")
    check("so is the page behind any other address", r2.headers.get("cache-control") == "no-cache")
else:
    print("  (skipped the page checks: frontend/dist is not built)")

for srv in started:
    srv.shutdown()
shutil.rmtree(scratch, ignore_errors=True)
print("\n" + (f"{len(fails)} FAILED: " + "; ".join(fails) if fails else "all checks passed"))
sys.exit(1 if fails else 0)

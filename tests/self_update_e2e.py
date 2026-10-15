"""End-to-end test of the self-updater on real, packaged builds.

    python tests/self_update_e2e.py --install DIR --old-zip OLD.zip
        --new-zip NEW.zip --new-version X.Y.Z [--mode click|launch]

Unpacks OLD.zip into DIR (the "installed" copy), serves a stand-in for GitHub's
"latest release" answer offering NEW.zip, and starts the old app against it.
Then, as a person would:
  click  -- waits for "ready", presses Restart to update (POST /api/update/apply)
  launch -- waits for "ready", quits the app, and opens it again
and checks that the new version comes up from the SAME install path and that
the copy moved aside is cleaned up. Exit status 0 = passed.

Windows: DIR/FiveAtlas/FiveAtlas.exe. macOS: DIR/FiveAtlas.app (the new copy is
started by `open`, which does not pass this script's environment, so it comes
up on its default port 8050).
"""
from __future__ import annotations

import argparse
import hashlib
import http.server
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request
from pathlib import Path

MAC = sys.platform == "darwin"


def log(msg):
    print(f"[e2e {time.strftime('%H:%M:%S')}] {msg}", flush=True)


def get(url, timeout=5):
    with urllib.request.urlopen(url, timeout=timeout) as r:
        return json.loads(r.read().decode())


def post(url, timeout=10):
    req = urllib.request.Request(url, data=b"", method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode())


def wait_for(what, fn, timeout):
    deadline = time.time() + timeout
    last = None
    while time.time() < deadline:
        try:
            v = fn()
            if v:
                return v
        except Exception as e:
            last = e
        time.sleep(1)
    raise SystemExit(f"FAILED: timed out waiting for {what} ({last})")


def serve(files: dict, latest: dict):
    """A tiny stand-in for GitHub: /latest and /files/<name>."""
    class H(http.server.BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_GET(self):
            if self.path.startswith("/latest"):
                body = json.dumps(latest).encode()
            elif self.path.startswith("/files/") and self.path[7:] in files:
                body = Path(files[self.path[7:]]).read_bytes()
            else:
                self.send_response(404)
                self.end_headers()
                return
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv.server_address[1]


def unpack(zip_path: Path, dest: Path):
    """Install the old build the way a person would."""
    if MAC:
        tmp = Path(tempfile.mkdtemp())
        subprocess.run(["ditto", "-x", "-k", str(zip_path), str(tmp)], check=True)
        app = next(tmp.rglob("FiveAtlas.app"))
        subprocess.run(["ditto", str(app), str(dest / "FiveAtlas.app")], check=True)
        subprocess.run(["xattr", "-dr", "com.apple.quarantine", str(dest / "FiveAtlas.app")])
        return dest / "FiveAtlas.app"
    import zipfile
    with zipfile.ZipFile(zip_path) as z:
        z.extractall(dest)
    return dest / "FiveAtlas"


def exe_of(installed: Path) -> Path:
    return (installed / "Contents" / "MacOS" / "FiveAtlas") if MAC else (installed / "FiveAtlas.exe")


def start(installed: Path, env):
    flags = subprocess.CREATE_NEW_CONSOLE if not MAC else 0
    return subprocess.Popen([str(exe_of(installed))], env=env, cwd=str(installed.parent),
                            creationflags=flags)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--install", required=True)
    ap.add_argument("--old-zip", required=True)
    ap.add_argument("--new-zip", required=True)
    ap.add_argument("--new-version", required=True)
    ap.add_argument("--mode", choices=["click", "launch"], default="click")
    ap.add_argument("--port", type=int, default=8071)
    a = ap.parse_args()

    install_root = Path(a.install).resolve()
    if install_root.exists():
        shutil.rmtree(install_root)
    install_root.mkdir(parents=True)
    workdir = Path(tempfile.mkdtemp(prefix="fa-e2e-wd-"))
    new_zip = Path(a.new_zip).resolve()
    zip_name = new_zip.name
    sums_name = "SHA256SUMS" if MAC else "SHA256SUMS-windows.txt"
    sums = Path(workdir) / sums_name
    sums.write_text(f"{hashlib.sha256(new_zip.read_bytes()).hexdigest()}  {zip_name}\n")

    port_files = {}
    latest = {"tag_name": f"v{a.new_version}", "html_url": "https://example.invalid/release",
              "body": f"# What's new in FiveAtlas v{a.new_version}\n\n## Test headline\n",
              "assets": []}
    srv_port = serve(port_files, latest)
    for name, path in ((zip_name, new_zip), (sums_name, sums)):
        port_files[name] = path
        latest["assets"].append({"name": name,
                                 "browser_download_url": f"http://127.0.0.1:{srv_port}/files/{name}"})

    installed = unpack(Path(a.old_zip).resolve(), install_root)
    log(f"installed old build at {installed}")
    env = dict(os.environ, ATLAS_UPDATE_API=f"http://127.0.0.1:{srv_port}/latest",
               ATLAS_NO_BROWSER="1", ATLAS_PORT=str(a.port), ATLAS_WORKDIR=str(workdir))
    base = f"http://127.0.0.1:{a.port}"
    proc = start(installed, env)
    old_version = wait_for("the old app", lambda: get(f"{base}/api/version")["version"], 120)
    log(f"old app up: {old_version}")

    chk = get(f"{base}/api/update-check", timeout=20)
    assert chk.get("newer") and chk.get("latest") == a.new_version, chk
    assert chk.get("headline") == "Test headline", chk
    assert "assets" not in chk, "download links leaked to the page"
    st = wait_for("the download", lambda: (lambda s: s if s["state"] in ("ready", "failed", "unsupported") else None)(
        get(f"{base}/api/update/status")), 300)
    log(f"status: {st}")
    if st["state"] != "ready":
        raise SystemExit(f"FAILED: update not ready: {st}")

    if a.mode == "click":
        log("pressing Restart to update")
        post(f"{base}/api/update/apply")
    else:
        log("quitting, then opening the old app again")
        post(f"{base}/api/quit")
        wait_for("the old app to exit", lambda: proc.poll() is not None, 60)
        proc = start(installed, env)
    if proc.poll() is None:
        wait_for("the old process to exit", lambda: proc.poll() is not None, 60)

    new_base = "http://127.0.0.1:8050" if MAC else base
    got = wait_for(f"FiveAtlas {a.new_version} at {new_base}",
                   lambda: (lambda v: v if v == a.new_version else None)(
                       get(f"{new_base}/api/version")["version"]), 180)
    log(f"new app up: {got}")
    assert exe_of(installed).exists(), "the installed path is gone"

    # the version the installed copy itself says it is
    if MAC:
        import plistlib
        with open(installed / "Contents" / "Info.plist", "rb") as fh:
            on_disk = plistlib.load(fh)["CFBundleShortVersionString"]
        assert on_disk == a.new_version, on_disk
    leftovers = wait_for("the old copy to be cleaned up", lambda: not [
        p for p in install_root.iterdir()
        if p.name.startswith(("FiveAtlas.old-", ".FiveAtlas-old-", "FiveAtlas-update-",
                              ".FiveAtlas-update-", ".FiveAtlas-unpack-"))] or None, 60)
    log(f"install folder now: {sorted(p.name for p in install_root.iterdir())}")
    try:
        post(f"{new_base}/api/quit")
    except Exception:
        pass
    log(f"PASSED ({a.mode}): {old_version} -> {a.new_version}")


if __name__ == "__main__":
    main()

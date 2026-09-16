"""Load regions file reads first and changes nothing; for a file with hemi it
reports damage in the file and in damage-only files beside it -- and never pulls
in other region files from that folder.

Runs against the ISOLATED backend (:8060) and a throwaway folder it builds.

    python test_read_file.py [scratch_dir]
"""
import json
import os
import shutil
import sys
import tempfile
import urllib.error
import urllib.request
from pathlib import Path

BASE = os.environ.get("ATLAS_TEST_BASE", "http://127.0.0.1:8060")
if ":8050" in BASE or ":8000" in BASE:
    raise SystemExit("refusing to run against the live backend -- see the docstring")

fails = []


def check(label, cond, detail=""):
    print(f"  {'ok  ' if cond else 'FAIL'}  {label}{('  ' + str(detail)) if detail != '' else ''}")
    if not cond:
        fails.append(label)


def call(method, path, body=None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(BASE + path, data=data, method=method,
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            return r.status, json.load(r)
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode()[:300]


def box(name, x0, y0, x1, y1):
    return {"type": "Feature", "properties": {"name": name},
            "geometry": {"type": "Polygon",
                         "coordinates": [[[x0, y0], [x1, y0], [x1, y1], [x0, y1], [x0, y0]]]}}


def write(p, feats):
    p.write_text(json.dumps({"type": "FeatureCollection", "features": feats}), encoding="utf-8")


scratch = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(tempfile.mkdtemp(prefix="readfile_"))
root = scratch / "READFILE_TEST_DS"
if root.exists():
    shutil.rmtree(root)
root.mkdir(parents=True)

# the dataset's own region file, so it opens with something on the map
write(root / "orig.regions.geojson", [box("A", 0, 0, 100, 100)])
# a folder of exported pieces: hemi (with one damage shape inside the same
# file), a damage-only file, and another regions file that must NOT be pulled in
pieces = root / "export"
pieces.mkdir()
write(pieces / "hemi.geojson", [box("hemi", 0, 0, 500, 500), box("bubble.1", 10, 10, 20, 20)])
write(pieces / "separation.4.geojson", [box("separation.4", 30, 30, 40, 40)])
write(pieces / "foldsmall.2.geojson", [box("foldsmall.2", 50, 50, 60, 60)])
write(pieces / "ISO.geojson", [box("ISO", 0, 0, 300, 300)])
write(pieces / "mixed.geojson", [box("bubble.9", 70, 70, 80, 80), box("CP", 0, 0, 5, 5)])

code, res = call("POST", "/api/datasets/open", {"path": str(root)})
if code != 200:
    raise SystemExit(f"could not open the scratch dataset: {code} {res}")
DS = res["id"]
REGIONS = f"/api/datasets/{DS}/regions"
READ = f"/api/datasets/{DS}/regions/read-file"

before = call("GET", REGIONS)[1]
code, res = call("POST", READ, {"path": str(pieces / "hemi.geojson")})
after = call("GET", REGIONS)[1]

print("\nreading changes nothing")
check("read succeeded", code == 200, res if code != 200 else "")
check("the regions on the server are exactly as they were", before == after)
check("no working copy was written", not (Path(os.environ.get("ATLAS_TEST_WD", "")) / DS
                                          / "regions_edited.geojson").exists()
      if os.environ.get("ATLAS_TEST_WD") else True)

print("\nit reports what it found")
names = [f["properties"]["name"] for f in res.get("features", [])]
check("the file's own regions come back", names == ["hemi", "bubble.1"], names)
check("hemi is noticed", res.get("_hasHemi") is True)
beside = res.get("_damageBeside") or {}
check("damage-only files beside it are found",
      sorted(beside.get("files", [])) == ["foldsmall.2.geojson", "separation.4.geojson"],
      beside.get("files"))
check("their shapes come back", sorted(f["properties"]["name"] for f in beside.get("features", []))
      == ["foldsmall.2", "separation.4"])
check("another region file in the folder is NOT pulled in",
      "ISO.geojson" not in beside.get("files", []))
check("a file mixing damage with a region is NOT pulled in",
      "mixed.geojson" not in beside.get("files", []))

print("\na file without hemi asks nothing about damage")
code, res = call("POST", READ, {"path": str(pieces / "ISO.geojson")})
check("no hemi", code == 200 and res.get("_hasHemi") is False)
check("nothing gathered beside it", (res.get("_damageBeside") or {}).get("files") == [])

print("\na missing file is a clear refusal")
code, res = call("POST", READ, {"path": str(pieces / "nope.geojson")})
check("400 with the file name", code == 400 and "nope.geojson" in str(res), code)

shutil.rmtree(root, ignore_errors=True)
print("\n" + (f"{len(fails)} FAILED: " + "; ".join(fails) if fails else "all checks passed"))
sys.exit(1 if fails else 0)

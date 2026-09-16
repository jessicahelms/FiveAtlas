"""Save as: choosing the file, remembering it, and never overwriting an original.

Runs against the ISOLATED backend (ATLAS_TEST_BASE, default :8060) and a
throwaway dataset folder it builds itself. It never opens a real dataset, so a
bug in the originals guard cannot reach a shared prediction file -- the case
this suite exists to catch.

    python test_save_as.py [scratch_dir]
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
    print(f"  {'ok  ' if cond else 'FAIL'}  {label}{('  ' + str(detail)) if detail else ''}")
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


def square(name, x):
    return {"type": "Feature", "properties": {"name": name},
            "geometry": {"type": "Polygon",
                         "coordinates": [[[x, 0], [x + 10, 0], [x + 10, 10], [x, 10], [x, 0]]]}}


def features_in(path):
    return len(json.load(open(path, encoding="utf-8"))["features"])


# ---- a throwaway dataset: one "original" region file ------------------------
scratch = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(tempfile.mkdtemp(prefix="saveas_"))
root = scratch / "SAVEAS_TEST_DS"
if root.exists():
    shutil.rmtree(root)
(root / "elsewhere").mkdir(parents=True)
original = root / "orig.regions.geojson"
original.write_text(json.dumps({"type": "FeatureCollection",
                                 "features": [square("A", 0), square("B", 20)]}),
                    encoding="utf-8")
original_bytes = original.read_bytes()

code, res = call("POST", "/api/datasets/open", {"path": str(root)})
if code != 200:
    raise SystemExit(f"could not open the scratch dataset: {code} {res}")
DS = res["id"]
# every run starts with nothing remembered
call("DELETE", f"/api/datasets/{DS}/regions/save-target")

FC2 = {"type": "FeatureCollection", "features": [square("A", 0), square("B", 20)]}
FC3 = {"type": "FeatureCollection", "features": [square("A", 0), square("B", 20), square("C", 40)]}
SAVE_AS = f"/api/datasets/{DS}/regions/save-as"
SAVE = f"/api/datasets/{DS}/regions"
TARGET = f"/api/datasets/{DS}/regions/save-target"

print("\nSave as a chosen file")
mine = root / "elsewhere" / "mine.geojson"
code, res = call("POST", SAVE_AS, {"fc": FC2, "path": str(mine)})
check("accepted", code == 200, code if code != 200 else "")
check("the chosen file is written", mine.exists() and features_in(mine) == 2)
check("the response names it", res.get("exported") == str(mine), res.get("exported"))
check("the app's own working copy is still written as the backup",
      res.get("saved") and Path(res["saved"]).exists())
check("the original is untouched", original.read_bytes() == original_bytes)
code, res = call("GET", TARGET)
check("it is remembered", res.get("path") == str(mine), res.get("path"))

print("\nlater clicks on Save update the same file")
code, res = call("PUT", SAVE, FC3)
check("Save says it wrote there", res.get("exported") == str(mine), res.get("exported"))
check("...and the file has the new region", features_in(mine) == 3)
check("no error", res.get("exportError") is None, res.get("exportError"))

print("\nan ORIGINAL region file is never overwritten")
code, res = call("POST", SAVE_AS, {"fc": FC3, "path": str(original)})
check("refused", code == 200 and res.get("refused") is True, res)
check("the original's bytes are unchanged", original.read_bytes() == original_bytes)
suggested = Path(res.get("suggested") or "")
check("a free name beside it is suggested",
      suggested.name == "orig.regions_edited.geojson" and suggested.parent == root,
      suggested)
check("...and nothing was created there yet", not suggested.exists())
code, res2 = call("GET", TARGET)
check("the remembered file did not change", res2.get("path") == str(mine), res2.get("path"))

print("\nthe same guard, spelled differently")
code, res = call("POST", SAVE_AS, {"fc": FC3, "path": str(original).upper()})
check("upper-case path still refused", res.get("refused") is True, res)
code, res = call("POST", SAVE_AS, {"fc": FC3, "path": str(original).replace("\\", "/")})
check("forward slashes still refused", res.get("refused") is True, res)

print("\nsaving beside the original, as suggested")
code, res = call("POST", SAVE_AS, {"fc": FC3, "path": str(suggested)})
check("written", code == 200 and suggested.exists() and features_in(suggested) == 3, res)
check("now remembered instead", call("GET", TARGET)[1].get("path") == str(suggested))

print("\nafter the folder is scanned again, that file is still OURS, not an original")
code, _ = call("POST", "/api/datasets/open", {"path": str(root)})
check("re-opened", code == 200)
code, res = call("GET", f"/api/datasets/{DS}/sources")
names = [r["name"] for r in (res.get("sources", {}).get("regions") or [])]
check("the scan now lists it as a region file", "orig.regions_edited" in names, names)
code, res = call("POST", SAVE_AS, {"fc": FC2, "path": str(suggested)})
check("Save as to it again is NOT refused", res.get("refused") is not True and res.get("exported") == str(suggested), res)
code, res = call("PUT", SAVE, FC3)
check("and plain Save still writes it", res.get("exported") == str(suggested) and res.get("exportError") is None, res.get("exportError"))

print("\nno extension, and a relative path")
bare = root / "elsewhere" / "noext"
code, res = call("POST", SAVE_AS, {"fc": FC2, "path": str(bare)})
check(".geojson is added", res.get("exported") == str(bare) + ".geojson"
      and Path(str(bare) + ".geojson").exists(), res.get("exported"))
code, res = call("POST", SAVE_AS, {"fc": FC2, "path": "relative.geojson"})
check("a relative path is refused", code == 400, code)

print("\na location that cannot be written")
remembered = call("GET", TARGET)[1].get("path")
bad = root / "no_such_folder" / "x.geojson"
code, res = call("POST", SAVE_AS, {"fc": FC3, "path": str(bad)})
check("reported, not a crash", code == 200 and res.get("exportError"), res)
check("the work is still saved in the app's own folder", res.get("saved") and Path(res["saved"]).exists())
check("the file being saved to before is kept",
      call("GET", TARGET)[1].get("path") == remembered, call("GET", TARGET)[1].get("path"))

print("\nStop saving here")
code, res = call("DELETE", TARGET)
check("forgotten", code == 200 and res.get("path") is None)
before = (root / "elsewhere" / "noext.geojson").read_bytes()
code, res = call("PUT", SAVE, FC2)
check("Save now writes only the app's own copy", res.get("exported") is None, res.get("exported"))

print("\nOpen folder is allowed for files FiveAtlas saved, and nothing else")
gone = root / "elsewhere" / "mine.geojson"
gone.unlink()           # so an allowed reveal answers 404 instead of opening Explorer
code, _ = call("POST", "/api/reveal", {"path": str(gone)})
check("a file this app saved is allowed (404 = allowed but gone)", code == 404, code)
code, _ = call("POST", "/api/reveal", {"path": str(root / "elsewhere" / "never_saved.geojson")})
check("a file it never saved is refused", code == 403, code)

shutil.rmtree(root, ignore_errors=True)
print("\n" + (f"{len(fails)} FAILED: " + "; ".join(fails) if fails else "all checks passed"))
sys.exit(1 if fails else 0)

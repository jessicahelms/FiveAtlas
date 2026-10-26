"""Loading a file must never damage the dataset you already have.

Four ways it did:

  * a GeoJSON holding NaN was SAVED as the working copy and only then failed to
    serialise, so the annotator saw an error, believed nothing had happened, and
    could not open the dataset again;
  * `path: "CON"` blocked for ever on the event loop, so EVERY route stopped
    answering -- Save included;
  * load-multi saved each file as it read, so a bad file after a good one left
    the working copy holding a partial load;
  * the dataset id goes straight into a path, and an id of '..' put the working
    copy and its snapshots in WORKDIR's PARENT.

Entirely offline: ATLAS_WORKDIR is pointed into a scratch directory BEFORE the
backend is imported and asserted afterwards, every file is written here, and the
routes are called in-process. No server, no port, nothing outside the scratch
directory -- and nothing on D:.

The one check that touches a device name runs in a SUBPROCESS with a timeout,
so a regression reports a failure instead of hanging this suite for ever -- a
thread is not enough, because the blocking open() holds the GIL.

Usage:  python test_load_safety.py [scratch dir]
"""
import asyncio
import json
import math
import os
import subprocess
import sys
import tempfile
from pathlib import Path

SCRATCH = Path(sys.argv[1] if len(sys.argv) > 1
               else tempfile.mkdtemp(prefix="loadsafe_"))
os.environ["ATLAS_WORKDIR"] = str(SCRATCH / "workdir")

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend"))

import config                                        # noqa: E402

if str(SCRATCH.resolve()) not in str(Path(config.WORKDIR).resolve()):
    raise SystemExit(f"ATLAS_WORKDIR escaped the scratch dir: {config.WORKDIR}")

import app as A                                      # noqa: E402
import datasets as ds                                # noqa: E402
import geo                                           # noqa: E402
import scan                                          # noqa: E402

fails = []


def check(label, cond, detail=""):
    print(f"  {'ok  ' if cond else 'FAIL'}  {label}{('  ' + detail) if detail else ''}")
    if not cond:
        fails.append(label)


def section(t):
    print(f"\n{t}")


def square(name, x, y, w=80):
    return {"type": "Feature", "properties": {"name": name},
            "geometry": {"type": "Polygon", "coordinates": [[
                [x, y], [x + w, y], [x + w, y + w], [x, y + w], [x, y]]]}}


GOOD = {"type": "FeatureCollection",
        "features": [square(f"R{i}", i * 200, 0) for i in range(4)]}


class Req:
    def __init__(self, body):
        self._b = body

    async def json(self):
        return self._b


def build(name):
    root = SCRATCH / "folders" / name
    root.mkdir(parents=True, exist_ok=True)
    (root / "regions.geojson").write_text(json.dumps(GOOD), encoding="utf-8")
    ds_id = ds.register(scan.scan_folder(str(root)))["id"]
    geo.save_edited(ds_id, GOOD)
    return ds_id, root


def nfeat(ds_id):
    try:
        return len(geo.load_regions(ds_id)[0].get("features") or [])
    except Exception as e:
        return f"UNREADABLE({type(e).__name__})"


def snapshots(ds_id):
    v = ds.workdir_for(ds_id) / "versions"
    return len(list(v.glob("regions_*.geojson"))) if v.exists() else 0


# ---------------------------------------------------------------------------
section("a file with NaN or infinity in it")

ds_id, root = build("NAN_DS")
for label, val in (("NaN", float("nan")), ("infinity", float("inf")),
                   ("-infinity", float("-inf"))):
    p = root / f"bad_{label}.geojson"
    p.write_text(json.dumps({"type": "FeatureCollection", "features": [
        {"type": "Feature", "properties": {"name": "X"},
         "geometry": {"type": "Polygon", "coordinates": [[
             [val, val], [1, 2], [3, 4], [val, val]]]}}]}), encoding="utf-8")
    before, snaps = nfeat(ds_id), snapshots(ds_id)
    try:
        asyncio.run(A.regions_load(ds_id, Req({"path": str(p)})))
        refused = False
    except A.HTTPException as e:
        refused = e.status_code == 400 and "not numbers" in str(e.detail)
    except Exception:
        refused = False
    check(f"a file holding {label} is refused", refused)
    check(f"...and the working copy is untouched", nfeat(ds_id) == before,
          f"{before} -> {nfeat(ds_id)}")
    check(f"...and nothing was snapshotted", snapshots(ds_id) == snaps)

ep = ds.edited_regions_path(ds_id)
txt = ep.read_text(encoding="utf-8")
check("the working copy on disk holds no bare NaN/Infinity",
      "NaN" not in txt and "Infinity" not in txt)


def strict(s):
    """Parse the way a tool that is not Python would: NaN is not JSON."""
    def boom(x):
        raise ValueError(x)
    json.loads(s, parse_constant=boom)


try:
    strict(txt)
    ok = True
except Exception:
    ok = False
check("and it is valid JSON for a strict parser", ok)

# The writer itself must refuse, so no path at all can produce such a file.
try:
    geo.save_edited(ds_id, {"type": "FeatureCollection", "features": [
        {"type": "Feature", "properties": {"name": "X"},
         "geometry": {"type": "Polygon", "coordinates": [[
             [float("nan"), 0], [1, 2], [3, 4], [float("nan"), 0]]]}}]})
    refused = False
except ValueError:
    refused = True
check("save_edited refuses to write a non-finite number", refused)
check("...and the previous working copy survived that too", nfeat(ds_id) == 4,
      f"{nfeat(ds_id)} features")

# ---------------------------------------------------------------------------
section("a path that is not a readable file")

# Pure name checks -- no I/O, so these can never block. Absent helper counts as
# a failure rather than an error, so this suite still reports on older code.
guard = getattr(geo, "_refuse_unreadable", None)


def refuses(p):
    if guard is None:
        return None
    try:
        guard(Path(p))
        return False
    except ValueError as e:
        return str(e)


for nm in ("CON", "con", "NUL", "nul.geojson", "COM1", "LPT1", "AUX", "PRN",
           "CON.json"):
    check(f"{nm!r} is refused as a reserved device name", bool(refuses(nm)),
          "no such guard" if guard is None else "")

said = refuses(root)
check("a folder is refused, and said to be a folder",
      bool(said) and "folder" in said, "no such guard" if guard is None else "")

for nm in ("regions.geojson", "CONtour.geojson", "NULL_region.geojson",
           "COM.geojson", "my.con.geojson"):
    check(f"{nm!r} is NOT mistaken for a device", refuses(nm) is False,
          "no such guard" if guard is None else "")

# End to end, in a SUBPROCESS with a timeout. A thread is not enough: open() on
# a Windows device blocks inside C with the GIL held, so the whole interpreter
# stops, main thread included -- against the unfixed code this suite hung rather
# than failing, which is worse than either.
PROBE = SCRATCH / "con_probe.py"
PROBE.write_text(
    "import asyncio, os, sys\n"
    f"os.environ['ATLAS_WORKDIR'] = r'{SCRATCH / 'probe_wd'}'\n"
    f"sys.path.insert(0, r'{Path(__file__).resolve().parent.parent / 'backend'}')\n"
    "import app as A, datasets as ds, scan\n"
    f"dsid = ds.register(scan.scan_folder(r'{root}'))['id']\n"
    "class Q:\n"
    "    async def json(self): return {'path': 'CON'}\n"
    "try:\n"
    "    asyncio.run(A.regions_read_file(dsid, Q()))\n"
    "    print('RETURNED')\n"
    "except A.HTTPException as e:\n"
    "    print('REFUSED', e.status_code)\n"
    "except Exception as e:\n"
    "    print('RAISED', type(e).__name__)\n",
    encoding="utf-8")
try:
    done = subprocess.run([sys.executable, "-W", "ignore", str(PROBE)],
                          capture_output=True, text=True, timeout=25)
    said = (done.stdout or "").strip().splitlines()[-1:] or [(done.stderr or "")[-60:]]
    said = said[0]
    hung = False
except subprocess.TimeoutExpired:
    said, hung = "never returned", True
check("read-file on a device name comes back at once", not hung, said)
check("...and it is refused", said.startswith("REFUSED 400"), said)

# ---------------------------------------------------------------------------
section("load-multi is all of them or none")

ds2, root2 = build("MULTI_DS")
one = root2 / "one.geojson"
one.write_text(json.dumps({"type": "FeatureCollection",
                           "features": [square("A", 0, 0)]}), encoding="utf-8")
two = root2 / "two.geojson"
two.write_text(json.dumps({"type": "FeatureCollection",
                           "features": [square("B", 500, 0)],
                           "_orientation": {"rot": 180, "flipH": False,
                                            "flipV": False},
                           "_provenance": [{"who": "A Colleague",
                                            "action": "trace"}]},
                          ), encoding="utf-8")
nan = root2 / "withnan.geojson"
nan.write_text(json.dumps({"type": "FeatureCollection", "features": [
    {"type": "Feature", "properties": {"name": "C"},
     "geometry": {"type": "Polygon", "coordinates": [[
         [float("nan"), 0], [1, 2], [3, 4], [float("nan"), 0]]]}}]}),
    encoding="utf-8")

for label, paths in (("a missing file after a good one",
                      [str(one), str(root2 / "nope.geojson")]),
                     ("a NaN file after a good one", [str(one), str(nan)])):
    before, snaps = nfeat(ds2), snapshots(ds2)
    try:
        asyncio.run(A.regions_load_multi(ds2, Req({"paths": paths})))
        refused = False
    except A.HTTPException as e:
        refused = e.status_code == 400
    check(f"{label} is refused", refused)
    check(f"...and the working copy is untouched", nfeat(ds2) == before,
          f"{before} -> {nfeat(ds2)}")
    check(f"...and nothing was snapshotted", snapshots(ds2) == snaps,
          f"{snaps} -> {snapshots(ds2)}")

# All good: it works, and carries the file-level members across.
res = asyncio.run(A.regions_load_multi(ds2, Req({"paths": [str(two), str(one)]})))
got = json.loads(bytes(res.body).decode("utf-8"))
check("several good files load", len(got.get("features") or []) == 2,
      f"{len(got.get('features') or [])} features")
# The file says it is rotated 180; the dataset is shown as imaged. It used to
# keep its own tag with its coordinates unmoved -- so the regions sat 180 deg
# off the image and every later rotate or export took them for already turned.
# Now they are brought into the dataset's frame and tagged with THAT.
check("a rotated file is brought into the dataset's frame and tagged with it",
      (got.get("_orientation") or {}).get("rot") == 0,
      repr(got.get("_orientation")))
two_src = json.loads(two.read_text(encoding="utf-8"))
moved = [f for f in got["features"]
         if f["properties"].get("name") == two_src["features"][0]["properties"].get("name")]
check("...its coordinates really were turned",
      bool(moved) and moved[0]["geometry"]["coordinates"] != two_src["features"][0]["geometry"]["coordinates"])
check("and its provenance trail is not dropped",
      any((e.get("who") == "A Colleague") for e in (got.get("_provenance") or [])),
      repr([e.get("who") for e in (got.get("_provenance") or [])]))

# ---------------------------------------------------------------------------
section("a dataset id can never write outside the workdir")

root_wd = Path(config.WORKDIR).resolve()
for raw in ("..", ".", "...", "../..", "a/../..", "C:/Windows", "..\\..\\x",
            "", "/", "\\", "con", "x:y"):
    w = ds.workdir_for(raw)
    inside = (root_wd in w.resolve().parents) or (w.resolve() == root_wd)
    check(f"workdir_for({raw!r}) stays inside the workdir", inside,
          str(w.resolve()))

for raw in ("DNMT3A_002_27_38_Het_F", "output~241fd5", "WAC_Het_006"):
    check(f"an ordinary id is not renamed: {raw!r}",
          ds.workdir_for(raw).name == raw, ds.workdir_for(raw).name)

check("an offline stub gets the same id as a scan would",
      ds._stub("C:/x/WAC Het 006")["id"] == scan._slug("WAC Het 006"),
      f"{ds._stub('C:/x/WAC Het 006')['id']!r} vs {scan._slug('WAC Het 006')!r}")

# ---------------------------------------------------------------------------
print()
if fails:
    print(f"{len(fails)} FAILED: " + "; ".join(fails))
    sys.exit(1)
print("all checks passed")

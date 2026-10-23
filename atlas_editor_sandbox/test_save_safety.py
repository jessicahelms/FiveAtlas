"""Save safety: the working copy is written atomically, the originals guard fails
closed, and PUT /orientation validates before it writes.

Entirely offline. No server, no port, no real dataset folder: ATLAS_WORKDIR is
pointed into a scratch directory BEFORE the backend is imported and asserted
afterwards, every dataset is synthetic geometry built here, and the dataset
"folders" are scratch folders too. Nothing outside the scratch directory is
written -- which is the point, because the three defects covered here are all
ways the app destroys a region file it was not asked to touch.

Usage:  python test_save_safety.py [scratch dir]
"""
import asyncio
import json
import os
import sys
import tempfile
import threading
import time
from pathlib import Path

SCRATCH = Path(sys.argv[1] if len(sys.argv) > 1
               else tempfile.mkdtemp(prefix="savesafe_"))
os.environ["ATLAS_WORKDIR"] = str(SCRATCH / "workdir")

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend"))

import config                                        # noqa: E402

if str(SCRATCH.resolve()) not in str(Path(config.WORKDIR).resolve()):
    raise SystemExit(f"ATLAS_WORKDIR escaped the scratch dir: {config.WORKDIR}")

import app as A                                      # noqa: E402
import datasets as ds                                # noqa: E402
import geo                                           # noqa: E402
import orientation as ORI                            # noqa: E402
import scan                                          # noqa: E402

fails = []


def check(label, cond, detail=""):
    print(f"  {'ok  ' if cond else 'FAIL'}  {label}{('  ' + detail) if detail else ''}")
    if not cond:
        fails.append(label)


def section(t):
    print(f"\n{t}")


def regions(n, tag="R"):
    """n separate squares in a row -- synthetic, nowhere near real data.

    200 px across, not 5: geo.load_regions() runs clean_features(spike=3.0) on
    everything it loads, and a square only a few pixels wide is itself narrower
    than the spike threshold, so it comes back altered and no comparison means
    anything.
    """
    feats = []
    for i in range(n):
        x = 1000 + i * 300
        feats.append({"type": "Feature", "properties": {"name": f"{tag}{i}"},
                      "geometry": {"type": "Polygon", "coordinates": [[
                          [x, 2000], [x + 200, 2000], [x + 200, 2200],
                          [x, 2200], [x, 2000]]]}})
    return {"type": "FeatureCollection", "features": feats}


def make_folder(name, nfeat=3):
    """A scratch folder holding one region file, scanned and registered."""
    root = SCRATCH / "folders" / name
    root.mkdir(parents=True, exist_ok=True)
    (root / "merged.regions.geojson").write_text(
        json.dumps(regions(nfeat, "ORIG")), encoding="utf-8")
    return root


def feats_on_disk(ds_id):
    return len(geo.load_regions(ds_id)[0].get("features") or [])


def coords_of(fc):
    return [f["geometry"]["coordinates"] for f in fc.get("features") or []]


class Req:
    """The only thing the route uses off a Request is .json()."""

    def __init__(self, body):
        self._b = body

    async def json(self):
        return self._b


def put_ori(ds_id, body):
    return asyncio.run(A.put_orientation(ds_id, Req(body)))


# ---------------------------------------------------------------------------
section("the working copy is written through a temp file and a rename")

root = make_folder("ATOMIC", 4)
DS1 = ds.register(scan.scan_folder(str(root)))["id"]
geo.save_edited(DS1, regions(40, "KEEP"))
ep = ds.edited_regions_path(DS1)
wd = ds.workdir_for(DS1)
check("a save produced a working copy", ep.exists(), f"{ep.stat().st_size} bytes")
check("and it left no temp file behind", not list(wd.glob("*fiveatlas-tmp*")))

# An interrupted write must leave the PREVIOUS file intact. Making os.replace
# fail is the honest way to stage it: the temp is written in full, then the
# rename -- the only step that touches the real file -- fails.
real_replace = geo.os.replace


def refuse_replace(src, dst):
    raise OSError("network name is no longer available")


geo.os.replace = refuse_replace
try:
    geo.save_edited(DS1, regions(90, "LOST"))
    interrupted = False
except OSError:
    interrupted = True
finally:
    geo.os.replace = real_replace

check("a save whose rename fails raises rather than reporting success", interrupted)
check("the previous working copy is still there and still whole",
      feats_on_disk(DS1) == 40, f"{feats_on_disk(DS1)} features")
check("the half-written temp file is cleaned up",
      not list(wd.glob("*fiveatlas-tmp*")),
      str([p.name for p in wd.glob("*fiveatlas-tmp*")]))

# Every write gets its own temp name. One fixed name lets two writers to the
# same target share it, so one can rename the other's half-written file in.
seen = []


def spy_replace(src, dst):
    seen.append(Path(src).name)
    return real_replace(src, dst)


geo.os.replace = spy_replace
try:
    for _ in range(5):
        geo.save_edited(DS1, regions(6, "T"))
finally:
    geo.os.replace = real_replace
check("every write uses its own temp name", len(seen) == len(set(seen)),
      f"{len(seen)} writes, {len(set(seen))} distinct names")
check("the temp name cannot be mistaken for a region file by a scan",
      all(not n.lower().endswith(".geojson") for n in seen))

# The real prize: a reader landing inside a save never sees a partial file.
# With open(path,"w") the live file drops to 0 bytes and grows back in place.
big = regions(6000, "BIG")
geo.save_edited(DS1, big)
bad = []
stop = threading.Event()


def reader():
    # A short pause between reads, deliberately. A tight loop keeps a handle on
    # the file essentially always open, and on Windows os.replace cannot rename
    # over a file another handle has open -- so a tight reader does not test
    # partial reads, it tests whether a pathological reader can starve the
    # writer out of its retry window. Real readers (this app's own load, a sync
    # client, antivirus) open, read and close. This still performs thousands of
    # reads across the saves below.
    while not stop.is_set():
        try:
            json.loads(ep.read_text(encoding="utf-8"))
        except (FileNotFoundError, PermissionError):
            # The rename window. On Windows a reader that opens the target at
            # the instant os.replace runs gets PermissionError, and on some
            # filesystems the directory entry is briefly absent. Neither is a
            # PARTIAL read, which is the property under test -- failing to open
            # the file is not the same as reading half of it. Caught by name
            # rather than broadly so a genuine corrupt read still shows up.
            pass
        except Exception as e:
            bad.append(type(e).__name__)
        time.sleep(0.001)


t = threading.Thread(target=reader, daemon=True)
t.start()
for _ in range(8):
    geo.save_edited(DS1, big)
stop.set()
t.join(timeout=10)
check("a read landing inside a save never sees a partial file", not bad,
      f"{len(bad)} unparseable reads" + (f" ({set(bad)})" if bad else ""))

# ---------------------------------------------------------------------------
section("the originals guard fails closed")

stub_root = make_folder("STUBBED", 3)
DS_STUB = ds.register(ds._stub(str(stub_root)))["id"]
check("a stub dataset has no scanned region sources",
      not (ds.get_dataset(DS_STUB).get("sources") or {}).get("regions"))
check("its own region file is protected all the same",
      A._is_original(DS_STUB, stub_root / "merged.regions.geojson"))

rootA = make_folder("GUARD_A", 3)
rootB = make_folder("GUARD_B", 3)
DSA = ds.register(scan.scan_folder(str(rootA)))["id"]
DSB = ds.register(scan.scan_folder(str(rootB)))["id"]
origA = rootA / "merged.regions.geojson"
origB = rootB / "merged.regions.geojson"
check("a dataset refuses its own original", A._is_original(DSA, origA))
check("and another dataset's original too", A._is_original(DSA, origB))

# The whitelist exists so the annotator's own file, saved beside the originals
# and picked up by the next scan, is not then refused to them.
mine = rootA / "merged.regions_edited.geojson"
geo.set_save_target(DSA, str(mine))
check("the app's own save-as file is not treated as an original",
      not A._is_original(DSA, mine))

# But it must not be usable to unprotect somebody else's.
geo.set_save_target(DSA, str(origB))
check("whitelisting cannot unprotect another dataset's original",
      A._is_original(DSA, origB))
check("the owner still refuses it as well", A._is_original(DSB, origB))

check("an unknown dataset fails closed", A._is_original("NO_SUCH_DATASET", origA))
check("a file that is nobody's original is still writable",
      not A._is_original(DSA, SCRATCH / "folders" / "somewhere_else.geojson"))

# ---------------------------------------------------------------------------
section("PUT /orientation validates before it writes")

root_o = make_folder("ORIENT", 3)
DSO = ds.register(scan.scan_folder(str(root_o)))["id"]
START = regions(12, "KEEP")
geo.save_edited(DSO, START)

for label, body in [
    ("an fc with an empty features list",
     {"rot": 90, "fc": {"type": "FeatureCollection", "features": []}}),
    ("an fc that is only {features: []}", {"rot": 90, "fc": {"features": []}}),
    ("an fc with no 'features' key at all",
     {"rot": 90, "fc": {"type": "FeatureCollection"}}),
]:
    before = feats_on_disk(DSO)
    try:
        put_ori(DSO, body)
        refused = False
    except A.HTTPException as e:
        refused = e.status_code == 400
    check(f"refuses {label}", refused)
    check(f"...and the working copy survives it", feats_on_disk(DSO) == before,
          f"{before} -> {feats_on_disk(DSO)}")

for label, body in [("a list", [90]), ("a bare number", 90), ("a string", "rot90")]:
    try:
        put_ori(DSO, body)
        refused = False
    except A.HTTPException as e:
        refused = e.status_code == 400
    check(f"refuses a body that is {label}", refused)

try:
    put_ori(DSO, {"rot": "banana"})
    refused = False
except A.HTTPException as e:
    refused = e.status_code == 400
check("refuses a rot that is not a number, instead of silently reporting rot 0",
      refused)
check("nothing above changed the working copy", feats_on_disk(DSO) == 12,
      f"{feats_on_disk(DSO)} features")

# ---------------------------------------------------------------------------
section("rotating a dataset with no imagery does not displace the regions")

# The baseline is what a LOAD gives for the unrotated state, not the dict we
# saved: every load re-cleans the geometry, so comparing against the raw input
# would measure the cleaner rather than the rotation.
geo.save_edited(DSO, START)
ORI.put(DSO, ORI.IDENTITY)
want = coords_of(geo.load_regions(DSO)[0])

combos = [(r, h, v) for r in (0, 90, 180, 270)
          for h in (False, True) for v in (False, True)]
off = []
for rot, fh, fv in combos:
    geo.save_edited(DSO, START)
    ORI.put(DSO, ORI.IDENTITY)
    put_ori(DSO, {"rot": rot, "flipH": fh, "flipV": fv})
    put_ori(DSO, {"rot": 0, "flipH": False, "flipV": False})
    if coords_of(geo.load_regions(DSO)[0]) != want:
        off.append(f"rot{rot}{'H' if fh else ''}{'V' if fv else ''}")
check("all 16 rot/flip combinations round-trip to the original coordinates",
      not off, f"{len(combos) - len(off)}/{len(combos)} exact"
      + (f"; off: {', '.join(off)}" if off else ""))

geo.save_edited(DSO, START)
ORI.put(DSO, ORI.IDENTITY)
for rot in (90, 180, 270, 0):
    put_ori(DSO, {"rot": rot, "flipH": False, "flipV": False})
check("turning a quarter at a time all the way round comes back exactly",
      coords_of(geo.load_regions(DSO)[0]) == want)

geo.save_edited(DSO, START)
ORI.put(DSO, ORI.IDENTITY)
put_ori(DSO, {"rot": 90, "flipH": False, "flipV": False})
canvas = ds.workdir_for(DSO) / "canvas.json"
check("the canvas is recorded once, so it cannot drift with the regions",
      canvas.exists(), str(json.load(open(canvas)) if canvas.exists() else {}))
first = json.load(open(canvas))
# Move the regions a long way, then rotate again: the canvas must not follow.
moved = {"type": "FeatureCollection", "features": [
    {**f, "geometry": {"type": "Polygon", "coordinates": [[
        [x + 9000, y + 9000] for x, y in f["geometry"]["coordinates"][0]]]}}
    for f in START["features"]]}
geo.save_edited(DSO, moved)
put_ori(DSO, {"rot": 180, "flipH": False, "flipV": False})
check("and it is still the same canvas after the regions moved 9,000 px",
      json.load(open(canvas)) == first,
      f"{first} -> {json.load(open(canvas))}")

# ---------------------------------------------------------------------------
print()
if fails:
    print(f"{len(fails)} FAILED: " + "; ".join(fails))
    sys.exit(1)
print("all checks passed")

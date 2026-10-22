"""The shared notes YAML must be MERGED, never rewritten from one working copy.

The file accumulates across annotators, machines and working copies. A copy that
holds only some of the damage shapes used to rewrite a whole key from its partial
view, so a colleague's record was deleted and the save reported success.

Entirely offline and entirely synthetic: ATLAS_WORKDIR is pointed into a scratch
directory BEFORE the backend is imported and asserted afterwards, the YAML and the
geometry are written here, and the route function is called in-process. No server,
no port, no real dataset folder, nothing written outside the scratch directory --
which matters more here than anywhere else, because this is the module that writes
into the dataset folder in anger.

Usage:  python test_notes_merge.py [scratch dir]
"""
import asyncio
import json
import os
import sys
import tempfile
from pathlib import Path

SCRATCH = Path(sys.argv[1] if len(sys.argv) > 1
               else tempfile.mkdtemp(prefix="notesmerge_"))
os.environ["ATLAS_WORKDIR"] = str(SCRATCH / "workdir")

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend"))

import config                                        # noqa: E402

if str(SCRATCH.resolve()) not in str(Path(config.WORKDIR).resolve()):
    raise SystemExit(f"ATLAS_WORKDIR escaped the scratch dir: {config.WORKDIR}")

import app as A                                      # noqa: E402
import damage as DMG                                 # noqa: E402
import datasets as ds                                # noqa: E402
import notes as N                                    # noqa: E402
import scan                                          # noqa: E402

fails = []


def check(label, cond, detail=""):
    print(f"  {'ok  ' if cond else 'FAIL'}  {label}{('  ' + detail) if detail else ''}")
    if not cond:
        fails.append(label)


def section(t):
    print(f"\n{t}")


# ---------------------------------------------------------------------------
section("the merge rule itself")

# The exact case from the audit: the file records two shapes, this copy holds one.
data = {"TH": {"damage": "bubble, separation", "voids": "bubble.1, separation.9"}}
upd = {"TH": {"damage": ["bubble"], "voids": ["bubble.1"]}}
known = {"TH": {"damage": {"bubble"}, "voids": {"bubble.1"}}}
res = N.apply_regions(data, upd, known=known)
check("a colleague's void survives a partial copy",
      data["TH"]["voids"] == "bubble.1, separation.9", repr(data["TH"]["voids"]))
check("and so does its designation",
      data["TH"]["damage"] == "bubble, separation", repr(data["TH"]["damage"]))
check("nothing is reported as changed", res["changes"] == [], repr(res["changes"]))
check("the preserved names are reported",
      sorted(k["names"][0] for k in res["kept"]) == ["separation", "separation.9"],
      repr([k.get("names") for k in res["kept"]]))

# Speaking for nothing (a fresh workdir, or Restore original) must drop nothing.
data = {"TH": {"damage": "bubble, separation", "voids": "bubble.1, separation.9"}}
N.apply_regions(data, {"TH": {"damage": [], "voids": []}}, known=None)
check("a copy with no damage shapes at all changes nothing",
      data["TH"]["voids"] == "bubble.1, separation.9"
      and data["TH"]["damage"] == "bubble, separation")

# A shape this copy HOLDS but has moved elsewhere must come off the old region.
data = {"TH": {"voids": "separation.1, bubble.4"}, "ISO": {"voids": ""}}
holds = {"voids": {"separation.1", "bubble.4"}, "damage": {"separation", "bubble"}}
N.apply_regions(data, {"TH": {"voids": ["bubble.4"]}, "ISO": {"voids": ["separation.1"]}},
                known={"TH": holds, "ISO": holds})
check("a shape moved to another region leaves the old one",
      data["TH"]["voids"] == "bubble.4", repr(data["TH"]["voids"]))
check("...and arrives at the new one",
      data["ISO"]["voids"] == "separation.1", repr(data["ISO"]["voids"]))

# Order: the file's own ordering survives, additions go on the end.
data = {"TH": {"voids": "separation.9, bubble.1"}}
N.apply_regions(data, {"TH": {"voids": ["bubble.1", "bubble.2"]}},
                known={"TH": {"voids": {"bubble.1", "bubble.2"}}})
check("the file's ordering is kept and additions are appended",
      data["TH"]["voids"] == "separation.9, bubble.1, bubble.2",
      repr(data["TH"]["voids"]))

# A file that uses a YAML list keeps its list.
data = {"TH": {"voids": ["bubble.1", "separation.9"]}}
N.apply_regions(data, {"TH": {"voids": ["bubble.1"]}},
                known={"TH": {"voids": {"bubble.1"}}})
check("a list-style value stays a list and keeps the other entry",
      data["TH"]["voids"] == ["bubble.1", "separation.9"], repr(data["TH"]["voids"]))

# An undrawn designation: droppable only when the client sent that region's ticks.
for asserted, expect in ((True, ""), (False, "missing")):
    data = {"TH": {"damage": "missing"}}
    k = {"TH": {"damage": {"missing"} if asserted else set()}}
    N.apply_regions(data, {"TH": {"damage": []}}, known=k)
    check(f"unticking Missing {'is honoured' if asserted else 'is NOT inferred'} "
          f"when the client {'sent' if asserted else 'did not send'} the ticks",
          data["TH"]["damage"] == expect, repr(data["TH"]["damage"]))

# A region this YAML has never heard of is still not added behind anyone's back.
data = {}
res = N.apply_regions(data, {"NEW": {"voids": ["bubble.1"]}},
                      known={"NEW": {"voids": {"bubble.1"}}})
check("a region the file has no entry for is not invented",
      data == {} and res["changes"] == [])


# ---------------------------------------------------------------------------
section("the whole route, on a shared file")


def shape(nm, x, y, w=120, h=120):
    return {"type": "Feature", "properties": {"name": nm},
            "geometry": {"type": "Polygon", "coordinates": [[
                [x, y], [x + w, y], [x + w, y + h], [x, y + h], [x, y]]]}}


def build(ds_name, yaml_text, damage_names):
    """A scratch dataset folder: two regions, a shared YAML, some damage shapes."""
    root = SCRATCH / "folders" / ds_name
    root.mkdir(parents=True, exist_ok=True)
    feats = [
        {"type": "Feature", "properties": {"name": "TH"},
         "geometry": {"type": "Polygon", "coordinates": [[
             [0, 0], [2000, 0], [2000, 2000], [0, 2000], [0, 0]]]}},
        {"type": "Feature", "properties": {"name": "ISO"},
         "geometry": {"type": "Polygon", "coordinates": [[
             [2000, 0], [4000, 0], [4000, 2000], [2000, 2000], [2000, 0]]]}},
    ]
    feats += [shape(nm, 200 + 300 * i, 300) for i, nm in enumerate(damage_names)]
    (root / "regions.geojson").write_text(
        json.dumps({"type": "FeatureCollection", "features": feats}), encoding="utf-8")
    (root / N.NOTES_NAME).write_text(yaml_text, encoding="utf-8")
    ds_id = ds.register(scan.scan_folder(str(root)))["id"]
    return ds_id, root, {"type": "FeatureCollection", "features": feats}


SHARED = """# annotation.notes.yaml - per-region annotation record.
experimentID: SYNTHETIC
notes: placeholder
TH:
  annotator: "A Colleague"
  region: ""
  damage: "bubble, separation"
  voids: "bubble.1, separation.9"
  enclaves: ""
  notes: "separation.9 traced on the other machine"
ISO:
  annotator: ""
  region: ""
  damage: ""
  voids: ""
  enclaves: ""
  notes: ""
"""

# This copy holds bubble.1 only -- separation.9 was drawn elsewhere.
ds_id, root, fc = build("MERGE_DS", SHARED, ["bubble.1"])
ctx = A._notes_context(ds_id, {"fc": fc, "scope": "all", "annotator": "Me"})
nf = ctx["files"]["notes"]
check("the route found the shared file", nf.get("found") and not nf.get("unreadable"))
after = "\n".join(l for l in nf["diff"]) if nf.get("diff") else ""
check("the preview does not propose dropping separation.9",
      "separation.9" not in after.replace("separation.9 traced", ""),
      repr([l for l in (nf.get("diff") or []) if "separation" in l]))
check("the preview reports what it is preserving",
      any("separation.9" in (k.get("names") or []) for k in (nf.get("kept") or [])),
      repr([k.get("names") for k in (nf.get("kept") or [])]))

# Write it for real (into the scratch folder) and read it back.
req = type("R", (), {"json": lambda self: asyncio.sleep(0, {"fc": fc, "scope": "all",
                                                            "annotator": "Me"})})()


async def _save():
    class Req:
        async def json(self):
            return {"fc": fc, "scope": "all", "annotator": "Me", "force": False}
    return await A.notes_save(ds_id, Req())


saved = asyncio.run(_save())
doc = N.read(root / N.NOTES_NAME)
th = doc["data"]["TH"]
check("after a real save, both voids are on disk",
      "separation.9" in str(th["voids"]) and "bubble.1" in str(th["voids"]),
      repr(th["voids"]))
check("...and both designations", "separation" in str(th["damage"])
      and "bubble" in str(th["damage"]), repr(th["damage"]))
check("the colleague's note is untouched",
      "other machine" in str(th["notes"]), repr(th["notes"]))
check("the colleague's name is untouched",
      str(th["annotator"]) == "A Colleague", repr(th["annotator"]))

# And the geometry-side case: the SAME file, a copy that holds nothing.
ds2, root2, fc2 = build("MERGE_DS_EMPTY", SHARED, [])
ctx2 = A._notes_context(ds2, {"fc": fc2, "scope": "all", "annotator": "Me"})
nf2 = ctx2["files"]["notes"]
check("a copy with no damage shapes proposes no damage change",
      not [c for c in (nf2.get("changes") or []) if c["key"] in ("damage", "voids")],
      repr([(c["region"], c["key"], c["after"])
            for c in (nf2.get("changes") or [])]))

# ---------------------------------------------------------------------------
print()
if fails:
    print(f"{len(fails)} FAILED: " + "; ".join(fails))
    sys.exit(1)
print("all checks passed")

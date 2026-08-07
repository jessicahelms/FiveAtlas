"""The notes routes, end to end, against a dataset folder built for the purpose.

READ THIS BEFORE RUNNING IT ANYWHERE ELSE. These routes are the only ones in the
app that WRITE INTO THE DATASET FOLDER. An isolated `ATLAS_WORKDIR` does not
protect against them — the workdir is not where they write. So this test builds
its own dataset folder in a scratch directory, opens THAT, and never names the
real one.

    ATLAS_WORKDIR=<scratch>/wd python -m uvicorn app:app --app-dir backend --port 8060
    python test_notes_routes.py <scratch>

The two YAMLs are copied from the real sample so the round-trip is exercised
against a file people actually hand-maintain, not a tidy invention.
"""
import json
import os
import shutil
import sys
import tempfile
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, r"C:\Users\FIVE\source\repos\Jess\atlas_editor\backend")

SAMPLE = Path(r"S:\Phys\FIV911 Atlas\RealDS\AllenBA 3D\Images from Box"
              r"\DNMT3A_002_27_38_Het_F")
BASE = os.environ.get("ATLAS_TEST_BASE", "http://127.0.0.1:8060")
if ":8050" in BASE or ":8000" in BASE:
    raise SystemExit("refusing to run against the live backend — see the docstring")
SCRATCH = Path(sys.argv[1] if len(sys.argv) > 1 else tempfile.mkdtemp(prefix="notesrt_"))

fails = []


def check(label, cond, detail=""):
    print(f"  {'ok  ' if cond else 'FAIL'}  {label}{('  ' + detail) if detail else ''}")
    if not cond:
        fails.append(label)


def req(method, path, body=None):
    data = json.dumps(body).encode() if body is not None else None
    r = urllib.request.Request(BASE + path, data=data, method=method,
                               headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(r, timeout=120) as resp:
            return resp.status, json.load(resp)
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode()[:300]


def box(name, x0, y0, x1, y1, **props):
    return {"type": "Feature",
            "properties": {"name": name, **props},
            "geometry": {"type": "Polygon",
                         "coordinates": [[[x0, y0], [x1, y0], [x1, y1], [x0, y1], [x0, y0]]]}}


def make_folder(tag, with_yamls=True):
    d = SCRATCH / tag
    if d.exists():
        shutil.rmtree(d)
    d.mkdir(parents=True)
    if with_yamls:
        for n in ("annotation.notes.yaml", "metadata.yml"):
            shutil.copy2(SAMPLE / n, d / n)
    with open(d / "merged.regions.geojson", "w", encoding="utf-8") as fh:
        json.dump(FC, fh)
    return d


# ISO and TH exist in the real notes file; separation.1 sits inside TH.
FC = {
    "type": "FeatureCollection",
    "features": [box("ISO", 0, 0, 100, 100), box("TH", 100, 0, 200, 100),
                 box("separation.1", 120, 20, 140, 40)],
}
ACCOUNT = os.environ.get("USERNAME") or os.environ.get("USER") or "unknown"
# The trail the client would have carried: this annotator edited ISO and drew
# separation.1. Nothing else is theirs to write.
MINE = {**FC, "_provenance": [
    {"t": "2026-08-07T10:00:00", "who": "Jessica", "account": ACCOUNT,
     "app": "test", "action": "share-borders", "regions": ["ISO"]},
    {"t": "2026-08-07T10:01:00", "who": "Jessica", "account": ACCOUNT,
     "app": "test", "action": "add-region", "regions": ["separation.1"]},
    {"t": "2026-08-07T09:00:00", "who": "Someone Else", "account": "other",
     "app": "test", "action": "share-borders", "regions": ["CP"]},
]}

print(f"scratch = {SCRATCH}\nbase    = {BASE}")

# --- open a dataset that is ours to write to ------------------------------------
folder = make_folder("ds_notes")
code, desc = req("POST", "/api/datasets/open", {"path": str(folder)})
if code != 200:
    raise SystemExit(f"could not open the scratch dataset: {code} {desc}")
ds_id = desc["id"]
print(f"dataset = {ds_id}  ({folder})")

# --- what is there ---------------------------------------------------------------
print("\nfinding the files")
code, st = req("GET", f"/api/datasets/{ds_id}/notes")
check("the route reports both files", code == 200
      and st["files"]["notes"]["found"] and st["files"]["metadata"]["found"])
check("it names the exact path, in the dataset folder",
      Path(st["files"]["notes"]["path"]) == folder / "annotation.notes.yaml")
check("it lists the regions the YAML knows",
      "ISO" in st["files"]["notes"]["regions"]
      and len(st["files"]["notes"]["regions"]) == 23,
      str(len(st["files"]["notes"]["regions"])))
check("the roster is read out of metadata.yml",
      st["files"]["metadata"]["annotators"] == [],
      str(st["files"]["metadata"]["annotators"]))

# --- preview is read-only --------------------------------------------------------
print("\npreview")
before = (folder / "annotation.notes.yaml").read_bytes()
code, pv = req("POST", f"/api/datasets/{ds_id}/notes/preview",
               {"fc": MINE, "annotator": "Jessica"})
check("preview succeeds", code == 200, str(pv)[:120] if code != 200 else "")
check("preview writes nothing", (folder / "annotation.notes.yaml").read_bytes() == before)
nf = pv["files"]["notes"]
check("it works out which regions are this annotator's",
      sorted(pv["workedOn"]) == ["ISO", "TH"], str(pv["workedOn"]))
check("...including the host of a damage shape they drew, not just the shape",
      "TH" in pv["workedOn"])
check("someone else's region is not in scope", "CP" not in pv["workedOn"])
changed = {c["region"] for c in nf["changes"]}
check("only their regions are changed", changed == {"ISO", "TH"}, str(changed))
check("the damage reaches the region that holds it",
      any(c["key"] == "damage" and c["region"] == "TH"
          and "separation" in str(c["after"]) for c in nf["changes"]),
      str([c for c in nf["changes"] if c["region"] == "TH"]))
check("the void is listed by shape id",
      any(c["key"] == "voids" and c["region"] == "TH"
          and "separation.1" in str(c["after"]) for c in nf["changes"]))
check("the annotator's name is written in",
      any(c["key"] == "annotator" and c["after"] == "Jessica" for c in nf["changes"]))
check("the diff is a diff, not a dump",
      len(nf["diff"]) < 30 and any(l.startswith("@@") for l in nf["diff"]),
      f"{len(nf['diff'])} lines")
check("no line changed that no edit accounts for", nf["unexpected"] == [],
      str(nf["unexpected"][:4]))
check("the roster edit is previewed too",
      pv["files"]["metadata"]["changes"] and not pv["files"]["metadata"]["unchanged"])

# Damage is decided by NAME, however the shape got there. A region RENAMED to a
# designation is damage from that moment — no add-region entry exists for it.
RENAMED = {"type": "FeatureCollection", "_provenance": MINE["_provenance"],
           "features": [box("ISO", 0, 0, 100, 100),
                        box("TH", 100, 0, 200, 100),
                        box("bubble.1", 120, 20, 140, 40)]}
code, pv_r = req("POST", f"/api/datasets/{ds_id}/notes/preview",
                 {"fc": RENAMED, "annotator": "Jessica"})
th = [c for c in pv_r["files"]["notes"]["changes"]
      if c["region"] == "TH" and c["key"] == "damage"]
check("a region renamed to a designation is picked up as damage",
      th and "bubble" in str(th[0]["after"]), str(th)[:120])
check("...and its host region comes into scope without any entry naming it",
      "TH" in pv_r["workedOn"], str(pv_r["workedOn"]))

# A shape a COLLEAGUE's trail claims stays theirs to record.
THEIRS = {**RENAMED, "_provenance": [
    {"t": "2026-08-07T09:00:00", "who": "Someone Else", "account": "other",
     "app": "test", "action": "add-region", "regions": ["bubble.1"]}]}
code, pv_x = req("POST", f"/api/datasets/{ds_id}/notes/preview",
                 {"fc": THEIRS, "annotator": "Jessica"})
check("damage another annotator drew is not ours to write",
      pv_x["workedOn"] == [], str(pv_x["workedOn"]))

# A designation nobody can draw is ticked on the region itself, and has to reach
# the YAML as well as the SmartSheet cell — otherwise half the vocabulary only
# ever exists in the clipboard.
TICKED = {**MINE, "features": [
    {**f, "properties": {**f["properties"], "_damage_extra": ["cutoff"],
                         "_damage_done": True}}
    if f["properties"]["name"] == "ISO" else f for f in MINE["features"]]}
code, pv_t = req("POST", f"/api/datasets/{ds_id}/notes/preview",
                 {"fc": TICKED, "annotator": "Jessica"})
iso_damage = [c for c in pv_t["files"]["notes"]["changes"]
              if c["region"] == "ISO" and c["key"] == "damage"]
check("a hand-ticked designation reaches the YAML's damage field",
      iso_damage and "cutoff" in str(iso_damage[0]["after"]), str(iso_damage)[:140])
code, cells = req("POST", f"/api/datasets/{ds_id}/damage/cells", {"fc": TICKED})
iso_cell = {c["region"]: c for c in cells["cells"]}["ISO"]
check("...and the same tick is the chip in the SmartSheet cell",
      iso_cell["values"] == ["Done", "Cutoff"], str(iso_cell["values"]))
check("the Done tick stays out of the YAML — it is not damage",
      "Done" not in str(iso_damage[0]["after"]), str(iso_damage[0]["after"]))

# 'all' widens it; 'mine' is the default
code, pv_all = req("POST", f"/api/datasets/{ds_id}/notes/preview",
                   {"fc": MINE, "annotator": "Jessica", "scope": "all"})
check("scope=all writes every region the geometry has",
      {c["region"] for c in pv_all["files"]["notes"]["changes"]} == {"ISO", "TH"},
      str({c["region"] for c in pv_all["files"]["notes"]["changes"]}))
# A file carrying no trail at all: the damage in it is still nobody's to lose, so
# the regions holding it come into scope — but nothing else does.
code, pv_none = req("POST", f"/api/datasets/{ds_id}/notes/preview",
                    {"fc": FC, "annotator": "Jessica"})
check("with no trail at all, the regions holding damage still come into scope",
      pv_none["workedOn"] == ["TH"], str(pv_none["workedOn"]))
check("...and a region with no damage is left alone",
      "ISO" not in pv_none["workedOn"]
      and all(c["region"] == "TH" for c in pv_none["files"]["notes"]["changes"]),
      str({c["region"] for c in pv_none["files"]["notes"]["changes"]}))

# --- saving ----------------------------------------------------------------------
print("\nsaving")
code, sv = req("POST", f"/api/datasets/{ds_id}/notes/save",
               {"fc": MINE, "annotator": "Jessica",
                "fingerprints": {k: v.get("fingerprint")
                                 for k, v in pv["files"].items()}})
check("both files are written", code == 200
      and sorted(w["file"] for w in sv["written"]) == ["metadata", "notes"],
      str([w["file"] for w in sv["written"]] + [s.get("reason") for s in sv["skipped"]]))
on_disk = (folder / "annotation.notes.yaml").read_text(encoding="utf-8")
check("the damage landed in the file", 'damage: "separation"' in on_disk)
check("the void landed in the file", 'voids: "separation.1"' in on_disk)
check("the annotator landed in the file", 'annotator: "Jessica"' in on_disk)
check("a region nobody touched is byte-identical to the original",
      'CP:\n  annotator: ""\n  region: "Caudoputamen"' in on_disk)
check("the roster now names the annotator",
      "- Jessica" in (folder / "metadata.yml").read_text(encoding="utf-8"))
check("metadata.yml kept its header and end marker",
      (folder / "metadata.yml").read_text(encoding="utf-8").startswith("# Sample Metadata"))

code, again = req("POST", f"/api/datasets/{ds_id}/notes/save",
                  {"fc": MINE, "annotator": "Jessica"})
check("saving twice writes nothing the second time",
      code == 200 and again["written"] == []
      and all(s["reason"] == "nothing-to-write" for s in again["skipped"]),
      str([s.get("reason") for s in again["skipped"]]))

# --- a colleague saves between preview and save ----------------------------------
print("\nthe file is shared")
folder2 = make_folder("ds_shared")
code, desc2 = req("POST", "/api/datasets/open", {"path": str(folder2)})
ds2 = desc2["id"]
code, pv2 = req("POST", f"/api/datasets/{ds2}/notes/preview",
                {"fc": MINE, "annotator": "Jessica"})
stale = {k: v.get("fingerprint") for k, v in pv2["files"].items()}
p2 = folder2 / "annotation.notes.yaml"
theirs = p2.read_text(encoding="utf-8").replace(          # read BEFORE opening to
    'CP:\n  annotator: ""', 'CP:\n  annotator: "Emma Jones"')   # write, or it truncates
with open(p2, "w", encoding="utf-8", newline="") as fh:      # the colleague's save
    fh.write(theirs)
check("the colleague's edit is on disk before we save",
      'annotator: "Emma Jones"' in p2.read_text(encoding="utf-8"))
code, sv2 = req("POST", f"/api/datasets/{ds2}/notes/save",
                {"fc": MINE, "annotator": "Jessica", "which": ["notes"],
                 "fingerprints": stale})
check("a file that changed since the preview is not overwritten",
      code == 200 and sv2["written"] == []
      and [s["reason"] for s in sv2["skipped"]] == ["changed-on-disk"],
      str(sv2["skipped"])[:160])
check("their edit is still on disk",
      'annotator: "Emma Jones"' in p2.read_text(encoding="utf-8"))
code, sv3 = req("POST", f"/api/datasets/{ds2}/notes/save",
                {"fc": MINE, "annotator": "Jessica", "which": ["notes"]})
check("previewing again and saving goes through",
      code == 200 and [w["file"] for w in sv3["written"]] == ["notes"],
      str(sv3["skipped"])[:120])
after2 = p2.read_text(encoding="utf-8")
check("and their edit survived ours", 'annotator: "Emma Jones"' in after2
      and 'annotator: "Jessica"' in after2)

# --- a file that cannot be read --------------------------------------------------
# An empty or truncated YAML on a share must not read as "no changes": that would
# skip the save silently, forever.
print("\nan unreadable file")
folder4 = make_folder("ds_broken")
code, desc4 = req("POST", "/api/datasets/open", {"path": str(folder4)})
ds4 = desc4["id"]
open(folder4 / "annotation.notes.yaml", "w").close()          # truncated to nothing
code, pv4 = req("POST", f"/api/datasets/{ds4}/notes/preview", {"fc": MINE})
check("an empty YAML is reported, not a 500",
      code == 200 and pv4["files"]["notes"].get("unreadable") is True, str(code))
code, sv6 = req("POST", f"/api/datasets/{ds4}/notes/save",
                {"fc": MINE, "which": ["notes"]})
check("and it is not written over",
      code == 200 and [s["reason"] for s in sv6["skipped"]] == ["unreadable"]
      and (folder4 / "annotation.notes.yaml").read_text(encoding="utf-8") == "",
      str(sv6.get("skipped")))

# --- creating one, only on purpose -----------------------------------------------
print("\ncreating")
folder3 = make_folder("ds_empty", with_yamls=False)
code, desc3 = req("POST", "/api/datasets/open", {"path": str(folder3)})
ds3 = desc3["id"]
code, st3 = req("GET", f"/api/datasets/{ds3}/notes")
check("a folder with no YAML says so", code == 200 and not st3["files"]["notes"]["found"])
check("...and names where it would go",
      Path(st3["files"]["notes"]["path"]) == folder3 / "annotation.notes.yaml")
code, pv3 = req("POST", f"/api/datasets/{ds3}/notes/preview", {"fc": MINE})
check("preview against a missing file does not create it",
      code == 200 and not pv3["files"]["notes"]["found"]
      and not (folder3 / "annotation.notes.yaml").exists())
code, sv4 = req("POST", f"/api/datasets/{ds3}/notes/save", {"fc": MINE})
check("neither does saving",
      code == 200 and not (folder3 / "annotation.notes.yaml").exists()
      and [s["reason"] for s in sv4["skipped"]].count("missing") >= 1,
      str([s.get("reason") for s in sv4["skipped"]]))
code, cr = req("POST", f"/api/datasets/{ds3}/notes/create", {"fc": MINE})
check("creating is its own, explicit call", code == 200
      and (folder3 / "annotation.notes.yaml").exists())
check("the new file has the regions from the geometry, damage excluded",
      cr["regions"] == ["ISO", "TH"], str(cr.get("regions")))
code, dup = req("POST", f"/api/datasets/{ds3}/notes/create", {"fc": MINE})
check("a second create is refused rather than replacing the first", code == 409, str(code))
code, sv5 = req("POST", f"/api/datasets/{ds3}/notes/save", {"fc": MINE,
                                                            "annotator": "Jessica"})
check("the created file then takes a normal save",
      code == 200 and [w["file"] for w in sv5["written"]] == ["notes"],
      str(sv5["skipped"])[:120])
check("and the damage is in it",
      'separation.1' in (folder3 / "annotation.notes.yaml").read_text(encoding="utf-8"))

print("\n" + (f"{len(fails)} FAILED: " + "; ".join(fails) if fails else "all checks passed"))
print(f"scratch left at {SCRATCH}")
sys.exit(1 if fails else 0)

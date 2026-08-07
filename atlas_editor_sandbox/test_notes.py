"""notes.py — round-trip fidelity, the shared-file rules, and what gets written.

Offline: no server, no dataset folder. The two real YAMLs are COPIED into a scratch
directory and every write happens there. This module is the one that writes to the
dataset folder in anger, so a test that pointed at the real one would be the exact
mistake this project has already made twice with the backend.

Usage:  python test_notes.py [scratch dir]
"""
import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, r"C:\Users\FIVE\source\repos\Jess\atlas_editor\backend")

import notes as N                              # noqa: E402

SAMPLE = Path(r"S:\Phys\FIV911 Atlas\RealDS\AllenBA 3D\Images from Box"
              r"\DNMT3A_002_27_38_Het_F")
SCRATCH = Path(sys.argv[1] if len(sys.argv) > 1 else tempfile.mkdtemp(prefix="notes_"))

fails = []


def check(label, cond, detail=""):
    print(f"  {'ok  ' if cond else 'FAIL'}  {label}{('  ' + detail) if detail else ''}")
    if not cond:
        fails.append(label)


def fresh():
    """A private copy of the sample's two YAMLs."""
    d = SCRATCH / "ds"
    if d.exists():
        shutil.rmtree(d)
    d.mkdir(parents=True)
    for n in ("annotation.notes.yaml", "metadata.yml"):
        shutil.copy2(SAMPLE / n, d / n)
    return d


if str(SAMPLE) in str(SCRATCH):
    raise SystemExit("scratch dir must not be inside the dataset folder")

print(f"scratch = {SCRATCH}")

# --- the round trip has to be exact ---------------------------------------------
# Everything else rests on this: if an untouched file does not come back byte for
# byte, every diff is full of noise and "refuse if an untouched line moved" is
# unusable.
print("\nround trip")
for name in ("annotation.notes.yaml", "metadata.yml"):
    raw = (SAMPLE / name).read_text(encoding="utf-8")
    data, render = N.parse(raw)
    out = render(data)
    check(f"{name} comes back byte for byte", out == raw,
          "" if out == raw else f"{len(raw)} -> {len(out)} chars")

meta_raw = (SAMPLE / "metadata.yml").read_text(encoding="utf-8")
check("the leading comment survives", N.parse(meta_raw)[1](N.parse(meta_raw)[0])
      .startswith("# Sample Metadata"))
check("the ... end marker survives",
      N.parse(meta_raw)[1](N.parse(meta_raw)[0]).rstrip().endswith("..."))

notes_raw = (SAMPLE / "annotation.notes.yaml").read_text(encoding="utf-8")
check("a file with no --- does not gain one",
      "---" not in N.parse(notes_raw)[1](N.parse(notes_raw)[0]))

# --- finding, never creating ----------------------------------------------------
print("\nfinding files")
ds = fresh()
found = N.find(ds)
check("both files are found", found["notes"]["found"] and found["metadata"]["found"])
check("the exact path is reported",
      Path(found["notes"]["path"]).name == "annotation.notes.yaml")
empty = SCRATCH / "empty"
empty.mkdir(exist_ok=True)
gone = N.find(empty)
check("an empty folder reports absence, not an error", not gone["notes"]["found"])
check("...and still names where it WOULD go",
      Path(gone["notes"]["path"]).parent == empty
      and Path(gone["notes"]["path"]).name == "annotation.notes.yaml")
check("nothing was created by looking", not any(empty.iterdir()),
      str([p.name for p in empty.iterdir()]))

# --- writing per-region damage --------------------------------------------------
print("\nwriting damage and voids")
doc = N.read(ds / "annotation.notes.yaml")
updates = {"ISO": {"damage": ["separation", "voidlarge"],
                   "voids": ["separation.1", "voidlarge.2"]},
           "TH": {"damage": ["bubble"], "voids": ["bubble.1"]}}
changes = N.apply_regions(doc["data"], updates, annotator="Jessica",
                          only={"ISO"})
after = doc["render"](doc["data"])
check("only the region in `only` is written",
      {c["region"] for c in changes} == {"ISO"}, str({c["region"] for c in changes}))
check("TH is untouched — a colleague's entry is not ours to write",
      'TH:\n  annotator: ""' in after)
# The values in this file are all double-quoted, and a replaced value keeps the
# quoting the key already had — so the edit reads like the rest of the file
# rather than announcing itself.
check("damage is written comma-joined, the way this file writes it",
      'damage: "separation, voidlarge"' in after,
      [l for l in after.splitlines() if "damage:" in l][:1][0].strip())
check("voids list the shape ids", 'voids: "separation.1, voidlarge.2"' in after)
check("the annotator is filled in", 'annotator: "Jessica"' in after)
check("the diff is only the lines we changed",
      len([l for l in N.diff(doc["text"], after)
           if l.startswith(("+", "-")) and not l.startswith(("+++", "---"))]) == 6,
      str([l for l in N.diff(doc["text"], after) if l.startswith(("+", "-"))][:8]))
check("nothing unexpected moved", N.unexpected_lines(doc["text"], after, changes) == [],
      str(N.unexpected_lines(doc["text"], after, changes)[:4]))
check("the rest of the file is identical",
      len(after.splitlines()) == len(doc["text"].splitlines()))

# an existing annotator is not overwritten
doc2 = N.read(ds / "annotation.notes.yaml")
doc2["data"]["ISO"]["annotator"] = "Emma Jones"
N.apply_regions(doc2["data"], updates, annotator="Jessica", only={"ISO"})
check("someone else's name in `annotator` is left alone",
      str(doc2["data"]["ISO"]["annotator"]) == "Emma Jones")

# a region the YAML has never heard of
doc3 = N.read(ds / "annotation.notes.yaml")
ch3 = N.apply_regions(doc3["data"], {"NOSUCH": {"damage": ["bubble"], "voids": []}},
                      only=None)
check("a region missing from the YAML is not silently added",
      ch3 == [] and "NOSUCH" not in doc3["render"](doc3["data"]))
check("...it is reported instead",
      N.missing_regions(doc3["data"], ["ISO", "NOSUCH"]) == ["NOSUCH"])

# placeholder means "not set", never data
check("placeholder reads as unset", N.is_unset("placeholder") and N.is_unset("")
      and not N.is_unset("separation"))

# --- metadata.yml: the annotator roster -----------------------------------------
print("\nthe annotator roster")
meta = N.read(ds / "metadata.yml")
c = N.add_annotator(meta["data"], "Jessica")
out = meta["render"](meta["data"])
check("a name replaces the seeded placeholder rather than joining it",
      "- Jessica" in out and "- placeholder" not in out.split("annotators:")[1],
      out.split("annotators:")[1].strip())
check("the change is reported", c and c["key"] == "annotators")
check("the header and markers still survive an edit",
      out.startswith("# Sample Metadata") and out.rstrip().endswith("..."))
check("adding the same name twice does nothing",
      N.add_annotator(meta["data"], "Jessica") is None)
meta2 = N.read(ds / "metadata.yml")
N.add_annotator(meta2["data"], "Emma Jones")
N.add_annotator(meta2["data"], "Jessica")
check("a second name is appended, not swapped in",
      "- Emma Jones" in meta2["render"](meta2["data"])
      and "- Jessica" in meta2["render"](meta2["data"]))
check("only the roster lines moved",
      N.unexpected_lines(meta2["text"], meta2["render"](meta2["data"]),
                         [{"key": "annotators"}]) == [],
      str(N.unexpected_lines(meta2["text"], meta2["render"](meta2["data"]),
                             [{"key": "annotators"}])))

# --- the shared-file rules ------------------------------------------------------
print("\nthe file is shared")
p = ds / "annotation.notes.yaml"
doc = N.read(p)
before_fp = doc["fingerprint"]
N.apply_regions(doc["data"], updates, only={"ISO"})
text = doc["render"](doc["data"])

# a colleague writes between our load and our save
with open(p, "w", encoding="utf-8", newline="") as fh:
    fh.write(doc["text"].replace('TH:\n  annotator: ""',
                                 'TH:\n  annotator: "Emma Jones"'))
res = N.save(p, text, expect=before_fp)
check("a file that changed under us is NOT overwritten", not res["ok"],
      str(res.get("reason")))
check("the refusal says it changed on disk", res.get("reason") == "changed-on-disk")
check("their edit is still there",
      'annotator: "Emma Jones"' in p.read_text(encoding="utf-8"))

# with a current fingerprint it goes through
doc = N.read(p)
N.apply_regions(doc["data"], updates, annotator="Jessica", only={"ISO"})
text = doc["render"](doc["data"])
res = N.save(p, text, expect=doc["fingerprint"])
check("a save against the current file succeeds", res.get("ok"))
on_disk = p.read_text(encoding="utf-8")
check("what landed on disk is exactly what was previewed", on_disk == text)
check("the colleague's edit survived our write",
      'annotator: "Emma Jones"' in on_disk)
check("the fingerprint moves after a write",
      res["fingerprint"]["sha1"] != doc["fingerprint"]["sha1"])
check("no stray temp file is left behind",
      not any(x.name.endswith(".tmp") for x in ds.iterdir()),
      str([x.name for x in ds.iterdir()]))
check("re-reading gives back what we wrote, unchanged by another round trip",
      N.read(p)["render"](N.read(p)["data"]) == on_disk)

# --- creating one, on purpose ---------------------------------------------------
print("\ncreating a file that does not exist")
newp = empty / "annotation.notes.yaml"
with open(newp, "w", encoding="utf-8", newline="") as fh:
    fh.write(N.new_notes_text(["ISO", "TH", "PAL.1"], experiment_id="SAMPLE_1",
                              by="Jessica"))
fresh_doc = N.read(newp)
check("a created file parses back", fresh_doc is not None
      and set(["ISO", "TH", "PAL.1"]).issubset(set(fresh_doc["data"].keys())))
check("it round-trips like the hand-written ones",
      fresh_doc["render"](fresh_doc["data"]) == fresh_doc["text"])
check("it says where it came from", fresh_doc["text"].startswith("# annotation.notes"))
check("every region has the six keys",
      all(set(N.REGION_KEYS) == set(fresh_doc["data"][r].keys())
          for r in ("ISO", "TH", "PAL.1")))
ch = N.apply_regions(fresh_doc["data"], {"ISO": {"damage": ["bubble"],
                                                 "voids": ["bubble.1"]}}, only=None)
check("and it takes an edit", [c["key"] for c in ch] == ["damage", "voids"])

print("\n" + (f"{len(fails)} FAILED: " + "; ".join(fails) if fails else "all checks passed"))
print(f"scratch left at {SCRATCH}")
sys.exit(1 if fails else 0)

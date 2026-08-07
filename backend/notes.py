"""Reading and writing the two per-sample YAMLs.

    annotation.notes.yaml   one entry per region: annotator, full name, damage,
                            voids, enclaves, notes
    metadata.yml            sample-level facts; the only thing we touch is the
                            `annotators` roster

Both are hand-maintained by several people and live in the DATASET folder, which
the rest of this app treats as read-only. Everything here follows from that:

* **ruamel round-trip, not PyYAML.** Comments, key order, quoting style and the
  `---` / `...` markers all have to survive. `render()` reproduces an untouched
  file byte for byte -- `test_notes.py` asserts exactly that on the real files --
  so any line in the diff is a line we meant to change. That is what makes the
  "refuse the save if a line nobody touched moved" rule usable rather than noise.
* **Never create one silently.** `find()` reports what is missing; creating a
  file is a separate, explicit call. A second competing copy of a shared record
  is worse than no copy.
* **Never write a copy held since load.** `save()` takes the fingerprint taken at
  load and refuses if the file has changed underneath -- that is how you silently
  revert a colleague.
"""
from __future__ import annotations

import difflib
import hashlib
import io
import re
from pathlib import Path

from ruamel.yaml import YAML

NOTES_NAME = "annotation.notes.yaml"
METADATA_NAME = "metadata.yml"

# Other spellings seen in the wild. The canonical name is written; these are only
# recognised, so an existing file is found rather than a second one created.
NOTES_ALIASES = ("annotation.notes.yaml", "annotation.notes.yml", "annotations.notes.yaml")
METADATA_ALIASES = ("metadata.yml", "metadata.yaml")

# The per-region keys, in the order the sample file uses them.
REGION_KEYS = ("annotator", "region", "damage", "voids", "enclaves", "notes")

# A real value in these files meaning "nobody has filled this in". It must read
# as unset, never as data -- writing "placeholder" into a damage list, or
# treating it as an annotator's name, are both wrong.
PLACEHOLDER = "placeholder"


def is_unset(v) -> bool:
    s = str(v if v is not None else "").strip()
    return s == "" or s.lower() == PLACEHOLDER


# --- round-tripping -------------------------------------------------------------

def _preamble(text: str):
    """Split off comment/blank lines that sit BEFORE the document start.

    ruamel drops them on dump (they belong to no node), and `metadata.yml` opens
    with `# Sample Metadata`. Carrying the text across verbatim is the only way
    that header survives a write.
    """
    lines = text.splitlines(keepends=True)
    out = []
    for ln in lines:
        s = ln.strip()
        if s.startswith("---"):
            return "".join(out), "".join(lines[len(out):])
        if s and not s.startswith("#"):
            break
        out.append(ln)
    return "", text


def _yaml_for(text: str) -> YAML:
    """A round-tripper configured from the file it is about to read back.

    The two files differ: `metadata.yml` has `---`/`...`, the notes file has
    neither, and adding or removing them would be an edit nobody asked for.
    """
    body = _preamble(text)[1]
    y = YAML()                       # round-trip by default
    y.preserve_quotes = True
    y.width = 4096                   # never re-wrap a long note into a new line
    y.explicit_start = body.lstrip().startswith("---")
    y.explicit_end = body.rstrip().endswith("...")
    return y


def parse(text: str):
    """-> (data, renderer). `renderer(data)` gives the text back."""
    pre, body = _preamble(text)
    y = _yaml_for(text)
    data = y.load(body)

    def render(d) -> str:
        buf = io.StringIO()
        y.dump(d, buf)
        return pre + buf.getvalue()

    return data, render


def fingerprint(path: Path) -> dict:
    """Size + mtime + hash of what is on disk right now. Compared at save so a
    colleague's edit between load and save is caught rather than overwritten."""
    p = Path(path)
    if not p.exists():
        return {"exists": False}
    raw = p.read_bytes()
    st = p.stat()
    return {"exists": True, "size": st.st_size, "mtime": int(st.st_mtime),
            "sha1": hashlib.sha1(raw).hexdigest()}


def read(path):
    """-> {path, text, data, render, fingerprint}. Missing file -> None."""
    p = Path(path)
    if not p.exists():
        return None
    text = p.read_text(encoding="utf-8")
    data, render = parse(text)
    return {"path": str(p), "text": text, "data": data, "render": render,
            "fingerprint": fingerprint(p)}


def find(folder):
    """What the dataset folder actually has. Reports absence; creates nothing."""
    d = Path(folder)
    out = {}
    for key, names in (("notes", NOTES_ALIASES), ("metadata", METADATA_ALIASES)):
        hit = next((d / n for n in names if (d / n).exists()), None)
        out[key] = {
            "found": hit is not None,
            # Where it would be created, so the UI can offer the exact path
            # rather than inventing one at write time.
            "path": str(hit) if hit else str(d / (NOTES_NAME if key == "notes"
                                                  else METADATA_NAME)),
        }
    return out


# --- editing --------------------------------------------------------------------

def _as_written(existing, values):
    """Render a list of names the way THIS file already writes that key.

    The sample writes `damage: ""`, i.e. a comma-joined string. A file that uses
    a YAML list keeps its list. Changing a key's shape under someone is exactly
    the kind of silent reformatting this module exists to avoid.
    """
    if isinstance(existing, list):
        return list(values)
    return ", ".join(values)


def _read_list(v):
    """The inverse: a stored value back to a list of names."""
    if isinstance(v, list):
        return [str(x).strip() for x in v if str(x).strip()]
    if is_unset(v):
        return []
    return [s.strip() for s in str(v).split(",") if s.strip()]


def apply_regions(data, updates, annotator=None, only=None):
    """Write per-region damage/voids into a loaded notes document, in place.

    `updates` is {region: {"damage": [...], "voids": [...]}} -- what assign()
    produces. `only` limits the write to the regions this annotator worked on;
    everyone else's entries are not touched at all, byte for byte.

    Returns a list of {region, key, before, after} for the preview.
    """
    changes = []
    for region in list(updates):
        if only is not None and region not in only:
            continue
        entry = data.get(region)
        if not hasattr(entry, "get"):
            # A region in the geometry that this YAML has never heard of. Adding
            # it silently would grow a shared file behind everyone's back; it is
            # reported instead (see missing_regions).
            continue
        want = updates[region] or {}
        for key in ("damage", "voids"):
            if key not in want:
                continue
            before = entry.get(key)
            after = _as_written(before, want[key])
            if before == after:
                continue
            entry[key] = after
            changes.append({"region": region, "key": key,
                            "before": "" if before is None else before,
                            "after": after})
        if annotator and str(annotator).strip():
            before = entry.get("annotator")
            if is_unset(before) and before != annotator:
                entry["annotator"] = _as_written(before, [annotator]) \
                    if isinstance(before, list) else str(annotator)
                changes.append({"region": region, "key": "annotator",
                                "before": "" if before is None else before,
                                "after": entry["annotator"]})
    return changes


def missing_regions(data, region_names):
    """Regions in the geometry that the notes file has no entry for.

    Reported, never auto-added: this file is shared, and a region name that
    disagrees with the YAML usually means a rename, not a new region.
    """
    known = set(data.keys()) if hasattr(data, "keys") else set()
    return sorted(n for n in region_names if n not in known)


def add_annotator(data, name):
    """Append a name to metadata.yml's roster if it is not already there.

    `placeholder` is the seeded value and means "nobody yet", so it is replaced
    rather than accumulated alongside.
    """
    name = str(name or "").strip()
    if not name or not hasattr(data, "get"):
        return None
    roster = data.get("annotators")
    if not isinstance(roster, list):
        return None
    if any(str(x).strip() == name for x in roster):
        return None
    placeholders = [i for i, x in enumerate(roster) if is_unset(x)]
    if placeholders:
        before = list(roster)
        roster[placeholders[0]] = name
        return {"key": "annotators", "before": before, "after": list(roster)}
    before = list(roster)
    roster.append(name)
    return {"key": "annotators", "before": before, "after": list(roster)}


def new_notes_text(region_names, experiment_id=None, by=None) -> str:
    """A fresh notes file, in the shape the lab's own files use.

    Written as text rather than dumped from a structure so the result is exactly
    what it looks like here. The header says where it came from: the next person
    to open it should not have to guess whether a tool wrote it.
    """
    head = ["# annotation.notes.yaml — per-region annotation record.",
            f"# Created by FiveAtlas{f' ({by})' if by else ''}. Edit this file; do not",
            "# make a second copy — it is shared between annotators for this sample.",
            f"experimentID: {experiment_id or PLACEHOLDER}",
            f"notes: {PLACEHOLDER}"]
    body = []
    for nm in region_names:
        body.append(f"{nm}:")
        for k in REGION_KEYS:
            body.append(f'  {k}: ""')
    return "\n".join(head + body) + "\n"


# --- previewing and writing -----------------------------------------------------

_TOP_KEY = re.compile(r"^([^\s#][^:]*):")


def diff(before_text: str, after_text: str, path="") -> list:
    """Unified diff of the rendered output against what is on disk."""
    name = Path(path).name or "notes"
    return list(difflib.unified_diff(
        before_text.splitlines(), after_text.splitlines(),
        f"{name} (on disk)", f"{name} (to write)", lineterm="", n=2))


def _blocks(text: str) -> list:
    """The top-level key each line belongs to.

    A region's lines are indented under `ISO:`, and a roster entry is a bare
    `- name` under `annotators:` — neither carries the name it belongs to, so
    matching a changed line by its own text would misjudge both.
    """
    out, cur = [], None
    for ln in text.splitlines():
        m = _TOP_KEY.match(ln)
        if m:
            cur = m.group(1).strip().strip('"\'')
        out.append(cur)
    return out


def unexpected_lines(before_text: str, after_text: str, changes) -> list:
    """Changed lines that no edit accounts for.

    This is why the preview diffs the RENDERED output instead of dumping it: if
    the round-trip reformats a line nobody touched, it shows up here and the save
    is refused rather than trusted. A change is accounted for only if it falls
    inside a block we set out to edit.
    """
    wanted = {str(c["region"]) for c in (changes or []) if c.get("region")}
    wanted |= {str(c["key"]) for c in (changes or [])
               if c.get("key") and not c.get("region")}

    a, b = before_text.splitlines(), after_text.splitlines()
    ablk, bblk = _blocks(before_text), _blocks(after_text)
    out = []
    sm = difflib.SequenceMatcher(None, a, b, autojunk=False)
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag == "equal":
            continue
        for i in range(i1, i2):
            if ablk[i] not in wanted:
                out.append(f"-{a[i]}")
        for j in range(j1, j2):
            if bblk[j] not in wanted:
                out.append(f"+{b[j]}")
    return out


def save(path, text: str, expect=None):
    """Write, but only if the file is still what it was when we loaded it.

    Re-reads immediately before writing rather than trusting the fingerprint
    handed in -- between the check and the write is exactly where a colleague's
    save lands.
    """
    p = Path(path)
    now = fingerprint(p)
    if expect is not None:
        was, is_now = dict(expect or {}), now
        if bool(was.get("exists")) != bool(is_now.get("exists")) \
                or (was.get("exists") and was.get("sha1") != is_now.get("sha1")):
            return {"ok": False, "reason": "changed-on-disk",
                    "expected": was, "actual": is_now}
    p.parent.mkdir(parents=True, exist_ok=True)
    # Write through a sibling temp file so an interrupted write cannot leave a
    # half-file where a shared record used to be. newline="" keeps the \n line
    # endings ruamel produced -- on Windows the default would turn every line in
    # the file into \r\n, i.e. a whole-file diff for a two-line edit.
    # (Path.write_text has no newline= before Python 3.10; the venv is 3.9.)
    tmp = p.with_suffix(p.suffix + ".tmp")
    with open(tmp, "w", encoding="utf-8", newline="") as fh:
        fh.write(text)
    tmp.replace(p)
    return {"ok": True, "path": str(p), "fingerprint": fingerprint(p)}

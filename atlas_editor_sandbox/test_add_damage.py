"""Drawing damage: /api/damage/designations and the `damage` arm of /regions/add.

RUN THIS AGAINST AN ISOLATED BACKEND, NEVER THE ONE SERVING THE USER'S WORKDIR
(whatever port that is on). This project's real working copy has been shredded
twice by a test pointed at the live server:

    ATLAS_WORKDIR=<scratch>/wd python -m uvicorn app:app --app-dir backend --port 8060

Copy the real workdir/opened_datasets.json into <scratch>/wd so the dataset is
registered; the dataset folder itself is only ever read.

Nothing here saves, but the isolation is the point: the rule is about where the
backend can write, not about what this file believes it does.
"""
import json
import os
import sys
import urllib.error
import urllib.request

sys.path.insert(0, r"C:\Users\FIVE\source\repos\Jess\atlas_editor\backend")

from shapely.geometry import box, shape     # noqa: E402
import damage as D                          # noqa: E402

BASE = os.environ.get("ATLAS_TEST_BASE", "http://127.0.0.1:8060")
if ":8050" in BASE or ":8000" in BASE:
    raise SystemExit("refusing to run against the live backend — see the docstring")

fails = []


def check(label, cond, detail=""):
    print(f"  {'ok  ' if cond else 'FAIL'}  {label}{('  ' + detail) if detail else ''}")
    if not cond:
        fails.append(label)


def get(path):
    with urllib.request.urlopen(BASE + path, timeout=120) as r:
        return json.load(r)


def post(path, body):
    req = urllib.request.Request(
        BASE + path, data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=300) as r:
            return r.status, json.load(r)
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode()[:300]


def area_of(fc, name, id_prop="name"):
    """Total area of every part carrying `name` — a region can be multi-part."""
    tot = 0.0
    for f in fc["features"]:
        if (f.get("properties") or {}).get(id_prop) == name:
            g = shape(f["geometry"])
            tot += (g if g.is_valid else g.buffer(0)).area
    return tot


def names_of(fc, id_prop="name"):
    return [(f.get("properties") or {}).get(id_prop) for f in fc["features"]]


# --- the vocabulary route -------------------------------------------------------
print("\ndesignations")
desig = get("/api/damage/designations")
by_tag = {d["tag"]: d for d in desig["designations"]}
check("all 13 designations served", len(desig["designations"]) == len(D.DESIGNATIONS),
      f"{len(desig['designations'])}")
check("labels match damage.py",
      all(by_tag[t]["label"] == lbl for t, (lbl, _) in D.DESIGNATIONS.items()))
check("drawn flag carries the SOP's three states",
      by_tag["separation"]["drawn"] is True and by_tag["missing"]["drawn"] is False
      and by_tag["voidsmall"]["drawn"] is None)
check("aliases resolve the display spellings the client will show",
      desig["aliases"]["smallvoid"] == "voidsmall"
      and desig["aliases"]["internalfold"] == "foldinternal")
check("enclave is not offered as a designation",
      desig["enclave"] == "enclave" and "enclave" not in by_tag)

# --- the dataset ----------------------------------------------------------------
# Whichever registered dataset is a real atlas: an isolated backend accumulates
# whatever other tests opened on it, and datasets[0] is not reliably this one.
# ATLAS_TEST_DS names it outright when that matters.
dsets = get("/api/datasets")
wanted = os.environ.get("ATLAS_TEST_DS")
ds_id = fc0 = info = None
for cand in ([d for d in dsets if d["id"] == wanted] if wanted else dsets):
    try:
        got = get(f"/api/datasets/{cand['id']}/regions")
    except Exception:
        continue
    if len(got.get("features") or []) >= 10:
        ds_id, fc0 = cand["id"], got
        break
if ds_id is None:
    raise SystemExit(f"no atlas-sized dataset registered on {BASE} "
                     f"(saw {[d['id'] for d in dsets]})")
info = get(f"/api/datasets/{ds_id}/info")
id_prop = info.get("idProp", "name")
base_names = names_of(fc0, id_prop)
print(f"\ndataset {ds_id} — {len(fc0['features'])} features, id_prop={id_prop}")

# A host region and a small square wholly inside it.
#
# NOT the largest region: that is `hemi`, the outline of the whole hemisphere,
# which contains all 22 others. Any shape drawn anywhere is inside it, so a test
# that used it as "the host" would prove nothing about landing in the right
# region. Middling regions are the ones a person actually annotates.
anat, _ = D.split_features(fc0["features"], id_prop)


def geom_of(f):
    g = shape(f["geometry"])
    return g if g.is_valid else g.buffer(0)


R = 60.0
host = square = None
for cand in sorted(anat, key=lambda f: geom_of(f).area):
    g = geom_of(cand)
    p = g.representative_point()
    sq = box(p.x - R, p.y - R, p.x + R, p.y + R)
    if g.covers(sq):
        host, square = cand, [[p.x - R, p.y - R], [p.x + R, p.y - R],
                              [p.x + R, p.y + R], [p.x - R, p.y + R]]
        host_sq = sq
        break
if host is None:
    raise SystemExit("no region big enough to hold a 120 px square — check the dataset")
host_name = (host["properties"] or {})[id_prop]
host_area0 = area_of(fc0, host_name, id_prop)

# On the real file this is {'hemi': [everything], 'ISO': ['SSp', 'RSP'],
# 'dft': ['VL.2']} — so containment cannot be a label on a region: ISO and dft are
# ordinary regions people draw in.
encloses = D.containment(anat, id_prop)
held = sorted(encloses)


def covering_of(sq):
    """Regions the square really sits in, by the rule assign() uses: ≥1% of the
    SHAPE inside the region, minus any region that contains another one it also
    sits in."""
    reached = {(f["properties"] or {})[id_prop] for f in anat
               if geom_of(f).intersection(sq).area / sq.area >= 0.01}
    return sorted(n for n in reached
                  if not any(i in reached and i != n for i in encloses.get(n, [])))


covering = covering_of(host_sq)
print(f"host = {host_name}  ({host_area0:,.0f} px²) — square lies in {covering}; "
      f"containment = { {k: len(v) for k, v in encloses.items()} }")

# --- drawing damage -------------------------------------------------------------
print("\ndamage shapes")
code, r1 = post(f"/api/datasets/{ds_id}/regions/add",
                {"fc": fc0, "points": square, "damage": "separation"})
check("a damage shape is accepted", code == 200, str(r1)[:120] if code != 200 else "")
expect1 = D.format_name("separation", D.next_number(base_names, "separation"))
check("named <designation>.<n>, numbered from the whole file",
      r1["name"] == expect1, f"{r1['name']}")
check("the tag comes back", r1.get("damage") == "separation")
check("it reports the region it landed in", r1.get("inside") == [host_name],
      str(r1.get("inside")))
check("the candidates are exactly the regions it really sits in",
      sorted(r1.get("candidates") or []) == covering, str(r1.get("candidates")))
check("a shape inside one region is not a question",
      r1.get("needsChoice") is False and r1.get("dominant") == host_name)
# On the real file: `hemi` wraps every region, so it holds 100% of this shape and
# would otherwise be offered against the region actually drawn in — turning every
# single shape into a question.
check("this dataset has regions that contain other regions", "hemi" in held, str(held))
check("the enclosing region is neither recorded nor offered",
      "hemi" not in (r1.get("inside") or []) and "hemi" not in (r1.get("candidates") or []))
check("the host is NOT carved — damage sits inside it, whole",
      abs(area_of(r1, host_name, id_prop) - host_area0) < 1e-6,
      f"{area_of(r1, host_name, id_prop) - host_area0:+.6f} px²")
check("nothing ceded", r1.get("ceded") == [])
check("exactly one feature added", len(r1["features"]) == len(fc0["features"]) + 1)
check("the file-level members survive (provenance)", "_provenance" in r1)
check("the shape is damage to the code that reads it back",
      D.is_damage(r1["name"]) and len(D.split_features(r1["features"], id_prop)[1])
      == len(D.split_features(fc0["features"], id_prop)[1]) + 1)

# a second one, on the file that now contains the first
code, r2 = post(f"/api/datasets/{ds_id}/regions/add",
                {"fc": r1, "points": [[x + 200, y] for x, y in square], "damage": "separation"})
n1 = D.parse_name(r1["name"])[1]
check("the next one takes the next number", code == 200 and r2["name"] == f"separation.{n1 + 1}",
      str(r2.get("name")))
check("both shapes are in the file",
      sorted(n for n in names_of(r2, id_prop) if str(n).startswith("separation."))
      == sorted([r1["name"], r2["name"]]))

# the display spelling the dropdown sends back
code, r3 = post(f"/api/datasets/{ds_id}/regions/add",
                {"fc": r2, "points": [[x, y + 200] for x, y in square], "damage": "Small void"})
check("a display name resolves to its tag", code == 200 and r3["name"].startswith("voidsmall."),
      str(r3.get("name")))

# --- the .N trap ----------------------------------------------------------------
print("\nordinary anatomy that ends in a number")
numbered = [n for n in base_names if D.parse_name(n) == (None, None)
            and any(ch.isdigit() for ch in str(n))]
check("this dataset really does have anatomy ending in a number",
      len(numbered) > 0, ", ".join(map(str, numbered[:6])))
check("none of it is treated as damage",
      not any(D.is_damage(n) for n in numbered))
check("adding damage leaves those regions untouched",
      all(abs(area_of(r3, n, id_prop) - area_of(fc0, n, id_prop)) < 1e-6 for n in numbered))
check("and they don't steal a damage number",
      D.next_number(base_names + ["PAL.9", "VL.7"], "separation")
      == D.next_number(base_names, "separation"))

# --- refusals and regressions ---------------------------------------------------
print("\nrefusals and the ordinary path")
code, body = post(f"/api/datasets/{ds_id}/regions/add",
                  {"fc": fc0, "points": square, "damage": "Isocortex"})
check("an unknown designation is refused, not guessed", code == 422, str(code))
check("the refusal says what it did not recognise", "Isocortex" in str(body))

code, plain = post(f"/api/datasets/{ds_id}/regions/add", {"fc": fc0, "points": square})
# Carving is a geometry operation, so it takes ground from a container too — that
# is right, and it is why the container guard belongs in assignment, not here.
touching = sorted({(f["properties"] or {})[id_prop] for f in anat
                   if geom_of(f).intersection(host_sq).area > 0})
check("an ordinary region still carves everything it covers",
      code == 200 and sorted(plain["ceded"]) == touching
      and area_of(plain, host_name, id_prop) < host_area0 - 1000,
      f"ceded={plain.get('ceded') if code == 200 else code}")
check("an ordinary region reports no designation",
      plain.get("damage") is None and plain.get("inside") == [])

# --- a region that contains others is still a region ----------------------------
# The failure a blanket "skip containers" rule would have caused on this exact
# file: ISO contains SSp (97%) and RSP (94%), so striking ISO out would strand
# every shape drawn in ISO's own ground — most of the isocortex.
print("\ndrawing inside a region that contains others")
nesting = [n for n in held if encloses.get(n) and n != "hemi"]
check("this dataset has an ordinary region that contains another",
      len(nesting) > 0, ", ".join(f"{n}⊃{'+'.join(encloses[n])}" for n in nesting))
if nesting:
    outer = nesting[0]
    og = geom_of(next(f for f in anat if (f["properties"] or {})[id_prop] == outer))
    own = og
    for kid in encloses[outer]:
        kg = geom_of(next(f for f in anat if (f["properties"] or {})[id_prop] == kid))
        own = own.difference(kg)
    p = own.representative_point()
    osq = box(p.x - R, p.y - R, p.x + R, p.y + R)
    opts = [[p.x - R, p.y - R], [p.x + R, p.y - R], [p.x + R, p.y + R], [p.x - R, p.y + R]]
    code, ro = post(f"/api/datasets/{ds_id}/regions/add",
                    {"fc": fc0, "points": opts, "damage": "bubble"})
    check(f"a shape in {outer}'s own ground is recorded against {outer}",
          code == 200 and ro.get("inside") == [outer], str(ro.get("inside"))[:80])
    check("...and is not left with nowhere to go",
          (ro.get("candidates") or []) != [] and covering_of(osq) == [outer],
          str(covering_of(osq)))

# --- a shape that straddles two regions -----------------------------------------
# The case the whole prompt exists for. Find two real neighbours and draw across
# their border, rather than asserting anything about a synthetic shape.
print("\nstraddling two regions")
from shapely.ops import nearest_points                                  # noqa: E402

peers = [f for f in anat if (f["properties"] or {})[id_prop] not in held]
straddle = None
for i, fa in enumerate(peers):
    ga = geom_of(fa)
    if ga.area < 5e5:
        continue
    for fb in peers[i + 1:]:
        gb = geom_of(fb)
        if gb.area < 5e5 or ga.distance(gb) > 1.0:
            continue
        p, q = nearest_points(ga, gb)
        cx, cy = (p.x + q.x) / 2, (p.y + q.y) / 2
        sq = box(cx - R, cy - R, cx + R, cy + R)
        if (ga.intersection(sq).area / sq.area >= 0.05
                and gb.intersection(sq).area / sq.area >= 0.05):
            straddle = (sq, [[cx - R, cy - R], [cx + R, cy - R],
                             [cx + R, cy + R], [cx - R, cy + R]])
            break
    if straddle:
        break

if straddle is None:
    check("found a pair of neighbours to straddle", False, "none within 1 px")
else:
    sq, pts = straddle
    want = covering_of(sq)
    code, r4 = post(f"/api/datasets/{ds_id}/regions/add",
                    {"fc": fc0, "points": pts, "damage": "bubble"})
    check("a straddling shape is accepted", code == 200, str(r4)[:120] if code != 200 else "")
    check("it really does straddle two regions", len(want) >= 2, "+".join(want))
    check("both regions are offered", sorted(r4.get("candidates") or []) == want,
          str(r4.get("candidates")))
    check("the annotator is asked rather than the geometry deciding",
          r4.get("needsChoice") is True)
    check("meanwhile it goes to the region holding most of it",
          r4.get("inside") == [r4.get("dominant")] and r4["dominant"] in want,
          f"dominant={r4.get('dominant')}")
    check("the dominant region really is the one holding most of it",
          r4["dominant"] == max(want, key=lambda n: sum(
              geom_of(f).intersection(sq).area for f in anat
              if (f["properties"] or {})[id_prop] == n)))

    # Answering it: the client writes the choice onto the shape itself.
    other = [n for n in want if n != r4["dominant"]][0]
    answered = json.loads(json.dumps(r4))
    for f in answered["features"]:
        if (f.get("properties") or {}).get(id_prop) == r4["name"]:
            f["properties"][D.CHOICE_PROP] = [other]
    code, tsv_ans = post(f"/api/datasets/{ds_id}/regions/smartsheet.tsv", {"fc": answered})
    got = {s["name"]: s for s in tsv_ans["shapes"]}[r4["name"]]
    check("the answer overrides the dominant region", got["regions"] == [other],
          str(got["regions"]))
    check("...and it stops being a question",
          not got["needsChoice"] and got["chosen"] == [other])
    check("the region the annotator chose gets the void",
          r4["name"] in tsv_ans["regions"][other]["voids"]
          and r4["name"] not in tsv_ans["regions"][r4["dominant"]]["voids"])

    # ...and "both", which is what the SOP asks for when two annotators each own
    # one side of the border.
    for f in answered["features"]:
        if (f.get("properties") or {}).get(id_prop) == r4["name"]:
            f["properties"][D.CHOICE_PROP] = list(want)
    code, tsv_both = post(f"/api/datasets/{ds_id}/regions/smartsheet.tsv", {"fc": answered})
    check("answering 'both' records it in both",
          all(r4["name"] in tsv_both["regions"][n]["voids"] for n in want))

# --- what the export will make of it --------------------------------------------
print("\nthe shapes reach the SmartSheet export")
code, tsvres = post(f"/api/datasets/{ds_id}/regions/smartsheet.tsv", {"fc": r3})
rows = {ln.split("\t")[0]: ln.split("\t") for ln in tsvres["tsv"].splitlines()[1:]}
check("the TSV finds the drawn shapes", code == 200 and len(tsvres["shapes"]) == 3,
      str(len(tsvres.get("shapes", []))))
check("the host row carries the designation drawn in it",
      "separation" in rows[host_name][3].split(","), rows[host_name][3])
check("the host row lists the shape by name",
      r1["name"] in rows[host_name][4].split(","), rows[host_name][4])
check("every shape reaches at least one region row",
      all(any(nm in r[4].split(",") for r in rows.values())
          for nm in (r1["name"], r2["name"], r3["name"])))
check("no shape is left over no region", tsvres["unassigned"] == [])
check("the export says which mode wrote it", tsvres["mode"] == "dominant")
check("the containment relation is named in the report, not hidden",
      sorted(tsvres["containment"]) == held, str(tsvres.get("containers")))
check("the outline collects none of the shapes drawn inside real regions",
      rows["hemi"][4] == "", f"voids={rows['hemi'][4]!r}")

# --- the per-region SmartSheet cells ---------------------------------------------
# What actually gets pasted: one multi-select cell per region, holding `Done` and
# the display names. The hand-ticked half of the vocabulary only exists here.
print("\nthe SmartSheet damage cells")
code, cl = post(f"/api/datasets/{ds_id}/damage/cells", {"fc": r3})
boxes = {c["region"]: c for c in cl["cells"]}
check("a box per region with damage", code == 200 and host_name in boxes,
      str(sorted(boxes))[:80])
check("the chips are the dropdown's own display names",
      "Separation" in boxes[host_name]["labels"], str(boxes[host_name]["labels"]))
check("regions with no damage are left out",
      all(b["tags"] or b["done"] for b in cl["cells"]))
check("...unless asked for", len(post(f"/api/datasets/{ds_id}/damage/cells",
                                      {"fc": r3, "includeEmpty": True})[1]["cells"])
      > len(cl["cells"]))
check("the default text is one quoted cell",
      boxes[host_name]["text"].startswith('"'), boxes[host_name]["text"][:40])
check("all three formats are offered",
      sorted(cl["separators"]) == ["cell", "comma", "lines"], str(cl["separators"]))

# tick a never-drawn designation and the Done box on the host region
ticked = json.loads(json.dumps(r3))
for f in ticked["features"]:
    if (f.get("properties") or {}).get(id_prop) == host_name:
        f["properties"]["_damage_extra"] = ["cutoff"]
        f["properties"]["_damage_done"] = True
code, cl2 = post(f"/api/datasets/{ds_id}/damage/cells", {"fc": ticked})
b2 = {c["region"]: c for c in cl2["cells"]}[host_name]
check("a designation that can never be drawn reaches the cell",
      "Cutoff" in b2["labels"] and b2["typed"] == ["cutoff"], str(b2["labels"]))
check("Done leads the cell", b2["values"][0] == cl2["doneLabel"], str(b2["values"]))
check("the box says which chips came from a shape",
      set(b2["drawn"]) == {"separation", "voidsmall"}, str(b2["drawn"]))
check("the pasted text is exactly the chips, newline separated in one quoted cell",
      b2["text"] == '"' + "\n".join(b2["values"]) + '"')

# the row export and the YAML must say the same thing as the box
code, tsv2 = post(f"/api/datasets/{ds_id}/regions/smartsheet.tsv", {"fc": ticked})
row = {ln.split("\t")[0]: ln.split("\t") for ln in tsv2["tsv"].splitlines()[1:]}[host_name]
check("the hand-ticked type reaches the row export too",
      "cutoff" in row[3].split(","), row[3])
# (the YAML side of the same cross-check lives in test_notes_routes.py, which has
#  its own dataset folder — nothing here goes near the real one's YAMLs)

print("\n" + (f"{len(fails)} FAILED: " + "; ".join(fails) if fails else "all checks passed"))
sys.exit(1 if fails else 0)

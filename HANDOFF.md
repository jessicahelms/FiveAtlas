# FiveAtlas — session handoff (2026-08-07)

> **START HERE.** Four features were asked for in one go. One is done, one is
> half-built, two are untouched. Details in "The four features" below; everything
> after that section is older context that is still accurate.

## Damage annotation + YAML metadata — the dropdown is in; the YAMLs are next

**Read `PLAN-metadata.md` and `WIRING-damage-tsv.md` before writing code.**

An earlier session built the LAST stage of this pipeline (the TSV export) before the
stages that feed it, and handed the user a button with nothing to detect. Do not
repeat that. The order is:

1. **The damage dropdown — DONE (2026-08-07).** "Add a new region or damage" now
   asks what you are drawing, and damage is named `<tag>.<n>` by the server.
2. **Which region a shape belongs to — DECIDED and built (2026-08-07).**
   Dominant + a prompt; see below.
3. **The two YAMLs — DONE (2026-08-07).** `backend/notes.py` + four routes + the
   "Annotation notes" panel. See below.
4. TSV — built and wired, and now meaningful: it reads the same assignment.

### 1. The dropdown — what was built

- **`GET /api/damage/designations`** — the 13 designations (`tag`, `label`,
  `drawn`) plus `aliases`, the fold→tag map behind `parse_name`. Served rather than
  hard-coded in the client so the vocabulary has one home; `frontend/src/damage.js`
  mirrors only the *stem + number* split, never the list.
- **`POST .../regions/add` takes `damage: <tag>`.** Two differences from an ordinary
  region and no others: the server names it `<tag>.<n>` using `next_number()` over
  **every name in the file** (numbering is global per designation), and it forces
  **`carve=False`** — damage lies inside its host and the host stays whole. It
  returns `{damage, inside}`, `inside` being the regions it landed in by the same
  rule the TSV uses. An unknown designation is a 422, never a guess.
- **Sidebar**: a "What are you drawing?" select — *Anatomical region* (default),
  then the 5 drawable designations, then the 8 tag-only ones **shown but disabled**,
  because hiding them makes half the vocabulary look non-existent. It previews the
  name it will take (`separation.4`), and the region list labels damage rows with
  their designation.
- The choice **persists between shapes** — an annotator draws several separations in
  a row; resetting to "region" after each one would be a trap.
- **`atlas_editor_sandbox/test_add_damage.py`, 44 checks, all passing** against an
  isolated backend on :8060 (it refuses to run against :8050/:8000 outright). It
  finds its own straddling pair and its own nested region on the real file rather
  than hard-coding names. `test_damage.py` is now 62 offline checks.

### 2. Which region a shape is recorded against — DECIDED 2026-08-07

**Dominant, with a prompt.** A damage shape goes to the ONE region holding most of
it. A shape that reaches into a second region is not settled by geometry: the
annotator is asked, and gets three answers — this region, the other one, or
**both**. `mode="all"` (the literal SOP reading) is still there and still tested,
one argument at the caller, because the written procedure says otherwise.

The answer is written **onto the shape** (`_damage_regions`, `damage.CHOICE_PROP`),
not held in the session — it is a judgement, and it has to survive a save, an email
and the next annotator opening the file. `assign()` honours a stored answer
verbatim and stops asking. An answer naming a region the shape no longer reaches
(dragged since) is **dropped and reported as `staleChoice`**, never written on.
Unanswered is not a dead end: the shape falls to the dominant region and is listed
again in the SmartSheet review, where the same three buttons appear.

### The export is a CELL, not a row — and the row export is gone from the UI

The tracking sheet's Damage column is a **multi-select dropdown**: one cell holds
several chips (`Done ×  Bubble ×  Cutoff ×`). So the panel is "SmartSheet damage",
one box per region, each holding `Done` plus the damage by its **display name** —
the dropdown's own options. `POST .../damage/cells`.

- **`Done` is not a designation.** It is the annotator's "I have been through this
  region" tick, so it is kept out of `DESIGNATIONS`, out of the YAML, and only
  ever added to the cell. A region with no damage still gets a box once ticked.
- **This is where the never-drawn half of the vocabulary finally lives.** `Cutoff`,
  `Missing`, `Low/no transcripts` and the rest are ticked per region from a
  dropdown in the box. They store on the REGION feature (`_damage_extra`,
  `_damage_done`, `damage.EXTRA_PROP`/`DONE_PROP`) and flow into the YAML's
  `damage:` field and the row export alike.
- **Three clipboard formats**, because pasting multi-line text into a grid usually
  splits it across ROWS. The default quotes the cell (`"Done\nBubble"`), which is
  the convention that keeps it in one; Lines and Commas are one click away. Which
  one SmartSheet actually wants is quicker to try than to look up — if a paste
  lands as rows, switch.
- **`⎘ Copy for SmartSheet` (the whole-row TSV) was removed from the sidebar** on
  the user's call, since the cells replace it. `POST .../regions/smartsheet.tsv`
  and `damage.tsv()` are still there and still tested, for the day the other
  columns (Full name, Annotator, Enclaves, Notes) are wanted.
- The straddling-shape question moved into this panel with it — that was the
  "you'll see it again in the review" promise made at draw time.

### Damage is decided by NAME, however the shape got there

Drawn with the damage tool, made from a gap, or **an existing region renamed to
`bubble.1`** — all the same thing. Two changes made that true end to end:

- **Client-side edits are stamped when they happen**, not at save. `stampFc()` in
  `App.jsx` appends a `_provenance` entry for a rename or a delete immediately,
  because the notes panel decides whose regions are whose from that trail, and a
  region renamed to a designation is damage from that moment. (The `{fc, actions}`
  contract on the save route still exists; nothing needs it now.)
- **Damage no one else's trail claims counts as yours.** Otherwise damage that
  arrived by any route without an entry — a rename before this existed, a
  colleague's file with no trail — would reach nobody's notes. Damage another
  annotator's trail DOES name stays theirs; `test_notes_routes.py` covers both.

### `hemi` covers everything, so nothing is ever a gap

Two features, one cause. Both tested by `test_outline.py` (30 checks).

**Switch a region off** — the ◉/◍ toggle in the region list. It stays in the file
and in every export; it is only ignored by the operations that assume a clean
partition (`dissolve-gap`, `fill-gap`, `validate`, via `exclude`) and hidden on the
map. Held-out regions are put back at their original index, geometry untouched, so
draw order survives. On the real file this is the difference between **"that point
is inside a region"** and finding a genuine 305,615 px² void, and it takes Check
geometry from 31 overlap problems to 9.

**Build the outline instead of tracing it** — `POST .../regions/outline`,
`topology.section_outline()`. The user has seen it on the real file and it is
right: a morphological closing (grow by `radius`, union, shrink back) that bridges
the hairline gaps between neighbours and still follows the real coastline, landing
at **0.96× the hand-drawn hemi**.

**The UI offers no radius and no method, deliberately — don't re-add them.**
Measured on this file: 40 px → 2000 px moves the outline area by **0.8% in total**
(0.23% across the 80/200/500 buttons that were there), because the regions already
tile the section tightly and there is nothing left for a wider bridge to close. The
user's words were "the different pixel sizes don't seem to do anything" — they were
right, and the knob went. The convex hull went with it: `hull` claims **3.4% more
ground** by cutting across every notch, and the user's call was bubble. Both
parameters still exist on the route and in `test_outline.py`.

It **excludes its own output from its input**, so rebuilding twice gives the same
outline (0.0 px² drift) instead of creeping outwards run after run, and it excludes
damage shapes, which sit inside the tissue and could only pull it in. Only the
outer ring is kept — an outline with holes is not an outline. Applied, it replaces
the existing `hemi` and is inserted FIRST so it draws underneath what it wraps.

### 3. The YAMLs — `backend/notes.py`, and what it guarantees

`annotation.notes.yaml` (per-region: annotator, region, damage, voids, enclaves,
notes) and `metadata.yml` (sample facts; we touch only the `annotators` roster).
Routes: `GET .../notes` (what is there and its exact path), `POST .../notes/preview`
(a diff, read-only), `POST .../notes/save`, `POST .../notes/create`. The panel is
"Annotation notes" in the sidebar's Data section.

**The round trip is exact.** `ruamel.yaml==0.18.6` (now in `requirements.txt` and
in `FiveAtlas.spec` hiddenimports — it is a namespace package with a C backend, so
following imports does not find it). `test_notes.py` asserts that both real files
come back **byte for byte** when untouched. Everything else depends on that: it is
what makes "refuse the save if a line nobody edited moved" a usable rule instead
of noise. Two things needed handling for it:

- ruamel drops comments that sit before the document start, and `metadata.yml`
  opens with `# Sample Metadata`. `_preamble()` carries that text across verbatim.
- `---` / `...` are set per file from what that file already had, so the notes
  file never gains a `---` it did not have.

**A replaced value keeps the quoting its key already had** (`preserve_quotes`), so
`damage: ""` becomes `damage: "separation, voidlarge"` — the edit reads like the
rest of the file rather than announcing itself. Lists stay lists.

**The rules the user asked for, and where they live:**
- Auto-load from the dataset folder, exact path always on screen — `find()`, and
  the panel's first line is the full path.
- **Never create one silently.** Preview and save on a missing file do nothing;
  `notes/create` is a separate call, and a second create is a 409.
- Re-read immediately before writing; a `sha1` mismatch is `changed-on-disk` and
  the save is refused — `save(expect=...)`, verified by making a colleague's edit
  land between preview and save.
- **Only the regions this annotator worked on**, from `_provenance`. Damage shapes
  are recorded in the trail under their own name, so `_notes_worked_on()` maps a
  shape back to the regions it was assigned to; otherwise drawing damage would
  update nobody's notes.
- Preview as a **diff**, and `unexpected_lines()` refuses the save if a changed
  line falls outside a block we set out to edit. It works on blocks, not line
  text: a roster entry is a bare `- name` and carries no key of its own.

**Traps found building it:**
- `Path.write_text(newline=...)` does not exist before Python 3.10 and **the venv
  is 3.9**. Without `newline=""` on Windows every line becomes `\r\n`, i.e. a
  whole-file diff for a two-line edit. Use `open(..., newline="")`.
- An empty or truncated YAML must not read as "no changes" — that silently skips
  the save forever. It is reported as `unreadable` and refused.
- **The annotator is a name, not an account.** `display_name()` falls back to the
  Windows account (`FIVE`), which is fine for the edit trail and wrong for a
  roster other people read. `GET /api/identity` now returns `set`, and the panel
  starts empty: blank means the annotator fields and the roster are left alone.
- **`notes/save` writes into the DATASET folder** — an isolated `ATLAS_WORKDIR`
  does NOT protect against these routes. `test_notes_routes.py` builds its own
  dataset folder in scratch and opens that; it never names the real one.

Tests: **`test_notes.py` 40 checks offline** (copies the real YAMLs into scratch)
and **`test_notes_routes.py` 43 checks** against :8060 with its own dataset folder.

### Containment is a RELATION, not a label — the trap in this

`hemi` wraps all 22 regions, so every shape drawn anywhere is also "inside hemi",
and under dominant-mode it would be offered against the region actually drawn in —
**turning every single shape into a question**. So where a shape lands in both a
region and something that region contains, only the inner one records it.

The trap: on this file `containment()` returns **`{hemi: [all 22], ISO: [RSP,
SSp], dft: [VL.2]}`**. ISO and dft are ordinary regions people draw in. A blanket
"skip containers" rule — which is what I wrote first — strands **every shape drawn
in ISO's own ground**, i.e. most of the isocortex. It has to be applied per shape,
pairwise, never as a property of a region. `test_add_damage.py` draws in
`ISO.difference(RSP ∪ SSp)` on the real file specifically to catch this.

Two consequences worth knowing:
- The tie-break alone (equal fraction → smaller region) already stops `hemi`
  *winning*; the relation rule is what stops it *being asked about*.
- A shape in a gap between regions — inside the outline and nothing else — is
  recorded against `hemi`. Deliberate: it says something true, where "belongs
  nowhere" does not.

### The architecture decision (supersedes earlier drafts in PLAN-metadata.md)

The user's call, and it removes most of the complexity I had planned:

> **Damage is just another region.** Same file, same layer, same list, same editing.
> The ONLY differences are the dropdown that names it, and the YAML output.

So: no separate damage layer, no separate `damage.geojson` handling, no excluding it
from anything. Earlier sections of `PLAN-metadata.md` argue for a separate layer —
**that is superseded**; treat those parts as history.

Two consequences to expect rather than discover:
- A damage shape sits INSIDE its host, so **Check geometry reports an overlap**
  between them. That is a warning, not an error, so export is not blocked. Leave it.
- Picking a damage shape for Share borders will be refused by the containment guard.
  Correct behaviour, no change needed.

### What was already built and verified before that

- **`backend/damage.py`** — 13 designations, name parsing, global-per-designation
  numbering, overlap assignment, canonical renaming, TSV rendering.
  `atlas_editor_sandbox/test_damage.py`, **35 checks, all offline** (pure functions,
  no server).
- **`POST /api/datasets/{id}/regions/smartsheet.tsv`** and
  **`POST /api/datasets/{id}/damage/canonicalise`** — both live in `app.py`, both
  strictly read-only.
- **"⎘ Copy for SmartSheet"** in the sidebar Data section, with a review panel that
  warns about shapes overlapping no region and shapes spanning two.
- **The two YAMLs are now seeded** into
  `S:\...\Images from Box\DNMT3A_002_27_38_Het_F\` — derived from this sample's
  `experiment.xenium` and region names, NOT copied from the Aldrin template (whose
  IDs describe a different mouse). Unknowable fields say `placeholder` on purpose.

### Traps found the hard way

- **`.N` is NOT a damage marker on its own.** This dataset's anatomical regions
  include `PAL.1/.2/.3`, `sAMY.1`–`.4`, `VL.1/.2`. `damage.parse_name()` handles it
  (the stem must match a designation), but any new code that keys off "ends in a
  number" will misfire.
- **This dataset has no `damage.geojson`** and had no YAMLs until now, so "it didn't
  detect the damage" meant there was nothing to detect. The UI should say *why* it
  found nothing, not just show blank columns.
- **`parse_name` tolerates hand-typed names** — `Separation 2`, `voidsmall`,
  `Small Void_1` all resolve, because Xenium Explorer names are typed by hand.

### YAML rules the user was explicit about

- **The same file is edited every time; a new one must never appear.** Auto-load from
  the dataset folder, always show the exact path being written, and if none exists
  offer "Create it here: `<path>`" as an explicit action rather than generating one.
- **It is shared.** Re-read immediately before writing, detect outside changes
  (hash at load vs at save), and update only the regions this annotator worked on —
  `_provenance` already knows which those are.
- **Preview before writing, as a DIFF not a dump.** Diff the rendered output against
  what is on disk: it shows the intended edits and catches `ruamel` reformatting a
  hand-maintained file. A line you never touched appearing in the diff means the
  save should be refused.

### Decisions already made — don't re-litigate

- **No autosave.** A reminder toast instead, naming what is unsaved ("14 region
  edits, 3 annotation changes"); a bare "unsaved changes" gets dismissed reflexively.
- **Undo is undo.** Do not record undo as an event; the trail is snapshotted with the
  geometry. (An earlier draft argued for append-only — the user said no.)
- **Undrawn designations need a tag-only path.** `missing`, `cutoff`, `transcripts`,
  `overlap`, `removal`, `distortion`, `foldsmall`, `foldlarge` can never be drawn but
  must still reach the `damage:` field. Without a per-region control for these, half
  the vocabulary is unreachable. The TSV route already accepts `extraDamage`.

### ~~The one open question that changes output~~ — answered 2026-08-07

Was: dominant-by-default vs the SOP's all-overlapping. **Answered: dominant, with a
prompt offering the other region and both.** Built and tested — see "Which region a
shape is recorded against" at the top. `mode="all"` remains available and tested.

## Never point a test at the live backend

The user's working copy was damaged by an automated test in this project. It ran
Share-borders on `hemi` + `ISO` against the running server on :8000, and saved. The
test's own assertions passed — it checked *area*, and a sliver fan does not change
area, only shape. The user found it by looking at the screen.

**It happened again on 2026-08-06.** `test_edit_while_rotated.py` ran Share-borders
on `hemi` + `ISO` against the live backend on **:8050**, and the orientation endpoint
it also exercises calls `geo.save_edited`. Same mistake, different port. The rule is
not "avoid :8000" — it is **never point a test at a backend serving the user's
workdir**, whatever port it is on. `atlas_editor_wip/` is a working copy of the
backend for exactly this: run it on :8060 with `ATLAS_WORKDIR` set to its own folder.

**And since 2026-08-07 the workdir is not the whole story.** The `notes/*` routes
write into the DATASET folder, which no `ATLAS_WORKDIR` protects. A test for those
must build its own dataset folder and open that — `test_notes_routes.py` copies the
two YAMLs plus a small geojson into scratch and opens the copy.

Every test that touches an endpoint now runs against a second backend with its own
workdir, so there is nothing to damage:

```bash
ATLAS_WORKDIR=<scratch>/wd python -m uvicorn app:app --port 8060
# copy the real workdir/opened_datasets.json in so the dataset is registered;
# the dataset itself is read from its own folder, never written
```

And when asserting that geometry survived an operation, assert on **shape**, not
just area. `test_contained_pick.py` has a `slivers()` helper that counts vertices
where an outline doubles back on itself — that is what catches a fan.

## Share borders refuses a region inside another

`hemi` is not a peer region: it is the outline of the whole hemisphere, and all 22
other regions sit 100% inside it. Sharing a border assumes two regions that meet
along a line. Given a container and its contents there is no such line, so the
nearest-region partition had nothing to divide and shredded the overlap into ~15
slivers with arms up to 761 px — silently, with no error.

`topology.containment()` catches it. On real data the separation is total: genuine
side-by-side neighbours overlap **0%** of the smaller region (they only touch),
while a container swallows **94–100%**. The threshold sits at 90%, in the empty
middle of that gap. It also catches partial nesting — `SSp` is 97% inside `ISO`,
`RSP` 94%, `VL.2` 100% inside `dft`.

- Picking the pair → `shared-border` returns `{contained, message}` at 200, so the
  sidebar says why the moment you pick, before anything is touched.
- `partition` and `move-border` → **422, no geometry returned**. Nothing changes.

`test_contained_pick.py` covers it (19 checks). One known leftover, unrelated: the
Voronoi partition leaves ~2 wedges of about 150 px² on an ordinary pair like
`ISO` + `TH`. Five orders of magnitude smaller than the fan, and
`clean_features(spike=3.0)` removes them on the next load — but the resample step
in between does not, so they do reach disk if the user saves. Not fixed.

## The rotation ghost — fixed, confirmed by the user

A ghost copy of the section kept appearing at the old orientation. Four separate
causes, found and fixed in turn:

1. Tile URLs didn't change on rotation, so the browser (`max-age=3600`) and deck's
   own tile cache both served stale tiles. → orientation is now in the URL.
2. deck keeps its tile cache **per layer id**, so a same-id layer with a new extent
   went on drawing the tiles it had. → the orientation is in the layer id, and the
   `Viewer` remounts on an orientation change.
3. `/genes` and `/stains` were fetched once at dataset load, so the gene bitmap was
   placed on the un-rotated bounds. → `applyOrientation` re-fetches both.
4. The gene composite bitmap is rebuilt by a debounced effect keyed on `channels`.
   Rotating changes the *image* (the server returns it already turned) without
   changing `channels`, so the old bitmap was drawn on the new bounds. →
   `geneOrientKey` added to that effect's deps in `App.jsx`.

If a ghost ever comes back: switch the stain layer and the gene layer off one at a
time in the channel panel. Whichever one removes the ghost is the layer holding a
stale placement. Do NOT re-investigate the backend — it is verified correct (see
below); every cause so far has been client-side caching.

## The four features

| # | asked for | state |
|---|---|---|
| 1 | Flip/rotate the tif and geojson | **done** — including export in either frame |
| 2 | Metadata / chain-of-custody | **backend done, no UI** |
| 3 | Higher-resolution transcripts + stains | **not started** — but see the finding below |
| 4 | Delete slivers by circling them | **not started** |

### 1. Rotate / flip — done

The slide itself was imaged upside down; image and regions agree with each other,
so it is ONE transform applied to everything. `backend/orientation.py` is the whole
transform; it is applied to the DATA, not the deck view — a view transform would
leave the editing layer in the untransformed frame and every drag would land wrong.

Sidebar has an "Orientation" section (↺/↻ 90°, flip L/R, flip T/B, back to
as-imaged). `PUT /api/datasets/{id}/orientation` moves image and regions together;
regions are stored in the DISPLAYED frame with `_orientation` recording which frame
that is, so changing orientation goes back to the original frame and forward into
the new one rather than transforming twice.

**Export in either frame is done, end to end.** The Export section grows a frame
chooser as soon as anything is rotated or flipped — "As you see it (90° ⇄)" vs
"As imaged (0°)", defaulting to what is on screen, because that is what has been
edited against. It applies to the merged `.geojson` and the separate `.zip` alike,
and to "Export anyway" after a geometry block.

- **displayed** — the coordinates on screen, byte-for-byte, with `_orientation` on
  the file so the frame is recoverable later. The marker is written even if the
  client posted an fc that never carried one.
- **original** — every edit rotated and flipped back to the as-imaged frame, and
  `_orientation` dropped, because the original frame is by definition unrotated.

The two files get different names (`regions_merged_rot90_flipH.geojson` vs
`regions_merged_original.geojson`) — the same regions can be exported twice and
two downloads called the same thing would land as "(1)". Nothing rotated means no
suffix, so an ordinary export keeps the name it always had. Per-region files in the
`.zip` now carry the file-level members (`_orientation`, `_provenance`) too; they
used to be bare `{type, features}`, i.e. an unlabelled bag of coordinates.

**Edits made while rotated carry over correctly** — verified by
`test_edit_while_rotated.py`: share borders at 90°, rotate back, and every region
keeps the area it had when edited (0.00 px² drift), untouched regions don't move,
the edit is not reverted, and export in the original frame still contains it.

**Both export frames are verified** — `test_export_frames.py`, 51 checks over
rot90+flipH, rot270+flipV and rot180+flipH+flipV on the real dataset. Each round
rotates, makes a real backend edit (share borders) *and* a hand vertex nudge in the
rotated frame, then exports both ways. The check that actually proves it is not the
round trip — a round trip can be self-consistently wrong — it is that after
rotating, editing and exporting "as imaged", **the 21 regions nobody touched come
back on top of the untouched original file to 1.8e-12 px**, while the edited ones
have moved. Areas match across frames, provenance survives both, and the `.zip`
geometry agrees with the merged file exactly.

Run it against an isolated backend so the real workdir is never written:

```bash
ATLAS_WORKDIR=<scratch>/frames_workdir python -m uvicorn app:app --port 8060
```

The bug that made this work was the same class as the provenance one: `stamped()`
rebuilt `{type, features, _provenance}` and so **dropped `_orientation`**, meaning
any edit stripped the marker saying which frame the file was in. Both
`provenance.stamped()/carry()` and the frontend `asFc()` now carry across EVERY
file-level member rather than the ones we thought to name. **Any new endpoint that
returns a feature list must go through `stamped()`/`asFc()`** or it will silently
drop them again — this is the third time this class of bug has appeared.

Verified (`scratchpad/test_orientation.py`, `test_rotate_live.py`, `test_rotate_tiles.py`):
all 16 orientations agree between the coordinate transform and the image transform
(80/80 marked-pixel cases — if those two ever disagree the regions drift off the
picture, which is the bug the feature exists to fix); a 90° round trip returns
byte-identical coordinates; tile content is conserved at **0.00% drift** for
morphology and stains across rot 90 / 180 / flipV.

### 2. Metadata — records correctly, no UI yet

`backend/provenance.py`. The trail is a `_provenance` member **on the
FeatureCollection**, not in a workdir — a region file gets emailed down a chain of
command, so the history has to be part of the file. Each entry records the display
name, **the Windows account it was made from** (a typed name alone is unverifiable),
the build version and a timestamp. Capped at 2000, and trimming is recorded rather
than silent.

Every mutating endpoint stamps itself and carries the incoming trail forward.
`GET/POST /api/identity` sets the display name; `POST .../regions/history` returns
the chain with per-person counts.

Trap already fixed: `App.jsx` rebuilt `{type, features}` by hand in 11 places, each
of which silently dropped the trail. They all go through `asFc()` now — **use it
for any new endpoint that returns a feature list.**

Still to do: the History panel in the sidebar, and recording the purely client-side
edits (rename, colour, delete, dragged points). The save endpoint already accepts an
`actions: [{action, detail, regions}]` list for exactly this; the client doesn't
send it yet.

### 3. Resolution — measured, and it is not what it looks like

**The transcript density is already exactly 10 µm bins** (`density/gene`,
`grid_size = [10.0, 10.0]`, 606 × 652) and **the stains are already full native
resolution** (28486 × 30687, 7 levels, 0.2125 µm/px). Nothing to recompute.

What looks coarse is the rendering: the gene layer is ONE 606 × 652 PNG stretched
across a 28,518 px canvas in a single `BitmapLayer`. Crisp bins are nearly free.

Finer than 10 µm is possible — the raw points are there: **115.5 M transcripts** in
`grids/0` across 592 tiles, with a 6-level LOD pyramid already built (coarsest is
1.4 M). Per-viewport re-binning at high zoom is the viable route.

Speed measurements (full-res tiles, data on the S: share):

| | sequential | 6 threads | speedup |
|---|---|---|---|
| morphology, 9 tiles | 1.25 s (0.13 s each) | 0.81 s | 1.5× |
| stains, 9 tiles (4 ch) | 2.64 s (0.29 s each) | 1.59 s | 1.7× |

Two levers, in order:
1. **Cache decoded per-channel tiles.** Decode is expensive, blending is nearly
   free. The stain tile URL embeds the contrast spec, so touching a slider changes
   every URL, misses both caches and re-decodes all four channels per tile. Caching
   channel tiles makes sliders instant.
2. **Narrow the decode lock** — `tiles.py` holds `self._lock` around the whole zarr
   read, and that read IS the JPEG2000 decode, so tiles decode one at a time. That
   is why 6 threads only buy 1.5×. (Not isolated from SMB latency, but the
   serialisation is real.)
3. A "quality" setting capping the deepest served level — each level dropped is 4×
   less to decode. Good for laptops; trades quality, so an option not a default.

### 4. Circle-delete — not started

Confirmed meaning: **stray slivers and hairline spikes**, not vertices or whole
regions. Circle an area and any sliver inside is cleaned away, geometry outside the
lasso untouched. Plan: a lasso mode mirroring the existing draw/dissolve modes, plus
a topology op that morphologically opens and de-crumbs ONLY inside the lasso —
`_drop_small_parts`, `clean_geom` and `_clean_for_partition` in `topology.py`
already do the pieces. Watch the usual trap: do not clean per region across a shared
edge or you strew slivers along every border (see "Bugs fixed" below).

---

# Earlier handoff (2026-08-04) — still accurate

Region-editing webapp for Xenium atlases. Renamed **FiveAtlas** this session
(was "Atlas Region Editor").

---

## Run it

```powershell
# backend (NO --reload: restart it after ANY .py change or the change is not live)
Start-Process -FilePath "C:\Users\FIVE\source\repos\Jess\.venv\Scripts\python.exe" `
  -ArgumentList "-m","uvicorn","app:app","--app-dir","backend","--host","127.0.0.1","--port","8050" `
  -WorkingDirectory "C:\Users\FIVE\source\repos\Jess\atlas_editor" -WindowStyle Hidden

# frontend (Vite, proxies /api -> 8050)
cd atlas_editor\frontend ; npm run dev      # http://localhost:5173
```

Verify a backend change is actually live via `http://127.0.0.1:8050/openapi.json`
(route list), **not** `/api/health` — health passes on the stale process.

The repo `.venv` is Python 3.9; WMI reports the VS Shared `Python39_64` path for it,
which is the same interpreter, not a missing-shapely problem.

## Build the exe

```powershell
powershell -ExecutionPolicy Bypass -File build_release.ps1 -Version 0.4.0
```

`-OutRoot` now defaults to **`%LOCALAPPDATA%\FiveAtlas_build`** — a local disk (the old
S: default built over SMB, far slower) and outside the repo, because `work/`, `dist/`,
`temp/` and `release/` are **not** in `.gitignore` and would otherwise flood `git status`.
Output: `<OutRoot>\release\FiveAtlas-<ver>-win64.zip`.

- Installer: `installer.iss` is written but needs `winget install JRSoftware.InnoSetup`;
  the build script compiles it automatically once ISCC exists. Until then it's zip-only.
- **Shipped build is 0.4.0** (2026-08-04): resample + the Share-borders fix + multi-part
  regions. `C:\Users\FIVE\AppData\Local\FiveAtlas_build\release\FiveAtlas-0.4.0-win64.zip`,
  66 MB zipped / 159 MB unpacked. Smoke-tested headlessly
  (`ATLAS_NO_BROWSER=1 ATLAS_PORT=8071`): serves the UI and all 35 routes.
- **Never run the built app off a network drive** — 159 MB / ~1,100 files over SMB never
  finished starting in 90 s; from a local disk it starts in ~1 s. The README says so.

---

## Multi-part regions (fixed 2026-08-04)

A name carried by more than one feature is **one region in several parts**, not two
regions. The loaded file has `Ventricles` ×2 and `Fiber Tracts` ×2 — two lobes each.

`_index()` used to keep only the **first** feature per name, so the other lobe was
invisible to an edit: neither a Voronoi generator nor protected ground, and the nearest
region absorbed it. `_guard_duplicate_names()` refused those operations outright as a
stopgap, which is why picking `Ventricles` gave no border arc and no drag handles.

Now, in `topology.py`:

- `_index_all()` maps a name to **every** feature index; `_body()` unions them into the
  region the operation actually reasons about.
- `_distribute()` hands the recomputed body back to the original features: each output
  piece goes to the feature it overlaps most, so two lobes stay two features. If that
  would starve a lobe — which happens when the edit bridged them into a single piece —
  it falls back to `_nearest_partition`, which splits the body down its midline instead.
- Share borders, move border, resample, merge, `region_gap`, `border_between`,
  `shared_borders` and `snap_to_edits` all go through those.
- `split_region` **keeps** the guard: "cut this region" doesn't say which lobe.
- A duplicate name is now a **warning**, not an error, so it no longer blocks export.

Sidebar rows for a repeated name are tagged `· part 1/2`, since clicking either one
selects (and edits) the whole region.

Verified (all in `atlas_editor_sandbox/`, each takes `<backend_dir>` or hard-codes it):

| script | covers |
|---|---|
| `bench_dup.py` | Share borders on all 11 pairs involving a duplicated name — **11/11** |
| `bench.py` | the original 23 unique-name pairs, unchanged — **23/23** |
| `test_ops.py` | `region_gap`, `border_between`, partition, `move_border`, resample, merge, split-still-refuses, validate |
| `test_distribute.py` | `_distribute` both branches, `_index_all`, `_body`, `_write_body` |

Live API check: `Ventricles + Thalamus` returns 1 arc / 56 handles and `Fiber Tracts +
Caudoputamen` 2 arcs / 221 handles, where the pre-fix code (guard neutered) returned
**zero** arcs on 7 of those 11 pairs.

---

## Architecture

- **Backend** `atlas_editor/backend/` — FastAPI. `scan.py` detects a dataset from one
  folder; `tiles.py` serves JPEG2000 pyramid tiles; **`topology.py` is all the shapely
  geometry**; `geo.py`/`datasets.py` load/save/registry.
- **Frontend** `atlas_editor/frontend/src/` — React + deck.gl. Does **no** geometry; every
  edit round-trips to the backend. Stateless, so undo/redo is snapshots of the FC.
- Dev = two servers; packaged = FastAPI serves the built UI too, one port.
- Originals are **read-only**. Edits go to a working copy; Export is the only way out.

### Where files live
```
backend/workdir/<ds>/regions_edited.geojson     working copy (dev)
backend/workdir/<ds>/versions/*.geojson         one immutable snapshot per Save
%LOCALAPPDATA%\FiveAtlas\<ds>\                  same, in the packaged exe
```

---

## Built this session

| feature | notes |
|---|---|
| **Fix a gap** | click a void → **Dissolve** into neighbours, or **New region**. Three detection kinds: `enclosed` (hole in the union), `notch` (open, bridged by a `tol` close), `between` (corridor from dragging a region away — grows the bridge with no size cap, ceiling is the data span). Refuses background/inside-a-region/lone-region space. |
| **Add a new region** | draw an outline; anything it covers is taken from the region underneath, so the file stays a partition. |
| **Delete region** | right-click on image or sidebar list. Leaves a hole that Fix-a-gap can then close. |
| **Rename / Colour / Delete** | one right-click menu. The old properties panel is gone. |
| **Colours** | read `classification.colorRGB` (override) → `classification.color` (the file's own atlas palette) → name hash. The app used to ignore the file's palette entirely. |
| **Geometry check + export gating** | export refuses self-intersections, empties, non-polygons, duplicate/missing names; offers Repair / Export anyway. |
| **Versioned saves** | every Save writes an immutable `versions/regions_<stamp>.geojson`. Added after Save overwrote the working copy and lost state. |
| **Restore original GeoJSON** | reloads the dataset's own file, snapshotting current work first. |
| **Resample points** | thins the **shared border** only; live preview as you drag the slider; auto-runs after Share borders. |
| **Proportional editing** | mouse-wheel radius + visible circle; moved below all edit tools. |
| **Split** | extends the cut by the region's diagonal (was a fixed 1000 px, useless on a 15,000 px region); sides judged by the drawn path, not the chord (hooked cuts were refused outright); refuses self-intersecting cuts and sliver cuts. |
| **Sidebar** | stain min/max sliders greyed out; proportional editing moved down. |

---

## Bugs fixed — and the pattern behind them

**Douglas-Peucker was silently redrawing geometry in three places.** Worth suspecting first
whenever something "looks like it didn't apply":

1. **`_simplify_shared` un-noded the network.** `polygonize()` requires pre-noded input;
   `simplify()` moves interior vertices and breaks that, so the shared arc became a dangling
   edge, ONE face came back covering the whole territory, the first region swallowed it, the
   second got zero faces and the cede loop deleted it. **This is what erased regions on
   Share borders** — 5 of 23 adjacent pairs. Fix: `unary_union(net)` after simplifying, plus
   zero-face and balloon guards, plus the cede loop now splits a substantial overlap down its
   midline instead of deleting a side. Verified 23/23.
2. **`border_between` simplified the arc for display**, so evenly-spaced resampled points
   (collinear on a straight run) were deleted — 150 px spacing displayed as a 1,204 px jump.
   Fix: `simplify=0` on the resample path.
3. Per-region cleaning after partitioning broke shared-edge coincidence, strewing hairline
   voids and overlaps. Fix: don't clean per region; thin the shared network instead.

**Right-click didn't finish a drawn shape** — not an event-plumbing problem, which is where
I wasted three attempts. `props.selectedIndexes` was `undefined` on the draw/split layers
because `selectedFeatureIndexes` was never passed, so `finishDrawing()` threw **every time**,
silently. One line per layer. `finishNow()` now logs and drops the sketch instead of failing
silently.

---

## Gotchas

- **`atlas_editor/` is NOT in git.** `git status` shows `?? atlas_editor/` — the
  whole app is untracked, so there is no restore point. The user was asked and
  chose to keep working uncommitted; don't commit without asking again.
- **Another session edits these same files.** A session titled *"Five Atlas on
  Avalonia for macOS"* owns `launcher.py`, `config.py`, `nativedialog.py` and
  `FiveAtlas.spec` (macOS `.app`, no console, log file, native alerts). Re-read
  those before editing — they change between turns. Version plumbing is theirs:
  `build_release.ps1` exports `FIVEATLAS_VERSION`, the spec reads it, and
  `config.VERSION` falls back to a literal.
- **Backend has no `--reload`.** Every `.py` change needs a restart.
- **A rename/edit only reaches disk on Save.** Force-reloading the page discards it.
- **deck.gl ignores synthetic mouse events** — you cannot test canvas interaction by
  dispatching events. Drive React props/handlers directly, or ask the user to click.
- **GUI dialogs from tool calls appear on the user's real screen** and block until dismissed.
  Don't pop file pickers to "test" them.
- The in-app Browser pane doesn't composite, so deck's canvas stays 300×150 and its viewport
  maths is wrong there. Not a code bug.
- `Remove-Item` is blocked on `C:`/`S:`-rooted paths (false positive). Use
  `[System.IO.Directory]::Delete(path, true)`.
- The dataset's own `merged.regions.geojson` is a **different** output (23 abbreviated
  regions: hemi, CP, CTXsp…, no colours) from the file the user is working on. **Restore
  original** goes to that one, not to the Downloads file.

---

## Sandbox

`atlas_editor_sandbox/` — created to fix Share borders with three parallel agents.

```
reference/           pre-fix copy of the project
a/ b/ c/backend/     three independent fixes, all reaching 23/23
bench.py             the shared judge: Share borders over all 23 adjacent pairs
bench_dup.py         the multi-part judge: pairs involving a duplicated name
topology.live-backup-*.py   pre-fix live topology.py
```

From the 2026-08-06 session (run with the repo `.venv` python; the live-API ones
need the backend up on :8050):

```
test_orientation.py    orientation maths, all 16 orientations, coords vs pixels
test_rotate_live.py    rotate the real dataset over HTTP; round trip must be exact
test_rotate_tiles.py   content conserved per orientation, morphology AND stains
test_provenance.py     the trail accumulates, survives a file round trip, hands off
test_minimal_folder.py what a dataset folder actually needs (answer: a geojson)
bench_tiles.py         tile latency, sequential vs parallel, cold vs warm
probe_points.py        raw transcript counts + LOD levels in transcripts.zarr
```

`bench_dup.py <backend_dir> [--verbose]` does NOT rename the duplicates apart, and checks
per **feature** rather than per name — so a lobe absorbed by a neighbour is caught even
though the name's total area looks fine. It also fails a pair that comes back with no
shared arc, which was the actual user-visible symptom.

`bench.py <backend_dir> [--verbose]` fails a pair if a region is destroyed or balloons, if a
meaningful overlap or void appears, if area isn't conserved, or if an unselected region moves.
**Agent A's version was ported live.** A was neutral-or-faster everywhere (12-region tile
2.6 s vs 3.3 s baseline); B was ~3× faster on pairs but 4.5× slower on 12 regions; C was the
smallest diff. All three independently found the `_simplify_shared` root cause — my briefed
hypothesis (inset seeding starving thin ribbons) was **wrong**, and they proved it with
measurements.

---

## Still open

1. **Installer** — needs Inno Setup installed.
2. **Split preview** — guards refuse bad cuts with clear messages, but there's no
   before-committing preview.
3. **Restore a specific version from the UI** — snapshots are written and there's a
   `GET /regions/versions` endpoint, but no picker.
4. **Resample distribution** — much better (min gap 8 px → 26 px) but the largest gaps remain
   ~1,100 px on convoluted borders; that's the arc-extraction corridor dropping points.
5. **Renaming a region onto an existing name is still refused** by the front end
   (`App.jsx`, "Another region is already named …"). Under the multi-part model that would
   be a legitimate way to join two lobes; the Merge button is the current route.

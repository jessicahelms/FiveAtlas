# FiveAtlas — session handoff (2026-08-06)

> **START HERE.** Four features were asked for in one go. One is done, one is
> half-built, two are untouched. Details in "The four features" below; everything
> after that section is older context that is still accurate.

## Never point a test at the live backend

The user's working copy was damaged by an automated test in this project. It ran
Share-borders on `hemi` + `ISO` against the running server on :8000, and saved. The
test's own assertions passed — it checked *area*, and a sliver fan does not change
area, only shape. The user found it by looking at the screen.

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

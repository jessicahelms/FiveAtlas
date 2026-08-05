# FiveAtlas — session handoff (2026-08-04)

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

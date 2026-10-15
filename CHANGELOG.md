# FiveAtlas changelog

Each release's section below lands at the top of its GitHub release page,
above the install instructions. Keep entries short and in plain language --
they are read by annotators, not developers. The heading must be `## vX.Y.Z`
exactly; CI matches it against the tag. Inside a section, `### ` headings
are allowed: on the release page they are printed one size up, so each new
feature reads as a big title with its explanation under it.

The first `### ` title of a section is also what the app's update banner
shows ("FiveAtlas 1.3.2 is out — Faster on laptops"), so lead with the change
people will care about most and keep that title short.

## v1.3.3

### Updates install themselves

- From this version on, a new FiveAtlas downloads in the background, and a bar
  at the top offers **Restart to update** — one click, and the new version
  opens in a new tab. Not clicked? It switches the next time you open
  FiveAtlas.
- Nothing is replaced while you work, and you are asked first if there are
  unsaved edits. If an update can't be installed, you keep the version you
  have and get the download link instead.
- **This one time**, install 1.3.3 by hand as usual; every update after it is
  automatic.

### SugJESStions

- A **💡 SugJESStions** button at the top right: the suggestion box for Jess.
  Type an idea, a bug, or a wish and press Send — it goes straight to her.
  Only what you type is sent, plus the FiveAtlas version and Windows or Mac.

## v1.3.2

### Faster on laptops

- Stain brightness and colour sliders change the picture as you drag,
  instead of a few seconds after you let go.
- Zooming in fills in faster, and the viewer works the graphics chip less on
  high-resolution laptop screens.

### Updates say what's new

- When a newer FiveAtlas is out, the notice at the top of the sidebar now
  says what it brings, not just its version number.

## v1.3.1

### Updating now really opens the new version

- **Mac, this one time:** before you open 1.3.1, quit the old FiveAtlas.
  Open **Activity Monitor**, search **FiveAtlas**, select it, press the stop
  button at the top and choose **Quit** (or just restart your Mac). A copy
  that is still running can't be switched over automatically, because macOS
  wakes the old copy instead of starting the new one. This is the last
  time you'll need to do this.
- **Mac, from now on:** drag a newer FiveAtlas into Applications while the
  old one is running and the open tab says **"FiveAtlas … has been
  installed"**, with a **Restart FiveAtlas** button — one click switches.
  With no tab open, the old one steps aside within a minute, so opening the
  app starts the new one.
- **Windows:** open the new FiveAtlas.exe even while the old one is still
  running — the new version now starts in its own tab instead of handing
  you the old one. Close the old black window when you're done with it.

### The page always loads fresh

- After an update your browser can no longer show a saved copy of the old
  version's page.

### See which version you're on

- The top-left corner of the window now says **FiveAtlas 1.3.1**.

## v1.3.0

### Stains load all the way, and faster

- A stain square that failed to load once (a slow network drive, Box still
  downloading the file) used to stay **blank until you reloaded the page**,
  so only part of the stain showed. Now it is **asked for again
  automatically**, and the whole stain fills in.
- Stain squares load **more than twice as fast** from a network drive: the
  channels are read side by side instead of one after another.
- Moving a **brightness slider** re-colours what is already loaded instead
  of reading every square from the drive again.

### Save is one button, and it tells you where your work went

- **Save, Reset, Undo and Redo** now sit together at the top right of the
  window, where they never scroll away. Beside them, **● unsaved** means
  you have edits that are not on disk yet; **saved** means everything is.
- The **first** time you press Save on a dataset, it asks where to put the
  file. It opens in the dataset's own folder with a name already filled in
  (for example `merged.regions_edited.geojson`). **Every Save after that
  writes the same file without asking.** "Saving to *file* (change)" beside
  the button shows which file; press **(change)** to pick a different one.
- After every Save, a panel right under the button says **exactly where it
  went**, with **Open folder** and **Copy path** buttons.
- It will **never overwrite the dataset's original region files**. If you
  pick one, it says so and offers a new name beside it instead.
- A dated copy of every Save is still kept in the app's own folder, so an
  earlier save can always be recovered.

### Delete points with a right-click

- In **Edit points**, **right-click a point to delete it**. The rest of that
  border re-spaces itself so no long empty stretch is left, and a point on a
  border you share with a neighbour is removed from **both** regions, so
  the border never opens up.
- Works on the red border in **Edit shared borders** too.
- A point where three regions meet, or where a shared border ends, is
  protected: the app tells you why instead of pulling the regions apart.

### Resample points now works in Edit points

- The **Resample points** slider (fine to coarse) now works on a single
  region in Edit points, not only on shared borders. You see
  "points on *region*: before → after" while you drag, then press
  **Apply** or **Cancel**. Every neighbour's side of a shared border is
  re-pointed with it, so no border opens.

### Load regions file asks: add, or replace?

- **Load regions file** reads the file first and then asks: **Add to the
  map** (keep what is there and put these beside it; a name that is already
  used gets changed so it isn't shared) or **Replace the map**. Ctrl+Z
  undoes either one, and nothing is written until you press Save.
- If the file contains **hemi**, it asks **"Load the damage with it?" Yes /
  No**. That covers damage inside the same file and damage-only files
  (like `separation.4.geojson`) sitting in the same folder.

### Drawing over a region: Replace, Overlap, or Fit around

- When a region you draw lands on another one, you choose what happens:
  - **Replace**: the new region takes that ground from the regions there.
  - **Overlap**: both keep their whole shape. A region drawn around another
    goes behind it, so the smaller one stays visible.
  - **Fit around**: the new region is trimmed to the free ground, and
    nothing already there changes.
- **Right-click any region** for **Bring to front** / **Send to back**.
- New **Move region** button: drag a whole region to a new spot without
  changing its shape.

### Easier to find your way around

- Bigger section titles with clear dividers: **What loaded**, **Regions**,
  **Edit regions**, **Files & export**.
- The screen you see when opening a dataset lists **every file the app can
  load, by its real name**: imagery, stains, `transcripts.zarr`, each
  region file, the annotation notes and the metadata file. Each has its own
  tick box. (The confusing "genes — 9" line is gone.)
- The **Snap neighbors** button is gone. Use **Share borders** to tidy the
  join between regions.

## v1.2.2

- Windows and Mac downloads now sit on the SAME release page, so one link
  works for everybody -- and the "a newer FiveAtlas is out" notice inside the
  app links straight to it.
- Opening a dataset now starts with a screen listing what is in the folder:
  the imagery, the stains, transcripts, genes, and each region file on its own
  line, all ticked. Untick anything you don't want and nothing about it is
  read from disk at all. The app no longer loads a previous dataset by itself
  when it starts, which is what used to leave stains black or the wrong
  picture under the regions when you then opened your own folder.
- Region files are picked individually: load just one, or several together.
  If you don't touch those tick boxes your edited work loads exactly as
  before.
- Several regions at once: Ctrl-click to add one, Shift-click to take a whole
  run -- in the region list or on the map -- then "Delete selected" removes
  them all in one step, and one Ctrl+Z brings them all back.
- Stains now work on datasets straight out of the Xenium machine, whose files
  are named morphology_focus_0000..0003 rather than ch0000_dapi. They come up
  named and coloured as usual (DAPI blue, boundary magenta, 18S yellow,
  Vimentin green). Previously such a folder showed no stains and no sliders.
- Two folders with the same name no longer replace one another -- each keeps
  its own imagery and its own edits.

## v1.2.0

- Share borders on a nested pair now truly merges the borders. Gaps up to
  the snap reach between the region's edge and the outline are absorbed
  however wide the band actually is (before, anything wider than about half
  the reach was silently skipped and the app still said "merged"). And where
  the region pokes PAST the outline, the outline now grows to cover it, so
  the two borders end up as one line on both kinds of stretch.
- New "Snap reach" slider (5-300 px, default 40) appears once you have
  picked regions in Edit shared borders. It sets how wide a gap Share
  borders bridges -- for nested pairs and side-by-side pairs alike -- and
  how wide a seam Merge dissolves. Wide bands may need the slider up around
  150; a second click is always safe and only tightens things further.
- Dragging a nested pair's border where it runs ON the outline now moves
  both as one line: pull the region's edge inward along the coast and the
  outline follows, exactly like an ordinary side-by-side border. Inland,
  the container still simply keeps covering, and neighbours are never taken.
- Honest messages: if a region sits too far inside the outline for the snap
  to bridge, the app now says how many pixels away it is and suggests
  raising Snap reach, instead of claiming there was nothing to merge.

## v1.1.1

- Share borders now works for a region inside another region (hemi + a
  coastal region, say): the hairline gap between the region's edge and the
  outline is absorbed, so they genuinely share the outline where they
  neighbour. Nothing else moves -- the container is untouched, neighbours
  are never taken, and fat unclaimed pockets are left alone.
- Picking hemi got easier to find: the Edit shared borders hint now says to
  pick regions by clicking their NAMES in the list -- the way to pick a
  region that sits underneath everything.

## v1.1.0

- Regions that sit inside another region now have an editable shared border:
  pick the pair in Edit shared borders and the inner region's outline appears
  as the border. Drag it and the inner reshapes; the containing region always
  keeps wrapping it, and real neighbours are never taken. (Share borders --
  the tiling button -- still refuses nested pairs, since there is nothing to
  divide; the message now points you at the drag instead.)

## v1.0.1

- Removed the Quit button. To stop FiveAtlas: on Windows close its window;
  on a Mac just close the browser tab -- it stops itself about ten minutes
  later.

## v1.0.0

First numbered release of the version scheme (same app as 0.5.7). From here
on: the middle number goes up when features arrive, the last number when
something small is fixed.

- Everything from the 0.5.x series, below, is included.

## v0.5.7

- Sidebar sections fold: every section title has a little arrow -- collapse
  what you are not using.
- Stain colours now match the Xenium Explorer defaults: DAPI blue, boundary
  stain (ATP1A1/CD45/E-Cadherin) magenta, 18S yellow, alphaSMA/Vimentin
  green.

## v0.5.6

- Settings (the gear button): text and button size.
- Heat-map colour scales: Viridis, Inferno, Magma, Plasma, Turbo -- the same
  ones the Xenium Explorer uses -- over the combined density of the shown
  genes.
- The sidebar now says when a newer FiveAtlas is on the download page.
  Nothing downloads itself; it is a note with a link.

## v0.5.5

- Import regions from a CSV of vertices (name,x,y per row; Xenium's micron
  columns convert automatically).
- Export AnnData (.h5ad): regions x genes transcript counts, ready for
  scanpy.
- Transcript heat maps in perfect squares (10/20/40/80 microns), light where
  sparse, dark where dense; two genes mix like inks. Every square holding
  any transcripts stays visibly tinted.
- A shared-border drag may reach past the section outline; the region grows
  into the empty ground beyond.
- Merge into one now dissolves the shared border -- hairline seams are
  sealed; genuinely detached pieces are never bridged.
- Per-region face and border visibility toggles, and Faces off/on for all.
- The sidebar is resizable.

## v0.5.0 - v0.5.4

- Damage annotation: draw damage shapes, tick undrawn kinds, SmartSheet
  cells to copy, and writes into the shared annotation.notes.yaml /
  metadata.yml with a diff preview and protection against overwriting a
  colleague's record.
- Build the hemisection outline from the regions themselves.
- Clean up stray lines: sweep a circle around leftover hairlines and they
  are removed.
- Snap neighbours no longer carves the moved region's shape through the
  hemisphere outline.
- Stain brightness sliders work.
- macOS builds hardened (runs on macOS 11+, self-tests every codec before a
  release is published).

# FiveAtlas changelog

Each release's section below lands at the top of its GitHub release page,
above the install instructions. Keep entries short and in plain language --
they are read by annotators, not developers. The heading must be `## vX.Y.Z`
exactly; CI matches it against the tag.

## v1.2.1

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

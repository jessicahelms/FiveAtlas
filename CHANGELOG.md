# FiveAtlas changelog

Each release's section below lands at the top of its GitHub release page,
above the install instructions. Keep entries short and in plain language --
they are read by annotators, not developers. The heading must be `## vX.Y.Z`
exactly; CI matches it against the tag.

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

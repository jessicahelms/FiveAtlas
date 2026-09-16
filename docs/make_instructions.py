# -*- coding: utf-8 -*-
"""FiveAtlas instructions sheet -- the numbered how-to handed to annotators.

    python docs/make_instructions.py            -> docs/FiveAtlas-instructions.pdf,
                                                   plus the copies at the repo root
                                                   (INSTRUCTIONS.pdf, INSTRUCTIONS.html)
    python docs/make_instructions.py OUT.pdf    -> just that PDF
    python docs/make_instructions.py OUT.html   -> just that web page

Plain prose, one numbered list, same voice throughout. Every copy is built from
the ITEMS below, so the PDF on the release page, the PDF in the repo and the web
page can never say different things. Needs reportlab (`pip install reportlab`)
for the PDFs; it is a doc tool, not part of the app.
"""
import html
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
TITLE = "FiveAtlas — Instructions"

ITEMS = [
    # -- getting it running: Windows ---------------------------------------------
    "On Windows: extract the zip file, then in the extracted folder double click "
    "FiveAtlas.exe and it will start running! The first time you run it, you "
    "might get a Windows error saying that it does not trust this app. Just "
    "press “More info” and then “Run anyway.” Copy the folder "
    "to your own computer first — do not run it straight from the shared drive.",

    # -- getting it running: Mac -----------------------------------------------------
    "On a Mac: it needs an Apple Silicon Mac (M1, M2, M3, M4…) on macOS 11 "
    "or newer — click the Apple menu, About This Mac, and check it says "
    "“Chip: Apple M…”; if it says Intel it will not open. Download "
    "FiveAtlas-…-macos-arm64.dmg on the Mac itself (passing it through a "
    "Windows computer or the shared drive can break it). Double click the .dmg, "
    "drag FiveAtlas onto the Applications folder in that window, then eject the "
    "disk image. Always start it from Applications.",

    "The first time you open it on a Mac, macOS will refuse: “FiveAtlas is "
    "damaged and can’t be opened” or “Apple could not verify FiveAtlas "
    "is free of malware”. That is Apple’s Gatekeeper reacting to an app "
    "that did not come from the App Store, not a problem with the app, and it "
    "only happens once per download. The fix that always works: press Cmd+Space, type "
    "Terminal and press Return, then paste this one line exactly and press "
    "Return:   xattr -dr com.apple.quarantine /Applications/FiveAtlas.app   "
    "Nothing is printed when it worked. Now open FiveAtlas again. (On macOS 15 "
    "you can instead open System Settings, Privacy &amp; Security, scroll down to "
    "where it says FiveAtlas was blocked, and press “Open Anyway”.)",

    "FiveAtlas has no window of its own. On a Mac, a few seconds after you open "
    "it (up to a minute the very first time, while macOS checks it) a new tab "
    "opens in your browser at http://127.0.0.1:8050 — if no tab appears, type "
    "that address into your browser yourself. It keeps running while you work, "
    "even if you close the tab for a while. To stop it, just close the tab — "
    "it stops by itself about ten minutes later. On Windows the black window that opens is the app: "
    "keep it open while you work and close it to stop.",

    # -- updating ----------------------------------------------------------------------
    "Updating: when a newer FiveAtlas is out, a line at the top of the sidebar "
    "says so and links to the download page, which has the Windows and the Mac "
    "download on one page. Updating is always your choice — nothing downloads "
    "itself. Before you start the new version, stop the old one: on Windows "
    "close its black window; on a Mac close its tab and wait about ten minutes, "
    "or quit FiveAtlas in Activity Monitor. While an old copy is still running, "
    "starting the new one just opens the old one again. On a Mac, drag the new "
    "FiveAtlas into Applications, choose Replace, and run the Terminal line "
    "above once more.",

    # -- opening a dataset ---------------------------------------------------------------
    "Your data lives on the shared drive. On a Mac, connect to it in the Finder "
    "first (Go, Connect to Server). Then press Open folder at the top of "
    "FiveAtlas and click on the folder that contains the morphology_focus "
    "folder, morphology.ome.tif, transcripts.zarr.zip, etc. All of these files "
    "should already be in one folder based on how Xenium exports their data. "
    "Before anything is loaded, a screen lists every file the app can load from "
    "that folder, by its real name: the imagery, the stains, transcripts.zarr, "
    "each region file on its own line, the annotation notes and the metadata "
    "file. Everything is ticked — untick anything you don’t want and it "
    "is not read at all, then press Load. Tick one region file or several (they "
    "load together). If you prefer to paste a path on a Mac, it must start with "
    "/Volumes/… (not smb://…).",

    # -- load regions file: add or replace ---------------------------------------------
    "To bring in a different region file later, open Files &amp; export in the "
    "sidebar and press “Load regions file” — it takes GeoJSON, or a CSV "
    "of vertices (one row per vertex with name,x,y columns; Xenium's micron "
    "column names are converted automatically). The file dialog may take a few "
    "seconds; if it does not pop up, look at your task bar (Windows) or your "
    "browser (Mac). The file is read first and nothing changes until you "
    "choose: Add to the map keeps what is already there and puts the new "
    "regions beside it (a name that is already used gets changed, so two "
    "regions never share one), and Replace the map swaps everything for the "
    "file. If the file contains hemi, you are asked first whether to load the "
    "damage with it — Yes or No — which covers damage shapes inside that "
    "file and damage-only files (like separation.4.geojson) in the same folder. "
    "Ctrl+Z undoes the load, and nothing is written until you Save.",

    "If you are ever unsure about how to use certain features, there are "
    "instructions that pop up after you click on a feature. The sidebar is in "
    "sections — What loaded, Regions, Edit regions, Files &amp; export and "
    "more — and every section title has a little arrow: click it to fold "
    "that section away and the sidebar stays short; click again to bring it "
    "back. The sidebar can be made wider or narrower by dragging the thin strip "
    "between it and the image. Settings (the gear button at the top of the "
    "sidebar) makes the text and buttons bigger or smaller.",

    # -- stains and brightness -------------------------------------------------------
    "The Stains panel lists every morphology_focus channel (DAPI, 18S, "
    "alphaSMA/Vimentin…). Tick a channel to show it, pick its colour, and "
    "set its brightness with the min and max sliders — min is the level "
    "that reads as black, max the level that reads as full colour, so "
    "narrowing the range brightens the faint staining in between. The image "
    "updates when you pause dragging for a moment. Gene channels have the "
    "same controls.",

    # -- transcript heat map ------------------------------------------------------------
    "Gene expression has two looks, switched at the top of the Genes panel. "
    "Glow is the familiar additive one. Heat map draws the transcripts as "
    "perfect squares: pick the square size (10, 20, 40 or 80 microns), and "
    "each gene reads light where it is sparse and dark where it is dense — "
    "a blue gene runs pale-blue to deep blue, and every square holding any "
    "transcripts at all stays visibly tinted. Add a second gene in red and "
    "the two mix like inks: where both are dense the squares go dark purple. "
    "Or pick one of the scientific colour scales (Viridis, Inferno, Magma, "
    "Plasma, Turbo — the same ones Xenium Explorer uses) to paint the "
    "combined density of every shown gene on one scale. The min/max sliders "
    "still set what counts as sparse and dense.",

    # -- the region list, selecting, right-click menu ---------------------------------------
    "All of your regions are listed in the Regions section of the sidebar. "
    "Click one to select it. Ctrl-click (Cmd-click on a Mac) adds another one, "
    "and Shift-click takes a whole run — in the list or on the image. With "
    "several selected, Delete selected removes them all in one step, and one "
    "Ctrl+Z brings them all back. If a name appears twice with “part "
    "1/2” beside it, that is one region made of two separate pieces, and "
    "anything you do applies to both.",

    "Right click a region (two-finger click on a Mac trackpad), either in the "
    "list or on the image, to rename it, change its colour, bring it to the "
    "front, send it to the back, or delete it. Bring to front draws it over "
    "everything else, so it is the one your clicks land on; Send to back draws "
    "it behind everything else, so whatever it contains stays visible.",

    # -- switch off --------------------------------------------------------------------
    "The eye beside each region in the list switches it off: it is hidden from "
    "the image and left out of gap-finding and Check geometry, but it stays in "
    "the file and comes back exactly as it was. Switching off the outline "
    "(“hemi”) is the easy way to hunt for gaps between the regions "
    "inside it.",

    # -- face / border toggles -----------------------------------------------------------
    "Beside the eye, every region also has a face toggle and a border toggle. "
    "These only change what you SEE — unlike the eye, the region still "
    "counts for gap-finding, snapping and Check geometry. Hide a face to see "
    "the imagery and the borders clean while you fill gaps; hide a border to "
    "see a fill without its outline. The Faces off / Faces on buttons above "
    "the list do every region at once.",

    # -- edit points ---------------------------------------------------------------------
    "Edit points reshapes a region by hand. Select the region, press Edit "
    "points, then drag the points along its outline. Click the outline between "
    "two points to add one. Right click a point to delete it: the rest of that "
    "border re-spaces itself so no long empty stretch is left, and a point on "
    "a border shared with a neighbour is removed from both regions, so the "
    "border never opens up. A point where three regions meet, or where a "
    "shared border ends, is protected and the app says why. Tick Proportional "
    "editing (at the bottom of Edit regions) and the nearby points will follow "
    "the one you are dragging, and you can scroll your mouse wheel while "
    "dragging to change how much of the outline comes with it.",

    "While you are in Edit points, the Resample points slider changes how many "
    "points the selected region's outline has. Drag it from fine to coarse and "
    "you see “points on (region): before → after” as you go, then "
    "press Apply to keep them or Cancel to put the old ones back. Every "
    "neighbour's side of a shared border is re-pointed with it, so no border "
    "opens up.",

    # -- move region -----------------------------------------------------------------------
    "Move region drags a whole region to a new place without changing its "
    "shape. Select the region, press Move region, and drag it on the image. "
    "Nothing is trimmed, so it may end up overlapping a neighbour — use "
    "Bring to front or Send to back from the right-click menu, or Share borders "
    "to tidy the join.",

    # -- shared borders ----------------------------------------------------------------------
    "Edit shared borders works on the line between two regions. Click two "
    "regions that touch and you can drag the border between them, and both "
    "sides update together so you never open up a gap. Right click the red "
    "border line to add a point there, or right click a point on it to delete "
    "it. This works for a "
    "region INSIDE another region too: pick the pair and the inner one's "
    "outline appears as their border — drag it, and the outer region keeps "
    "wrapping it — and pressing Share borders on such a pair merges the "
    "inner region's edge into the outline where they neighbour, so they "
    "genuinely share it. To pick a region that sits "
    "underneath everything (like “hemi”), click its NAME in the "
    "Regions list. A drag may also reach "
    "PAST the section outline — the region grows into the empty ground "
    "beyond it (rebuild “hemi” afterwards if you want the outline to "
    "catch up). Other regions' territory is never taken this way.",

    "Pick two or more regions and press Share borders to tidy up all the "
    "boundaries at once: small gaps get filled, overlaps get removed, and "
    "every pair ends up with one shared edge. The Snap reach slider sets how "
    "wide a gap Share borders will bridge — if a gap is left, raise it and "
    "press Share borders again; a second press is always safe. Merge into one "
    "instead combines everything you picked into a single region.",

    # -- merge -----------------------------------------------------------------------------
    "Merge into one truly dissolves the border between the regions you picked: "
    "the shared line disappears and they become a single region — even "
    "when a hairline gap ran along the old border, it is sealed. Pieces that "
    "genuinely do not touch stay as separate parts of the one region; the app "
    "never invents tissue between them.",

    "Once you have shared borders, a Resample points slider appears there too. "
    "Drag it from fine to coarse and the points on the border change as you "
    "go, then press Apply to keep them or Cancel to put the old ones back. "
    "This is useful when a border has far more points on it than you can work "
    "with.",

    "Split a region cuts one region into two. Select the region, then draw a "
    "line all the way across it on the image, clicking for each point, and "
    "right click (or press Enter) to finish. Press Esc to throw the line away "
    "and start again.",

    # -- drawing a region: replace / overlap / fit around --------------------------------------
    "Add a new region or damage lets you draw a region from scratch. Choose "
    "“Anatomical region” under “What are you drawing?”, "
    "then trace the outline on the image, clicking for each point, then right "
    "click or Enter to finish and Esc to cancel. If your outline lands on "
    "another region, you choose what happens. Replace: the new region takes "
    "that ground from the regions already there. Overlap: both keep their "
    "whole shape — a region drawn around another goes behind it, so the "
    "smaller one stays visible. Fit around: the new region is trimmed to the "
    "free ground, and nothing already there changes. Cancel throws the outline "
    "away. (A choice that would erase a region, or leave nothing of your "
    "shape, is not offered.)",

    # -- drawing damage ------------------------------------------------------------------
    "The same button draws damage. Pick a damage type instead (Separation, "
    "Large void, Bubble…) and trace the damaged area the same way. The "
    "shape names itself — separation.1, voidlarge.2 — with numbering "
    "running across the whole file. Unlike a region, a damage shape sits "
    "inside the region it covers: nothing is taken away, and the two overlap "
    "on purpose. If a shape reaches into more than one region, you are asked "
    "which region it belongs to — the one holding most of it is suggested "
    "— or record it in both if two annotators each own one side. Some "
    "damage types (Cutoff, Missing, Low/no transcripts) are never drawn; you "
    "tick those per region in the damage cells panel instead (see below).",

    # -- hemisection outline ---------------------------------------------------------------
    "Build the hemisection outline creates (or rebuilds) “hemi”, the "
    "region that wraps around all the others, computed from the regions "
    "themselves rather than traced by hand. You see the outline and its area "
    "as a preview first, then press Create hemi or Replace hemi. Damage "
    "shapes are left out, rebuilding never uses the old outline as input (so "
    "it cannot creep outwards), and a rebuilt hemi keeps its colour and "
    "anything you had ticked on it.",

    "Fix a gap is for empty spaces left behind between regions. Click inside "
    "one and it will be outlined in red, then press Dissolve to hand it to "
    "the regions around it, or New region to turn it into a region of its "
    "own.",

    # -- clean up stray lines --------------------------------------------------------------
    "Clean up stray lines removes the hairline slivers geometry operations "
    "sometimes leave behind — too thin to click, too thin for Fix a gap. "
    "Hold the left button and sweep a circle around them, then release. "
    "Everything sliver-thin inside the circle is found and highlighted in red "
    "with a report of where it came from and where the ground will go; press "
    "Apply to remove it, Cancel to keep everything, Esc mid-sweep to throw "
    "the circle away. Healthy regions are never touched — even if your "
    "circle crosses straight through one — and neither are damage shapes.",

    # -- orientation -----------------------------------------------------------------------------
    "Orientation is for a slide that was imaged upside down or mirrored. "
    "Rotate or flip the whole thing — image and regions move together — "
    "and work the way round that makes sense. When you export, you choose "
    "whether the file keeps the displayed orientation or is turned back to "
    "the as-imaged one.",

    # (the buttons are described, not drawn: the PDF fonts have no curved-arrow
    # glyphs and would print black squares)
    "Undo and redo are the two curved-arrow buttons at the top right of the "
    "window, or Ctrl+Z and Ctrl+Y (Cmd+Z and Cmd+Shift+Z on a Mac), and they "
    "work for everything above.",

    # -- saving ----------------------------------------------------------------------------------
    "Save is at the top right too, next to undo and redo. “● unsaved” "
    "beside it means you have edits that are not on disk yet, and "
    "“saved” means everything is. The first time you press Save on a "
    "dataset, it asks where to put the regions file — it opens in the "
    "dataset's own folder with a name already filled in, like "
    "merged.regions_edited.geojson. Every Save after that updates the same "
    "file without asking: “Saving to (file)” shows which one, and "
    "(change) picks a different file. After each Save a panel under the button "
    "shows exactly where it went, with Open folder and Copy path buttons. It "
    "never overwrites the dataset's original region files — if you pick "
    "one, it offers a new name beside it instead.",

    "Every save also keeps a dated copy in the app's own folder "
    "(%LOCALAPPDATA%\\FiveAtlas on Windows; ~/Library/Application "
    "Support/FiveAtlas on a Mac — in Finder press Shift+Cmd+G and paste "
    "that path), so you can always go back to an earlier one; the log file "
    "lives there too. Reset, beside Save, throws away every edit since the "
    "last save. Restore original GeoJSON (in Files &amp; export) takes you "
    "back to the dataset’s own file — your current work is "
    "snapshotted first, so even that can be undone. Your original files are "
    "never changed.",

    "Check geometry will tell you if anything is wrong with your outlines, "
    "and offers to repair the ones it can fix on its own. Regions you have "
    "switched off are left out of the check.",

    # -- damage cells ----------------------------------------------------------------------------
    "Damage cells, region by region is for the SmartSheet. It shows one box "
    "per region with that region’s damage, using the sheet’s own "
    "dropdown names. Tick Done, or add a damage type by hand — this is "
    "where the never-drawn kinds (Cutoff, Missing, Low/no transcripts) are "
    "recorded. Copy a box, click that region’s Damage cell in SmartSheet, "
    "and paste: it stays in one cell. A ◆ marks damage that came from a "
    "shape you drew — delete the shape to remove it.",

    # -- annotation notes --------------------------------------------------------------------------
    "Annotation notes writes what you found into the sample’s shared "
    "files (annotation.notes.yaml and metadata.yml, in the dataset folder). "
    "Type your name in Annotator — left blank, no name is written and "
    "only damage and voids are. Choose Only what I edited or All regions, "
    "then press Preview notes changes: every line that would change is shown "
    "as a diff before anything is written, and nothing is written until you "
    "press Save in that panel. These files are shared, so the app protects "
    "them: if a colleague saved after your preview, your save is refused "
    "rather than overwriting them, and damage already recorded in the file "
    "is never cleared just because your working copy has no shape for it. If "
    "the sample has no notes file yet, Create it here makes one — but "
    "check no one has started one in another folder for the same sample "
    "first.",

    "When you are done, press Export. Merged gives you one .geojson with "
    "every region in it, and Separate gives you a zip with one file per "
    "region. AnnData (.h5ad) exports a regions × genes count table for "
    "scanpy — the transcript density summed inside each region, with areas "
    "and centroids — ready for Python analysis.",
]


def build_pdf(out: Path):
    from reportlab.lib.pagesizes import letter
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.lib.units import inch
    from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer

    title_style = ParagraphStyle(
        "title", fontName="Helvetica-Bold", fontSize=11.5, leading=15, spaceAfter=14,
    )
    item_style = ParagraphStyle(
        "item", fontName="Helvetica", fontSize=10, leading=14.5,
        leftIndent=26, firstLineIndent=0, spaceAfter=9, bulletIndent=6,
    )
    doc = SimpleDocTemplate(
        str(out), pagesize=letter,
        leftMargin=0.9 * inch, rightMargin=0.9 * inch,
        topMargin=0.85 * inch, bottomMargin=0.85 * inch,
        title=TITLE, author="FiveAtlas",
    )
    story = [Paragraph(TITLE, title_style), Spacer(1, 2)]
    for i, txt in enumerate(ITEMS, 1):
        story.append(Paragraph(txt, item_style, bulletText=f"{i}."))
    doc.build(story)
    print("wrote", out, f"({len(ITEMS)} items)")


_PAGE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title}</title>
<style>
  :root {{ color-scheme: light; }}
  body {{
    background: #ffffff;
    color: #000000;
    font-family: "Segoe UI", Calibri, Arial, sans-serif;
    font-size: 11pt;
    line-height: 1.5;
    margin: 0 auto;
    padding: 2.5rem 1.5rem 4rem;
    max-width: 44rem;
  }}
  p.title {{ font-weight: 700; margin: 0 0 1.5rem; }}
  ol {{ margin: 0; padding-left: 1.6rem; }}
  li {{ margin-bottom: 0.9rem; overflow-wrap: anywhere; }}
  li:last-child {{ margin-bottom: 0; }}

  @media print {{
    body {{ padding: 0; font-size: 10.5pt; max-width: none; }}
    @page {{ margin: 18mm 16mm; }}
    li {{ break-inside: avoid; }}
  }}
</style>
</head>
<body>

<p class="title">{title}</p>

<ol>
{items}
</ol>

</body>
</html>
"""


def build_html(out: Path):
    # ITEMS are reportlab paragraph markup: plain text with &amp; entities. Undo
    # those, then escape for HTML, so both formats print the same characters.
    def as_html(txt):
        return html.escape(html.unescape(re.sub(r"<[^>]+>", "", txt)), quote=False)
    items = "\n\n".join(f"  <li>{as_html(t)}</li>" for t in ITEMS)
    out.write_text(_PAGE.format(title=html.escape(TITLE), items=items),
                   encoding="utf-8", newline="\n")
    print("wrote", out, f"({len(ITEMS)} items)")


if __name__ == "__main__":
    if len(sys.argv) > 1:
        target = Path(sys.argv[1])
        (build_html if target.suffix.lower() in (".html", ".htm") else build_pdf)(target)
    else:
        build_pdf(HERE / "FiveAtlas-instructions.pdf")
        # the older copies at the repo root, rebuilt from the same list so no
        # copy of the instructions can drift out of date again
        build_pdf(HERE.parent / "INSTRUCTIONS.pdf")
        build_html(HERE.parent / "INSTRUCTIONS.html")

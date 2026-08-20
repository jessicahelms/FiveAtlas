# -*- coding: utf-8 -*-
"""FiveAtlas instructions sheet -- the one-page(ish) PDF handed to annotators.

    python docs/make_instructions.py            -> docs/FiveAtlas-instructions.pdf
    python docs/make_instructions.py OUT.pdf    -> wherever you say

Plain prose, one numbered list, same voice throughout. Needs reportlab
(`pip install reportlab`); it is a doc tool, not part of the app.
"""
import sys
from pathlib import Path

from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import inch
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer

HERE = Path(__file__).resolve().parent
OUT = Path(sys.argv[1]) if len(sys.argv) > 1 else HERE / "FiveAtlas-instructions.pdf"

title_style = ParagraphStyle(
    "title", fontName="Helvetica-Bold", fontSize=11.5, leading=15, spaceAfter=14,
)
item_style = ParagraphStyle(
    "item", fontName="Helvetica", fontSize=10, leading=14.5,
    leftIndent=26, firstLineIndent=0, spaceAfter=9, bulletIndent=6,
)

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
    "only happens once. The fix that always works: press Cmd+Space, type "
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

    "Your data lives on the shared drive. On a Mac, connect to it in the Finder "
    "first (Go, Connect to Server). Then press Open Folder in FiveAtlas and "
    "click on the folder that contains the morphology_focus folder, "
    "morphology.ome.tiff, transcripts.zarr.zip, etc. All of these files should "
    "already be in one folder based on how Xenium exports their data. If there "
    "is a .geojson file in that folder, it will load in automatically. If you "
    "want to load in a different one, you can scroll down on the sidebar and "
    "press “Load regions file” — it takes GeoJSON, or a CSV of vertices (one row per vertex with name,x,y columns; Xenium's micron column names are converted automatically). This may take a few seconds, and if it "
    "does not pop up, look at your task bar (Windows) or your browser (Mac) for "
    "a new tab! If you prefer to paste a path on a Mac, it must start with "
    "/Volumes/… (not smb://…). Your edits, the dated backup from every Save, "
    "and the log file are kept in ~/Library/Application Support/FiveAtlas on a "
    "Mac (in Finder press Shift+Cmd+G and paste that path) — your original "
    "files are never changed.",

    "If you are ever unsure about how to use certain features, there are "
    "instructions that pop up after you click on a feature. The sidebar can be "
    "made wider or narrower by dragging the thin strip between it and the "
    "image. Every section title has a little arrow — click it to fold that "
    "section away and the sidebar stays short; click again to bring it back. "
    "The gear button at the top opens Settings for a bigger or smaller text "
    "size. When a newer FiveAtlas is on the download page, a line at the top "
    "of the sidebar says so and links to it — updating is always your choice, "
    "nothing downloads itself.",

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

    "All of your regions are listed in the sidebar. Click one to select it. "
    "Right click a region (two-finger click on a Mac trackpad), either in the "
    "list or on the image, to rename it, change its colour, or delete it. If a "
    "name appears twice with “part 1/2” beside it, that is one region "
    "made of two separate pieces, and anything you do applies to both.",

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

    # -- merge -----------------------------------------------------------------------------
    "Merge into one truly dissolves the border between the regions you picked: "
    "the shared line disappears and they become a single region — even "
    "when a hairline gap ran along the old border, it is sealed. Pieces that "
    "genuinely do not touch stay as separate parts of the one region; the app "
    "never invents tissue between them.",

    "Edit points reshapes a region by hand. Select the region, then drag the "
    "points along its outline. Tick Proportional editing underneath and the "
    "nearby points will follow the one you are dragging, and you can scroll "
    "your mouse wheel while dragging to change how much of the outline comes "
    "with it.",

    "Edit shared borders works on the line between two regions. Click two "
    "regions that touch and you can drag the border between them, and both "
    "sides update together so you never open up a gap. A drag may also reach "
    "PAST the section outline — the region grows into the empty ground "
    "beyond it (rebuild “hemi” afterwards if you want the outline to "
    "catch up). Other regions' territory is never taken this way.",

    "Pick two or more regions and press Share borders to tidy up all the "
    "boundaries at once: small gaps get filled, overlaps get removed, and "
    "every pair ends up with one shared edge. Merge into one instead combines "
    "everything you picked into a single region.",

    "Once you have shared borders, a Resample points slider appears. Drag it "
    "from fine to coarse and the points on the border change as you go, then "
    "press Apply to keep them or Cancel to put the old ones back. This is "
    "useful when a border has far more points on it than you can work with.",

    "Split a region cuts one region into two. Select the region, then draw a "
    "line all the way across it on the image, clicking for each point, and "
    "right click (or press Enter) to finish. Press Esc to throw the line away "
    "and start again.",

    "Add a new region or damage lets you draw a region from scratch. Choose "
    "“Anatomical region” under “What are you drawing?”, "
    "then trace the outline on the image, clicking for each point, then right "
    "click or Enter to finish and Esc to cancel. Whatever you draw over is "
    "taken out of the region underneath.",

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

    "Snap neighbors is for after you have moved a region by hand. It makes "
    "the neighbouring regions line up with the one you changed, filling in "
    "anywhere it moved away from and giving up anywhere it moved into.",

    # -- orientation -----------------------------------------------------------------------------
    "Orientation is for a slide that was imaged upside down or mirrored. "
    "Rotate or flip the whole thing — image and regions move together — "
    "and work the way round that makes sense. When you export, you choose "
    "whether the file keeps the displayed orientation or is turned back to "
    "the as-imaged one.",

    "Undo is Ctrl+Z (Cmd+Z on a Mac) and redo is Ctrl+Y (Cmd+Shift+Z on a "
    "Mac), and they work for everything above.",

    "Press Save whenever you want to keep your progress. Your original files "
    "are never changed, and every save keeps a dated copy so you can always "
    "go back to an earlier one. Restore original GeoJSON takes you back to "
    "the dataset’s own file — your current work is snapshotted "
    "first, so even that can be undone.",

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


def build(out: Path):
    doc = SimpleDocTemplate(
        str(out), pagesize=letter,
        leftMargin=0.9 * inch, rightMargin=0.9 * inch,
        topMargin=0.85 * inch, bottomMargin=0.85 * inch,
        title="FiveAtlas — Instructions", author="FiveAtlas",
    )
    story = [Paragraph("FiveAtlas — Instructions", title_style), Spacer(1, 2)]
    for i, txt in enumerate(ITEMS, 1):
        story.append(Paragraph(txt, item_style, bulletText=f"{i}."))
    doc.build(story)
    print("wrote", out, f"({len(ITEMS)} items)")


if __name__ == "__main__":
    build(OUT)

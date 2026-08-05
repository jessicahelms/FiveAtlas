# FiveAtlas — install & run (for colleagues)

A local web app to view Xenium spatial data at high resolution and **edit the
GeoJSONs that segment brain regions** — drag shared borders, share/tile borders
between neighbours, merge, split, and proportional (Blender-style) vertex editing.

Everything runs on your own machine. **Your data never leaves your computer.**

---

## Requirements

- **Windows**, or **macOS 11+** (see [macOS](#macos) below)
- **Python 3.10–3.12** — install from <https://www.python.org/downloads/> and tick
  *"Add python.exe to PATH"* during install.

The user interface is already built and bundled, so you do **not** need Node.js.

If you were sent a packaged build (`FiveAtlas-…-setup.exe` on Windows, or
`FiveAtlas-…-macos-….dmg` on a Mac) you do not need Python either — skip to the
platform section for your machine.

## First-time setup

1. Double-click **`setup.bat`** — creates a local Python environment and installs
   the dependencies (a few minutes the first time).

## Running

1. Double-click **`run.bat`** — starts the server and opens
   **http://localhost:8000** in your browser. Keep the black console window open
   while you work; close it to stop the app.
2. Click **📂 Open folder…** and choose a Xenium dataset folder — the one that
   contains `experiment.xenium`, the `morphology_focus` folder, `transcripts.zarr`,
   and your region GeoJSONs. The app auto-detects the pieces.

## macOS

FiveAtlas is the same app on a Mac — a local server plus your own browser — so
every feature below works identically. Only the install differs.

**From a packaged `.dmg`:**

1. Open the `.dmg` and drag **FiveAtlas** into **Applications**.
2. **First launch only**, macOS will refuse to open it — "FiveAtlas cannot be
   opened because the developer cannot be verified", or "FiveAtlas is damaged".
   This is Gatekeeper reacting to an app that did not come from the App Store,
   not a problem with the app. Either **right-click FiveAtlas → Open → Open**, or
   run this once in Terminal:

   ```bash
   xattr -dr com.apple.quarantine /Applications/FiveAtlas.app
   ```

3. Double-click FiveAtlas. It has no window of its own: it starts the local
   server and opens your browser. **Quit it from the Dock** when you are done.

Pick the download that matches your Mac — `arm64` for Apple Silicon (M1 and
later), `x86_64` for an Intel Mac. An arm64 build will not start on an Intel Mac.

Edits, the opened-dataset list and `FiveAtlas.log` live in
`~/Library/Application Support/FiveAtlas`. Your original files are never touched.

**From source** (Mac or Linux), instead of `setup.bat` / `run.bat`:

```bash
cd atlas_editor && ./run_dev.sh
```

**Building the `.dmg`** — this must happen *on* a Mac, and on the same CPU
architecture you are shipping to, because PyInstaller cannot cross-compile:

```bash
cd atlas_editor && ./build_release_mac.sh --version 0.2.0
```

No Mac to hand? Push to GitHub and run the **Build FiveAtlas (macOS)** workflow
from the Actions tab — it builds both architectures on GitHub's Mac runners and
gives you the `.dmg` files as downloads.

## Editing regions — quick guide

- **Edit vertices** — select a region, drag its outline. Turn on **Proportional
  editing** so nearby vertices follow with a falloff (scroll to resize the radius
  while dragging).
- **Edit shared borders** — pick two regions to see and drag their shared border
  (both rebuild to share it). Pick several and **Share borders** to tile them, or
  **Merge into one** to combine them.
- **Split a region** — select it, then draw a line across it.
- **Undo / Redo** — buttons or `Ctrl+Z` / `Ctrl+Shift+Z`.
- **Save** writes an editable working copy; **Export** gives one merged `.geojson`
  or a `.zip` of per-region files. Loading a region set auto-cleans spikes /
  invalid polygons.

## Sharing with colleagues

Copy the whole `atlas_editor` folder to their machine (or a shared drive). They run
`setup.bat` once, then `run.bat`. Each person uses their own local data.

## Troubleshooting

- *"Python was not found"* → install Python and re-run `setup.bat`.
- Port 8000 in use → close other app/console windows first.
- Developers changing the UI: run `npm run build` in `frontend/` to refresh the
  bundled build (end users just use what's shipped).

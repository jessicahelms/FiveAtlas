# FiveAtlas — install & run (for colleagues)

A local web app to view Xenium spatial data at high resolution and **edit the
GeoJSONs that segment brain regions** — drag shared borders, share/tile borders
between neighbours, merge, split, and proportional (Blender-style) vertex editing.

Everything runs on your own machine. **Your data never leaves your computer.**

---

## Requirements

- **Windows**, or **macOS 11+ on Apple Silicon** (see [macOS](#macos) below)
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

0. It needs an **Apple Silicon** Mac (M1 or later) on **macOS 11 or newer**.
   There is no Intel build. Download the `.dmg` **on the Mac itself** — a copy
   that has been through a Windows machine or a shared drive can lose the
   symlinks and permissions the app needs and macOS will call it "damaged".
1. Open the `.dmg` and drag **FiveAtlas** into **Applications**, then eject the
   disk image. Always start it from Applications.
2. **First launch only**, macOS will refuse to open it — "FiveAtlas is damaged
   and can't be opened", or "Apple could not verify FiveAtlas is free of
   malware". This is Gatekeeper reacting to an app that did not come from the
   App Store, not a problem with the app. The fix that works on every macOS
   version, once — open Terminal (Cmd+Space, type `Terminal`) and run:

   ```bash
   xattr -dr com.apple.quarantine /Applications/FiveAtlas.app
   ```

   (On macOS 15 Sequoia you can instead use System Settings → Privacy &
   Security → scroll to "FiveAtlas was blocked" → **Open Anyway**. Right-click →
   Open no longer bypasses Gatekeeper there.)

3. Double-click FiveAtlas. It has no window of its own and no Dock icon: after a
   few seconds (up to a minute the first time, while macOS verifies it) a tab
   opens in your browser at <http://127.0.0.1:8050>. If no tab appears, type
   that address into your browser. **To stop it, just close the tab** — it
   stops on its own about ten minutes later.
4. Your data is on the lab share: connect to it in Finder first (Go → Connect to
   Server). Then **Open Folder** in FiveAtlas and pick the experiment folder
   under *Locations*. A pasted path must look like `/Volumes/…`, not `smb://…`.

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

No Mac to hand? Push a `v*` tag (or run the **Build FiveAtlas (macOS)** workflow
from the Actions tab) — it builds the Apple Silicon app on GitHub's Mac runner,
smoke-tests it (including a real JPEG2000 decode), and attaches the `.dmg`,
`.zip` and `SHA256SUMS` to a Release whose notes are the install steps above.

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

# FiveAtlas — install & run (for colleagues)

A local web app to view Xenium spatial data at high resolution and **edit the
GeoJSONs that segment brain regions** — drag shared borders, share/tile borders
between neighbours, merge, split, and proportional (Blender-style) vertex editing.

Everything runs on your own machine. **Your data never leaves your computer.**

---

## Download (the easy way)

**<https://github.com/jessicahelms/FiveAtlas/releases/latest>** — one page with
both downloads, always the newest version:

| You are on | Download |
|---|---|
| **Windows** 10 / 11 | `FiveAtlas-<version>-win64.zip` |
| **Mac** (Apple Silicon, macOS 11+) | `FiveAtlas-<version>-macos-arm64.dmg` |
| **How to use every feature** | `FiveAtlas-instructions.pdf` |

No Python and no Node.js needed for these. The sections below say how to
install each; the instructions PDF walks through every feature.

**Windows:** unzip, copy the **FiveAtlas** folder to your own hard drive (not a
network drive — it starts far faster locally), and double-click
`FiveAtlas.exe`. The first time, Windows may say "Windows protected your PC"
because the app is not code-signed — click **More info**, then **Run anyway**.
A black console window opens with the address and your browser opens on it;
keep that window open while you work and close it to stop the app.

**Mac:** see [macOS](#macos) below.

### Updating to a new version

**From 1.3.3 on, FiveAtlas updates itself.** When a newer version is out it
downloads in the background (the sidebar shows the progress), and a bar across
the top then says **"FiveAtlas … is ready"** with a **Restart to update**
button. Save your work and click it: FiveAtlas closes and the new version opens
in a new tab a few seconds later, from the same place it was installed. If you
don't click it, the switch happens the next time you open FiveAtlas.

It can't update itself when it has no permission to replace its own folder
(for example a Mac account that is not an administrator), or when it is run
from source. If an update can't be installed, the old version simply carries on
and the sidebar links to the download page — update by hand as below.

The version you are running is always shown in the top-left corner of the
window.

**By hand** (1.3.2 and older, or when the automatic update can't):

- **Windows:** unzip the new version and open its `FiveAtlas.exe`. Since 1.3.1
  the new version starts in its own tab even if the old one is still open;
  close the old black window when you are done with it.
- **Mac, updating from 1.3.0 or older:** quit the old FiveAtlas first — open
  **Activity Monitor**, search **FiveAtlas**, select it, press the stop button
  and choose **Quit** (or restart the Mac). Then drag the new FiveAtlas into
  Applications (**Replace**), run the Terminal line below once more, and open
  it. An old copy that is still running cannot be switched automatically:
  macOS wakes the running copy instead of starting the new one.
- **Mac, updating from 1.3.1 or newer:** drag the new FiveAtlas into
  Applications (**Replace**) and run the Terminal line once more. If the old
  one is still running, its tab says the new version has been installed and
  offers **Restart FiveAtlas**; with no tab open, it steps aside within a
  minute, so opening the app starts the new one.

---

## From source (developers)

### Requirements

- **Windows**, or **macOS 11+ on Apple Silicon**
- **Python 3.10–3.12** — install from <https://www.python.org/downloads/> and tick
  *"Add python.exe to PATH"* during install.

The user interface is already built and bundled, so you do **not** need Node.js.

### First-time setup

1. Double-click **`setup.bat`** — creates a local Python environment and installs
   the dependencies (a few minutes the first time).

### Running

1. Double-click **`run.bat`** — starts the server and opens
   **http://localhost:8000** in your browser. Keep the black console window open
   while you work; close it to stop the app.
2. Click **📂 Open folder…** and choose a Xenium dataset folder — the one that
   contains `experiment.xenium`, the `morphology_focus` folder, `transcripts.zarr`,
   and your region GeoJSONs. A screen lists every file the app can load from it;
   untick anything you don't want and press **Load**.

## macOS

FiveAtlas is the same app on a Mac — a local server plus your own browser — so
every feature works identically. Only the install differs.

**From a packaged `.dmg`:**

0. It needs an **Apple Silicon** Mac (M1 or later) on **macOS 11 or newer**.
   There is no Intel build. Download the `.dmg` **on the Mac itself** — a copy
   that has been through a Windows machine or a shared drive can lose the
   symlinks and permissions the app needs and macOS will call it "damaged".
1. Open the `.dmg` and drag **FiveAtlas** into **Applications** (choose
   **Replace** if an older one is there), then eject the disk image. Always
   start it from Applications.
2. **First launch of each download**, macOS will refuse to open it — "FiveAtlas
   is damaged and can't be opened", or "Apple could not verify FiveAtlas is free
   of malware". This is Gatekeeper reacting to an app that did not come from the
   App Store, not a problem with the app. The fix that works on every macOS
   version — open Terminal (Cmd+Space, type `Terminal`) and run:

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

Dated backups of every save, the opened-dataset list and `FiveAtlas.log` live in
`~/Library/Application Support/FiveAtlas` (on Windows, `%LOCALAPPDATA%\FiveAtlas`).
Your original files are never touched.

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
`.zip` and `SHA256SUMS` to a Release. The same tag builds the Windows zip on
GitHub's Windows runner and attaches it to the same Release, with the
instructions PDF. The release page opens with that version's section of
`CHANGELOG.md`, in big headings.

## Editing regions — quick guide

The full, step-by-step guide is `docs/FiveAtlas-instructions.pdf` (rebuild it
with `python docs/make_instructions.py` after changing a feature).

- **Save** (top right, beside undo/redo) — the first Save asks where to put the
  regions file; every Save after that updates the same file, and **(change)**
  picks another. The panel under Save shows exactly where it went (**Open
  folder**, **Copy path**). Original region files are never overwritten, and a
  dated backup of every save is kept. **● unsaved** means edits not on disk yet.
- **Edit points** — select a region, drag its points. **Right-click a point to
  delete it** (the border re-spaces, and a shared point goes from both regions);
  click the outline to add one. **Resample points** re-spaces the whole outline,
  keeping every shared border joined. Turn on **Proportional editing** so nearby
  points follow with a falloff (scroll to resize the radius while dragging).
- **Move region** — drag a whole region without changing its shape.
- **Edit shared borders** — pick two regions to see and drag their shared border
  (both rebuild to share it). Pick several and **Share borders** to tile them
  (**Snap reach** sets how wide a gap it bridges), or **Merge into one** to
  combine them.
- **Add a new region** — draw it; if it lands on another region, choose
  **Replace**, **Overlap** or **Fit around**. Right-click any region for **Bring
  to front** / **Send to back**.
- **Load regions file** (Files & export) — reads the file, then asks **Add to
  the map** or **Replace the map**; a file with `hemi` also asks whether to load
  its damage.
- **Split a region** — select it, then draw a line across it.
- **Undo / Redo** — buttons at the top right, or `Ctrl+Z` / `Ctrl+Y`
  (`Ctrl+Shift+Z` works too).
- **Export** gives one merged `.geojson`, a `.zip` of per-region files, or an
  AnnData `.h5ad`.

## Troubleshooting

- *"Python was not found"* (from source) → install Python and re-run `setup.bat`.
- The new version still looks like the old one → check the top-left corner of
  the window. If it shows an older number, or no number at all (1.3.0 and older
  do not show one), an old FiveAtlas is still running; quit it (see *Updating*
  above) and open the new one again.
- Port 8000 in use (from source) → close other app/console windows first.
- Developers changing the UI: run `npm run build` in `frontend/` to refresh the
  bundled build (end users just use what's shipped).

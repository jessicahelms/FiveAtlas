import { API } from './config';

export async function getInfo(ds, { noImagery = false } = {}) {
  const r = await fetch(`${API}/datasets/${ds}/info${noImagery ? '?noimg=1' : ''}`);
  if (!r.ok) throw new Error(`info ${r.status}`);
  return r.json();
}

export async function getRegions(ds) {
  const r = await fetch(`${API}/datasets/${ds}/regions`);
  if (!r.ok) throw new Error(`regions ${r.status}`);
  return r.json();
}

// Heartbeat. The packaged app (macOS especially) has no window of its own, so
// the server has no other way to know a UI is still attached; it stops itself
// after a long silence. Fire-and-forget -- a failed ping is not an error.
export function ping() {
  return fetch(`${API}/ping`, { method: 'POST' }).catch(() => {});
}

// Stop the server on purpose. The response comes back before it exits.
export async function quitApp() {
  const r = await fetch(`${API}/quit`, { method: 'POST' });
  if (!r.ok) throw new Error(`quit ${r.status}`);
  return r.json();
}

export async function saveRegions(ds, fc) {
  const r = await fetch(`${API}/datasets/${ds}/regions`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(fc),
  });
  if (!r.ok) throw new Error(`save ${r.status}`);
  return r.json();
}

export async function snap(ds, before, after, moved, exclude = null) {
  const r = await fetch(`${API}/datasets/${ds}/snap`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ before, after, moved, exclude }),
  });
  if (!r.ok) throw new Error(`snap ${r.status}: ${await r.text()}`);
  return r.json();
}

export function tileTemplate(ds) {
  return `${API}/datasets/${ds}/tiles/{z}/{x}/{y}.png`;
}

// The shared arc(s) between two explicitly-picked regions. Sends the current
// (possibly already-edited) FC so the arc reflects on-screen geometry. If they
// don't touch, the backend bridges the gap (or resolves an overlap) and returns
// an updated `fc` alongside the arc.
export async function sharedBorder(ds, regionA, regionB, fc, { bridge = false, tol = 40 } = {}) {
  const r = await fetch(`${API}/datasets/${ds}/regions/shared-border`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ regionA, regionB, fc, bridge, tol }),
  });
  if (!r.ok) throw new Error(`shared-border ${r.status}: ${await r.text()}`);
  return r.json(); // { regionA, regionB, arcs, bridged, gapPx, touching, fc? }
}

// Make a set of regions tile cleanly (fill small gaps, remove small overlaps,
// give every pair one shared border). Returns updated FC + shared borders.
export async function partitionRegions(ds, regions, fc, tol = 40) {
  const r = await fetch(`${API}/datasets/${ds}/regions/partition`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ regions, fc, tol }),
  });
  if (!r.ok) throw new Error(`${r.status}: ${await r.text()}`);
  return r.json(); // { type, features, borders }
}

// Thin the shared border between the picked regions. Returns the updated FC plus
// before/after vertex counts, so the client can show the effect before keeping it.
export async function resampleRegions(ds, regions, fc, tol = 150) {
  const r = await fetch(`${API}/datasets/${ds}/regions/resample`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ regions, fc, tol }),
  });
  if (!r.ok) throw new Error(`${r.status}: ${await r.text()}`);
  return r.json(); // { type, features, counts, tol }
}

// Combine several regions into one feature. Returns updated FC + the new name.
export async function mergeRegions(ds, regions, fc, name, tol = 40) {
  const r = await fetch(`${API}/datasets/${ds}/regions/merge`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ regions, fc, name, tol }),
  });
  if (!r.ok) throw new Error(`${r.status}: ${await r.text()}`);
  return r.json(); // { type, features, name }
}

// Cut one region into two along a drawn line. Returns updated FC + the two names.
export async function splitRegion(ds, region, fc, points, name) {
  const r = await fetch(`${API}/datasets/${ds}/regions/split`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ region, fc, points, name }),
  });
  if (!r.ok) throw new Error(`${r.status}: ${await r.text()}`);
  return r.json(); // { type, features, names:[a,b] }
}

// Find the leftover void under a clicked point and hand it to the region(s)
// around it. Returns the gap outline (for preview) AND the updated FC, so the
// client can show what it found and commit it without a second round trip.
export async function dissolveGap(ds, fc, point, tol = 40, exclude = null) {
  const r = await fetch(`${API}/datasets/${ds}/regions/dissolve-gap`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ fc, point, tol, exclude }),
  });
  if (!r.ok) throw new Error(`${r.status}: ${await r.text()}`);
  return r.json(); // { type, features, gap, area, kind, regions }
}

// Circle stray hairlines and remove them: sliver parts of regions and sliver
// voids between them, inside the traced loop. Returns the updated FC plus the
// report (removed/deleted/filled/freed), so preview and commit need one call.
export async function cleanLines(ds, fc, points, { width = 12, exclude = null } = {}) {
  const r = await fetch(`${API}/datasets/${ds}/regions/clean-lines`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ fc, points, width, exclude }),
  });
  if (!r.ok) throw new Error(`${r.status}: ${await r.text()}`);
  return r.json(); // { type, features, removed, deleted, filled, covered, freed, area }
}

// Open an arbitrary GeoJSON (by path) as the editable working copy.
export async function loadRegionsFile(ds, path) {
  const r = await fetch(`${API}/datasets/${ds}/regions/load`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ path }),
  });
  if (!r.ok) throw new Error(`load ${r.status}: ${await r.text()}`);
  return r.json();
}

// One or several region files combined into the working copy (the pre-load
// checklist's region rows).
export async function loadRegionsMulti(ds, paths) {
  const r = await fetch(`${API}/datasets/${ds}/regions/load-multi`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ paths }),
  });
  if (!r.ok) throw new Error(`load ${r.status}: ${await r.text()}`);
  return r.json();
}

export async function browseFile() {
  const r = await fetch(`${API}/browse-file`, { method: 'POST' });
  if (!r.ok) throw new Error(`browse ${r.status}`);
  return r.json(); // { path }
}

// Add a brand-new region from a drawn outline. carve=true (default) makes any
// region the outline covers cede that ground, so the file stays a clean partition.
// `damage` is a designation tag: the server names the shape <tag>.<n> and never
// carves, because damage sits inside its host and the host stays whole.
export async function addRegion(ds, fc, points, { name = null, carve = true, damage = null } = {}) {
  const r = await fetch(`${API}/datasets/${ds}/regions/add`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ fc, points, name, carve, damage }),
  });
  if (!r.ok) throw new Error(`${r.status}: ${await r.text()}`);
  return r.json(); // { type, features, name, area, ceded, damage, inside }
}

// Build the hemisection outline from the regions themselves. apply=false previews.
export async function sectionOutline(ds, body) {
  const r = await fetch(`${API}/datasets/${ds}/regions/outline`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body || {}),
  });
  if (!r.ok) throw new Error(`${r.status}: ${await r.text()}`);
  return r.json();
}

// One SmartSheet cell per region — the chips for its multi-select Damage dropdown.
export async function damageCells(ds, body) {
  const r = await fetch(`${API}/datasets/${ds}/damage/cells`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body || {}),
  });
  if (!r.ok) throw new Error(`${r.status}: ${await r.text()}`);
  return r.json(); // { cells: [{region, done, tags, labels, values, text}], ... }
}

// --- the two per-sample YAMLs ---------------------------------------------------
// These live in the DATASET folder, which the rest of the app only ever reads, and
// they are shared between annotators. Hence: state first (what is there, and its
// exact path), preview second (a diff), save last, and creation is its own call
// that never happens by accident.

export async function notesState(ds) {
  const r = await fetch(`${API}/datasets/${ds}/notes`);
  if (!r.ok) throw new Error(`notes ${r.status}`);
  return r.json(); // { root, files: { notes, metadata } }
}

export async function notesPreview(ds, body) {
  const r = await fetch(`${API}/datasets/${ds}/notes/preview`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body || {}),
  });
  if (!r.ok) throw new Error(`${r.status}: ${await r.text()}`);
  return r.json();
}

export async function notesSave(ds, body) {
  const r = await fetch(`${API}/datasets/${ds}/notes/save`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body || {}),
  });
  if (!r.ok) throw new Error(`${r.status}: ${await r.text()}`);
  return r.json(); // { written: [...], skipped: [...] }
}

export async function notesCreate(ds, body) {
  const r = await fetch(`${API}/datasets/${ds}/notes/create`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body || {}),
  });
  if (!r.ok) throw new Error(`${r.status}: ${await r.text()}`);
  return r.json();
}

// Who edits are attributed to. The account is read-only — a typed name on its own
// is unverifiable, which is the point of a chain of custody.
export async function getIdentity() {
  const r = await fetch(`${API}/identity`);
  if (!r.ok) throw new Error(`identity ${r.status}`);
  return r.json(); // { name, account }
}

export async function setIdentity(name) {
  const r = await fetch(`${API}/identity`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ name }),
  });
  if (!r.ok) throw new Error(`identity ${r.status}`);
  return r.json();
}

// The damage vocabulary and the spellings that resolve to it. Static, so it is
// fetched once — not per dataset.
export async function designations() {
  const r = await fetch(`${API}/damage/designations`);
  if (!r.ok) throw new Error(`designations ${r.status}`);
  return r.json(); // { designations: [{tag,label,drawn}], aliases, enclave }
}

// Turn the gap under a point into a NEW region, rather than dissolving it into
// the surrounding regions.
export async function fillGap(ds, fc, point, { name = null, tol = 40, exclude = null } = {}) {
  const r = await fetch(`${API}/datasets/${ds}/regions/fill-gap`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ fc, point, name, tol, exclude }),
  });
  if (!r.ok) throw new Error(`${r.status}: ${await r.text()}`);
  return r.json(); // { type, features, name, area, kind, gap }
}

// Throw the working copy away and reload the dataset's own GeoJSON. The working
// copy is snapshotted first, so this can be undone from versions/.
export async function restoreOriginal(ds) {
  const r = await fetch(`${API}/datasets/${ds}/regions/restore-original`, { method: 'POST' });
  if (!r.ok) throw new Error(`${r.status}: ${await r.text()}`);
  return r.json(); // { type, features, source, backup, count }
}

// Rotate/flip the whole dataset -- image AND regions together, for a slide that
// was imaged the wrong way up. Returns the re-oriented FeatureCollection plus the
// new canvas size, so the client can refit the view.
export async function setOrientation(ds, o, fc) {
  const r = await fetch(`${API}/datasets/${ds}/orientation`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ ...o, fc }),
  });
  if (!r.ok) throw new Error(`${r.status}: ${await r.text()}`);
  return r.json(); // { type, features, _orientation, orientation, width, height }
}

// One TSV row per region for the tracking sheet, plus the damage-to-region
// assignment behind it so the user can check it before pasting. Read-only.
export async function smartsheetTsv(ds, body) {
  const r = await fetch(`${API}/datasets/${ds}/regions/smartsheet.tsv`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body || {}),
  });
  if (!r.ok) throw new Error(`${r.status}: ${await r.text()}`);
  return r.json(); // { tsv, columns, shapes, unassigned, regions, designations }
}

// Every snapshot Save has written, newest first.
export async function listVersions(ds) {
  const r = await fetch(`${API}/datasets/${ds}/regions/versions`);
  if (!r.ok) throw new Error(`versions ${r.status}`);
  return r.json(); // { versions: [{path, name, bytes, saved}] }
}

// Check the regions for self-intersections, empties, missing names, overlaps.
// Read-only: nothing is modified.
export async function validateRegions(ds, fc, exclude = null) {
  const r = await fetch(`${API}/datasets/${ds}/regions/validate`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ fc, exclude }),
  });
  if (!r.ok) throw new Error(`validate ${r.status}: ${await r.text()}`);
  return r.json(); // { problems, counts, ok }
}

// Heal invalid/empty polygons. Geometry only — names are never touched.
export async function repairRegions(ds, fc) {
  const r = await fetch(`${API}/datasets/${ds}/regions/repair`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ fc }),
  });
  if (!r.ok) throw new Error(`repair ${r.status}: ${await r.text()}`);
  return r.json(); // { type, features, fixed }
}

// Thrown when export is blocked by geometry errors; carries the problem list.
export class GeometryError extends Error {
  constructor(payload) {
    super(payload.message || 'the regions have geometry errors');
    this.name = 'GeometryError';
    this.problems = payload.problems || [];
    this.counts = payload.counts || {};
  }
}

// Export as one merged .geojson or a .zip of separate per-region files; the
// response is a file the browser downloads. Broken geometry blocks the write
// unless `force` is set. `frame` is 'displayed' (the rotated/flipped coordinates
// on screen) or 'original' (everything rotated back to the as-imaged frame).
export async function exportRegions(ds, mode, fc, { force = false, frame = 'displayed' } = {}) {
  const r = await fetch(`${API}/datasets/${ds}/regions/export`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ mode, fc, force, frame }),
  });
  if (r.status === 422) {
    const text = await r.text();
    try {
      // FastAPI wraps our JSON payload in {"detail": "<json string>"}
      const detail = JSON.parse(text).detail;
      throw new GeometryError(typeof detail === 'string' ? JSON.parse(detail) : detail);
    } catch (e) {
      if (e instanceof GeometryError) throw e;
      throw new Error(`export 422: ${text}`);
    }
  }
  if (!r.ok) throw new Error(`export ${r.status}: ${await r.text()}`);
  const blob = await r.blob();
  const cd = r.headers.get('Content-Disposition') || '';
  const m = cd.match(/filename="([^"]+)"/);
  const name = m ? m[1] : (mode === 'separate' ? 'regions_separate.zip' : 'regions_merged.geojson');
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url; a.download = name; a.rel = 'noopener';
  document.body.appendChild(a); a.click(); a.remove();
  // Do NOT revoke synchronously. Chrome has consumed the blob by the time click()
  // returns; Safari starts the download asynchronously, so revoking on the next
  // line pulls the blob out from under it. Safari is also inconsistent about
  // honouring `download` on a blob: URL -- when it doesn't, it NAVIGATES to the
  // blob instead, which opens a tab. Hold the URL for a minute, then release it.
  setTimeout(() => URL.revokeObjectURL(url), 60_000);
  return name;
}

// Locally reshape the shared border: only the sliver swept between this drag's
// starting arc (`dragStart`) and the dragged `points` changes hands.
export async function moveBorder(ds, fc, regionA, regionB, points, dragStart) {
  const r = await fetch(`${API}/datasets/${ds}/regions/move-border`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ fc, regionA, regionB, points, dragStart, orig: dragStart }),
  });
  if (!r.ok) throw new Error(`${r.status}: ${await r.text()}`);
  return r.json(); // updated FeatureCollection
}

export async function getGenes(ds) {
  const r = await fetch(`${API}/datasets/${ds}/genes`);
  if (!r.ok) throw new Error(`genes ${r.status}`);
  return r.json();
}

export async function geneContrast(ds, gene) {
  const r = await fetch(`${API}/datasets/${ds}/genes/contrast?gene=${encodeURIComponent(gene)}`);
  if (!r.ok) return null;
  return r.json();
}

// POST channel spec -> composited RGBA PNG -> ImageBitmap for a BitmapLayer.
// Regions x genes counts as an AnnData .h5ad (needs transcripts.zarr).
export async function exportAnnData(ds, fc) {
  const r = await fetch(`${API}/datasets/${ds}/regions/export-anndata`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ fc }),
  });
  if (!r.ok) throw new Error(`${r.status}: ${await r.text()}`);
  return r.blob();
}

export async function updateCheck() {
  const r = await fetch(`${API}/update-check`);
  if (!r.ok) throw new Error(`update-check ${r.status}`);
  return r.json(); // { current, latest?, url?, newer?, error? }
}

export async function compositeBitmap(ds, spec, { mode = 'glow', binUm = null, palette = null } = {}) {
  const r = await fetch(`${API}/datasets/${ds}/genes/composite.png`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ channels: spec, mode, binUm, palette }),
  });
  if (!r.ok) throw new Error(`composite ${r.status}`);
  const blob = await r.blob();
  return createImageBitmap(blob);
}

// ---- datasets / folder loading ----
export async function listDatasets() {
  const r = await fetch(`${API}/datasets`);
  if (!r.ok) throw new Error(`datasets ${r.status}`);
  return r.json();
}

export async function browseFolder() {
  const r = await fetch(`${API}/browse`, { method: 'POST' });
  if (!r.ok) throw new Error(`browse ${r.status}`);
  return r.json(); // { path }
}

export async function openDataset(path) {
  const r = await fetch(`${API}/datasets/open`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ path }),
  });
  if (!r.ok) throw new Error(`open ${r.status}: ${await r.text()}`);
  return r.json(); // { id, label, sources, pixelSizeUm }
}

export async function getSources(ds) {
  const r = await fetch(`${API}/datasets/${ds}/sources`);
  if (!r.ok) throw new Error(`sources ${r.status}`);
  return r.json();
}

// ---- morphology_focus stains ----
export async function getStains(ds) {
  const r = await fetch(`${API}/datasets/${ds}/stains`);
  if (!r.ok) return null; // dataset may have no morphology_focus
  return r.json();
}

export async function stainCompositeBitmap(ds, spec) {
  const r = await fetch(`${API}/datasets/${ds}/stains/composite.png`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(spec),
  });
  if (r.status === 202) return 'preparing';
  if (!r.ok) throw new Error(`stains ${r.status}`);
  return createImageBitmap(await r.blob());
}

export async function stainContrast(ds, idx) {
  const r = await fetch(`${API}/datasets/${ds}/stains/contrast?idx=${idx}`);
  if (r.status === 202) return null; // still decoding
  if (!r.ok) throw new Error(`stain contrast ${r.status}`);
  return r.json();
}

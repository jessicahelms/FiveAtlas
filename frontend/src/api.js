import { API } from './config';

export async function getInfo(ds) {
  const r = await fetch(`${API}/datasets/${ds}/info`);
  if (!r.ok) throw new Error(`info ${r.status}`);
  return r.json();
}

export async function getRegions(ds) {
  const r = await fetch(`${API}/datasets/${ds}/regions`);
  if (!r.ok) throw new Error(`regions ${r.status}`);
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

export async function snap(ds, before, after, moved) {
  const r = await fetch(`${API}/datasets/${ds}/snap`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ before, after, moved }),
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
export async function mergeRegions(ds, regions, fc, name) {
  const r = await fetch(`${API}/datasets/${ds}/regions/merge`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ regions, fc, name }),
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
export async function dissolveGap(ds, fc, point, tol = 40) {
  const r = await fetch(`${API}/datasets/${ds}/regions/dissolve-gap`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ fc, point, tol }),
  });
  if (!r.ok) throw new Error(`${r.status}: ${await r.text()}`);
  return r.json(); // { type, features, gap, area, kind, regions }
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

export async function browseFile() {
  const r = await fetch(`${API}/browse-file`, { method: 'POST' });
  if (!r.ok) throw new Error(`browse ${r.status}`);
  return r.json(); // { path }
}

// Add a brand-new region from a drawn outline. carve=true (default) makes any
// region the outline covers cede that ground, so the file stays a clean partition.
export async function addRegion(ds, fc, points, { name = null, carve = true } = {}) {
  const r = await fetch(`${API}/datasets/${ds}/regions/add`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ fc, points, name, carve }),
  });
  if (!r.ok) throw new Error(`${r.status}: ${await r.text()}`);
  return r.json(); // { type, features, name, area, ceded }
}

// Turn the gap under a point into a NEW region, rather than dissolving it into
// the surrounding regions.
export async function fillGap(ds, fc, point, { name = null, tol = 40 } = {}) {
  const r = await fetch(`${API}/datasets/${ds}/regions/fill-gap`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ fc, point, name, tol }),
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

// Every snapshot Save has written, newest first.
export async function listVersions(ds) {
  const r = await fetch(`${API}/datasets/${ds}/regions/versions`);
  if (!r.ok) throw new Error(`versions ${r.status}`);
  return r.json(); // { versions: [{path, name, bytes, saved}] }
}

// Check the regions for self-intersections, empties, missing names, overlaps.
// Read-only: nothing is modified.
export async function validateRegions(ds, fc) {
  const r = await fetch(`${API}/datasets/${ds}/regions/validate`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ fc }),
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
// unless `force` is set.
export async function exportRegions(ds, mode, fc, { force = false } = {}) {
  const r = await fetch(`${API}/datasets/${ds}/regions/export`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ mode, fc, force }),
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
export async function compositeBitmap(ds, spec) {
  const r = await fetch(`${API}/datasets/${ds}/genes/composite.png`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(spec),
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

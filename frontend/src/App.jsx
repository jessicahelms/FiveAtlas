import { useEffect, useState, useCallback, useRef, useMemo } from 'react';
import Viewer, { regionRgb, rgbToPacked } from './Viewer';
import Sidebar from './Sidebar';
import ChannelPanel from './ChannelPanel';
import DatasetBar from './DatasetBar';
import * as api from './api';

const MOVE_EDITS = new Set([
  'addPosition', 'removePosition', 'movePosition', 'finishMovePosition',
  'translating', 'translated', 'rotating', 'rotated', 'scaling', 'scaled',
]);
// edit types that should land in the undo history (not every mid-drag frame)
const COMMIT_EDITS = new Set(['finishMovePosition', 'addPosition', 'removePosition',
  'translated', 'rotated', 'scaled']);

const DEFAULT_LAYERS = {
  showDapi: true, showGenes: true, showRegions: true, geneOpacity: 1, stainOpacity: 1,
};

const GENE_PALETTE = [
  [255, 64, 64], [80, 220, 80], [90, 130, 255], [255, 215, 60],
  [255, 110, 245], [70, 235, 225], [255, 150, 60], [180, 120, 255],
];

// polyline length, for picking the main arc when a pair shares several
function polyLen(pts) {
  let s = 0;
  for (let i = 1; i < pts.length; i++) {
    s += Math.hypot(pts[i][0] - pts[i - 1][0], pts[i][1] - pts[i - 1][1]);
  }
  return s;
}

function normalizeBorderArcs(arcs) {
  return (arcs || [])
    .filter((arc) => arc && arc.length >= 2)
    .map((arc) => arc.map((p) => [+p[0], +p[1]]))
    .sort((a, b) => polyLen(b) - polyLen(a));
}

function pointDist(a, b) {
  if (!a || !b) return Infinity;
  return Math.hypot(a[0] - b[0], a[1] - b[1]);
}

function midpointSample(pts) {
  return pts && pts.length ? pts[Math.floor((pts.length - 1) / 2)] : null;
}

function arcMatchDistance(candidate, target) {
  if (!candidate || !target || candidate.length < 2 || target.length < 2) return Infinity;
  const c0 = candidate[0];
  const c1 = candidate[candidate.length - 1];
  const t0 = target[0];
  const t1 = target[target.length - 1];
  const endpoints = Math.min(
    pointDist(c0, t0) + pointDist(c1, t1),
    pointDist(c0, t1) + pointDist(c1, t0),
  );
  return endpoints + pointDist(midpointSample(candidate), midpointSample(target));
}

function closestArcIndex(arcs, target) {
  if (!arcs || !arcs.length) return 0;
  let best = 0;
  let bestDist = Infinity;
  arcs.forEach((arc, i) => {
    const d = arcMatchDistance(arc, target);
    if (d < bestDist) { best = i; bestDist = d; }
  });
  return best;
}

function borderArcsFc(arcs) {
  return {
    type: 'FeatureCollection',
    features: (arcs || []).map((arc, i) => ({ type: 'Feature', properties: { _border: true, _segment: i },
      geometry: { type: 'LineString', coordinates: arc.map((p) => [+p[0], +p[1]]) } })),
  };
}

// Endpoints hand back a fresh feature list, and rebuilding { type, features } by
// hand quietly drops the FeatureCollection's OWN members -- above all
// `_provenance`, the edit trail that has to travel with the file through the
// hand-off chain. Every rebuild goes through here: the trail comes from the
// response when the server stamped one, otherwise from the collection we had.
// An edit the client makes without a round trip — rename, colour, delete — has no
// endpoint to stamp it, so it is stamped here, onto the collection, the moment it
// happens. Deferring it to the save would be too late for anything that reads the
// trail in between: the notes panel decides which regions are YOURS from it, and a
// region renamed to `bubble.1` is damage the moment it is renamed.
function stampFc(fc, who, action, detail, regions) {
  const entry = {
    t: new Date().toISOString().slice(0, 19),
    who: (who && who.name) || (who && who.account) || 'unknown',
    account: (who && who.account) || 'unknown',
    app: 'client',
    action,
  };
  if (detail) entry.detail = String(detail);
  if (regions && regions.length) entry.regions = regions.map(String);
  const trail = Array.isArray(fc && fc._provenance) ? fc._provenance : [];
  return { ...fc, _provenance: [...trail, entry] };
}

function asFc(res, prev) {
  // Carry every file-level member, not just the ones we can name: `_provenance`
  // (the edit trail) and `_orientation` (which frame the coordinates are in), and
  // whatever gets added later. Losing _orientation means an edit made while
  // rotated produces a file that no longer says it is rotated.
  const keep = (o) => Object.fromEntries(
    Object.entries(o || {}).filter(([k]) => k !== 'type' && k !== 'features'));
  return {
    ...keep(prev),
    ...keep(res),
    type: 'FeatureCollection',
    features: (res && res.features) || [],
  };
}

function copyArcCoords(coords) {
  return coords && coords.length ? coords.map((p) => [+p[0], +p[1]]) : null;
}

function arcChanged(a, b, eps = 0.25) {
  if (!a || !b || a.length !== b.length) return true;
  for (let i = 0; i < a.length; i++) {
    if (pointDist(a[i], b[i]) > eps) return true;
  }
  return false;
}

function closestPointOnSegment(pt, a, b) {
  const dx = b[0] - a[0];
  const dy = b[1] - a[1];
  const len2 = dx * dx + dy * dy;
  if (!len2) return { point: [a[0], a[1]], t: 0, dist: pointDist(pt, a) };
  const raw = ((pt[0] - a[0]) * dx + (pt[1] - a[1]) * dy) / len2;
  const t = Math.max(0, Math.min(1, raw));
  const point = [a[0] + dx * t, a[1] + dy * t];
  return { point, t, dist: pointDist(pt, point) };
}

function insertPointOnArc(arc, pt, minSpacing = 2) {
  if (!arc || arc.length < 2 || !pt) return { arc, inserted: false };
  let best = null;
  for (let i = 0; i < arc.length - 1; i++) {
    const cand = closestPointOnSegment(pt, arc[i], arc[i + 1]);
    if (!best || cand.dist < best.dist) best = { ...cand, index: i };
  }
  if (!best) return { arc, inserted: false };
  const a = arc[best.index];
  const b = arc[best.index + 1];
  if (pointDist(best.point, a) < minSpacing || pointDist(best.point, b) < minSpacing) {
    return { arc, inserted: false, nearExisting: true };
  }
  const next = arc.map((p) => [p[0], p[1]]);
  next.splice(best.index + 1, 0, best.point);
  return { arc: next, inserted: true, index: best.index + 1 };
}

function closestArcToPointIndex(arcs, pt) {
  if (!arcs || !arcs.length || !pt) return 0;
  let best = 0;
  let bestDist = Infinity;
  arcs.forEach((arc, i) => {
    for (let j = 0; j < arc.length - 1; j++) {
      const cand = closestPointOnSegment(pt, arc[j], arc[j + 1]);
      if (cand.dist < bestDist) { best = i; bestDist = cand.dist; }
    }
  });
  return best;
}

function featureName(feature, idProp = 'name') {
  const props = (feature && feature.properties) || {};
  const value = props[idProp] ?? props.name;
  return value == null ? '' : String(value);
}

function featureByName(fc, name, idProp = 'name') {
  return fc && fc.features && fc.features.find((f) => featureName(f, idProp) === String(name));
}

function hasMovedGeometryDelta(before, after, moved, idProp = 'name') {
  if (!before || !after || !moved || !moved.length) return false;
  return moved.some((name) => {
    const bf = featureByName(before, name, idProp);
    const af = featureByName(after, name, idProp);
    if (!bf || !af) return Boolean(bf || af);
    return JSON.stringify(bf.geometry) !== JSON.stringify(af.geometry);
  });
}

function findSnapBaseline(preferred, after, moved, history, idProp = 'name') {
  if (hasMovedGeometryDelta(preferred, after, moved, idProp)) return preferred;
  for (let i = (history || []).length - 2; i >= 0; i--) {
    if (hasMovedGeometryDelta(history[i], after, moved, idProp)) return history[i];
  }
  return preferred;
}

function renameFeatureProperties(feature, idProp, nextProps) {
  const current = (feature && feature.properties) || {};
  const props = { ...current, ...(nextProps || {}) };
  const oldName = featureName(feature, idProp);
  const rawName = props[idProp] ?? props.name;
  const newName = String(rawName == null ? '' : rawName).trim();
  if (!newName) throw new Error('Region name cannot be blank.');
  props[idProp] = newName;
  if (Object.prototype.hasOwnProperty.call(props, 'name')) props.name = newName;
  if (props.classification && typeof props.classification === 'object' && !Array.isArray(props.classification)) {
    props.classification = { ...props.classification, name: newName };
  }
  return { props, oldName, newName };
}

// FastAPI reports our refusal reasons as {"detail": "..."} inside the body text.
// Pull the reason out so the user sees "no gap there", not "422: {"detail":...}".
function apiDetail(e) {
  const msg = String(e).replace(/^Error:\s*/, '').replace(/^\d+:\s*/, '');
  try {
    const parsed = JSON.parse(msg);
    return parsed.detail || msg;
  } catch {
    return msg;
  }
}

function formatGapPx(value) {
  const n = Number(value);
  if (!Number.isFinite(n)) return 'a measurable gap';
  const places = n < 10 ? 1 : 0;
  return `${n.toFixed(places)} px`;
}

/* ==== vertex-count control DISABLED (paused per request) ====
// ---- client-side vertex-count control (arrow keys) ----
// Densify by splitting the longest edges (shape-preserving: new points lie on the
// existing outline, so the polygon is geometrically identical). Coarsen with
// Douglas-Peucker (keeps corners). Never redistributes, so shared-border bridging
// still works after adding points.
const samePt = (a, b) => a[0] === b[0] && a[1] === b[1];

function densifyRing(coords, n) {
  if (!coords || coords.length < 4) return coords;
  const closed = samePt(coords[0], coords[coords.length - 1]);
  const pts = closed ? coords.slice(0, -1) : coords.slice();
  let guard = 0;
  while (pts.length < n && guard++ < 100000) {
    let li = 0, ld = -1;
    for (let i = 0; i < pts.length; i++) {
      const a = pts[i], b = pts[(i + 1) % pts.length];
      const d = Math.hypot(b[0] - a[0], b[1] - a[1]);
      if (d > ld) { ld = d; li = i; }
    }
    const a = pts[li], b = pts[(li + 1) % pts.length];
    pts.splice(li + 1, 0, [(a[0] + b[0]) / 2, (a[1] + b[1]) / 2]);
  }
  pts.push(pts[0].slice());
  return pts;
}
function perpDist(p, a, b) {
  const dx = b[0] - a[0], dy = b[1] - a[1];
  const L = Math.hypot(dx, dy) || 1;
  return Math.abs((p[0] - a[0]) * dy - (p[1] - a[1]) * dx) / L;
}
function rdp(pts, eps) {
  if (pts.length < 3) return pts.slice();
  let dmax = 0, idx = 0;
  const a = pts[0], b = pts[pts.length - 1];
  for (let i = 1; i < pts.length - 1; i++) {
    const d = perpDist(pts[i], a, b);
    if (d > dmax) { dmax = d; idx = i; }
  }
  if (dmax > eps) {
    return rdp(pts.slice(0, idx + 1), eps).slice(0, -1).concat(rdp(pts.slice(idx), eps));
  }
  return [a, b];
}
function simplifyRing(coords, n) {
  if (!coords || coords.length < 6) return coords;
  const closed = samePt(coords[0], coords[coords.length - 1]);
  const pts = closed ? coords.slice(0, -1) : coords.slice();
  if (pts.length <= n) return coords;
  // split the ring at pts[0] and its farthest vertex, RDP both arcs; binary-search eps
  let fi = 1, fd = -1;
  for (let i = 1; i < pts.length; i++) {
    const d = Math.hypot(pts[i][0] - pts[0][0], pts[i][1] - pts[0][1]);
    if (d > fd) { fd = d; fi = i; }
  }
  const arc1 = pts.slice(0, fi + 1);
  const arc2 = pts.slice(fi).concat([pts[0]]);
  const simp = (eps) => rdp(arc1, eps).slice(0, -1).concat(rdp(arc2, eps).slice(0, -1));
  let lo = 0, hi = (fd || 1000) * 2, best = pts;
  for (let it = 0; it < 24; it++) {
    const mid = (lo + hi) / 2;
    const s = simp(mid);
    if (s.length > n) lo = mid; else { hi = mid; best = s; }
  }
  if (best.length < 4) return coords;   // refuse to collapse
  const out = best.slice();
  out.push(out[0].slice());
  return out;
}
function ringCount(coords) { return coords && coords.length ? coords.length - 1 : 0; }
function resampleRingTo(coords, n) {
  const cur = ringCount(coords);
  if (n > cur) return densifyRing(coords, n);
  if (n < cur) return simplifyRing(coords, n);
  return coords;
}
function exteriorCount(geom) {
  if (!geom) return 0;
  const polys = geom.type === 'MultiPolygon' ? geom.coordinates : [geom.coordinates];
  return polys.reduce((s, p) => s + Math.max(0, (p[0] ? p[0].length - 1 : 0)), 0);
}
function resampleGeom(geom, n) {
  const one = (poly) => poly.map((ring, ri) => resampleRingTo(ring, ri === 0 ? n : Math.max(6, Math.round(n / 2))));
  if (geom.type === 'MultiPolygon') return { type: 'MultiPolygon', coordinates: geom.coordinates.map(one) };
  return { type: 'Polygon', coordinates: one(geom.coordinates) };
}
==== end disabled vertex-count control ==== */

// ---- proportional editing ----
function smoothInfluence(t) {
  const x = 1 - t;
  return x * x * (3 - 2 * x);
}

function resolveRing(geom, posIdx) {
  if (!geom || !posIdx) return null;
  if (geom.type === 'Polygon') return { ring: geom.coordinates[posIdx[0]], vIdx: posIdx[1] };
  if (geom.type === 'MultiPolygon') return { ring: geom.coordinates[posIdx[0]][posIdx[1]], vIdx: posIdx[2] };
  return null;
}
function setRing(geom, posIdx, newRing) {
  if (geom.type === 'Polygon') {
    return { type: 'Polygon', coordinates: geom.coordinates.map((r, i) => (i === posIdx[0] ? newRing : r)) };
  }
  if (geom.type === 'MultiPolygon') {
    return { type: 'MultiPolygon', coordinates: geom.coordinates.map((poly, i) => (i === posIdx[0]
      ? poly.map((r, j) => (j === posIdx[1] ? newRing : r)) : poly)) };
  }
  return geom;
}
// nearest vertex of a (Multi)Polygon geometry to a point -> its ring + path
function nearestVertex(geom, pt) {
  let bd = Infinity, posIdx = null, ring = null, vIdx = -1;
  const scan = (r, prefix) => {
    for (let i = 0; i < r.length; i++) {
      const d = Math.hypot(r[i][0] - pt[0], r[i][1] - pt[1]);
      if (d < bd) { bd = d; posIdx = [...prefix, i]; ring = r; vIdx = i; }
    }
  };
  if (!geom) return null;
  if (geom.type === 'Polygon') geom.coordinates.forEach((r, ri) => scan(r, [ri]));
  else if (geom.type === 'MultiPolygon') geom.coordinates.forEach((poly, pi) => poly.forEach((r, ri) => scan(r, [pi, ri])));
  return posIdx ? { posIdx, ring, vIdx, dist: bd } : null;
}

// offset nearby vertices by distance from the dragged point, within radius R
function computeRing(base, o, delta, R) {
  const n = base.length;
  const closed = n > 1 && base[0][0] === base[n - 1][0] && base[0][1] === base[n - 1][1];
  const out = base.map((p) => {
    const d = Math.hypot(p[0] - o[0], p[1] - o[1]);
    const w = d >= R ? 0 : smoothInfluence(d / R);
    return [p[0] + delta[0] * w, p[1] + delta[1] * w];
  });
  if (closed) out[n - 1] = out[0].slice();
  return out;
}

export default function App() {
  const [datasets, setDatasets] = useState([]);
  const [dsId, setDsId] = useState(null);
  const [sources, setSources] = useState(null);
  const [dsBusy, setDsBusy] = useState(false);

  const [info, setInfo] = useState(null);
  const [fc, setFc] = useState(null);
  const [baseline, setBaseline] = useState(null);
  const idProp = (info && info.idProp) || 'name';
  const [selected, setSelected] = useState([]);
  const [mode, setMode] = useState('view');       // 'view' | 'modify' | 'border'
  const [moved, setMoved] = useState(() => new Set());
  // shared-border editing: an ordered set of picked regions + the draggable arc
  const [borderPicks, setBorderPicks] = useState([]);
  const [borderArc, setBorderArc] = useState(null);   // FC with one LineString
  const [borderSegments, setBorderSegments] = useState([]);
  const [borderSegmentIndex, setBorderSegmentIndex] = useState(0);
  const [borderMsg, setBorderMsg] = useState(null);
  const [borderShared, setBorderShared] = useState(false);   // true after a Share/tile -> hide the Share/Merge buttons (fine-tune phase)
  // split: a line the user draws across one region to cut it in two
  const [splitDraw, setSplitDraw] = useState({ type: 'FeatureCollection', features: [] });
  const [splitMsg, setSplitMsg] = useState(null);
  // dissolve: click a leftover void -> preview it, then hand it to its neighbours
  const [gapFind, setGapFind] = useState(null);   // { gap, area, kind, regions, fc }
  const [gapMsg, setGapMsg] = useState(null);
  // Regions switched off. `hemi` is the whole-hemisphere outline and covers every
  // other region, so with it in play nothing is ever outside a region and NO GAP
  // can be found — and Check geometry reports 22 overlaps that are all correct.
  // Switching a region off leaves it in the file, untouched; it is only ignored
  // by the operations that assume a clean partition, and hidden on the map.
  // UI settings: sidebar scale and which tools are shown. Hiding is DISPLAY
  // only -- a hidden tool's backend routes still exist; the button is just not
  // rendered for the person who never uses it.
  const [uiSettings, setUiSettings] = useState(() => {
    try {
      const v = JSON.parse(localStorage.getItem('fiveatlas.settings') || '{}');
      return { zoom: Number(v.zoom) || 1,
               collapsed: new Set(Array.isArray(v.collapsed) ? v.collapsed : []) };
    } catch (e) { return { zoom: 1, collapsed: new Set() }; }
  });
  const applyUiSettings = useCallback((next) => {
    setUiSettings(next);
    try {
      localStorage.setItem('fiveatlas.settings',
        JSON.stringify({ zoom: next.zoom, collapsed: [...next.collapsed] }));
    } catch (e) { /* ok */ }
    // Folding the Edit section away while inside one of its modes would leave
    // the mode running with no visible way out -- fall back to view.
    const editModes = new Set(['modify', 'border', 'split', 'draw', 'dissolve', 'clean']);
    setMode((m) => (editModes.has(m) && next.collapsed.has('edit') ? 'view' : m));
  }, []);

  // sidebar width, draggable via the splitter; remembered across sessions
  const [paneW, setPaneW] = useState(() => {
    try {
      const v = parseInt(localStorage.getItem('fiveatlas.paneW') || '', 10);
      if (Number.isFinite(v)) return Math.max(260, Math.min(720, v));
    } catch (e) { /* ok */ }
    return 340;
  });
  const [regionsOff, setRegionsOff] = useState(() => new Set());
  // Display-only per-region toggles. Unlike the eye (regionsOff), these change
  // NOTHING about operations -- a region with its face hidden still counts for
  // gap-finding, snapping and Check geometry. They exist so you can work over
  // a clean view: faces off to see the imagery and every border while filling
  // gaps; borders off to see a fill without its outline.
  const [bordersOff, setBordersOff] = useState(() => new Set());
  const [fillsOff, setFillsOff] = useState(() => new Set());
  const toggleBorderOff = useCallback((nm) => setBordersOff((prev) => {
    const n = new Set(prev); n.has(nm) ? n.delete(nm) : n.add(nm); return n;
  }), []);
  const toggleFillOff = useCallback((nm) => setFillsOff((prev) => {
    const n = new Set(prev); n.has(nm) ? n.delete(nm) : n.add(nm); return n;
  }), []);
  const setAllFaces = useCallback((show) => {
    if (show) { setFillsOff(new Set()); return; }
    setFillsOff(new Set((fcRef.current && fcRef.current.features || [])
      .map((f) => featureName(f, idProp)).filter(Boolean)));
  }, [idProp]);
  const regionsOffRef = useRef(regionsOff);
  regionsOffRef.current = regionsOff;
  const offList = useCallback(
    () => (regionsOffRef.current.size ? [...regionsOffRef.current] : null), []);
  const toggleRegionOff = useCallback((nm) => {
    setRegionsOff((prev) => {
      const next = new Set(prev);
      if (!next.delete(nm)) next.add(nm);
      return next;
    });
  }, []);

  // draw: an outline the user traces to create a brand-new region
  const [drawPoly, setDrawPoly] = useState({ type: 'FeatureCollection', features: [] });
  // clean: a loop traced around stray hairlines; the found slivers wait as a
  // preview until Apply, so nothing is removed sight-unseen
  const [cleanPoly, setCleanPoly] = useState({ type: 'FeatureCollection', features: [] });
  const [cleanFound, setCleanFound] = useState(null);   // { res, next }
  const [cleanMsg, setCleanMsg] = useState(null);
  const [drawMsg, setDrawMsg] = useState(null);
  // ...and WHAT it is: 'region', or a damage designation tag. Damage is an
  // ordinary region here — same file, same list, same editing — so this only
  // decides the name and whether the outline carves its host.
  const [drawKind, setDrawKind] = useState('region');
  const [designations, setDesignations] = useState(null);   // { designations, aliases }
  // A damage shape that reaches into a second region. Damage is recorded against
  // the ONE region holding most of it, so a straddling shape is a question for
  // the annotator — asked now, while they can still see what they drew.
  const [damageAsk, setDamageAsk] = useState(null);   // {name, candidates, dominant}
  // resample: thin the shared border after Share borders
  const [resampleTol, setResampleTol] = useState(150);
  const [resample, setResample] = useState(null);   // { counts, fc, tol }
  const resampleTimer = useRef(null);
  const resampleUndoRef = useRef(null);   // handles as they were before previewing
  const [snapInfo, setSnapInfo] = useState(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);

  // undo/redo: snapshots of fc. Refs are the source of truth; histTick re-renders.
  const historyRef = useRef([]);
  const histIdxRef = useRef(-1);
  const [histTick, setHistTick] = useState(0);
  // const resampleRef = useRef(null);   // vertex-count control disabled (paused)

  // proportional editing (drag one point, nearby points follow within a radius)
  const [propEdit, setPropEdit] = useState(false);
  const [propRadius, setPropRadius] = useState(400);
  const [propRing, setPropRing] = useState(null);   // {center, radius} drawn while dragging
  // right-click a region -> rename it
  const [regionMenu, setRegionMenu] = useState(null);   // {index, name, x, y}
  const [menuView, setMenuView] = useState('menu');     // 'menu' | 'rename' | 'delete'
  const [renameDraft, setRenameDraft] = useState('');
  // geometry check / blocked export
  const [geomReport, setGeomReport] = useState(null);   // {problems, counts, mode, title}
  const pedRef = useRef({ active: false });   // frozen base ring during a vertex drag
  const fcRef = useRef(fc);
  fcRef.current = fc;
  const propRadiusRef = useRef(propRadius);
  propRadiusRef.current = propRadius;
  const borderArcRef = useRef(borderArc);
  borderArcRef.current = borderArc;
  const borderSegmentsRef = useRef(borderSegments);
  borderSegmentsRef.current = borderSegments;
  const dragStartArcRef = useRef(null);    // current shown arc captured at this drag's start, not a persistent original
  const pendingBridgeRef = useRef(null);   // full-border FC from a pick; applied only on the first edit

  const currentBorderArcCoords = useCallback((segmentIndex = borderSegmentIndex) => {
    const features = borderArcRef.current && borderArcRef.current.features;
    const safeIndex = Math.max(0, Math.min(segmentIndex || 0, (features || []).length - 1));
    const cur = features && features[safeIndex] && features[safeIndex].geometry.coordinates;
    return copyArcCoords(cur);
  }, [borderSegmentIndex]);

  const stableBorderArcCoords = useCallback((segmentIndex = borderSegmentIndex) => {
    const segments = borderSegmentsRef.current || [];
    const safeIndex = Math.max(0, Math.min(segmentIndex || 0, Math.max(segments.length - 1, 0)));
    return copyArcCoords(segments[safeIndex]) || currentBorderArcCoords(safeIndex);
  }, [borderSegmentIndex, currentBorderArcCoords]);

  const replaceVisibleBorderArc = useCallback((coords, segmentIndex = borderSegmentIndex) => {
    const arc = copyArcCoords(coords);
    if (!arc) return false;
    const safeIndex = Math.max(0, segmentIndex || 0);
    setBorderArc((prev) => {
      const features = prev && prev.features && prev.features.length
        ? prev.features.slice()
        : borderArcsFc(borderSegments.length ? borderSegments : [arc]).features;
      features[safeIndex] = { type: 'Feature', properties: { _border: true, _segment: safeIndex },
        geometry: { type: 'LineString', coordinates: arc } };
      return { type: 'FeatureCollection', features };
    });
    setBorderSegments((prev) => (prev.length
      ? prev.map((seg, i) => (i === safeIndex ? arc : seg))
      : [arc]));
    return true;
  }, [borderSegmentIndex, borderSegments]);

  // genes (composite bitmap)
  const [geneInfo, setGeneInfo] = useState(null);
  const [channels, setChannels] = useState(null);
  const [geneBitmap, setGeneBitmap] = useState(null);
  // density rendering: additive "glow" or square-bin "ink" heatmap, and the
  // bin size in microns (10 = the grid's native resolution)
  const [geneMode, setGeneMode] = useState('glow');
  const [geneBin, setGeneBin] = useState(10);
  const [genePalette, setGenePalette] = useState('genes');
  const [geneBounds, setGeneBounds] = useState(null);
  // stains (morphology_focus, full-res tiled)
  const [stainInfo, setStainInfo] = useState(null);
  const [stainChannels, setStainChannels] = useState(null);

  const [layers, setLayers] = useState(DEFAULT_LAYERS);

  // ---- undo/redo history ----
  const record = useCallback((nextFc) => {
    const base = historyRef.current.slice(0, histIdxRef.current + 1);
    base.push(nextFc);
    while (base.length > 200) base.shift();
    historyRef.current = base;
    histIdxRef.current = base.length - 1;
    setHistTick((t) => t + 1);
  }, []);
  const commit = useCallback((nextFc) => { fcRef.current = nextFc; setFc(nextFc); record(nextFc); }, [record]);
  const initHistory = useCallback((fc0) => {
    historyRef.current = [fc0];
    histIdxRef.current = 0;
    setHistTick((t) => t + 1);
  }, []);
  const undo = useCallback(() => {
    if (histIdxRef.current <= 0) return;
    histIdxRef.current -= 1;
    const prevFc = historyRef.current[histIdxRef.current];
    fcRef.current = prevFc;
    setFc(prevFc);
    setBorderArc(null); setHistTick((t) => t + 1);
  }, []);
  const redo = useCallback(() => {
    if (histIdxRef.current >= historyRef.current.length - 1) return;
    histIdxRef.current += 1;
    const nextFc = historyRef.current[histIdxRef.current];
    fcRef.current = nextFc;
    setFc(nextFc);
    setBorderArc(null); setHistTick((t) => t + 1);
  }, []);
  const canUndo = histIdxRef.current > 0;
  const canRedo = histIdxRef.current < historyRef.current.length - 1;

  useEffect(() => {
    (async () => {
      try {
        const list = await api.listDatasets();
        setDatasets(list);
        setDsId((cur) => cur || (list[0] && list[0].id) || null);
      } catch (e) { /* ignore */ }
    })();
  }, []);

  // A newer release on GitHub? Checked once per app load, shown as a quiet
  // line in the sidebar. Never downloads anything by itself.
  const [updateInfo, setUpdateInfo] = useState(null);
  useEffect(() => {
    (async () => {
      try {
        const u = await api.updateCheck();
        if (u && u.newer) setUpdateInfo(u);
      } catch (e) { /* offline is fine */ }
    })();
  }, []);

  // Heartbeat: tells the server a UI is attached. On a Mac the app has no
  // window and nothing the Dock can quit, so the server stops itself once this
  // has been silent for ~10 minutes after the last tab closes (the browser
  // throttles a background tab's timers to about once a minute, which is
  // still plenty). One ping straight away so "a UI has connected" is true
  // from the first paint. This heartbeat IS how the app gets stopped -- the
  // Quit button it once fed was removed on request.
  useEffect(() => {
    api.ping();
    const t = setInterval(() => api.ping(), 15000);
    return () => clearInterval(t);
  }, []);

  // The damage vocabulary. Static and dataset-independent, so once is enough.
  // Null until it arrives — the draw panel then offers regions only, rather than
  // showing an empty damage list as though there were no designations.
  useEffect(() => {
    (async () => {
      try { setDesignations(await api.designations()); }
      catch (e) { /* the dropdown falls back to "anatomical region" only */ }
    })();
  }, []);

  useEffect(() => {
    if (!dsId) return;
    let cancel = false;
    (async () => {
      setInfo(null); setFc(null); setError(null);
      setGeneInfo(null); setChannels(null); setGeneBitmap(null); setGeneBounds(null);
      setStainInfo(null); setStainChannels(null);
      try {
        const i = await api.getInfo(dsId);
        if (cancel) return;
        let r = { type: 'FeatureCollection', features: [] };
        try { r = await api.getRegions(dsId); } catch (e) { /* none */ }
        let s = null;
        try { s = await api.getSources(dsId); } catch (e) { /* optional */ }
        if (cancel) return;
        fcRef.current = r;
        setInfo(i); setFc(r); setBaseline(r); setSources(s); initHistory(r);
        setSelected([]); setMoved(new Set()); setSnapInfo(null); setMode('view');
        setBorderPicks([]); setBorderArc(null); setBorderSegments([]); setBorderSegmentIndex(0); setBorderMsg(null);
        // Every panel that holds a report ABOUT a dataset has to go with it.
        // Left standing, each one still has live buttons that write the old
        // dataset's answer into the new dataset's regions.
        setResample(null); resampleUndoRef.current = null;
        setCellsReport(null); setDamageAsk(null); setOutlinePreview(null);
        setGeomReport(null); setRegionsOff(new Set());
        try {
          const g = await api.getGenes(dsId);
          if (!cancel && g && g.defaults) {
            setGeneInfo(g); setGeneBounds(g.bounds);
            setChannels(g.defaults.map((c) => ({
              gene: c.gene, color: c.color, min: c.min, max: c.max,
              dataMax: c.dataMax, visible: true,
            })));
          }
        } catch (e) { /* no genes */ }
        try {
          const st = await api.getStains(dsId);
          if (!cancel && st && st.channels && st.channels.length) {
            setStainInfo(st);
            setStainChannels(st.channels.map((c) => ({
              index: c.index, name: c.name, color: c.color,
              min: 0, max: 1000, dataMax: 65535, visible: true, contrastLoaded: false,
            })));
            setLayers((l) => ({ ...l, showDapi: false })); // stains include DAPI
            st.channels.forEach((c) => loadStainContrast(c.index));
          }
        } catch (e) { /* no stains */ }
      } catch (e) { if (!cancel) setError(String(e)); }
    })();
    return () => { cancel = true; };
  }, [dsId]);

  // debounced gene composite
  //
  // The orientation is a dependency even though it is not used in the body: the
  // server returns the composite already turned to match the bounds /genes
  // reports, so a rotation changes the IMAGE without changing `channels`. Keyed
  // on channels alone, the old un-rotated bitmap stayed and was drawn on the new
  // rotated bounds -- the ghost section.
  const geneOrientKey = info && info.orientation
    ? `${info.orientation.rot}${info.orientation.flipH ? 'h' : ''}${info.orientation.flipV ? 'v' : ''}`
    : '';
  useEffect(() => {
    if (!channels) return;
    let cancel = false;
    const t = setTimeout(async () => {
      try {
        const bmp = await api.compositeBitmap(dsId, channels,
          { mode: geneMode, binUm: geneBin, palette: genePalette });
        if (!cancel) setGeneBitmap(bmp);
      } catch (e) { /* ignore */ }
    }, 120);
    return () => { cancel = true; clearTimeout(t); };
  }, [channels, dsId, geneOrientKey, geneMode, geneBin, genePalette]);

  const loadStainContrast = useCallback(async (idx, tries = 0) => {
    try {
      const c = await api.stainContrast(dsId, idx);
      if (!c) { if (tries < 12) setTimeout(() => loadStainContrast(idx, tries + 1), 5000); return; }
      setStainChannels((chs) => chs && chs.map((ch) => (ch.index === idx
        ? { ...ch, min: c.min, max: c.max, dataMax: c.dataMax, contrastLoaded: true } : ch)));
    } catch (e) { /* ignore */ }
  }, [dsId]);

  const loadOpened = useCallback(async (path) => {
    setDsBusy(true); setError(null);
    try {
      const res = await api.openDataset(path);
      setDatasets(await api.listDatasets());
      setDsId(res.id);
    } catch (e) { setError(String(e)); } finally { setDsBusy(false); }
  }, []);

  const openFolder = useCallback(async () => {
    setDsBusy(true); setError(null);
    try {
      const { path } = await api.browseFolder();
      if (path) {
        const res = await api.openDataset(path);
        setDatasets(await api.listDatasets());
        setDsId(res.id);
      }
    } catch (e) { setError(String(e)); } finally { setDsBusy(false); }
  }, []);

  // Proportional editing: the library moved ONE vertex; re-derive every vertex in
  // that ring from the frozen pre-drag base, limited by the radius.
  const applyProportional = useCallback((updatedData, editContext, editType) => {
    const fi = editContext.featureIndexes[0];
    const posIdx = editContext.positionIndexes;
    const feat = updatedData.features[fi];
    if (!feat || !posIdx) return updatedData;
    const cur = resolveRing(feat.geometry, posIdx);
    if (!cur || !cur.ring) return updatedData;
    const newPos = cur.ring[cur.vIdx];
    const ped = pedRef.current;
    const key = fi + ':' + posIdx.join(',');
    if (!ped.active || ped.key !== key) {              // drag start -> freeze base
      const baseFeat = fcRef.current && fcRef.current.features[fi];
      const br = baseFeat && resolveRing(baseFeat.geometry, posIdx);
      if (!br || !br.ring) return updatedData;
      ped.active = true; ped.kind = 'region'; ped.key = key; ped.fi = fi; ped.posIdx = posIdx;
      ped.baseRing = br.ring.map((p) => [p[0], p[1]]);
      ped.origPos = ped.baseRing[br.vIdx].slice();
    }
    ped.lastDelta = [newPos[0] - ped.origPos[0], newPos[1] - ped.origPos[1]];
    setPropRing({ center: ped.origPos.slice(), radius: Math.max(1, propRadius) });
    const out = computeRing(ped.baseRing, ped.origPos, ped.lastDelta, Math.max(1, propRadius));
    const newGeom = setRing(feat.geometry, posIdx, out);
    const feats = updatedData.features.map((f, i) => (i === fi ? { ...f, geometry: newGeom } : f));
    if (editType === 'finishMovePosition') ped.active = false;
    return { ...updatedData, features: feats };
  }, [propRadius]);

  const onEdit = useCallback(({ updatedData, editType, editContext }) => {
    let data = updatedData;
    if (propEdit && (editType === 'movePosition' || editType === 'finishMovePosition')
        && editContext && editContext.featureIndexes && editContext.positionIndexes) {
      data = applyProportional(updatedData, editContext, editType);
    }
    fcRef.current = data;
    setFc(data);                              // live (every drag frame)
    if (MOVE_EDITS.has(editType)) {
      const idxs = (editContext && editContext.featureIndexes) || selected;
      setMoved((prev) => {
        const nn = new Set(prev);
        idxs.forEach((i) => {
          const f = data.features[i];
          const nm = featureName(f, idProp);
          if (nm) nn.add(nm);
        });
        return nn;
      });
    }
    if (COMMIT_EDITS.has(editType)) record(data);   // one undo step per edit
  }, [selected, record, propEdit, applyProportional, idProp]);

  // re-apply proportional movement with a new radius mid-drag
  const recomputeProportional = useCallback((radius) => {
    const ped = pedRef.current;
    if (!ped.active || !ped.baseRing || !ped.lastDelta) return;
    setPropRing({ center: ped.origPos.slice(), radius: Math.max(1, radius) });
    const out = computeRing(ped.baseRing, ped.origPos, ped.lastDelta, Math.max(1, radius));
    if (ped.kind === 'border') {   // reshape the shared-border arc live (regions rebuild on release)
      // pin the endpoints so the segment stays anchored to the two regions' outlines
      out[0] = ped.baseRing[0].slice();
      out[out.length - 1] = ped.baseRing[ped.baseRing.length - 1].slice();
      const seg = Math.max(0, ped.seg || 0);
      // rewrite ONLY the dragged segment — the other interrupted segments stay on screen
      setBorderArc((prev) => {
        const features = prev && prev.features && prev.features.length ? prev.features.slice() : [];
        features[seg] = { type: 'Feature', properties: { _border: true, _segment: seg },
          geometry: { type: 'LineString', coordinates: out } };
        return { type: 'FeatureCollection', features };
      });
      return;
    }
    if (!fcRef.current) return;
    const feat = fcRef.current.features[ped.fi];
    if (!feat) return;
    const newGeom = setRing(feat.geometry, ped.posIdx, out);
    const feats = fcRef.current.features.map((f, i) => (i === ped.fi ? { ...f, geometry: newGeom } : f));
    const nextFc = { ...fcRef.current, features: feats };
    fcRef.current = nextFc;
    setFc(nextFc);
  }, []);

  // scroll wheel resizes the proportional radius WHILE a vertex is being dragged
  // (otherwise the wheel zooms the map as usual)
  useEffect(() => {
    const el = document.querySelector('.canvas-wrap');
    if (!el) return undefined;
    const onWheel = (e) => {
      if (!propEdit || !pedRef.current.active) return;
      e.preventDefault(); e.stopPropagation();
      const factor = e.deltaY < 0 ? 1.1 : 1 / 1.1;
      const nr = Math.max(10, Math.min(8000, Math.round(propRadiusRef.current * factor)));
      setPropRadius(nr);
      recomputeProportional(nr);
    };
    el.addEventListener('wheel', onWheel, { passive: false, capture: true });
    return () => el.removeEventListener('wheel', onWheel, { capture: true });
  }, [propEdit, recomputeProportional]);

  const setProportionalEditing = useCallback((enabled) => {
    setPropEdit(Boolean(enabled));
    if (!enabled) { pedRef.current.active = false; setPropRing(null); }
  }, []);


  // Arm on GRAB (mouse-down on a vertex handle) so the wheel resizes the radius
  // immediately, rather than only after the first move. Base geometry is frozen on
  // the first actual move; before that, the wheel just resizes the ring.
  const onGrabVertex = useCallback((layerId, featureIndex = 0) => {
    const isBorder = layerId && String(layerId).includes('border-edit');
    // Capture the current visible segment for this one drag, so move-border only
    // swaps the sliver swept by this gesture.
    if (isBorder) {
      const safeIndex = Math.max(0, featureIndex || 0);
      setBorderSegmentIndex(safeIndex);
      dragStartArcRef.current = stableBorderArcCoords(safeIndex);
    }
    if (!propEdit) return;
    const ped = pedRef.current;
    ped.active = true;
    ped.kind = isBorder ? 'border' : 'region';
    ped.seg = isBorder ? Math.max(0, featureIndex || 0) : 0;
    ped.key = null; ped.bkey = null;    // force the base to re-freeze on the first move
  }, [propEdit, stableBorderArcCoords]);
  const onReleaseDrag = useCallback(() => {
    pedRef.current.active = false;
    setPropRing(null);      // the falloff circle only shows while a point is held
  }, []);

  const clearBorder = useCallback(() => {
    setBorderPicks([]); setBorderArc(null); setBorderMsg(null); setBorderShared(false);
    setBorderSegments([]); setBorderSegmentIndex(0);
    dragStartArcRef.current = null;
    pendingBridgeRef.current = null;
    // A resample preview belongs to the pair it was built for. Left behind, its
    // Apply button comes back to life the moment the NEXT pair is shared, and
    // applying it commits that earlier FeatureCollection over the current one —
    // discarding every edit since, across datasets if the panel survived one.
    setResample(null);
  }, []);
  const clearSplit = useCallback(() => {
    setSplitDraw({ type: 'FeatureCollection', features: [] }); setSplitMsg(null);
  }, []);
  const clearGap = useCallback(() => { setGapFind(null); setGapMsg(null); }, []);
  const clearDraw = useCallback(() => {
    setDrawPoly({ type: 'FeatureCollection', features: [] }); setDrawMsg(null);
    // An unanswered question falls back to the dominant region — the same shape
    // is listed again in the export review, so nothing is lost by leaving.
    setDamageAsk(null);
  }, []);
  const clearClean = useCallback(() => {
    setCleanPoly({ type: 'FeatureCollection', features: [] });
    setCleanFound(null); setCleanMsg(null);
  }, []);

  const onToggleModify = useCallback(() => {
    setMode((m) => (m === 'modify' ? 'view' : 'modify'));
    clearBorder(); clearSplit(); clearGap(); clearDraw(); clearClean();
  }, [clearBorder, clearSplit, clearGap, clearDraw, clearClean]);

  const onToggleBorder = useCallback(() => {
    setMode((m) => (m === 'border' ? 'view' : 'border'));
    setSelected([]); clearBorder(); clearSplit(); clearGap(); clearDraw(); clearClean();
  }, [clearBorder, clearSplit, clearGap, clearDraw, clearClean]);

  const onToggleSplit = useCallback(() => {
    setMode((m) => (m === 'split' ? 'view' : 'split'));
    clearBorder(); clearSplit(); clearGap(); clearDraw(); clearClean();
  }, [clearBorder, clearSplit, clearGap, clearDraw, clearClean]);

  const onToggleDissolve = useCallback(() => {
    setMode((m) => (m === 'dissolve' ? 'view' : 'dissolve'));
    setSelected([]); clearBorder(); clearSplit(); clearGap(); clearDraw(); clearClean();
  }, [clearBorder, clearSplit, clearGap, clearDraw, clearClean]);

  const onToggleDraw = useCallback(() => {
    setMode((m) => (m === 'draw' ? 'view' : 'draw'));
    setSelected([]); clearBorder(); clearSplit(); clearGap(); clearDraw(); clearClean();
  }, [clearBorder, clearSplit, clearGap, clearDraw, clearClean]);

  const onToggleClean = useCallback(() => {
    setMode((m) => (m === 'clean' ? 'view' : 'clean'));
    setSelected([]); clearBorder(); clearSplit(); clearGap(); clearDraw(); clearClean();
  }, [clearBorder, clearSplit, clearGap, clearDraw, clearClean]);

  // Border mode: clicking a region toggles it in the pick set. Exactly two picks
  // -> fetch (and if needed bridge) their shared border as a draggable arc.
  const pickBorderRegion = useCallback(async (nm) => {
    if (!nm) return;
    if (borderPicks.length === 2 && borderArcRef.current && borderPicks.includes(nm)) {
      setBorderMsg('Shared border stays selected - drag it, or Clear to pick another pair.');
      return;
    }
    setBorderShared(false);              // changing the pick set -> back to build phase
    pendingBridgeRef.current = null;
    dragStartArcRef.current = null;
    const has = borderPicks.includes(nm);
    const next = has ? borderPicks.filter((x) => x !== nm) : [...borderPicks, nm];
    setBorderPicks(next);
    setBorderArc(null); setBorderSegments([]); setBorderSegmentIndex(0);
    if (next.length === 2) {
      setBorderMsg('finding shared border…');
      try {
        // CHEAP detection (a slice of one region's outline) -- no partition, so
        // picking stays snappy. If the outlines are merely near each other, prompt
        // the user to Share borders before exposing a draggable shared edge.
        const res = await api.sharedBorder(dsId, next[0], next[1], fc, { bridge: false });
        // One region inside the other: the inner's outline IS the border.
        // The server sends it as the arcs; wire them up like any other border
        // and let its message explain what dragging will do. Only a nested
        // pair with no ring at all (degenerate geometry) still stops here.
        if (res && res.contained) {
          const ringArcs = normalizeBorderArcs(res.arcs);
          if (!ringArcs.length) {
            setBorderArc(null); setBorderSegments([]);
            setBorderMsg(res.message);
            return;
          }
          setBorderSegments(ringArcs); setBorderSegmentIndex(0);
          setBorderArc(borderArcsFc(ringArcs));
          setBorderMsg(res.message);
          return;
        }
        const arcs = normalizeBorderArcs(res && res.arcs);
        if (!arcs.length) {
          setBorderArc(null);
          const gap = formatGapPx(res && res.gapPx);
          setBorderMsg(res && res.touching === false
            ? `"${next[0]}" and "${next[1]}" are ${gap} apart - click Share borders first to tile the gap.`
            : `"${next[0]}" and "${next[1]}" aren't adjacent - no shared border to edit.`);
          return;
        }
        if (res && res.touching === false) {
          const gap = formatGapPx(res.gapPx);
          setBorderArc(null);
          setBorderMsg(`"${next[0]}" and "${next[1]}" are ${gap} apart - click Share borders first to make a shared edge, then drag it to fine-tune.`);
          return;
        }
        setBorderSegments(arcs); setBorderSegmentIndex(0); setBorderArc(borderArcsFc(arcs));
        const gapNote = res && Number(res.gapPx) > 0 ? ` (${formatGapPx(res.gapPx)} tolerance gap)` : '';
        const segNote = arcs.length > 1 ? ` ${arcs.length} interrupted segments shown.` : '';
        setBorderMsg(`Shared border detected${gapNote} - drag it to edit; both regions rebuild on release.${segNote}`);
      } catch (e) { setBorderMsg(String(e)); }
    } else if (next.length < 2) {
      setBorderSegments([]); setBorderSegmentIndex(0);
      setBorderMsg(next.length === 1
        ? 'Click another region to share a border — or keep picking to tile several.'
        : null);
    } else {
      setBorderSegments([]); setBorderSegmentIndex(0);
      setBorderMsg(`${next.length} regions picked — “Share borders” tiles them all; unpick to 2 to drag one border.`);
    }
  }, [borderPicks, dsId, fc, commit]);

  const applyMoveBorder = useCallback(async (coords, dragStart, segmentIndex = borderSegmentIndex) => {
    if (borderPicks.length !== 2 || !coords || coords.length < 2) return;
    const nextCoords = copyArcCoords(coords);
    const startCoords = copyArcCoords(dragStart);
    if (startCoords && !arcChanged(startCoords, nextCoords)) {
      dragStartArcRef.current = null;
      setBorderMsg('No border move detected - drag a point to edit, or Clear to pick another pair.');
      return;
    }
    const [a, b] = borderPicks;
    const workingFc = fcRef.current;
    if (!workingFc) return;
    setBusy(true); setError(null);
    try {
      // `dragStart` is the arc at the start of this gesture, so partial edits
      // work without anchoring future drags to an older border.
      const res = await api.moveBorder(dsId, workingFc, a, b, nextCoords, startCoords);
      commit(res);
      let segmentNote = '';
      let refreshed = false;
      try {
        const sb = await api.sharedBorder(dsId, a, b, res, { bridge: false });
        const arcs = normalizeBorderArcs(sb && sb.arcs);
        if (arcs.length) {
          const nextIndex = closestArcIndex(arcs, nextCoords);
          setBorderSegments(arcs);
          setBorderSegmentIndex(nextIndex);
          setBorderArc(borderArcsFc(arcs));
          segmentNote = arcs.length > 1 ? ` ${arcs.length} interrupted segments refreshed.` : '';
          refreshed = true;
        }
      } catch (refreshErr) { /* keep the drawn arc if the canonical refresh fails */ }
      if (!refreshed) {
        const nextSegments = borderSegments.length
          ? borderSegments.map((seg, i) => (i === segmentIndex ? nextCoords : seg))
          : [nextCoords];
        setBorderSegments(nextSegments);
        setBorderArc(borderArcsFc(nextSegments));
      }
      setMoved((prev) => { const n = new Set(prev); n.add(a); n.add(b); return n; });
      const segNote = segmentNote || (borderSegments.length > 1 ? ` ${borderSegments.length} interrupted segments shown.` : '');
      setBorderMsg(`Border moved ✓.${segNote} Drag again, or Clear to pick another pair.`);
    } catch (e) {
      setBorderMsg(`Couldn't move border: ${apiDetail(e)}`);
    } finally { setBusy(false); dragStartArcRef.current = null; }
  }, [dsId, borderPicks, borderSegmentIndex, borderSegments, commit]);

  const onAddBorderPoint = useCallback((pt, featureIndex = null) => {
    const segmentIndex = Number.isInteger(featureIndex)
      ? Math.max(0, featureIndex)
      : closestArcToPointIndex(borderSegments, pt);
    const cur = currentBorderArcCoords(segmentIndex);
    const res = insertPointOnArc(cur, pt);
    if (!res.inserted) {
      setBorderMsg(res.nearExisting
        ? 'There is already a point there - drag it to move the border.'
        : 'Right-click the red border to add a point.');
      return;
    }
    setBorderSegmentIndex(segmentIndex);
    replaceVisibleBorderArc(res.arc, segmentIndex);
    dragStartArcRef.current = null;
    setBorderMsg(`Point added (${res.arc.length} points). Drag it to move the shared border.`);
  }, [borderSegments, currentBorderArcCoords, replaceVisibleBorderArc]);

  const onBorderEdit = useCallback(({ updatedData, editType, editContext, featureIndexes }) => {
    let data = updatedData;
    const segmentIndex = Math.max(0,
      (editContext && editContext.featureIndexes && editContext.featureIndexes[0])
      ?? (featureIndexes && featureIndexes[0])
      ?? borderSegmentIndex);
    if (editType === 'removePosition') {
      dragStartArcRef.current = null;
      setBorderMsg('Point clicks are protected - drag points to move, or right-click the red border to add one.');
      return;
    }
    if (editType === 'addPosition') {
      if (!dragStartArcRef.current) dragStartArcRef.current = stableBorderArcCoords(segmentIndex);
      setBorderSegmentIndex(segmentIndex);
      const f = data.features[segmentIndex];
      const coords = f && f.geometry && f.geometry.coordinates;
      if (coords) replaceVisibleBorderArc(coords, segmentIndex);
      setBorderMsg(`Point added (${coords ? coords.length : 0} points). Drag it to move the shared border.`);
      return;
    }
    if ((editType === 'movePosition' || editType === 'finishMovePosition') && !dragStartArcRef.current) {
      dragStartArcRef.current = stableBorderArcCoords(segmentIndex);
    }
    // proportional editing on the shared-border arc: nearby arc vertices follow
    if (propEdit && (editType === 'movePosition' || editType === 'finishMovePosition')
        && editContext && editContext.positionIndexes && updatedData.features[segmentIndex]) {
      const coords = updatedData.features[segmentIndex].geometry.coordinates;
      const vi = editContext.positionIndexes[editContext.positionIndexes.length - 1];
      const newPos = coords[vi];
      const ped = pedRef.current;
      if (newPos && (!ped.active || ped.kind !== 'border' || ped.bkey !== vi)) {   // freeze base
        const base = borderArcRef.current && borderArcRef.current.features[segmentIndex]
          && borderArcRef.current.features[segmentIndex].geometry.coordinates;
        if (base && base[vi]) {
          ped.active = true; ped.kind = 'border'; ped.bkey = vi; ped.seg = segmentIndex;
          ped.baseRing = base.map((p) => [p[0], p[1]]);
          ped.origPos = ped.baseRing[vi].slice();
        }
      }
      if (newPos && ped.kind === 'border' && ped.baseRing && ped.origPos) {
        ped.lastDelta = [newPos[0] - ped.origPos[0], newPos[1] - ped.origPos[1]];
        setPropRing({ center: ped.origPos.slice(), radius: Math.max(1, propRadius) });
        const out = computeRing(ped.baseRing, ped.origPos, ped.lastDelta, Math.max(1, propRadius));
        // pin the arc endpoints so the border stays anchored to the two regions' outlines
        out[0] = ped.baseRing[0].slice();
        out[out.length - 1] = ped.baseRing[ped.baseRing.length - 1].slice();
        const features = updatedData.features.slice();
        features[segmentIndex] = { type: 'Feature', properties: { _border: true, _segment: segmentIndex },
          geometry: { type: 'LineString', coordinates: out } };
        data = { type: 'FeatureCollection', features };
      }
    }
    const dragStartArc = dragStartArcRef.current || stableBorderArcCoords(segmentIndex);
    setBorderSegmentIndex(segmentIndex);
    setBorderArc(data);
    if (editType === 'finishMovePosition' || editType === 'addPosition' || editType === 'removePosition') {
      pedRef.current.active = false;
      const f = data.features[segmentIndex];
      if (f && f.geometry && f.geometry.coordinates) applyMoveBorder(f.geometry.coordinates, dragStartArc, segmentIndex);
    }
  }, [propEdit, propRadius, applyMoveBorder, currentBorderArcCoords, replaceVisibleBorderArc, stableBorderArcCoords, borderSegmentIndex]);

  const doShareBorders = useCallback(async () => {
    if (borderPicks.length < 2) return;
    setBusy(true); setError(null);
    try {
      const res = await api.partitionRegions(dsId, borderPicks, fc);
      let newFc = asFc(res, fc);

      // Sharing leaves a Voronoi-dense, unevenly spaced border, so even it out
      // straight away rather than making the user reach for the slider. The
      // slider is still there to go coarser or finer afterwards.
      let repointed = null;
      try {
        const rs = await api.resampleRegions(dsId, borderPicks, newFc, resampleTol);
        if (rs && rs.features && rs.handles) {
          newFc = asFc(rs, newFc);
          repointed = rs;
        }
      } catch (e) { /* keep the un-resampled result rather than failing the share */ }

      commit(newFc); setBaseline(newFc);
      setMoved((prev) => { const n = new Set(prev); borderPicks.forEach((x) => n.add(x)); return n; });
      setBorderShared(true);          // done sharing -> hide Share/Merge (fine-tune phase)
      if (borderPicks.length === 2) {
        // now they share a border -> offer it for dragging
        let arced = false;
        let segmentCount = 0;
        // the resample already told us the arcs; only ask again if it didn't run
        let arcs = repointed ? normalizeBorderArcs(repointed.arcPoints) : [];
        if (!arcs.length) {
          try {
            const sb = await api.sharedBorder(dsId, borderPicks[0], borderPicks[1], newFc, { bridge: false });
            arcs = normalizeBorderArcs(sb.arcs);
          } catch (e) { /* ignore */ }
        }
        if (arcs.length) {
          setBorderSegments(arcs); setBorderSegmentIndex(0); setBorderArc(borderArcsFc(arcs));
          arced = true; segmentCount = arcs.length;
        }
        const spread = repointed && repointed.handles
          ? ` Points evened out, ${repointed.handles.before} → ${repointed.handles.after}.`
          : '';
        setBorderMsg(arced
          ? `Joined ${borderPicks[0]} & ${borderPicks[1]} ✓ —${spread} drag the border to fine-tune.${segmentCount > 1 ? ` ${segmentCount} interrupted segments shown.` : ''}`
          : `Joined ${borderPicks[0]} & ${borderPicks[1]} ✓.${spread}`);
      } else {
        setBorderArc(null); setBorderSegments([]); setBorderSegmentIndex(0);
        setBorderMsg(`Shared borders across ${borderPicks.length} regions ✓ (${(res.borders || []).length} borders). Save / Export when done.`);
      }
    } catch (e) {
      // apiDetail unwraps FastAPI's {"detail": …} so a refusal reads as the
      // sentence the backend wrote, not as `Error: 422: {"detail":"…"}`.
      setBorderMsg(`Share failed: ${apiDetail(e)}`);
    } finally { setBusy(false); }
  }, [dsId, fc, borderPicks, commit, resampleTol]);

  // Preview a resample: the backend returns both the counts and the resulting FC,
  // so Apply commits what was already computed. Debounced -- the slider fires a
  // lot and each call is real geometry work.
  const previewResample = useCallback((tolValue) => {
    setResampleTol(tolValue);
    // remember the handles as they are now, so Cancel can put them back
    if (!resampleUndoRef.current) {
      resampleUndoRef.current = {
        segments: borderSegmentsRef.current, arc: borderArcRef.current,
      };
    }
    if (resampleTimer.current) clearTimeout(resampleTimer.current);
    resampleTimer.current = setTimeout(async () => {
      if (borderPicks.length < 2 || !fcRef.current) return;
      const source = resample && resample.baseFc ? resample.baseFc : fcRef.current;
      setBusy(true);
      try {
        const res = await api.resampleRegions(dsId, borderPicks, source, tolValue);
        // Draw the previewed handles straight away -- the whole point is to see
        // the density change while dragging the slider, not after Apply.
        const arcs = normalizeBorderArcs(res.arcPoints);
        if (arcs.length) {
          setBorderSegments(arcs); setBorderSegmentIndex(0); setBorderArc(borderArcsFc(arcs));
        }
        // Stamp what this preview belongs to. Apply refuses if either has moved
        // since -- the preview is only valid for the pair and dataset it was
        // built from.
        setResample({ counts: res.counts, tol: res.tol, handles: res.handles,
                      baseFc: source, dsId, picks: [...borderPicks],
                      fc: asFc(res, fc) });
      } catch (e) {
        setBorderMsg(apiDetail(e));
      } finally { setBusy(false); }
    }, 300);
  }, [dsId, borderPicks, resample]);

  const cancelResample = useCallback(() => {
    const undo = resampleUndoRef.current;
    if (undo) {
      if (undo.segments) setBorderSegments(undo.segments);
      if (undo.arc) setBorderArc(undo.arc);
      setBorderSegmentIndex(0);
    }
    resampleUndoRef.current = null;
    setResample(null);
  }, []);

  const applyResample = useCallback(() => {
    if (!resample || !resample.fc) return;
    // Belt and braces on top of clearing it: committing a preview built for
    // another pair -- or another dataset -- would silently revert everything
    // done since, and Save would write that to disk.
    const samePicks = (resample.picks || []).length === borderPicks.length
      && (resample.picks || []).every((n, i) => n === borderPicks[i]);
    if (resample.dsId !== dsId || !samePicks) {
      setResample(null);
      setBorderMsg('That resample preview was for a different selection — '
        + 'nudge the slider again to preview this one.');
      return;
    }
    // the handles on screen are already the previewed ones, so this just makes
    // the geometry behind them permanent (and undoable)
    commit(resample.fc); setBaseline(resample.fc);
    setMoved((prev) => { const n = new Set(prev); borderPicks.forEach((x) => n.add(x)); return n; });
    const kept = resample.handles ? resample.handles.after : null;
    resampleUndoRef.current = null;
    setResample(null);
    setBorderMsg(kept != null
      ? `Resampled — ${kept} drag points on the border.`
      : 'Resampled.');
  }, [resample, borderPicks, commit, dsId]);

  const doMerge = useCallback(async () => {
    if (borderPicks.length < 2) return;
    setBusy(true); setError(null);
    try {
      const res = await api.mergeRegions(dsId, borderPicks, fc);
      const newFc = asFc(res, fc);
      commit(newFc); setBaseline(newFc);
      setBorderPicks([]); setBorderArc(null);
      // Say what actually happened to the border between them: dissolved, or
      // the pieces never touched and remain separate parts of one region.
      const sealed = res.sealed ? ' — hairline seam dissolved' : '';
      const still = res.parts > 1
        ? ` — ${res.parts} separate pieces (they don't touch; the border between touching pieces is gone)`
        : '';
      setBorderMsg(`Merged ${borderPicks.length} regions into “${res.name}” ✓${sealed}${still}`);
    } catch (e) {
      setBorderMsg(`Merge failed: ${String(e).replace(/^Error:\s*/, '')}`);
    } finally { setBusy(false); }
  }, [dsId, fc, borderPicks, commit]);

  const onClickFeature = useCallback((idx, obj) => {
    const t = obj && obj.geometry && obj.geometry.type;
    if (idx == null || idx < 0 || !(t === 'Polygon' || t === 'MultiPolygon')) return;
    if (mode === 'border') { pickBorderRegion(featureName(obj, idProp)); return; }
    setSelected([idx]);
  }, [mode, pickBorderRegion, idProp]);

  const updateRegionProperties = useCallback((featureIndex, nextProps) => {
    const currentFc = fcRef.current;
    if (!currentFc || !currentFc.features || !currentFc.features[featureIndex]) return false;
    setError(null);
    try {
      const feature = currentFc.features[featureIndex];
      const { props, oldName, newName } = renameFeatureProperties(feature, idProp, nextProps);
      const duplicate = currentFc.features.some((f, i) => i !== featureIndex && featureName(f, idProp) === newName);
      if (duplicate) throw new Error(`Another region is already named "${newName}".`);

      const features = currentFc.features.map((f, i) => (i === featureIndex ? { ...f, properties: props } : f));
      let nextFc = { ...currentFc, features };
      // Record it now, not at save: renaming a region to `bubble.1` makes it
      // damage, and the notes panel works out whose regions are whose from this.
      if (oldName !== newName) {
        nextFc = stampFc(nextFc, identityRef.current, 'rename',
                         `${oldName} → ${newName}`, [newName]);
      }
      commit(nextFc);
      setSelected([featureIndex]);

      setBaseline((prev) => {
        if (!prev || !prev.features || !prev.features[featureIndex]) return prev;
        const baseFeature = prev.features[featureIndex];
        let baseProps = props;
        try {
          baseProps = renameFeatureProperties(baseFeature, idProp, props).props;
        } catch (e) { /* keep the edited props */ }
        return { ...prev, features: prev.features.map((f, i) => (i === featureIndex ? { ...f, properties: baseProps } : f)) };
      });

      if (oldName !== newName) {
        setMoved((prev) => {
          const next = new Set(prev);
          if (next.delete(oldName)) next.add(newName);
          return next;
        });
        setBorderPicks((prev) => prev.map((nm) => (nm === oldName ? newName : nm)));
      }
      return true;
    } catch (e) {
      setError(String(e).replace(/^Error:\s*/, ''));
      return false;
    }
  }, [commit, idProp]);

  // ---- right-click a region (on the image or in the list) -> Rename / Delete ----
  const openRegionMenu = useCallback((m) => {
    if (!m || m.index == null || m.index < 0) return;
    setRegionMenu(m);
    setMenuView('menu');
    setRenameDraft(m.name || '');
    setSelected([m.index]);
  }, []);
  const closeRegionMenu = useCallback(() => { setRegionMenu(null); setMenuView('menu'); }, []);
  const applyRename = useCallback(() => {
    if (!regionMenu) { closeRegionMenu(); return; }
    const next = String(renameDraft == null ? '' : renameDraft).trim();
    if (!next || next === regionMenu.name) { closeRegionMenu(); return; }
    updateRegionProperties(regionMenu.index, { [idProp]: next });
    closeRegionMenu();
  }, [regionMenu, renameDraft, idProp, updateRegionProperties, closeRegionMenu]);

  // Colour lives in classification.colorRGB (QuPath's own packed-int convention),
  // so an edited colour survives export straight back into their pipeline.
  const setRegionColour = useCallback((featureIndex, hex) => {
    const cur = fcRef.current;
    const feat = cur && cur.features && cur.features[featureIndex];
    if (!feat) return;
    const rgb = [1, 3, 5].map((i) => parseInt(hex.slice(i, i + 2), 16));
    const cls = (feat.properties || {}).classification;
    updateRegionProperties(featureIndex, {
      classification: { ...(typeof cls === 'object' && cls ? cls : {}),
                        name: featureName(feat, idProp), colorRGB: rgbToPacked(rgb) },
    });
  }, [updateRegionProperties, idProp]);

  const clearRegionColour = useCallback((featureIndex) => {
    const cur = fcRef.current;
    const feat = cur && cur.features && cur.features[featureIndex];
    if (!feat) return;
    const cls = (feat.properties || {}).classification;
    const next = { ...(typeof cls === 'object' && cls ? cls : {}) };
    delete next.colorRGB;                      // back to the name-derived hue
    updateRegionProperties(featureIndex, {
      classification: { ...next, name: featureName(feat, idProp) },
    });
  }, [updateRegionProperties, idProp]);

  // Delete just drops the feature. The hole it leaves is a normal gap, so Fix a
  // gap will find it if you want the neighbours to take the ground back.
  const deleteRegion = useCallback((featureIndex) => {
    const cur = fcRef.current;
    if (!cur || !cur.features || !cur.features[featureIndex]) return;
    const nm = featureName(cur.features[featureIndex], idProp);
    const next = stampFc({ ...cur, features: cur.features.filter((_, i) => i !== featureIndex) },
                         identityRef.current, 'delete-region', nm, [nm]);
    commit(next);
    // keep the snap baseline aligned -- match by name, not index
    setBaseline((prev) => ((prev && prev.features)
      ? { ...prev, features: prev.features.filter((f) => featureName(f, idProp) !== nm) }
      : prev));
    setSelected([]);
    setMoved((prev) => { const n = new Set(prev); n.delete(nm); return n; });
    setBorderPicks((prev) => prev.filter((x) => x !== nm));
    setSnapInfo((s) => ({ ...(s || {}), saved: `deleted “${nm}” — Ctrl+Z undoes it` }));
  }, [commit, idProp]);

  // Escape, or a click anywhere outside it, dismisses the menu
  useEffect(() => {
    if (!regionMenu) return undefined;
    const onKey = (e) => { if (e.key === 'Escape') closeRegionMenu(); };
    const onDown = (e) => {
      if (e.target && e.target.closest && e.target.closest('.ctxmenu')) return;
      closeRegionMenu();
    };
    window.addEventListener('keydown', onKey);
    window.addEventListener('pointerdown', onDown, true);
    return () => {
      window.removeEventListener('keydown', onKey);
      window.removeEventListener('pointerdown', onDown, true);
    };
  }, [regionMenu, closeRegionMenu]);

  // ---- arrow-key vertex density on the single selected region ----
  const selectedName = (selected.length === 1 && fc && fc.features[selected[0]])
    ? featureName(fc.features[selected[0]], idProp) : null;
  // vertex-count control disabled (paused):
  // useEffect(() => {
  //   if (selectedName == null) { resampleRef.current = null; return; }
  //   const f = fc && fc.features.find((ff) => (ff.properties || {}).name === selectedName);
  //   if (f) resampleRef.current = { name: selectedName, base: f.geometry, n: exteriorCount(f.geometry) };
  //   // eslint-disable-next-line react-hooks/exhaustive-deps
  // }, [selectedName]);

  const applySplit = useCallback(async (coords) => {
    if (!selectedName) { setSplitMsg('Pick the region to split first — click one in the list.'); return; }
    setBusy(true); setError(null);
    try {
      const res = await api.splitRegion(dsId, selectedName, fc, coords);
      const newFc = asFc(res, fc);
      commit(newFc); setBaseline(newFc);
      setMoved((prev) => { const n = new Set(prev); res.names.forEach((x) => n.add(x)); return n; });
      setSelected([]);
      setSplitMsg(`Split into “${res.names[0]}” and “${res.names[1]}” ✓ — draw again, or leave Split.`);
    } catch (e) {
      setSplitMsg(`Split failed: ${String(e).replace(/^Error:\s*/, '')}`);
    } finally { setBusy(false); }
  }, [dsId, fc, selectedName, commit]);

  const onSplitEdit = useCallback(({ updatedData, editType }) => {
    if (editType === 'addFeature') {
      const f = updatedData.features[updatedData.features.length - 1];
      const coords = f && f.geometry && f.geometry.coordinates;
      setSplitDraw({ type: 'FeatureCollection', features: [] });     // clear the drawn line
      if (coords && coords.length >= 2) applySplit(coords);
    } else {
      setSplitDraw(updatedData);                                     // in-progress line
    }
  }, [applySplit]);

  // Dissolve mode: click a leftover void -> the backend locates that void and
  // returns both its outline (previewed in red) and the already-filled FC, so
  // "Dissolve" commits without recomputing.
  const onPickGap = useCallback(async (coord) => {
    if (!coord || !fc) return;
    setBusy(true); setError(null); setGapFind(null);
    setGapMsg('looking for a gap there…');
    try {
      const res = await api.dissolveGap(dsId, fc, [coord[0], coord[1]], 40, offList());
      setGapFind({
        gap: res.gap, area: res.area, kind: res.kind, regions: res.regions,
        point: [coord[0], coord[1]],     // kept so "New region" can reuse the click
        fc: asFc(res, fc),
      });
      const who = (res.regions || []).join(' + ') || 'its neighbour';
      setGapMsg(`Gap found — ${Math.round(res.area).toLocaleString()} px². Dissolve it into ${who}, or make it a new region.`);
    } catch (e) {
      setGapMsg(apiDetail(e));
    } finally { setBusy(false); }
  }, [dsId, fc]);

  // Same clicked gap, second option: keep it as its own region instead of
  // handing it to the neighbours. The gap outline is reused exactly, so the new
  // region abuts them with no fresh hairline.
  const applyFillGap = useCallback(async () => {
    if (!gapFind || !fc) return;
    setBusy(true); setError(null);
    try {
      const res = await api.fillGap(dsId, fc, gapFind.point, { exclude: offList() });
      const newFc = asFc(res, fc);
      commit(newFc); setBaseline(newFc);
      setMoved((prev) => { const n = new Set(prev); n.add(res.name); return n; });
      setGapFind(null);
      setGapMsg(`Gap became “${res.name}” ✓ — right-click it to rename.`);
    } catch (e) {
      setGapMsg(apiDetail(e));
    } finally { setBusy(false); }
  }, [dsId, fc, gapFind, commit]);

  const applyDissolve = useCallback(() => {
    if (!gapFind || !gapFind.fc) return;
    commit(gapFind.fc); setBaseline(gapFind.fc);
    setMoved((prev) => {
      const n = new Set(prev);
      (gapFind.regions || []).forEach((x) => n.add(x));
      return n;
    });
    const who = (gapFind.regions || []).join(' + ');
    setGapFind(null);
    setGapMsg(`Gap dissolved into ${who} ✓ — click another gap, or leave Dissolve.`);
  }, [gapFind, commit]);

  // Draw mode: trace an outline, and it becomes a new region. Any region the
  // outline covers cedes that ground, so the file stays a clean partition —
  // EXCEPT for damage, which lies inside its host and leaves it whole.
  const applyAddRegion = useCallback(async (coords) => {
    if (!coords || coords.length < 3 || !fc) return;
    const dmgTag = drawKind === 'region' ? null : drawKind;
    setBusy(true); setError(null);
    setDrawMsg(dmgTag ? 'adding damage…' : 'creating…');
    try {
      const res = await api.addRegion(dsId, fc, coords, { damage: dmgTag });
      const newFc = asFc(res, fc);
      commit(newFc); setBaseline(newFc);
      setMoved((prev) => {
        const n = new Set(prev);
        n.add(res.name);
        (res.ceded || []).forEach((x) => n.add(x));
        return n;
      });
      const area = `${Math.round(res.area).toLocaleString()} px²`;
      if (res.damage) {
        // Where it landed matters more than the area: a shape over no region is
        // the one case the annotator has to fix, and it says so now rather than
        // at export time.
        const where = (res.inside || []).length
          ? ` in ${res.inside.join(' + ')}`
          : ' — it overlaps NO region, so it will not reach the sheet';
        if (res.needsChoice) {
          setDamageAsk({ name: res.name, candidates: res.candidates || [], dominant: res.dominant });
          setDrawMsg(`Added “${res.name}” (${area}) — it reaches into ${(res.candidates || []).length} regions.`);
        } else {
          setDamageAsk(null);
          setDrawMsg(`Added “${res.name}” (${area})${where}. Draw another, or change the kind above.`);
        }
      } else {
        const took = (res.ceded || []).length
          ? ` — ${res.ceded.join(', ')} gave up the overlap`
          : '';
        setDrawMsg(`Created “${res.name}” (${area})${took}. Right-click it to rename.`);
      }
    } catch (e) {
      setDrawMsg(apiDetail(e));
    } finally { setBusy(false); }
  }, [dsId, fc, commit, drawKind]);

  // The answer goes on the shape itself (`_damage_regions`), not into session
  // state: it is an annotator's judgement, and it has to survive a save, an
  // email and the next person opening the file.
  const answerDamage = useCallback((regions, shapeName) => {
    const nm = shapeName || (damageAsk && damageAsk.name);
    if (!nm || !fc) return null;
    const next = asFc({
      features: fc.features.map((f) => (featureName(f, idProp) === nm
        ? { ...f, properties: { ...(f.properties || {}), _damage_regions: regions } }
        : f)),
    }, fc);
    commit(next); setBaseline(next);
    if (damageAsk && damageAsk.name === nm) setDamageAsk(null);
    setDrawMsg(`“${nm}” recorded in ${regions.join(' + ')}. Draw another, or change the kind above.`);
    return next;                      // so a caller can re-report against it
  }, [damageAsk, fc, idProp, commit]);

  const onDrawEdit = useCallback(({ updatedData, editType }) => {
    if (editType === 'addFeature') {
      const f = updatedData.features[updatedData.features.length - 1];
      const ring = f && f.geometry && f.geometry.coordinates && f.geometry.coordinates[0];
      setDrawPoly({ type: 'FeatureCollection', features: [] });   // clear the sketch
      if (ring && ring.length >= 3) applyAddRegion(ring);
    } else {
      setDrawPoly(updatedData);                                   // in-progress outline
    }
  }, [applyAddRegion]);

  // Clean mode: a traced loop goes to the backend, which finds every
  // sliver-thin artifact inside it. The result WAITS as a preview — the freed
  // lines highlighted on the image, the report in the sidebar — and nothing
  // changes until Apply. One call does both: the response carries the updated
  // FC, so Apply is a local commit.
  const runCleanLines = useCallback(async (ring) => {
    if (!ring || ring.length < 3 || !fc) return;
    setBusy(true); setError(null); setCleanMsg('looking for stray lines…');
    try {
      const res = await api.cleanLines(dsId, fc, ring, { exclude: offList() });
      setCleanFound({ res, next: asFc(res, fc) });
      const bits = [];
      if (res.removed.length) {
        bits.push(`${res.removed.length} sliver piece(s) from ${res.removed
          .map((r) => r.region).join(', ')}`);
      }
      if (res.deleted.length) bits.push(`${res.deleted.join(', ')} deleted whole`);
      if (res.filled.length) bits.push(`ground to ${res.filled.join(' + ')}`);
      setCleanMsg(`Found ${Math.round(res.area).toLocaleString()} px² of stray `
        + `lines${bits.length ? ` — ${bits.join('; ')}` : ''}. Apply to remove.`);
    } catch (e) {
      setCleanFound(null);
      setCleanMsg(apiDetail(e));
    } finally { setBusy(false); }
  }, [dsId, fc, offList]);

  const onCleanEdit = useCallback(({ updatedData, editType }) => {
    if (editType === 'addFeature') {
      const f = updatedData.features[updatedData.features.length - 1];
      const ring = f && f.geometry && f.geometry.coordinates && f.geometry.coordinates[0];
      setCleanPoly({ type: 'FeatureCollection', features: [] });
      if (ring && ring.length >= 3) runCleanLines(ring);
    } else {
      setCleanPoly(updatedData);
    }
  }, [runCleanLines]);

  const applyClean = useCallback(() => {
    if (!cleanFound) return;
    const { res, next } = cleanFound;
    commit(next); setBaseline(next);
    setMoved((prev) => {
      const n = new Set(prev);
      res.removed.forEach((r) => n.add(r.region));
      res.filled.forEach((x) => n.add(x));
      return n;
    });
    setCleanFound(null);
    setCleanMsg(`Removed ${Math.round(res.area).toLocaleString()} px² of stray `
      + 'lines. Circle more, or Save to keep it.');
  }, [cleanFound, commit]);

  const bumpPoints = () => {};   // vertex-count control disabled (paused)
  /* was:
  const bumpPoints = useCallback((dir) => {
    const rs = resampleRef.current;
    if (!rs || !fc) return;
    const step = Math.max(3, Math.round(rs.n * 0.2));
    const n = Math.max(6, rs.n + dir * step);
    if (n === rs.n) return;
    const idx = fc.features.findIndex((f) => (f.properties || {}).name === rs.name);
    if (idx < 0) return;
    const geom = resampleGeom(rs.base, n);
    const next = { ...fc, features: fc.features.map((f, i) => (i === idx ? { ...f, geometry: geom } : f)) };
    rs.n = n;
    commit(next);
    setMoved((prev) => { const s = new Set(prev); s.add(rs.name); return s; });
  }, [fc, commit]);
  */

  // keyboard: undo/redo + arrow-key point density
  useEffect(() => {
    const onKey = (e) => {
      const tag = (e.target && e.target.tagName) || '';
      if (/INPUT|TEXTAREA|SELECT/.test(tag) || (e.target && e.target.isContentEditable)) return;
      if ((e.ctrlKey || e.metaKey) && (e.key === 'z' || e.key === 'Z')) {
        e.preventDefault(); if (e.shiftKey) redo(); else undo(); return;
      }
      if ((e.ctrlKey || e.metaKey) && (e.key === 'y' || e.key === 'Y')) { e.preventDefault(); redo(); return; }
      // vertex-count control disabled (arrow keys removed)
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [undo, redo]);

  const onChannelChange = useCallback((i, patch) => {
    setChannels((chs) => chs.map((c, j) => (j === i ? { ...c, ...patch } : c)));
  }, []);

  const onAddGene = useCallback(async (name) => {
    if (!name) return;
    const c = await api.geneContrast(dsId, name);
    setChannels((chs) => {
      if (chs.some((ch) => ch.gene === name)) return chs;
      return [...chs, {
        gene: name, color: GENE_PALETTE[chs.length % GENE_PALETTE.length],
        min: c ? c.min : 0, max: c ? c.max : 1, dataMax: c ? c.dataMax : 1, visible: true,
      }];
    });
  }, [dsId]);

  const onRemoveGene = useCallback((name) => {
    setChannels((chs) => chs.filter((c) => c.gene !== name));
  }, []);

  const onStainChange = useCallback((i, patch) => {
    setStainChannels((chs) => chs.map((c, j) => (j === i ? { ...c, ...patch } : c)));
    if (patch.visible === true) {
      const ch = stainChannels[i];
      if (ch && !ch.contrastLoaded) loadStainContrast(ch.index);
    }
  }, [stainChannels, loadStainContrast]);

  const onLayerChange = useCallback((patch) => setLayers((l) => ({ ...l, ...patch })), []);

  const doSnap = useCallback(async () => {
    if (!fc || !baseline) return;
    const movedList = [...moved];
    const snapBaseline = findSnapBaseline(baseline, fc, movedList, historyRef.current, idProp);
    if (!hasMovedGeometryDelta(snapBaseline, fc, movedList, idProp)) {
      setSnapInfo({ movers: movedList, notes: ['No unsnapped geometry changes were detected.'] });
      return;
    }
    setBusy(true); setError(null);
    try {
      const res = await api.snap(dsId, snapBaseline, fc, movedList, offList());
      const clean = asFc(res, fc);
      commit(clean); setBaseline(clean); setMoved(new Set());
      setSnapInfo({ movers: res._movers, notes: res._notes });
    } catch (e) { setError(String(e)); } finally { setBusy(false); }
  }, [dsId, fc, baseline, moved, commit, idProp, offList]);

  const doSave = useCallback(async () => {
    setBusy(true); setError(null);
    try {
      const r = await api.saveRegions(dsId, fc);
      const stamped = r.version ? String(r.version).split(/[\\/]/).pop() : null;
      setSnapInfo((s) => ({
        ...(s || {}),
        saved: stamped
          ? `saved — snapshot ${stamped} (${r.versions} kept, nothing overwritten)`
          : r.saved,
      }));
    } catch (e) { setError(String(e)); } finally { setBusy(false); }
  }, [dsId, fc]);

  const doRestoreOriginal = useCallback(async () => {
    setBusy(true); setError(null);
    try {
      const res = await api.restoreOriginal(dsId);
      const newFc = asFc(res, fc);
      commit(newFc); setBaseline(newFc);
      setSelected([]); setMoved(new Set()); setMode('view');
      clearBorder(); clearSplit(); clearGap(); clearDraw(); clearClean();
      const kept = res.backup ? String(res.backup).split(/[\\/]/).pop() : null;
      setSnapInfo({ saved: `restored the original (${res.count} regions)`
        + (kept ? ` — your previous work is kept as ${kept}` : '') });
    } catch (e) {
      setError(apiDetail(e));
    } finally { setBusy(false); }
  }, [dsId, commit, clearBorder, clearSplit, clearGap, clearDraw]);

  // Rotate/flip the whole dataset because the slide was imaged the wrong way up.
  // The backend moves the image AND the regions together and hands back both the
  // re-oriented FeatureCollection and the new canvas size; `info` has to be
  // updated too or deck keeps fitting the view to the old extent.
  const applyOrientation = useCallback(async (change) => {
    if (!dsId) return;
    setBusy(true); setError(null);
    try {
      const now = (info && info.orientation) || { rot: 0, flipH: false, flipV: false };
      const next = typeof change === 'function' ? change(now) : { ...now, ...change };
      const res = await api.setOrientation(dsId, next, fc);
      const newFc = asFc(res, fc);
      commit(newFc); setBaseline(newFc);
      setInfo((prev) => (prev ? { ...prev, width: res.width, height: res.height,
        orientation: res.orientation } : prev));
      // The gene and stain layers are placed from info fetched once when the
      // dataset loaded. Leave that stale and the gene bitmap keeps sitting on the
      // UN-rotated bounds while everything else turns -- the section appears
      // twice, once correct and once as a ghost. Both endpoints already report
      // the rotated extent, so just ask them again.
      try {
        const g = await api.getGenes(dsId);
        if (g && g.bounds) {
          setGeneInfo(g);
          setGeneBounds(g.bounds);
        }
      } catch (e) { /* dataset has no transcripts */ }
      try {
        const st = await api.getStains(dsId);
        if (st && st.channels && st.channels.length) setStainInfo(st);
      } catch (e) { /* dataset has no morphology_focus */ }
      setSelected([]); setMoved(new Set()); setMode('view');
      clearBorder(); clearSplit(); clearGap(); clearDraw(); clearClean();
      const o = res.orientation;
      setSnapInfo({ saved: `view is now ${o.rot}°`
        + (o.flipH ? ' + flipped left/right' : '')
        + (o.flipV ? ' + flipped top/bottom' : '')
        + ' — Export asks which frame to write' });
    } catch (e) {
      setError(apiDetail(e));
    } finally { setBusy(false); }
  }, [dsId, info, fc, commit, clearBorder, clearSplit, clearGap, clearDraw]);

  // Build the tracking-sheet TSV and put it on the clipboard. The clipboard write
  // must happen in the click's own task on some browsers, so the text is written
  // first and the review table shown after -- never the other way round.
  // The two per-sample YAMLs in the dataset folder.
  const [notesInfo, setNotesInfo] = useState(null);     // what is there, and where
  const [notesReport, setNotesReport] = useState(null); // the previewed diff
  const [notesScope, setNotesScope] = useState('mine');
  const [identity, setIdentity] = useState(null);       // { name, account }
  // read by client-side stamps, which must not re-create themselves on every
  // identity change
  const identityRef = useRef(null);
  identityRef.current = identity;

  useEffect(() => {
    (async () => {
      try {
        const who = await api.getIdentity();
        // `name` falls back to the Windows account when nobody has entered one.
        // That is fine for the edit trail and wrong for a shared roster, so the
        // notes panel starts empty until a real name is typed.
        setIdentity({ ...who, name: who.set ? who.name : '' });
      } catch (e) { /* the panel asks for a name anyway */ }
    })();
  }, []);

  const refreshNotes = useCallback(async (id) => {
    if (!id) return;
    try { setNotesInfo(await api.notesState(id)); }
    catch (e) { setNotesInfo(null); }
  }, []);

  useEffect(() => { setNotesReport(null); refreshNotes(dsId); }, [dsId, refreshNotes]);

  const doNotesPreview = useCallback(async (scope) => {
    if (!dsId || !fc) return;
    const which = scope || notesScope;
    setBusy(true); setError(null);
    try {
      const res = await api.notesPreview(dsId, {
        fc, scope: which, annotator: identity && identity.name,
      });
      setNotesReport(res);
    } catch (e) { setError(apiDetail(e)); } finally { setBusy(false); }
  }, [dsId, fc, notesScope, identity]);

  // Save re-derives everything server-side from the file on disk; the
  // fingerprints from the preview are what make a colleague's edit in between a
  // refusal rather than an overwrite.
  const doNotesSave = useCallback(async (which) => {
    if (!dsId || !fc || !notesReport) return;
    const files = notesReport.files || {};
    const fingerprints = {};
    Object.entries(files).forEach(([k, v]) => {
      if (v && v.fingerprint) fingerprints[k] = v.fingerprint;
    });
    setBusy(true); setError(null);
    try {
      const res = await api.notesSave(dsId, {
        fc, scope: notesScope, annotator: identity && identity.name,
        which: which || Object.keys(files).filter((k) => files[k] && files[k].found),
        fingerprints,
      });
      setNotesReport(res);
      await refreshNotes(dsId);
    } catch (e) { setError(apiDetail(e)); } finally { setBusy(false); }
  }, [dsId, fc, notesReport, notesScope, identity, refreshNotes]);

  const doNotesCreate = useCallback(async () => {
    if (!dsId || !fc) return;
    setBusy(true); setError(null);
    try {
      await api.notesCreate(dsId, { fc });
      await refreshNotes(dsId);
      await doNotesPreview();
    } catch (e) { setError(apiDetail(e)); } finally { setBusy(false); }
  }, [dsId, fc, refreshNotes, doNotesPreview]);

  // Typing updates the field; only leaving it persists, so a name is not saved
  // one keystroke at a time.
  const doSetIdentity = useCallback(async (name, persist) => {
    setIdentity((cur) => ({ ...(cur || {}), name, set: true }));
    if (!persist) return;
    try { await api.setIdentity(name); } catch (e) { /* kept locally anyway */ }
  }, []);

  // The per-region SmartSheet cells. The sheet's Damage column is a multi-select
  // dropdown, so what gets copied is one cell per region, not one long row.
  const [cellsReport, setCellsReport] = useState(null);
  const [cellSep, setCellSep] = useState('cell');
  const [cellsAll, setCellsAll] = useState(false);   // boxes for regions with none

  const loadCells = useCallback(async (nextFc, opts) => {
    if (!dsId) return;
    const use = nextFc || fc;
    if (!use) return;
    const o = opts || {};
    try {
      setCellsReport(await api.damageCells(dsId, {
        fc: use,
        sep: o.sep || cellSep,
        includeEmpty: 'all' in o ? o.all : cellsAll,
      }));
    } catch (e) { setError(apiDetail(e)); }
  }, [dsId, fc, cellSep, cellsAll]);

  // Ticking Done, or adding a designation nobody can draw, is an annotation ON
  // the region — so it is stored on the region feature, the same way a damage
  // shape stores which region it belongs to. It survives save, export and reload.
  const setRegionDamage = useCallback(async (region, patch) => {
    if (!fc) return;
    const next = asFc({
      features: fc.features.map((f) => {
        if (featureName(f, idProp) !== region) return f;
        const props = { ...(f.properties || {}) };
        if ('done' in patch) props._damage_done = !!patch.done;
        // add/remove are RELATIVE, and the panel uses them. Its list of ticks
        // comes from cellsReport, which only catches up on the round trip that
        // follows each tick — so two ticks in quick succession both computed
        // their new list from the same pre-tick snapshot, and the second wrote
        // the first one back out. The feature's own props are never stale.
        const cur = Array.isArray(props._damage_extra) ? props._damage_extra : [];
        if ('extra' in patch) props._damage_extra = [...patch.extra];
        if (patch.addExtra) props._damage_extra = [...new Set([...cur, patch.addExtra])];
        if (patch.removeExtra) props._damage_extra = cur.filter((x) => x !== patch.removeExtra);
        return { ...f, properties: props };
      }),
    }, fc);
    commit(next); setBaseline(next);
    await loadCells(next);
  }, [fc, idProp, commit, loadCells]);

  // Build the hemisection outline from the regions themselves, rather than
  // drawing it by hand around 22 of them.
  const [outlinePreview, setOutlinePreview] = useState(null);
  const doOutline = useCallback(async (opts) => {
    if (!dsId || !fc) return;
    setBusy(true); setError(null);
    try {
      // NOT offList(). Switching a region off is a viewing/gap-hunting control;
      // it must not decide what the section is made of. Excluding one silently
      // shrinks the outline — switching OLF off leaves 99.9% of it outside —
      // and the outline already excludes its own previous version.
      const res = await api.sectionOutline(dsId, { fc, ...(opts || {}) });
      if (!(opts || {}).apply) {
        setOutlinePreview({ ...res, ...(opts || {}) });
        return;
      }
      const newFc = asFc(res, fc);
      commit(newFc); setBaseline(newFc);
      setMoved((prev) => new Set(prev).add(res.name));
      setOutlinePreview(null);
      setSnapInfo((s) => ({
        ...(s || {}),
        saved: `“${res.name}” ${res.replaced ? 'rebuilt' : 'created'} by ${res.method}`
          + ` — ${Math.round(res.area).toLocaleString()} px² around ${res.sources.length} regions`
          + (res.parts > 1 ? `, in ${res.parts} separate pieces` : ''),
      }));
    } catch (e) { setError(apiDetail(e)); } finally { setBusy(false); }
  }, [dsId, fc, commit]);

  // The whole-row TSV export is gone from the UI (the sheet's Damage column is a
  // multi-select, so the per-region cells replaced it). `regions/smartsheet.tsv`
  // is still there and still tested, for the day the other columns are wanted.

  // Answering a straddling shape from the review panel: record it, then rebuild
  // the boxes from the file that now carries the answer — a panel still showing
  // the old assignment would be the thing people paste.
  const answerDamageInReport = useCallback(async (regions, shapeName) => {
    const next = answerDamage(regions, shapeName);
    if (!next) return;
    await loadCells(next);
  }, [answerDamage, loadCells]);

  const doReset = useCallback(async () => {
    setBusy(true); setError(null);
    try {
      const r = await api.getRegions(dsId);
      setFc(r); setBaseline(r); initHistory(r);
      setMoved(new Set()); setSelected([]);
      setSnapInfo(null); setMode('view'); clearBorder();
    } catch (e) { setError(String(e)); } finally { setBusy(false); }
  }, [dsId, clearBorder, initHistory]);

  const loadRegionsFromFile = useCallback(async () => {
    setBusy(true); setError(null);
    try {
      const { path } = await api.browseFile();
      if (!path) return;
      const r = await api.loadRegionsFile(dsId, path);
      setFc(r); setBaseline(r); initHistory(r);
      setMoved(new Set()); setSelected([]); setSnapInfo(null); setMode('view'); clearBorder();
    } catch (e) { setError(String(e)); } finally { setBusy(false); }
  }, [dsId, clearBorder, initHistory]);

  // Export refuses to write broken geometry. On a block we surface the problem
  // list and let the user Repair, force it through, or go fix it by hand.
  const doExportAnnData = useCallback(async () => {
    setBusy(true); setError(null);
    try {
      const blob = await api.exportAnnData(dsId, fc);
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url; a.download = `${dsId}_regions.h5ad`;
      document.body.appendChild(a); a.click(); a.remove();
      setTimeout(() => URL.revokeObjectURL(url), 5000);
    } catch (e) { setError(apiDetail(e)); } finally { setBusy(false); }
  }, [dsId, fc]);

  const doExport = useCallback(async (exportMode, { force = false, frame = 'displayed' } = {}) => {
    setBusy(true); setError(null);
    try {
      const name = await api.exportRegions(dsId, exportMode, fcRef.current || fc,
                                           { force, frame });
      setGeomReport(null);
      setSnapInfo((s) => ({ ...(s || {}), saved: `exported ${name}` }));
    } catch (e) {
      if (e instanceof api.GeometryError) {
        // Keep the frame on the report: "Export anyway" has to write the frame
        // that was asked for, not silently fall back to the displayed one.
        setGeomReport({ problems: e.problems, counts: e.counts, mode: exportMode, frame,
                        title: 'Export blocked — fix these first' });
      } else {
        setError(String(e));
      }
    } finally { setBusy(false); }
  }, [dsId, fc]);

  const doValidate = useCallback(async () => {
    setBusy(true); setError(null);
    try {
      const res = await api.validateRegions(dsId, fcRef.current || fc, offList());
      setGeomReport({
        problems: res.problems, counts: res.counts, mode: null,
        title: res.ok
          ? (res.problems.length ? 'No errors — some things to look at' : 'Geometry is clean ✓')
          : 'Geometry errors found',
      });
    } catch (e) { setError(String(e)); } finally { setBusy(false); }
  }, [dsId, fc]);

  const doRepair = useCallback(async () => {
    setBusy(true); setError(null);
    try {
      const res = await api.repairRegions(dsId, fcRef.current || fc);
      const newFc = asFc(res, fc);
      commit(newFc);
      const fixed = res.fixed || [];
      setGeomReport(null);
      setSnapInfo((s) => ({ ...(s || {}),
        saved: fixed.length ? `repaired ${fixed.length}: ${fixed.join(', ')}` : 'nothing needed repairing' }));
      if (fixed.length) setMoved((prev) => { const n = new Set(prev); fixed.forEach((x) => n.add(x)); return n; });
    } catch (e) { setError(String(e)); } finally { setBusy(false); }
  }, [dsId, fc, commit]);

  // vertex-count control disabled (paused): pointCount removed
  const pointCount = null;
  const movedList = useMemo(() => [...moved], [moved]);
  const snapBaseline = useMemo(
    () => findSnapBaseline(baseline, fc, movedList, historyRef.current, idProp),
    [baseline, fc, movedList, histTick, idProp],
  );
  const canSnap = hasMovedGeometryDelta(snapBaseline, fc, movedList, idProp);

  return (
    <div className="root">
      <DatasetBar
        datasets={datasets} activeId={dsId} onSelect={setDsId}
        onOpenFolder={openFolder} onOpenPath={loadOpened} busy={dsBusy}
      />
      <div className="app">
        {!dsId && <div className="empty">No dataset loaded. Click <b>📂 Open folder…</b> above to load a Xenium folder.</div>}
        {dsId && error && !info && <div className="fatal">Error: {error}</div>}
        {dsId && !error && (!info || !fc) && <div className="loading">Loading dataset…</div>}
        {dsId && info && fc && (
          <>
            <aside className="sidebar"
              style={{ flexBasis: paneW, width: paneW, zoom: uiSettings.zoom }}>
              <Sidebar
                info={info} fc={fc} sources={sources} selected={selected}
                onRegionMenu={openRegionMenu}
                onSelectName={(nm) => {
                  if (mode === 'border') { pickBorderRegion(nm); return; }
                  const i = fc.features.findIndex((f) => featureName(f, idProp) === nm);
                  if (i >= 0) setSelected([i]);
                }}
                mode={mode}
                onToggleModify={onToggleModify}
                onToggleBorder={onToggleBorder}
                onToggleSplit={onToggleSplit}
                onToggleDissolve={onToggleDissolve}
                onToggleDraw={onToggleDraw}
                onToggleClean={onToggleClean}
                cleanFound={cleanFound && cleanFound.res} cleanMsg={cleanMsg}
                onApplyClean={applyClean} onCancelClean={clearClean}
                borderPicks={borderPicks} borderMsg={borderMsg} borderShared={borderShared}
                onClearBorder={clearBorder} onShareBorders={doShareBorders}
                onMerge={doMerge} splitMsg={splitMsg}
                resample={resample} resampleTol={resampleTol}
                onResampleTol={previewResample} onApplyResample={applyResample}
                onCancelResample={cancelResample}
                gapFind={gapFind} gapMsg={gapMsg} drawMsg={drawMsg}
                drawKind={drawKind} onDrawKind={setDrawKind} designations={designations}
                damageAsk={damageAsk} onAnswerDamage={answerDamage}
                onDissolve={applyDissolve} onFillGap={applyFillGap} onClearGap={clearGap}
                selectedName={selectedName}
                moved={moved} canSnap={canSnap} onSnap={doSnap} onSave={doSave} onReset={doReset}
                onUndo={undo} onRedo={redo} canUndo={canUndo} canRedo={canRedo}
                propEdit={propEdit} propRadius={propRadius}
                onPropEditChange={setProportionalEditing}
                geomReport={geomReport} onValidate={doValidate} onRepair={doRepair}
                onDismissReport={() => setGeomReport(null)}
                onForceExport={(m, frame) => doExport(m, { force: true, frame })}
                onRestoreOriginal={doRestoreOriginal}
                orientation={(info && info.orientation) || null}
                onOrientation={applyOrientation}
                onAnswerReportDamage={answerDamageInReport}
                regionsOff={regionsOff} onToggleRegionOff={toggleRegionOff}
                bordersOff={bordersOff} onToggleBorderOff={toggleBorderOff}
                fillsOff={fillsOff} onToggleFillOff={toggleFillOff}
                onAllFaces={setAllFaces}
                outlinePreview={outlinePreview} onOutline={doOutline}
                onDismissOutline={() => setOutlinePreview(null)}
                cellsReport={cellsReport} cellSep={cellSep} cellsAll={cellsAll}
                onLoadCells={() => loadCells()} onDismissCells={() => setCellsReport(null)}
                onCellSep={(s) => { setCellSep(s); loadCells(null, { sep: s }); }}
                onCellsAll={(v) => { setCellsAll(v); loadCells(null, { all: v }); }}
                onRegionDamage={setRegionDamage}
                notesInfo={notesInfo} notesReport={notesReport} notesScope={notesScope}
                onNotesScope={(s) => { setNotesScope(s); if (notesReport) doNotesPreview(s); }}
                onNotesPreview={() => doNotesPreview()} onNotesSave={doNotesSave}
                onNotesCreate={doNotesCreate} onDismissNotes={() => setNotesReport(null)}
                identity={identity} onIdentity={doSetIdentity}
                uiSettings={uiSettings} onUiSettings={applyUiSettings}
                updateInfo={updateInfo}
                onLoadFile={loadRegionsFromFile} onExport={doExport}
                onExportAnnData={sources && sources.sources
                  && sources.sources.transcripts ? doExportAnnData : null}
                snapInfo={snapInfo} busy={busy} error={error}
              />
              <ChannelPanel
                geneInfo={geneInfo} channels={channels} onChannelChange={onChannelChange}
                geneList={geneInfo ? geneInfo.genes : null}
                onAddGene={onAddGene} onRemoveGene={onRemoveGene}
                stainInfo={stainInfo} stainChannels={stainChannels} onStainChange={onStainChange}
                layers={layers} onLayerChange={onLayerChange}
                geneMode={geneMode} onGeneMode={setGeneMode}
                geneBin={geneBin} onGeneBin={setGeneBin}
                genePalette={genePalette} onGenePalette={setGenePalette}
              />
            </aside>
            <div
              className="splitter"
              title="drag to resize the sidebar"
              onPointerDown={(e) => {
                e.preventDefault();
                const startX = e.clientX;
                const start = paneW;
                const move = (ev) => {
                  const w = Math.max(260, Math.min(720, start + (ev.clientX - startX)));
                  setPaneW(w);
                };
                const up = () => {
                  window.removeEventListener('pointermove', move);
                  window.removeEventListener('pointerup', up);
                  setPaneW((w) => {
                    try { localStorage.setItem('fiveatlas.paneW', String(w)); } catch (e2) { /* ok */ }
                    return w;
                  });
                };
                window.addEventListener('pointermove', move);
                window.addEventListener('pointerup', up);
              }}
            />
            <div className="canvas-wrap">
              <Viewer
                // Remount on an orientation change. A quarter turn swaps the
                // canvas extent, and deck keeps both its tile cache and its
                // viewport across a prop change -- so the old image stayed on
                // screen next to the new one and the view stayed fitted to the
                // old shape. Rotating is rare, so a fresh viewer is the honest fix.
                key={`${dsId}|${info && info.orientation
                  ? `${info.orientation.rot}${info.orientation.flipH ? 'h' : ''}${info.orientation.flipV ? 'v' : ''}`
                  : ''}`}
                dsId={dsId} info={info} fc={fc} mode={mode} idProp={idProp}
                selectedIndexes={selected} onEdit={onEdit} onClickFeature={onClickFeature}
                borderPicks={borderPicks}
                borderArc={borderArc} onBorderEdit={onBorderEdit}
                onAddBorderPoint={onAddBorderPoint}
                splitDraw={splitDraw} onSplitEdit={onSplitEdit}
                drawPoly={drawPoly} onDrawEdit={onDrawEdit}
                cleanPoly={cleanPoly} onCleanEdit={onCleanEdit}
                cleanPreview={cleanFound && cleanFound.res.freed}
                gapPreview={gapFind && gapFind.gap} onPickGap={onPickGap}
                propRing={propRing} onRegionMenu={openRegionMenu}
                onGrabVertex={onGrabVertex} onReleaseDrag={onReleaseDrag}
                geneBitmap={geneBitmap} geneBounds={geneBounds} geneMode={geneMode}
                stainInfo={stainInfo} stainChannels={stainChannels}
                layers={layers} regionsOff={regionsOff}
                bordersOff={bordersOff} fillsOff={fillsOff}
              />
            </div>
          </>
        )}
      </div>

      {regionMenu && (
        <div className="ctxmenu" style={{ left: regionMenu.x, top: regionMenu.y }}>
          <div className="ctx-title">{regionMenu.name || '(unnamed)'}</div>

          {menuView === 'menu' && (
            <>
              <button className="ctx-item" onClick={() => setMenuView('rename')}>Rename</button>
              <button className="ctx-item" onClick={() => setMenuView('colour')}>Colour…</button>
              <button className="ctx-item danger" onClick={() => setMenuView('delete')}>Delete</button>
            </>
          )}

          {menuView === 'colour' && (() => {
            const feat = fc && fc.features && fc.features[regionMenu.index];
            const [r, g, b] = feat ? regionRgb(feat, idProp) : [128, 128, 128];
            const hex = '#' + [r, g, b].map((v) => v.toString(16).padStart(2, '0')).join('');
            const custom = !!((feat && feat.properties && feat.properties.classification || {}).colorRGB != null);
            return (
              <>
                <div className="ctx-row">
                  <input
                    type="color"
                    value={hex}
                    onChange={(e) => setRegionColour(regionMenu.index, e.target.value)}
                  />
                  <span className="ctx-note">{custom ? 'custom' : 'from the name'}</span>
                </div>
                <div className="row">
                  {custom && (
                    <button className="btn sm" onClick={() => clearRegionColour(regionMenu.index)}>
                      Auto
                    </button>
                  )}
                  <button className="btn sm" onClick={closeRegionMenu}>Done</button>
                </div>
              </>
            );
          })()}

          {menuView === 'rename' && (
            <input
              className="ctx-input"
              autoFocus
              value={renameDraft}
              onChange={(e) => setRenameDraft(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === 'Enter') { e.preventDefault(); applyRename(); }
                if (e.key === 'Escape') { e.preventDefault(); closeRegionMenu(); }
              }}
              onBlur={applyRename}
            />
          )}

          {menuView === 'delete' && (
            <>
              <div className="ctx-note">Delete this region? Ctrl+Z undoes it.</div>
              <div className="row">
                <button className="btn sm danger"
                  onClick={() => { deleteRegion(regionMenu.index); closeRegionMenu(); }}>
                  Delete
                </button>
                <button className="btn sm" onClick={() => setMenuView('menu')}>Cancel</button>
              </div>
            </>
          )}
        </div>
      )}
    </div>
  );
}

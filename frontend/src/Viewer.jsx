import { useEffect, useMemo, useRef } from 'react';
import {
  DeckGL,
  OrthographicView,
  COORDINATE_SYSTEM,
  TileLayer,
  BitmapLayer,
  GeoJsonLayer,
} from 'deck.gl';
import {
  EditableGeoJsonLayer,
  ViewMode,
  ModifyMode,
  DrawLineStringMode,
  DrawPolygonMode,
} from '@deck.gl-community/editable-layers';

const MODES = { view: ViewMode, modify: ModifyMode };

// deck delivers a right-click to the mode as an ordinary click, so without this
// check the right button ADDS A POINT before (or after) the shape is finished.
function isRightButton(event) {
  const src = event && event.sourceEvent;
  if (!src) return false;
  return src.button === 2 || (src.buttons & 2) === 2 || src.which === 3;
}

// The library only finishes a drawn shape on double-click, which is fiddly over a
// busy image -- an imprecise second click just adds a stray point. These wrappers
// keep a handle on the mode's own finishDrawing() so a RIGHT-CLICK can end the
// shape instead. (Enter and double-click still work; this only adds a way out.)
function finishableMode(Base, minPoints) {
  return class extends Base {
    handleClick(event, props) {
      this._props = props;
      // a right-click ends the shape and must never leave a stray vertex behind
      if (isRightButton(event)) { this.finishNow(); return; }
      super.handleClick(event, props);
    }
    handlePointerMove(event, props) { this._props = props; super.handlePointerMove(event, props); }
    pointCount() { return this.getClickSequence().length; }
    finishNow() {
      if (!this._props || this.pointCount() < minPoints) return false;
      try {
        this.finishDrawing(this._props);
      } catch (err) {
        // Don't strand the user with a line glued to the cursor: drop the sketch
        // and say so, rather than failing silently the way this did before.
        this.resetClickSequence();
        // eslint-disable-next-line no-console
        console.error('[FiveAtlas] could not finish the shape:', err);
        return false;
      }
      this.resetClickSequence();
      return true;
    }
  };
}
const FinishableLineMode = finishableMode(DrawLineStringMode, 2);
const FinishablePolygonMode = finishableMode(DrawPolygonMode, 3);
const DEFAULT_LAYERS = { showDapi: true, showGenes: true, showRegions: true, geneOpacity: 1, stainOpacity: 1 };
const PICK_COLORS = [[80, 230, 120], [90, 150, 255], [190, 120, 255]]; // A, B, extra

function hashHue(s) {
  let h = 0;
  for (let i = 0; i < s.length; i++) h = (h * 31 + s.charCodeAt(i)) >>> 0;
  return h % 360;
}
function hslToRgb(h, s, l) {
  h /= 360;
  const a = s * Math.min(l, 1 - l);
  const f = (n) => {
    const k = (n + h * 12) % 12;
    return Math.round(255 * (l - a * Math.max(-1, Math.min(k - 3, 9 - k, 1))));
  };
  return [f(0), f(8), f(4)];
}
// A stored colour wins; otherwise the name is hashed to a hue, so an unedited
// region always looks the same from session to session.
// QuPath writes colours as a packed int in classification.colorRGB, so that's
// what we read and write -- it round-trips back into their pipeline.
export function packedToRgb(value) {
  const n = Number(value);
  if (!Number.isFinite(n)) return null;
  const u = n < 0 ? n >>> 0 : n;
  return [(u >> 16) & 255, (u >> 8) & 255, u & 255];
}
export function rgbToPacked([r, g, b]) {
  return ((r & 255) << 16) | ((g & 255) << 8) | (b & 255);
}

const clamp255 = (v) => Math.max(0, Math.min(255, Number(v) | 0));

export function regionRgb(feature, idProp) {
  const props = (feature && feature.properties) || {};
  const cls = (props.classification && typeof props.classification === 'object')
    ? props.classification : {};
  // 1. an explicit override set here (packed int, QuPath's convention)
  if (cls.colorRGB != null) {
    const rgb = packedToRgb(cls.colorRGB);
    if (rgb) return rgb;
  }
  // 2. the colour the file already carries. Every region in these atlases has
  //    one (Isocortex [76,114,180], Hypothalamus [204,185,58]...) and ignoring
  //    it meant the app painted hash colours over the real atlas palette.
  if (Array.isArray(cls.color) && cls.color.length >= 3) {
    return cls.color.slice(0, 3).map(clamp255);
  }
  if (Array.isArray(props.color) && props.color.length >= 3) {
    return props.color.slice(0, 3).map(clamp255);
  }
  // 3. nothing stored -> derive a stable hue from the name
  return hslToRgb(hashHue(regionName(feature, idProp)), 0.6, 0.55);
}

function regionName(feature, idProp) {
  const props = (feature && feature.properties) || {};
  const value = props[idProp] ?? props.name;
  return value == null ? '' : String(value);
}

function pickedFeatureIndex(info) {
  const props = (info && info.object && info.object.properties) || {};
  const candidates = [props.featureIndex, props._segment, info && info.object && info.object.featureIndex, info && info.index];
  for (const value of candidates) {
    if (Number.isInteger(value) && value >= 0) return value;
  }
  return 0;
}

// A pyramidal PNG-tile layer (used for both morphology and stain composites).
function pyramidTileLayer({ id, levels, tileSize, width, height, opacity, urlFor, refetchKey }) {
  const maxLevel = levels - 1;
  return new TileLayer({
    id,
    tileSize,
    minZoom: -maxLevel, // deck non-geo: full-res = 0, coarser = negative
    maxZoom: 0,
    extent: [0, 0, width, height],
    opacity,
    getTileData: ({ index, signal }) => {
      const backendZ = maxLevel + index.z;
      if (backendZ < 0 || backendZ > maxLevel) return Promise.resolve(null);
      return fetch(urlFor(backendZ, index.x, index.y), { signal })
        .then((r) => (r.ok ? r.blob() : null))
        .then((b) => (b ? createImageBitmap(b) : null));
    },
    renderSubLayers: (props) => {
      const bb = props.tile.boundingBox;
      if (!props.data) return null;
      return new BitmapLayer(props, {
        data: null,
        image: props.data,
        bounds: [bb[0][0], bb[1][1], bb[1][0], bb[0][1]], // flipY [left,bottom,right,top]
      });
    },
    updateTriggers: { getTileData: [refetchKey] },
  });
}

const EMPTY_SET = new Set();

export default function Viewer({
  info, dsId, fc, mode, idProp = 'name', selectedIndexes, onEdit, onClickFeature,
  borderPicks, borderArc, onBorderEdit,
  onAddBorderPoint,
  splitDraw, onSplitEdit,
  drawPoly, onDrawEdit,
  cleanPoly, onCleanEdit, cleanPreview,
  gapPreview, onPickGap,
  propRing, onRegionMenu,
  onGrabVertex, onReleaseDrag,
  geneBitmap, geneBounds, stainInfo, stainChannels, layers, regionsOff,
}) {
  const L = layers || DEFAULT_LAYERS;
  const picks = borderPicks || [];
  const picksKey = picks.join('|');
  const off = regionsOff || EMPTY_SET;
  const offKey = [...off].sort().join('|');
  const deckRef = useRef(null);

  // One instance each, kept for the life of the component: a new mode object
  // would reset the click sequence mid-draw.
  const splitModeRef = useRef(null);
  if (!splitModeRef.current) splitModeRef.current = new FinishableLineMode();
  const drawModeRef = useRef(null);
  if (!drawModeRef.current) drawModeRef.current = new FinishablePolygonMode();
  const cleanModeRef = useRef(null);
  if (!cleanModeRef.current) cleanModeRef.current = new FinishablePolygonMode();

  const initialViewState = useMemo(() => {
    const vw = Math.max(window.innerWidth - 340, 300);
    const vh = window.innerHeight;
    const zoom = Math.log2(Math.min(vw / info.width, vh / info.height));
    return { target: [info.width / 2, info.height / 2, 0], zoom, minZoom: -8, maxZoom: 8 };
  }, [info.width, info.height]);

  const selected = new Set(selectedIndexes);
  // changes whenever any stored colour changes, so deck knows to repaint
  const colourKey = useMemo(() => (fc.features || [])
    .map((f) => {
      const p = f.properties || {};
      const cls = (p.classification && typeof p.classification === 'object') ? p.classification : {};
      return [cls.colorRGB, cls.color, p.color].filter((v) => v != null).join('|');
    }).join(','), [fc]);
  const showEditableRegions = L.showRegions || mode === 'modify' || mode === 'border'
    || mode === 'split' || mode === 'dissolve' || mode === 'draw' || mode === 'clean';

  // base64 spec of the visible stain channels -> drives the stain tile URLs
  const stainSpec = useMemo(() => {
    if (!stainChannels) return '';
    const vis = stainChannels.filter((c) => c.visible).map((c) => ({
      index: c.index, color: c.color, min: c.min, max: c.max, visible: true,
    }));
    if (!vis.length) return '';
    return encodeURIComponent(btoa(JSON.stringify(vis)));
  }, [stainChannels]);

  // Rotating changes what a tile CONTAINS but not its (z, x, y), so nothing else
  // would tell either cache to let go: the browser holds tiles for an hour under
  // Cache-Control, and deck keeps its own by tile index. Putting the orientation
  // in the URL makes the rotated tiles different resources, and putting it in
  // refetchKey makes deck go and ask for them.
  const oKey = (() => {
    const o = (info && info.orientation) || {};
    return `${o.rot || 0}${o.flipH ? 'h' : ''}${o.flipV ? 'v' : ''}`;
  })();

  const morph = pyramidTileLayer({
    // the orientation is part of the layer identity: deck keeps a tile cache per
    // layer, and a same-id layer with a new extent goes on drawing the tiles it
    // already had -- which showed up as the old and new image both on screen
    id: `morphology-${oKey}`, refetchKey: `${dsId}|${oKey}`,
    levels: info.levels, tileSize: info.tileSize,
    width: info.width, height: info.height, opacity: 1,
    urlFor: (z, x, y) => `/api/datasets/${dsId}/tiles/${z}/${x}/${y}.png?o=${oKey}`,
  });

  const stains = (stainInfo && stainSpec)
    ? pyramidTileLayer({
        id: `stain-tiles-${oKey}`, refetchKey: `${stainSpec}|${oKey}`,
        levels: stainInfo.levels, tileSize: stainInfo.tileSize,
        // the displayed extent, which a quarter turn swaps -- info is the one the
        // backend re-reports on rotation, so both layers agree
        width: info.width, height: info.height, opacity: L.stainOpacity,
        urlFor: (z, x, y) =>
          `/api/datasets/${dsId}/stains/tiles/${z}/${x}/${y}.png?s=${stainSpec}&o=${oKey}`,
      })
    : null;

  const genes = geneBitmap && geneBounds
    ? new BitmapLayer({ id: 'genes', image: geneBitmap, bounds: geneBounds, opacity: L.geneOpacity, pickable: false })
    : null;

  const regions = new EditableGeoJsonLayer({
    id: 'regions',
    data: fc,
    mode: MODES[mode] || ViewMode,   // 'border' -> ViewMode: clicks pick regions
    coordinateSystem: COORDINATE_SYSTEM.CARTESIAN,
    selectedFeatureIndexes: selectedIndexes,
    // in split/draw/clean mode the canvas clicks are the sketch, not a region pick
    pickable: mode !== 'split' && mode !== 'draw' && mode !== 'clean',
    // A region switched off in the sidebar is still IN the file and still in this
    // layer -- indices have to keep matching `selected` -- it is just not drawn.
    getFillColor: (f) => {
      const nm = regionName(f, idProp);
      if (nm && off.has(nm)) return [0, 0, 0, 0];
      const pi = nm ? picks.indexOf(nm) : -1;
      if (pi >= 0) { const [r, g, b] = PICK_COLORS[Math.min(pi, 2)]; return [r, g, b, 80]; }
      const [r, g, b] = regionRgb(f, idProp);
      return [r, g, b, 70];
    },
    getLineColor: (f) => {
      const nm = regionName(f, idProp);
      if (nm && off.has(nm)) return [0, 0, 0, 0];
      const pi = nm ? picks.indexOf(nm) : -1;
      if (pi >= 0) { const [r, g, b] = PICK_COLORS[Math.min(pi, 2)]; return [r, g, b, 255]; }
      if (selected.has(fc.features.indexOf(f))) return [255, 80, 80, 255];
      const [r, g, b] = regionRgb(f, idProp);
      return [r, g, b, 230];      // outline matches the fill, so a colour reads
    },
    lineWidthUnits: 'pixels',
    getLineWidth: (f) => {
      const nm = regionName(f, idProp);
      if (nm && picks.indexOf(nm) >= 0) return 3;
      return selected.has(fc.features.indexOf(f)) ? 3 : 1;
    },
    updateTriggers: {
      // colourKey so a colour change repaints -- deck caches these accessors
      getFillColor: [picksKey, colourKey, offKey],
      getLineColor: [selectedIndexes, picksKey, colourKey, offKey],
      getLineWidth: [selectedIndexes, picksKey],
    },
    // draggable vertex handles (ModifyMode) -- pixel-sized + white-outlined so
    // they stay clearly visible at any zoom over the busy image
    editHandlePointRadiusScale: 1,
    getEditHandlePointRadius: 6,
    editHandlePointRadiusMinPixels: 6,
    editHandlePointRadiusMaxPixels: 14,
    editHandlePointOutline: true,
    editHandlePointStrokeWidth: 2,
    getEditHandlePointColor: [255, 60, 60, 255],
    getEditHandlePointOutlineColor: [255, 255, 255, 255],
    onEdit,
  });

  const borderFeatureIndexes = borderArc && borderArc.features
    ? borderArc.features.map((_, i) => i)
    : [];

  // Shared border segment(s) between the two picked regions, dragged in ModifyMode.
  // Editing one calls move-border, which rebuilds BOTH polygons to share the line.
  const borderLayer = (mode === 'border' && picks.length === 2 && borderArc)
    ? new EditableGeoJsonLayer({
        id: 'border-edit',
        data: borderArc,
        mode: ModifyMode,
        coordinateSystem: COORDINATE_SYSTEM.CARTESIAN,
        selectedFeatureIndexes: borderFeatureIndexes,
        pickable: true,
        getFillColor: [0, 0, 0, 0],
        getLineColor: [255, 45, 45, 255],
        lineWidthUnits: 'pixels',
        getLineWidth: 3,
        editHandlePointRadiusScale: 2.6,
        getEditHandlePointColor: [255, 45, 45, 255],
        onEdit: onBorderEdit,
      })
    : null;

  // Split mode: draw a cut line across a region (double-click to finish).
  const splitLayer = (mode === 'split')
    ? new EditableGeoJsonLayer({
        id: 'split-draw',
        data: splitDraw || { type: 'FeatureCollection', features: [] },
        mode: splitModeRef.current,
        coordinateSystem: COORDINATE_SYSTEM.CARTESIAN,
        selectedFeatureIndexes: [],
        pickable: true,
        getFillColor: [0, 0, 0, 0],
        getLineColor: [255, 45, 45, 255],
        getTentativeLineColor: [255, 120, 45, 255],
        lineWidthUnits: 'pixels',
        getLineWidth: 2,
        getTentativeLineWidth: 2,
        editHandlePointRadiusScale: 2.2,
        getEditHandlePointColor: [255, 45, 45, 255],
        onEdit: onSplitEdit,
      })
    : null;

  // Draw mode: trace an outline for a brand-new region (double-click to close it).
  const drawLayer = (mode === 'draw')
    ? new EditableGeoJsonLayer({
        id: 'region-draw',
        data: drawPoly || { type: 'FeatureCollection', features: [] },
        mode: drawModeRef.current,
        coordinateSystem: COORDINATE_SYSTEM.CARTESIAN,
        // REQUIRED: the mode reads props.selectedIndexes.length when it commits a
        // polygon. Without it finishDrawing() throws and the shape never ends --
        // which is exactly why neither right-click nor double-click closed it.
        selectedFeatureIndexes: [],
        pickable: true,
        getFillColor: [80, 230, 120, 60],
        getLineColor: [80, 230, 120, 255],
        getTentativeLineColor: [255, 200, 60, 255],
        getTentativeFillColor: [255, 200, 60, 40],
        lineWidthUnits: 'pixels',
        getLineWidth: 2,
        getTentativeLineWidth: 2,
        editHandlePointRadiusScale: 2.2,
        getEditHandlePointColor: [80, 230, 120, 255],
        onEdit: onDrawEdit,
      })
    : null;

  // Clean mode: trace a loop around stray hairlines (right-click to close it).
  const cleanLayer = (mode === 'clean')
    ? new EditableGeoJsonLayer({
        id: 'clean-draw',
        data: cleanPoly || { type: 'FeatureCollection', features: [] },
        mode: cleanModeRef.current,
        coordinateSystem: COORDINATE_SYSTEM.CARTESIAN,
        selectedFeatureIndexes: [],       // required -- see the draw layer above
        pickable: true,
        getFillColor: [255, 120, 40, 40],
        getLineColor: [255, 120, 40, 255],
        getTentativeLineColor: [255, 200, 60, 255],
        getTentativeFillColor: [255, 200, 60, 30],
        lineWidthUnits: 'pixels',
        getLineWidth: 2,
        getTentativeLineWidth: 2,
        editHandlePointRadiusScale: 2.2,
        getEditHandlePointColor: [255, 120, 40, 255],
        onEdit: onCleanEdit,
      })
    : null;

  // ...and the stray lines it found, highlighted until Apply or Cancel.
  const cleanFoundLayer = (mode === 'clean' && cleanPreview)
    ? new GeoJsonLayer({
        id: 'clean-found',
        data: { type: 'Feature', properties: {}, geometry: cleanPreview },
        coordinateSystem: COORDINATE_SYSTEM.CARTESIAN,
        pickable: false,
        filled: true,
        getFillColor: [255, 45, 45, 150],
        getLineColor: [255, 45, 45, 255],
        lineWidthUnits: 'pixels',
        getLineWidth: 1.5,
        lineWidthMinPixels: 1.5,
      })
    : null;

  // Dissolve mode: the void the backend found under the last click, highlighted
  // so you can see exactly what "Dissolve" is about to hand to its neighbours.
  const gapLayer = (mode === 'dissolve' && gapPreview)
    ? new GeoJsonLayer({
        id: 'gap-preview',
        data: { type: 'Feature', properties: {}, geometry: gapPreview },
        coordinateSystem: COORDINATE_SYSTEM.CARTESIAN,
        pickable: false,
        filled: true,
        getFillColor: [255, 45, 45, 110],
        getLineColor: [255, 45, 45, 255],
        lineWidthUnits: 'pixels',
        getLineWidth: 2,
        lineWidthMinPixels: 2,
      })
    : null;

  // Proportional editing: the falloff circle around the point being dragged, so
  // the radius the scroll wheel is changing is actually visible.
  const ringLayer = (propRing && propRing.center && propRing.radius > 0)
    ? new GeoJsonLayer({
        id: 'prop-ring',
        data: {
          type: 'Feature',
          properties: {},
          geometry: {
            type: 'LineString',
            coordinates: Array.from({ length: 65 }, (_, i) => {
              const a = (i / 64) * Math.PI * 2;
              return [propRing.center[0] + Math.cos(a) * propRing.radius,
                      propRing.center[1] + Math.sin(a) * propRing.radius];
            }),
          },
        },
        coordinateSystem: COORDINATE_SYSTEM.CARTESIAN,
        pickable: false,
        filled: false,
        getLineColor: [255, 255, 255, 210],
        lineWidthUnits: 'pixels',
        getLineWidth: 1.5,
        lineWidthMinPixels: 1.5,
      })
    : null;

  const stack = [];
  if (L.showDapi) stack.push(morph);
  if (stains) stack.push(stains);
  if (L.showGenes && genes) stack.push(genes);
  if (showEditableRegions) stack.push(regions);
  if (borderLayer) stack.push(borderLayer);
  if (splitLayer) stack.push(splitLayer);
  if (drawLayer) stack.push(drawLayer);
  if (cleanLayer) stack.push(cleanLayer);
  if (cleanFoundLayer) stack.push(cleanFoundLayer);
  if (gapLayer) stack.push(gapLayer);
  if (ringLayer) stack.push(ringLayer);

  // Dissolve mode: a gap has no polygon under it, so there is no picked feature to
  // click. deck still reports the map coordinate under the pointer and that is the
  // preferred source; the host-div handler is the fallback for when deck's own
  // onClick doesn't fire. Whichever runs first claims the click.
  const downAtRef = useRef(null);
  const gapClickAtRef = useRef(0);
  const takeGapClick = (coord) => {
    if (!coord || !onPickGap) return;
    const now = Date.now();
    if (now - gapClickAtRef.current < 300) return;   // the other handler already took it
    gapClickAtRef.current = now;
    onPickGap([coord[0], coord[1]]);
  };
  const handleHostPointerDown = (e) => { downAtRef.current = [e.clientX, e.clientY]; };
  const handleHostClick = (e) => {
    if (mode !== 'dissolve' || !onPickGap) return;
    if (Date.now() - gapClickAtRef.current < 300) return;   // deck's onClick got it
    const down = downAtRef.current;
    if (down && Math.hypot(e.clientX - down[0], e.clientY - down[1]) > 5) return;  // that was a pan
    const deck = deckRef.current && deckRef.current.deck;
    if (!deck) return;
    const canvas = deck.getCanvas && deck.getCanvas();
    const rect = (canvas || e.currentTarget).getBoundingClientRect();
    const x = e.clientX - rect.left;
    const y = e.clientY - rect.top;
    // getViewports({x, y}) matches nothing for a bare point in deck v9 -- give it a
    // 1px rect, and fall back to the single view this app renders.
    const hit = deck.getViewports({ x, y, width: 1, height: 1 });
    const viewport = (hit && hit[0]) || (deck.getViewports() || [])[0];
    if (!viewport) return;
    takeGapClick(viewport.unproject([x - viewport.x, y - viewport.y]));
  };

  // Right-click has to be caught in the CAPTURE phase on the host element. React's
  // onContextMenu is a bubble-phase handler on the React root, so deck's own event
  // machinery (which listens on a child, .deck-events-root) can swallow it first --
  // which is why right-click appeared to do nothing while sketching.
  //
  // While sketching we go earlier still, to pointerdown: `contextmenu` timing
  // differs between browsers, and by the time it lands deck may already have
  // treated the press as a click and added a stray vertex. Finishing on the
  // button-2 press also stops the rubber-band line dead, which is the point --
  // you should not have to drag a trailing line anywhere to end the shape.
  const hostRef = useRef(null);
  const ctxHandlerRef = useRef(null);
  const sketchModeRef = useRef(null);
  sketchModeRef.current = mode === 'split' ? splitModeRef.current
    : mode === 'draw' ? drawModeRef.current
    : mode === 'clean' ? cleanModeRef.current : null;

  useEffect(() => {
    const el = hostRef.current;
    if (!el) return undefined;
    const swallow = (e) => { e.preventDefault(); e.stopPropagation(); };
    const onDown = (e) => {
      if (e.button !== 2 || !sketchModeRef.current) return;
      swallow(e);
      sketchModeRef.current.finishNow();   // too few points -> sketch left alone
    };
    const onCtx = (e) => {
      if (sketchModeRef.current) {         // never show the browser menu mid-sketch
        swallow(e);
        sketchModeRef.current.finishNow(); // fallback if pointerdown was missed
        return;
      }
      if (ctxHandlerRef.current) ctxHandlerRef.current(e);
    };
    el.addEventListener('pointerdown', onDown, true);
    el.addEventListener('mousedown', onDown, true);
    el.addEventListener('contextmenu', onCtx, true);
    return () => {
      el.removeEventListener('pointerdown', onDown, true);
      el.removeEventListener('mousedown', onDown, true);
      el.removeEventListener('contextmenu', onCtx, true);
    };
  }, []);

  const handleContextMenu = (e) => {
    // While sketching, right-click ENDS the shape -- no hunting for a double-click.
    // The mode's own handleClick normally does this (it can see the button); this
    // is the fallback for when deck doesn't forward the right button as a click.
    // finishNow() is a no-op once the sequence is empty, so both firing is safe.
    if (mode === 'split' || mode === 'draw' || mode === 'clean') return;   // handled on pointerdown above

    const deck = deckRef.current && deckRef.current.deck;
    if (!deck) return;
    const canvas = deck.getCanvas && deck.getCanvas();
    const rect = (canvas || e.currentTarget).getBoundingClientRect();
    const x = e.clientX - rect.left;
    const y = e.clientY - rect.top;

    // border mode: right-click the red arc to drop a new point on it
    if (mode === 'border' && picks.length === 2 && borderArc && onAddBorderPoint) {
      const pick = deck.pickObject({ x, y, radius: 18 });
      const layerId = pick && pick.layer && String(pick.layer.id);
      if (layerId && layerId.includes('border-edit')) {
        e.preventDefault();
        const viewport = (deck.getViewports({ x, y }) || [])[0];
        const coord = (pick && pick.coordinate)
          || (viewport && viewport.unproject([x - viewport.x, y - viewport.y]));
        if (coord) onAddBorderPoint([coord[0], coord[1]], pickedFeatureIndex(pick));
        return;
      }
    }

    // anywhere else: right-click a region to rename it
    if (!onRegionMenu) return;
    const hit = deck.pickObject({ x, y, radius: 2 });
    if (!hit || !hit.layer || !String(hit.layer.id).includes('regions')) return;
    const t = hit.object && hit.object.geometry && hit.object.geometry.type;
    if (t !== 'Polygon' && t !== 'MultiPolygon') return;   // not a vertex handle
    e.preventDefault();
    onRegionMenu({
      index: hit.index,
      name: regionName(hit.object, idProp),
      x: e.clientX,
      y: e.clientY,
    });
  };
  ctxHandlerRef.current = handleContextMenu;   // always the latest closure

  return (
    <div className="deck-host" ref={hostRef}
      onPointerDown={handleHostPointerDown} onClick={handleHostClick}>
      <DeckGL
      ref={deckRef}
      views={[new OrthographicView({ flipY: true, controller: true })]}
      initialViewState={initialViewState}
      layers={stack}
      getCursor={() => (mode === 'modify' || mode === 'border' || mode === 'split'
        || mode === 'dissolve' || mode === 'draw' || mode === 'clean' ? 'crosshair' : 'grab')}
      onDragStart={(info) => {
        if (info && info.object && info.object.geometry
            && info.object.geometry.type === 'Point' && onGrabVertex) {
          onGrabVertex(info.layer && info.layer.id, pickedFeatureIndex(info));
        }
      }}
      onDragEnd={() => { if (onReleaseDrag) onReleaseDrag(); }}
      onClick={(pickInfo) => {
        if (mode === 'dissolve') { takeGapClick(pickInfo && pickInfo.coordinate); return; }
        if (pickInfo && pickInfo.layer && String(pickInfo.layer.id).includes('regions')) {
          onClickFeature(pickInfo.index, pickInfo.object);
        }
      }}
      />
    </div>
  );
}

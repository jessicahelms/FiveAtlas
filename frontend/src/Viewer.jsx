import { useEffect, useMemo, useRef, useState, useSyncExternalStore } from 'react';
import {
  DeckGL,
  OrthographicView,
  COORDINATE_SYSTEM,
  BitmapLayer,
  GeoJsonLayer,
} from 'deck.gl';
import {
  EditableGeoJsonLayer,
  ViewMode,
  ModifyMode,
  TranslateMode,
  DrawLineStringMode,
  DrawPolygonMode,
  DrawPolygonByDraggingMode,
} from '@deck.gl-community/editable-layers';
import { PointEditMode, existingPointProps } from './pointEditMode';
import { bunchedPoints, pointKey } from './bunched';
import { stainTileLayer } from './stainLayer';
import { morphTileLayer } from './morphLayer';
import { subscribeMorphLive, getMorphLive } from './morphLive';
import { subscribeStainLive, getStainLive } from './stainLive';

// Edit points uses PointEditMode: a plain click on a point never deletes it,
// a right-click does (see handleContextMenu). The shared-border editor below
// keeps the library's ModifyMode.
const MODES = { view: ViewMode, modify: PointEditMode, translate: TranslateMode };

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
//
// dropShort: asking to finish with too few points DROPS them. Kept, the lone
// point of a split line whose other click deck never delivered (it holds each
// click 300 ms for a possible double-click, and a press inside that window
// cancels it) silently became the first point of the NEXT line -- and the
// detour from it changed which region the cut was for.
function finishableMode(Base, minPoints, dropShort = false) {
  return class extends Base {
    // No "Distance: N pixels" label. DrawLineStringMode hangs it on the LAST
    // placed point, which is the one spot you are looking at while placing the
    // next one, and it covers the image underneath. DrawPolygonMode never had
    // one, so in practice this only changes the split tool.
    getTooltips() { return []; }

    handleClick(event, props) {
      this._props = props;
      // a right-click ends the shape and must never leave a stray vertex behind
      if (isRightButton(event)) { this.finishNow(); return; }
      super.handleClick(event, props);
    }
    handlePointerMove(event, props) { this._props = props; super.handlePointerMove(event, props); }
    // Enter finishes the way a right-click does (the library's Enter left a
    // too-short line in place)
    handleKeyUp(event, props) {
      this._props = props;
      if (dropShort && event && event.key === 'Enter') { this.finishNow(); return; }
      super.handleKeyUp(event, props);
    }
    pointCount() { return this.getClickSequence().length; }
    // drop the points placed so far, and redraw without them
    cancelNow() {
      if (!this.pointCount()) return;
      this.resetClickSequence();
      if (this._props) {
        this._props.onEdit({ updatedData: this._props.data, editType: 'cancelFeature',
          editContext: { tooFewPoints: true } });
      }
    }
    finishNow() {
      if (!this._props) return false;
      if (this.pointCount() < minPoints) {
        if (dropShort) this.cancelNow();
        return false;
      }
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
const FinishableLineMode = finishableMode(DrawLineStringMode, 2, true);
const FinishablePolygonMode = finishableMode(DrawPolygonMode, 3);
const DEFAULT_LAYERS = {
  showDapi: true, showGenes: true, showRegions: true, geneOpacity: 1, stainOpacity: 1,
  regionFill: 70, outlineWidth: 1,
};
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

// A {center, radius} ring as a closed LineString feature, or null. Built once per
// ring value (memoised by the caller) rather than on every render.
function circleFeature(ring, segments) {
  if (!ring || !ring.center || !(ring.radius > 0)) return null;
  return {
    type: 'Feature',
    properties: {},
    geometry: {
      type: 'LineString',
      coordinates: Array.from({ length: segments + 1 }, (_, i) => {
        const a = (i / segments) * Math.PI * 2;
        return [ring.center[0] + Math.cos(a) * ring.radius,
                ring.center[1] + Math.sin(a) * ring.radius];
      }),
    },
  };
}

const EMPTY_SET = new Set();
const TILE_CACHE_BYTES = 256 * 1024 * 1024;   // per tile layer
const STAIN_CACHE_BYTES = 192 * 1024 * 1024;  // all stain channels together

// Draw at full device resolution when the view is still -- the sharpest the
// panel can show -- but drop to 1x while panning/zooming/dragging, when the
// moving image does not need the pixels and a laptop's integrated GPU cannot
// spare them. Idle is capped at 2x so a 3x phone panel does not quadruple the
// fill cost. The viewer flips between the two on deck's interaction state.
// (Was a flat 1.5x cap, which softened everything on a 2x laptop even at rest.)
const DEVICE_PIXELS_IDLE = Math.min(window.devicePixelRatio || 1, 2);
const DEVICE_PIXELS_MOVING = 1;

// One view for the life of the app: a new View every render makes deck diff it.
const VIEWS = [new OrthographicView({ flipY: true, controller: true })];

// The morphology is drawn grey. One array for the life of the app: deck compares
// a layer's non-function props by identity, and a fresh [255,255,255] each render
// made the tile layer throw away every cached tile's sublayer on every frame.
const MORPH_TINT = [255, 255, 255];

// How long the view must sit still before it is redrawn at full resolution.
const SETTLE_MS = 200;

export default function Viewer({
  info, dsId, fc, mode, idProp = 'name', selectedIndexes, onEdit, onClickFeature,
  borderPicks, borderArc, onBorderEdit,
  onAddBorderPoint,
  splitDraw, onSplitEdit,
  drawPoly, onDrawEdit,
  cleanPoly, onCleanEdit, cleanPreview,
  gapPreview, onPickGap,
  propRing, reachRing, onRegionMenu, onDeletePoint, onDeleteBorderPoint,
  onGrabVertex, onReleaseDrag, propEdit, propRadius,
  geneBitmap, geneBounds, geneMode, stainInfo, stainChannels, morphWin, layers, regionsOff,
  bordersOff, fillsOff, focus, peek,
}) {
  const L = layers || DEFAULT_LAYERS;
  const picks = borderPicks || [];
  const picksKey = picks.join('|');
  const off = regionsOff || EMPTY_SET;
  const offKey = [...off].sort().join('|');
  const bOff = bordersOff || EMPTY_SET;
  const fOff = fillsOff || EMPTY_SET;
  const bOffKey = [...bOff].sort().join('|');
  const fOffKey = [...fOff].sort().join('|');
  const deckRef = useRef(null);
  // Full resolution when still, 1x while the view is moving (see DEVICE_PIXELS_*
  // above). Every view change (pan, wheel, double-click, keyboard, transition
  // frame) marks the view as moving and restarts a short settle timer, so the
  // flag cannot get stuck: deck's interaction-state flags could, after a zoom
  // clamped at min/max zoom never started the transition that clears them.
  // `interacting` only flips at the edges, so this is two re-renders per gesture.
  const [interacting, setInteracting] = useState(false);
  const idleTimerRef = useRef(null);
  useEffect(() => () => { if (idleTimerRef.current) clearTimeout(idleTimerRef.current); }, []);
  const markMoving = () => {
    setInteracting(true);                         // React bails if already true
    if (idleTimerRef.current) clearTimeout(idleTimerRef.current);
    idleTimerRef.current = setTimeout(() => {
      idleTimerRef.current = null;
      setInteracting(false);
    }, SETTLE_MS);
  };

  // One instance each, kept for the life of the component: a new mode object
  // would reset the click sequence mid-draw.
  const splitModeRef = useRef(null);
  if (!splitModeRef.current) splitModeRef.current = new FinishableLineMode();
  const drawModeRef = useRef(null);
  if (!drawModeRef.current) drawModeRef.current = new FinishablePolygonMode();
  // Clean mode draws by DRAGGING -- you circle a stray line the way you would
  // with a pen, and releasing the button closes the loop. Click-per-vertex was
  // wrong here: "circle the lines" means a sweep, and a left-drag in a click
  // mode is a pan, which read as the tool not working at all. The mode cancels
  // the pan itself while the button is down.
  const cleanModeRef = useRef(null);
  if (!cleanModeRef.current) cleanModeRef.current = new DrawPolygonByDraggingMode();
  // The points placed so far live in these MODE objects, which outlive their
  // layers: App's clearSplit/clearDraw only empty the layer data. So a point
  // left from an earlier attempt (Split or Draw left mid-line) came back as the
  // start of the next line. Start clean on every mode change, and whenever App
  // clears the split sketch. Done in render, so the stale point is never drawn.
  const sketchSeenRef = useRef({ mode, splitDraw });
  if (sketchSeenRef.current.mode !== mode || sketchSeenRef.current.splitDraw !== splitDraw) {
    if (sketchSeenRef.current.mode !== mode) drawModeRef.current.resetClickSequence();
    splitModeRef.current.resetClickSequence();
    sketchSeenRef.current = { mode, splitDraw };
  }

  const initialViewState = useMemo(() => {
    const vw = Math.max(window.innerWidth - 340, 300);
    const vh = window.innerHeight;
    const zoom = Math.log2(Math.min(vw / info.width, vh / info.height));
    return { target: [info.width / 2, info.height / 2, 0], zoom, minZoom: -8, maxZoom: 8 };
  }, [info.width, info.height]);

  // changes whenever any stored colour changes, so deck knows to repaint
  const colourKey = useMemo(() => (fc.features || [])
    .map((f) => {
      const p = f.properties || {};
      const cls = (p.classification && typeof p.classification === 'object') ? p.classification : {};
      return [cls.colorRGB, cls.color, p.color].filter((v) => v != null).join('|');
    }).join(','), [fc]);
  const showEditableRegions = L.showRegions || mode === 'modify' || mode === 'border'
    || mode === 'split' || mode === 'dissolve' || mode === 'draw' || mode === 'clean';

  // Stains are blended on the GPU (stainLayer.js). Only WHICH channels are
  // visible decides what gets downloaded; colour and contrast are uniforms --
  // including the live values a slider is mid-drag on, which never touch App.
  const stainLive = useSyncExternalStore(subscribeStainLive, getStainLive);
  const stainFetch = useMemo(() => (stainChannels || [])
    .filter((c) => c.visible).map((c) => c.index), [stainChannels]);
  const stainSettings = useMemo(() => {
    const out = {};
    (stainChannels || []).forEach((c) => {
      out[c.index] = { color: c.color, min: c.min, max: c.max, gamma: c.gamma, visible: c.visible, ...stainLive[c.index] };
    });
    return out;
  }, [stainChannels, stainLive]);

  // The morphology window (brightness/contrast/gamma) is a GPU uniform too, with
  // the live mid-drag value kept out of App the same way the stains are. Falls
  // back to the dataset's auto-levelled contrast until the user touches it.
  const morphLive = useSyncExternalStore(subscribeMorphLive, getMorphLive);
  const morphWindow = useMemo(() => {
    const m = { ...(morphWin || {}), ...(morphLive || {}) };
    const c = (info && info.contrast) || {};
    const lo = Number(m.lo);
    const hi = Number(m.hi);
    const g = Number(m.gamma);
    return [
      Number.isFinite(lo) ? lo : (Number(c.lo) || 0),
      Number.isFinite(hi) ? hi : (Number(c.hi) || 1),
      Number.isFinite(g) ? g : 1,
    ];
  }, [morphWin, morphLive, info]);

  // Rotating changes what a tile CONTAINS but not its (z, x, y), so nothing else
  // would tell either cache to let go: the browser holds tiles for an hour under
  // Cache-Control, and deck keeps its own by tile index. Putting the orientation
  // in the URL makes the rotated tiles different resources, and putting it in
  // refetchKey makes deck go and ask for them.
  const oKey = (() => {
    const o = (info && info.orientation) || {};
    return `${o.rot || 0}${o.flipH ? 'h' : ''}${o.flipV ? 'v' : ''}`;
  })();

  const morph = morphTileLayer({
    // the orientation is part of the layer identity: deck keeps a tile cache per
    // layer, and a same-id layer with a new extent goes on drawing the tiles it
    // already had -- which showed up as the old and new image both on screen
    id: `morphology-${oKey}`, refetchKey: `${dsId}|${oKey}`,
    levels: info.levels, tileSize: info.tileSize,
    width: info.width, height: info.height, opacity: 1,
    win: morphWindow, tint: MORPH_TINT,
    urlFor: (z, x, y) => `/api/datasets/${dsId}/tiles/raw/${z}/${x}/${y}?o=${oKey}`,
    cacheBytes: TILE_CACHE_BYTES,
  });

  const stains = (stainInfo && stainFetch.length)
    ? stainTileLayer({
        id: `stain-tiles-${oKey}`, dsId, oKey,
        levels: stainInfo.levels, tileSize: stainInfo.tileSize,
        // the displayed extent, which a quarter turn swaps -- info is the one the
        // backend re-reports on rotation, so both layers agree
        width: info.width, height: info.height, opacity: L.stainOpacity,
        fetchIndexes: stainFetch, settings: stainSettings,
        // one channel tile is 2 MB; on integrated graphics GPU memory is system
        // RAM, so the stains get a tighter budget than the morphology
        cacheBytes: STAIN_CACHE_BYTES,
      })
    : null;

  // Ink mode is SQUARE BINS: nearest sampling keeps them square. The default
  // linear filter smears each hot bin into a soft plus-shape -- the "crosses".
  const genes = geneBitmap && geneBounds
    ? new BitmapLayer({
        id: `genes-${geneMode || 'glow'}`,
        image: geneBitmap,
        bounds: geneBounds,
        opacity: L.geneOpacity,
        pickable: false,
        textureParameters: geneMode === 'ink'
          ? { minFilter: 'nearest', magFilter: 'nearest' }
          : undefined,
      })
    : null;

  // colourKey so a colour change repaints -- deck caches these accessors. Kept as
  // ONE object across renders: the editable layer nests these arrays inside its
  // own triggers, and deck compares a nested trigger by identity, so fresh arrays
  // every render recoloured every region on every pointer move of a drag.
  const fillAlpha = Math.max(0, Math.min(255, Math.round(Number(L.regionFill ?? 70))));
  const lineW = Math.max(0.5, Number(L.outlineWidth ?? 1));
  // Edit points: the selected region's points that sit right next to another
  // are drawn orange, so a cluster that Tidy points would clear is visible.
  const bunched = useMemo(() => {
    if (mode !== 'modify' || !fc || !selectedIndexes || selectedIndexes.length !== 1) return null;
    const pts = bunchedPoints(fc.features[selectedIndexes[0]]);
    return pts.length ? new Set(pts.map(pointKey)) : null;
  }, [mode, fc, selectedIndexes]);
  const bunchedKey = bunched ? bunched.size + ':' + (fc && fc.features.length) : 0;
  const regionTriggers = useMemo(() => ({
    getFillColor: [picksKey, colourKey, offKey, fOffKey, fillAlpha],
    getLineColor: [selectedIndexes, picksKey, colourKey, offKey, bOffKey],
    getLineWidth: [selectedIndexes, picksKey, lineW],
    getEditHandlePointColor: [bunchedKey, bunched],
  }), [picksKey, colourKey, offKey, fOffKey, bOffKey, selectedIndexes, fillAlpha, lineW,
    bunchedKey, bunched]);

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
      // a pick highlight shows first -- an operation you are mid-way through must
      // stay legible even on a region you switched off or whose face is hidden
      // (picking a switched-off hemi by name used to show nothing at all)
      const pi = nm ? picks.indexOf(nm) : -1;
      if (pi >= 0) { const [r, g, b] = PICK_COLORS[Math.min(pi, 2)]; return [r, g, b, 80]; }
      if (nm && off.has(nm)) return [0, 0, 0, 0];
      if (nm && fOff.has(nm)) return [0, 0, 0, 0];
      const [r, g, b] = regionRgb(f, idProp);
      return [r, g, b, fillAlpha];
    },
    // The editable layer passes isSelected as the second argument -- it checks
    // its own short list of selected features, where fc.features.indexOf(f)
    // scanned every region for every region on each repaint.
    getLineColor: (f, isSelected) => {
      const nm = regionName(f, idProp);
      // picks and the selection show even when switched off or hidden
      const pi = nm ? picks.indexOf(nm) : -1;
      if (pi >= 0) { const [r, g, b] = PICK_COLORS[Math.min(pi, 2)]; return [r, g, b, 255]; }
      if (isSelected === true) return [255, 80, 80, 255];
      if (nm && off.has(nm)) return [0, 0, 0, 0];
      if (nm && bOff.has(nm)) return [0, 0, 0, 0];
      const [r, g, b] = regionRgb(f, idProp);
      return [r, g, b, 230];      // outline matches the fill, so a colour reads
    },
    lineWidthUnits: 'pixels',
    getLineWidth: (f, isSelected) => {
      const nm = regionName(f, idProp);
      if (nm && picks.indexOf(nm) >= 0) return Math.max(3, lineW + 2);
      return isSelected === true ? Math.max(3, lineW + 2) : lineW;
    },
    updateTriggers: regionTriggers,
    // draggable vertex handles (ModifyMode) -- pixel-sized + white-outlined so
    // they stay clearly visible at any zoom over the busy image
    editHandlePointRadiusScale: 1,
    getEditHandlePointRadius: 6,
    editHandlePointRadiusMinPixels: 6,
    editHandlePointRadiusMaxPixels: 14,
    editHandlePointOutline: true,
    editHandlePointStrokeWidth: 2,
    getEditHandlePointColor: bunched
      ? (h) => (h && h.geometry && bunched.has(pointKey(h.geometry.coordinates))
        ? [255, 165, 0, 255] : [255, 60, 60, 255])
      : [255, 60, 60, 255],
    getEditHandlePointOutlineColor: [255, 255, 255, 255],
    onEdit,
  });

  // stable across renders: a new array each time churned the border layer's own
  // update triggers on every drag frame
  const nBorderFeatures = (borderArc && borderArc.features) ? borderArc.features.length : 0;
  const borderFeatureIndexes = useMemo(
    () => Array.from({ length: nBorderFeatures }, (_, i) => i), [nBorderFeatures]);

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
        modeConfig: { throttleMs: 50 },   // ~20 vertices/s is plenty for a loop
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
  // the radius the scroll wheel is changing is actually visible. Resized with no
  // point held (slider, [ ], Alt + scroll -- the touchpad way), it shows for a
  // moment at the pointer, or mid-view when the pointer is off the map, so the
  // size can be judged before grabbing. The pointer is kept in a ref: following
  // it in state would re-render the map on every mouse move.
  const hoverRef = useRef(null);
  const [ringPreview, setRingPreview] = useState(null);
  const previewTimer = useRef(null);
  const lastRadius = useRef(propRadius);
  useEffect(() => () => clearTimeout(previewTimer.current), []);
  useEffect(() => {
    const changed = lastRadius.current !== propRadius;
    lastRadius.current = propRadius;
    if (!changed || !propEdit || propRing || !(mode === 'modify' || mode === 'border')) return;
    let center = hoverRef.current;
    if (!center) {
      const vp = deckRef.current && deckRef.current.deck
        && deckRef.current.deck.getViewports()[0];
      if (!vp) return;
      center = vp.unproject([vp.width / 2, vp.height / 2]);
    }
    setRingPreview({ center: [center[0], center[1]], radius: propRadius });
    clearTimeout(previewTimer.current);
    previewTimer.current = setTimeout(() => setRingPreview(null), 1200);
  }, [propRadius, propEdit, propRing, mode]);
  useEffect(() => { if (propRing || !propEdit) setRingPreview(null); }, [propRing, propEdit]);
  const propRingData = useMemo(() => circleFeature(propRing || ringPreview, 64),
    [propRing, ringPreview]);
  const ringLayer = propRingData
    ? new GeoJsonLayer({
        id: 'prop-ring',
        data: propRingData,
        coordinateSystem: COORDINATE_SYSTEM.CARTESIAN,
        pickable: false,
        filled: false,
        getLineColor: [255, 255, 255, 210],
        lineWidthUnits: 'pixels',
        getLineWidth: 1.5,
        lineWidthMinPixels: 1.5,
      })
    : null;

  // Snap reach: the circle is as WIDE as the widest gap that reach will bridge
  // (a close of radius r closes a gap up to 2r across), sitting at the narrowest
  // point between the two picked regions.
  const reachRingData = useMemo(() => circleFeature(reachRing, 96), [reachRing]);
  const reachLayer = reachRingData
    ? new GeoJsonLayer({
        id: 'reach-ring',
        data: reachRingData,
        coordinateSystem: COORDINATE_SYSTEM.CARTESIAN,
        pickable: false,
        filled: false,
        getLineColor: [255, 196, 64, 220],
        lineWidthUnits: 'pixels',
        getLineWidth: 2,
        lineWidthMinPixels: 2,
      })
    : null;

  const stack = [];
  if (L.showDapi) stack.push(morph);
  if (stains) stack.push(stains);
  if (L.showGenes && genes) stack.push(genes);
  // Holding H peeks at the imagery: regions and the border handle step aside.
  if (showEditableRegions && !peek) stack.push(regions);
  if (borderLayer && !peek) stack.push(borderLayer);
  if (splitLayer) stack.push(splitLayer);
  if (drawLayer) stack.push(drawLayer);
  if (cleanLayer) stack.push(cleanLayer);
  if (cleanFoundLayer) stack.push(cleanFoundLayer);
  if (gapLayer) stack.push(gapLayer);
  if (reachLayer) stack.push(reachLayer);
  if (ringLayer) stack.push(ringLayer);

  // Dissolve mode: a gap has no polygon under it, so there is no picked feature to
  // click. deck still reports the map coordinate under the pointer and that is the
  // preferred source; the host-div handler is the fallback for when deck's own
  // onClick doesn't fire. Whichever runs first claims the click.
  const downAtRef = useRef(null);
  const gapClickAtRef = useRef(0);
  const downSeqRef = useRef(0);       // one count per press on the map
  const gapClaimRef = useRef(-1);     // the press that already asked for a gap
  // One press, one gap request. A 300 ms window was not enough: deck's onClick
  // arrived ~318 ms after the host's, so every click asked the backend twice and
  // whichever answer landed last won the preview. The time cap is only a fallback
  // in case a press was ever missed by the host's pointerdown.
  const gapAlreadyClaimed = () => gapClaimRef.current === downSeqRef.current
    && Date.now() - gapClickAtRef.current < 1500;
  const takeGapClick = (coord) => {
    if (!coord || !onPickGap) return;
    if (gapAlreadyClaimed()) return;            // the other handler already took it
    gapClaimRef.current = downSeqRef.current;
    gapClickAtRef.current = Date.now();
    onPickGap([coord[0], coord[1]]);
  };
  const handleHostPointerDown = (e) => {
    downAtRef.current = [e.clientX, e.clientY];
    downSeqRef.current += 1;
  };
  const handleHostClick = (e) => {
    if (mode !== 'dissolve' || !onPickGap) return;
    if (gapAlreadyClaimed()) return;            // deck's onClick got it
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

  // Zoom to a region, or fit the section, on request. deck re-fits whenever
  // initialViewState changes; the seq term makes a repeat request for the same
  // region (after panning away) count as a change.
  const [viewOverride, setViewOverride] = useState(null);
  // The viewer is remounted for a new dataset or orientation, and the focus it
  // mounts with is the LAST zoom -- in the old dataset's (or the unrotated)
  // coordinates. Only a request made after mount moves the view.
  const mountFocus = useRef(focus);
  useEffect(() => {
    if (!focus || focus === mountFocus.current) return;
    const host = hostRef.current;
    const vw = (host && host.clientWidth) || Math.max(window.innerWidth - 340, 300);
    const vh = (host && host.clientHeight) || window.innerHeight;
    const [x0, y0, x1, y1] = focus.bounds || [0, 0, info.width, info.height];
    const w = Math.max(x1 - x0, 1);
    const h = Math.max(y1 - y0, 1);
    const zoom = Math.min(6, Math.log2(Math.min(vw / w, vh / h)) - 0.2);
    setViewOverride({
      target: [(x0 + x1) / 2, (y0 + y1) / 2, 0],
      zoom: zoom + (focus.seq % 2) * 1e-9, minZoom: -8, maxZoom: 8,
    });
  }, [focus]); // eslint-disable-line react-hooks/exhaustive-deps

  const ctxHandlerRef = useRef(null);
  const sketchModeRef = useRef(null);
  // clean mode is NOT here: it draws by dragging and finishes on release, so
  // right-click has no finishing job to do (and the drag mode has no finishNow).
  sketchModeRef.current = mode === 'split' ? splitModeRef.current
    : mode === 'draw' ? drawModeRef.current : null;

  useEffect(() => {
    const el = hostRef.current;
    if (!el) return undefined;
    const swallow = (e) => { e.preventDefault(); e.stopPropagation(); };
    const onDown = (e) => {
      if (e.button !== 2 || !sketchModeRef.current) return;
      swallow(e);
      sketchModeRef.current.finishNow();   // too few points: a split drops them, a drawing keeps them
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

    // border mode: right-click a POINT on the red arc to delete it, or the line
    // between points to drop a new one there
    if (mode === 'border' && picks.length === 2 && borderArc && onAddBorderPoint) {
      const pick = deck.pickObject({ x, y, radius: 18 });
      const layerId = pick && pick.layer && String(pick.layer.id);
      if (layerId && layerId.includes('border-edit')) {
        e.preventDefault();
        if (existingPointProps(pick.object) && onDeleteBorderPoint) {
          onDeleteBorderPoint(pick.object.geometry.coordinates);
          return;
        }
        const viewport = (deck.getViewports({ x, y }) || [])[0];
        const coord = (pick && pick.coordinate)
          || (viewport && viewport.unproject([x - viewport.x, y - viewport.y]));
        if (coord) onAddBorderPoint([coord[0], coord[1]], pickedFeatureIndex(pick));
        return;
      }
    }

    // Edit points: right-click a point to delete it. Picked with a radius a
    // little larger than the handle, so it does not take pixel-perfect aim. The
    // server does the deleting: a point on a shared border goes from both
    // regions, and the rest of that stretch re-spaces to close the gap.
    if (mode === 'modify' && onDeletePoint) {
      const near = deck.pickObject({ x, y, radius: 10 });
      const pr = near && existingPointProps(near.object);
      if (pr) {
        e.preventDefault();                // and no browser menu over the point
        const f = fc && fc.features && fc.features[pr.featureIndex];
        if (f) onDeletePoint(regionName(f, idProp), near.object.geometry.coordinates);
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
      feature: hit.object,     // the menu checks the index still points at it
      name: regionName(hit.object, idProp),
      x: e.clientX,
      y: e.clientY,
      // where on the map, so the menu can offer "Delete this piece" for the
      // separate piece of a region that was right-clicked
      coord: hit.coordinate ? [hit.coordinate[0], hit.coordinate[1]] : null,
    });
  };
  ctxHandlerRef.current = handleContextMenu;   // always the latest closure

  return (
    <div className="deck-host" ref={hostRef}
      onPointerDown={handleHostPointerDown} onClick={handleHostClick}>
      <DeckGL
      ref={deckRef}
      views={VIEWS}
      useDevicePixels={interacting ? DEVICE_PIXELS_MOVING : DEVICE_PIXELS_IDLE}
      initialViewState={viewOverride || initialViewState}
      // the region under the pointer, by name -- they were known by colour alone
      getTooltip={({ object, layer }) => {
        if (!object || !layer || !String(layer.id).startsWith('regions')) return null;
        const g = object.geometry;
        if (!g || (g.type !== 'Polygon' && g.type !== 'MultiPolygon')) return null;
        const nm = regionName(object, idProp);
        if (!nm) return null;
        return { text: off.has(nm) ? `${nm} (switched off)` : nm };
      }}
      // returns nothing, so deck keeps owning the view (initialViewState)
      onViewStateChange={() => { markMoving(); }}
      // where the pointer is on the map, for the proportional ring preview
      onHover={(h) => { hoverRef.current = h && h.coordinate ? h.coordinate : null; }}
      layers={stack}
      getCursor={() => (mode === 'modify' || mode === 'border' || mode === 'split'
        || mode === 'dissolve' || mode === 'draw' || mode === 'clean' ? 'crosshair' : 'grab')}
      onDragStart={(info) => {
        // EVERY drag start is reported, not only one whose pick landed on a
        // vertex handle, so the proportional base is always invalidated -- a
        // missed pick used to leave the previous gesture's base in place.
        if (!onGrabVertex) return;
        const onVertex = !!(info && info.object && info.object.geometry
          && info.object.geometry.type === 'Point');
        onGrabVertex(info.layer && info.layer.id, pickedFeatureIndex(info), onVertex);
      }}
      onDragEnd={() => { if (onReleaseDrag) onReleaseDrag(); }}
      onClick={(pickInfo, event) => {
        if (mode === 'dissolve') { takeGapClick(pickInfo && pickInfo.coordinate); return; }
        if (pickInfo && pickInfo.layer && String(pickInfo.layer.id).includes('regions')) {
          const src = event && event.srcEvent;
          onClickFeature(pickInfo.index, pickInfo.object, {
            ctrl: !!(src && (src.ctrlKey || src.metaKey)),
            shift: !!(src && src.shiftKey),
          });
        }
      }}
      />
    </div>
  );
}

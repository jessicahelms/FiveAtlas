import { useEffect, useRef, useState } from 'react';
import { setStainLive, clearStainLive } from './stainLive';
import { setMorphLive, clearMorphLive } from './morphLive';
import { withStableHandlers } from './stableProps';
import FoldTitle from './FoldTitle';

const toHex = (rgb) =>
  '#' + rgb.map((v) => Math.max(0, Math.min(255, v | 0)).toString(16).padStart(2, '0')).join('');
const fromHex = (h) => [1, 3, 5].map((i) => parseInt(h.slice(i, i + 2), 16));

// gamma ("contrast" in Xenium): 1 = linear, >1 lifts dim structure, <1 deepens
const GAMMA_MIN = 0.3;
const GAMMA_MAX = 3;

function ChannelRow({ c, i, keyName, onChange, onRemove, live, withGamma }) {
  const dataMax = Math.max(1, Number(c.dataMax) || 1);
  const step = Math.max(1, Math.round(dataMax / 500));
  const minCeil = Math.max(0, dataMax - step);

  // The slider tracks the hand; the CHANNEL only takes the value once the hand
  // pauses. Committing per tick re-requests the whole tile pyramid for every
  // pixel of drag — which is why these sliders used to be greyed out entirely.
  const [pend, setPend] = useState(null);          // {min,max} while dragging
  const timer = useRef(null);
  useEffect(() => () => clearTimeout(timer.current), []);
  useEffect(() => {                                      // outside update wins
    // ...unless the hand is still moving: a commit from an earlier pause would
    // otherwise snap the slider (and the live stain preview) back mid-drag
    if (timer.current) return;
    setPend(null);
    if (live) clearStainLive(c.index);
  }, [c.min, c.max, c.gamma]);
  useEffect(() => () => { if (live) clearStainLive(c.index); }, []);

  const shownMin = Math.min(Math.max(0, Number(pend ? pend.min : c.min) || 0), minCeil);
  const shownMax = Math.min(Math.max(shownMin + step,
    Number(pend ? pend.max : c.max) || dataMax), dataMax);
  const shownGamma = Math.min(GAMMA_MAX, Math.max(GAMMA_MIN,
    Number(pend && 'gamma' in pend ? pend.gamma : (c.gamma != null ? c.gamma : 1)) || 1));

  const drag = (patch) => {
    const next = { min: shownMin, max: shownMax, ...(withGamma ? { gamma: shownGamma } : {}), ...patch };
    next.min = Math.min(Math.max(0, next.min), minCeil);
    next.max = Math.min(Math.max(next.max, next.min + step), dataMax);
    if (withGamma && next.gamma != null) {
      next.gamma = Math.min(GAMMA_MAX, Math.max(GAMMA_MIN, Number(next.gamma) || 1));
    }
    setPend(next);
    // stains repaint on the GPU right away; the commit below still waits
    if (live) setStainLive(c.index, next);
    clearTimeout(timer.current);
    timer.current = setTimeout(() => { timer.current = null; onChange(i, next); }, 250);
  };

  return (
    <div className={'channel' + (c.visible ? ' on' : '')}>
      <div className="channel-head">
        <label className="chk">
          <input type="checkbox" checked={c.visible}
            onChange={(e) => onChange(i, { visible: e.target.checked })} />
          <span className="cname">{c[keyName]}</span>
        </label>
        <span className="ch-actions">
          <input type="color" value={toHex(c.color)}
            onChange={(e) => onChange(i, { color: fromHex(e.target.value) })} />
          {onRemove && <button className="rm" onClick={onRemove} title="remove">×</button>}
        </span>
      </div>
      {c.visible && (
        <div className="channel-ctrls">
          <div className="rng">
            <span>min</span>
            <input type="range" min="0" max={minCeil}
              step={step} value={shownMin}
              onChange={(e) => drag({ min: parseFloat(e.target.value) })} />
            <span className="v">{Math.round(shownMin)}</span>
          </div>
          <div className="rng">
            <span>max</span>
            <input type="range" min={step} max={dataMax}
              step={step} value={shownMax}
              onChange={(e) => drag({ max: parseFloat(e.target.value) })} />
            <span className="v">{Math.round(shownMax)}</span>
          </div>
          {withGamma && (
            <div className="rng">
              <span>γ</span>
              <input type="range" min={GAMMA_MIN} max={GAMMA_MAX} step="0.05" value={shownGamma}
                onChange={(e) => drag({ gamma: parseFloat(e.target.value) })} />
              <span className="v">{shownGamma.toFixed(2)}</span>
            </div>
          )}
        </div>
      )}
    </div>
  );
}

// Brightness/contrast for the single grayscale morphology (DAPI) layer. Same
// live-while-dragging, commit-on-pause pattern as ChannelRow, but one window in
// raw uint16 units and no colour/visibility (the DAPI toggle handles that).
function MorphRow({ info, win, onChange }) {
  const dataMax = Math.max(1, Number(info && info.dataMax) || 1);
  const step = Math.max(1, Math.round(dataMax / 500));
  const minCeil = Math.max(0, dataMax - step);
  // `win` is null until a slider is first moved, and Number(null) is 0 -- which
  // read as a real window, started the sliders at 0/19 while the image was drawn
  // at the dataset's auto-level, and the first touch then whited out the image.
  // Only a value that is actually set counts; otherwise fall back to auto-level.
  const curLo = win && win.lo != null ? Number(win.lo) : NaN;
  const curHi = win && win.hi != null ? Number(win.hi) : NaN;
  const baseLo = Number.isFinite(curLo) ? curLo : (Number(info && info.lo) || 0);
  const baseHi = Number.isFinite(curHi) ? curHi : (Number(info && info.hi) || dataMax);
  const baseGamma = Number(win && win.gamma) || 1;

  const [pend, setPend] = useState(null);
  const timer = useRef(null);
  useEffect(() => () => clearTimeout(timer.current), []);
  useEffect(() => {                                     // outside update wins, unless mid-drag
    if (timer.current) return;
    setPend(null);
    clearMorphLive();
  }, [baseLo, baseHi, baseGamma]);
  useEffect(() => () => clearMorphLive(), []);

  const shownMin = Math.min(Math.max(0, pend ? pend.lo : baseLo), minCeil);
  const shownMax = Math.min(Math.max(shownMin + step, pend ? pend.hi : baseHi), dataMax);
  const shownGamma = Math.min(GAMMA_MAX, Math.max(GAMMA_MIN,
    Number(pend && 'gamma' in pend ? pend.gamma : (Number(win && win.gamma) || 1)) || 1));

  const drag = (patch) => {
    const next = { lo: shownMin, hi: shownMax, gamma: shownGamma, ...patch };
    next.lo = Math.min(Math.max(0, next.lo), minCeil);
    next.hi = Math.min(Math.max(next.hi, next.lo + step), dataMax);
    next.gamma = Math.min(GAMMA_MAX, Math.max(GAMMA_MIN, Number(next.gamma) || 1));
    setPend(next);
    setMorphLive(next);                                 // repaints on the GPU at once
    clearTimeout(timer.current);
    timer.current = setTimeout(() => { timer.current = null; onChange(next); }, 250);
  };

  return (
    <div className="channel-ctrls">
      <div className="rng">
        <span>min</span>
        <input type="range" min="0" max={minCeil} step={step} value={shownMin}
          onChange={(e) => drag({ lo: parseFloat(e.target.value) })} />
        <span className="v">{Math.round(shownMin)}</span>
      </div>
      <div className="rng">
        <span>max</span>
        <input type="range" min={step} max={dataMax} step={step} value={shownMax}
          onChange={(e) => drag({ hi: parseFloat(e.target.value) })} />
        <span className="v">{Math.round(shownMax)}</span>
      </div>
      <div className="rng">
        <span>γ</span>
        <input type="range" min={GAMMA_MIN} max={GAMMA_MAX} step="0.05" value={shownGamma}
          onChange={(e) => drag({ gamma: parseFloat(e.target.value) })} />
        <span className="v">{shownGamma.toFixed(2)}</span>
      </div>
    </div>
  );
}

function ChannelPanel({
  geneInfo, channels, onChannelChange, geneList, onAddGene, onRemoveGene,
  stainInfo, stainChannels, onStainChange,
  morphInfo, morphWin, onMorphChange,
  layers, onLayerChange,
  geneMode, onGeneMode, geneBin, onGeneBin,
  genePalette, onGenePalette,
  uiSettings, onUiSettings,
}) {
  const addGene = (input) => {
    const value = input.value.trim();
    if (!value) return;
    if (geneList && !geneList.includes(value)) {
      input.setCustomValidity('Choose a gene from the list');
      input.reportValidity();
      return;
    }
    input.setCustomValidity('');
    onAddGene(value);
    input.value = '';
  };
  // Folding: the same remembered set the left sidebar's sections use.
  const collapsed = (uiSettings && uiSettings.collapsed) || new Set();
  const isOpen = (k) => !collapsed.has(k);
  const Fold = ({ k, children }) => (
    <FoldTitle open={isOpen(k)} onToggle={() => {
      if (!onUiSettings) return;
      const next = new Set(collapsed);
      next.has(k) ? next.delete(k) : next.add(k);
      onUiSettings({ ...uiSettings, collapsed: next });
    }}>{children}</FoldTitle>
  );

  return (
    <div className="pane">
      <div className="section">
        <Fold k="layers">Layers</Fold>
        {isOpen('layers') && (<>
        <label className="chk">
          <input type="checkbox" checked={layers.showDapi}
            onChange={(e) => onLayerChange({ showDapi: e.target.checked })} /> DAPI morphology
        </label>
        {layers.showDapi && morphInfo && (
          <MorphRow info={morphInfo} win={morphWin} onChange={onMorphChange} />
        )}
        <label className="chk">
          <input type="checkbox" checked={layers.showRegions}
            onChange={(e) => onLayerChange({ showRegions: e.target.checked })} /> Region outlines
        </label>
        {/* both used to be fixed: faces at alpha 70, outlines 1 px */}
        <div className="slabel">Region faces {Math.round(((layers.regionFill ?? 70) / 255) * 100)}%</div>
        <input type="range" min="0" max="255" step="5" value={layers.regionFill ?? 70}
          onChange={(e) => onLayerChange({ regionFill: parseFloat(e.target.value) })} />
        <div className="slabel">Outline width {Number(layers.outlineWidth ?? 1).toFixed(1)} px</div>
        <input type="range" min="0.5" max="4" step="0.5" value={layers.outlineWidth ?? 1}
          onChange={(e) => onLayerChange({ outlineWidth: parseFloat(e.target.value) })} />
        {geneInfo && (
          <>
            <label className="chk">
              <input type="checkbox" checked={layers.showGenes}
                onChange={(e) => onLayerChange({ showGenes: e.target.checked })} /> Gene expression
            </label>
            <div className="slabel">Gene opacity {Math.round(layers.geneOpacity * 100)}%</div>
            <input type="range" min="0" max="1" step="0.05" value={layers.geneOpacity}
              onChange={(e) => onLayerChange({ geneOpacity: parseFloat(e.target.value) })} />
          </>
        )}
        {stainInfo && (
          <>
            <div className="slabel">Stain opacity {Math.round(layers.stainOpacity * 100)}%</div>
            <input type="range" min="0" max="1" step="0.05" value={layers.stainOpacity}
              onChange={(e) => onLayerChange({ stainOpacity: parseFloat(e.target.value) })} />
          </>
        )}
        </>)}
      </div>

      {stainInfo && stainChannels && (
        <div className="section">
          <Fold k="stains">Stains — morphology_focus ({stainChannels.length})</Fold>
          {isOpen('stains') && stainChannels.map((c, i) => (
            <ChannelRow key={c.index} c={c} i={i} keyName="name" onChange={onStainChange} live withGamma />
          ))}
        </div>
      )}

      {geneInfo && channels && (
        <div className="section">
          <Fold k="genes">
            Genes ({channels.length}{geneList ? ` of ${geneList.length}` : ''})
          </Fold>
          {isOpen('genes') && (<>
          {/* density rendering: glow (additive) or ink (square-bin heat map:
              a gene's colour dilutes toward white when sparse, saturates dark
              when dense, and two genes multiply like inks) */}
          <div className="row">
            <button className={`btn sm${geneMode !== 'ink' ? ' on' : ''}`}
              title="additive glow over the dark imagery"
              onClick={() => onGeneMode && onGeneMode('glow')}>
              Glow
            </button>
            <button className={`btn sm${geneMode === 'ink' ? ' on' : ''}`}
              title="square-bin heat map: light = sparse, dark = dense; genes mix like inks"
              onClick={() => onGeneMode && onGeneMode('ink')}>
              Heat map
            </button>
            {geneMode === 'ink' && (
              <select value={genePalette || 'genes'}
                title="colour scale: your per-gene colours mixing like inks, or one scientific map over the combined density"
                onChange={(e) => onGenePalette && onGenePalette(e.target.value)}>
                <option value="genes">Gene colours</option>
                <option value="viridis">Viridis</option>
                <option value="inferno">Inferno</option>
                <option value="magma">Magma</option>
                <option value="plasma">Plasma</option>
                <option value="turbo">Turbo</option>
              </select>
            )}
            <select value={geneBin || 10}
              title="bin size — the squares' size in microns (10 is the data's native grid)"
              onChange={(e) => onGeneBin && onGeneBin(parseInt(e.target.value, 10))}>
              {[10, 20, 40, 80].map((b) => (
                <option key={b} value={b}>{b} µm</option>
              ))}
            </select>
          </div>
          <input
            className="gene-add"
            list="genelist"
            placeholder="add a gene…"
            onInput={(e) => e.target.setCustomValidity('')}
            onChange={(e) => {
              if (!geneList || geneList.includes(e.target.value.trim())) addGene(e.target);
            }}
            onKeyDown={(e) => {
              if (e.key === 'Enter') { e.preventDefault(); addGene(e.target); }
            }}
          />
          <datalist id="genelist">
            {geneList && geneList.map((g) => <option key={g} value={g} />)}
          </datalist>
          {channels.map((c, i) => (
            <ChannelRow key={c.gene} c={c} i={i} keyName="gene"
              onChange={onChannelChange} onRemove={() => onRemoveGene(c.gene)} />
          ))}
          </>)}
        </div>
      )}
    </div>
  );
}

// Memoised (stableProps.js): nothing here changes while a region is dragged.
export default withStableHandlers(ChannelPanel);

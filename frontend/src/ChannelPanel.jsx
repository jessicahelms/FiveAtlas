import { useEffect, useRef, useState } from 'react';

const toHex = (rgb) =>
  '#' + rgb.map((v) => Math.max(0, Math.min(255, v | 0)).toString(16).padStart(2, '0')).join('');
const fromHex = (h) => [1, 3, 5].map((i) => parseInt(h.slice(i, i + 2), 16));

function ChannelRow({ c, i, keyName, onChange, onRemove }) {
  const dataMax = Math.max(1, Number(c.dataMax) || 1);
  const step = Math.max(1, Math.round(dataMax / 500));
  const minCeil = Math.max(0, dataMax - step);

  // The slider tracks the hand; the CHANNEL only takes the value once the hand
  // pauses. Committing per tick re-requests the whole tile pyramid for every
  // pixel of drag — which is why these sliders used to be greyed out entirely.
  const [pend, setPend] = useState(null);          // {min,max} while dragging
  const timer = useRef(null);
  useEffect(() => () => clearTimeout(timer.current), []);
  useEffect(() => { setPend(null); }, [c.min, c.max]);   // outside update wins

  const shownMin = Math.min(Math.max(0, Number(pend ? pend.min : c.min) || 0), minCeil);
  const shownMax = Math.min(Math.max(shownMin + step,
    Number(pend ? pend.max : c.max) || dataMax), dataMax);

  const drag = (patch) => {
    const next = { min: shownMin, max: shownMax, ...patch };
    next.min = Math.min(Math.max(0, next.min), minCeil);
    next.max = Math.min(Math.max(next.max, next.min + step), dataMax);
    setPend(next);
    clearTimeout(timer.current);
    timer.current = setTimeout(() => onChange(i, next), 250);
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
        </div>
      )}
    </div>
  );
}

export default function ChannelPanel({
  geneInfo, channels, onChannelChange, geneList, onAddGene, onRemoveGene,
  stainInfo, stainChannels, onStainChange,
  layers, onLayerChange,
  geneMode, onGeneMode, geneBin, onGeneBin,
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

  return (
    <div className="pane">
      <div className="section">
        <div className="section-title">Layers</div>
        <label className="chk">
          <input type="checkbox" checked={layers.showDapi}
            onChange={(e) => onLayerChange({ showDapi: e.target.checked })} /> DAPI morphology
        </label>
        <label className="chk">
          <input type="checkbox" checked={layers.showRegions}
            onChange={(e) => onLayerChange({ showRegions: e.target.checked })} /> Region outlines
        </label>
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
      </div>

      {stainInfo && stainChannels && (
        <div className="section">
          <div className="section-title">Stains — morphology_focus ({stainChannels.length})</div>
          {stainChannels.map((c, i) => (
            <ChannelRow key={c.index} c={c} i={i} keyName="name" onChange={onStainChange} />
          ))}
        </div>
      )}

      {geneInfo && channels && (
        <div className="section">
          <div className="section-title">
            Genes ({channels.length}{geneList ? ` of ${geneList.length}` : ''})
          </div>
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
        </div>
      )}
    </div>
  );
}

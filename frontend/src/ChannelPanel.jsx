const toHex = (rgb) =>
  '#' + rgb.map((v) => Math.max(0, Math.min(255, v | 0)).toString(16).padStart(2, '0')).join('');
const fromHex = (h) => [1, 3, 5].map((i) => parseInt(h.slice(i, i + 2), 16));

function ChannelRow({ c, i, keyName, onChange, onRemove, ctrlsDisabled = false }) {
  const dataMax = Math.max(1, Number(c.dataMax) || 1);
  const step = Math.max(1, Math.round(dataMax / 500));
  const minCeil = Math.max(0, dataMax - step);
  const safeMin = Math.min(Math.max(0, Number(c.min) || 0), minCeil);
  const safeMax = Math.min(Math.max(safeMin + step, Number(c.max) || dataMax), dataMax);
  const setMin = (value) => {
    const min = Math.min(Math.max(0, value), minCeil);
    onChange(i, { min, max: Math.max(safeMax, min + step) });
  };
  const setMax = (value) => {
    const max = Math.min(Math.max(value, safeMin + step), dataMax);
    onChange(i, { min: safeMin, max });
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
        <div className={'channel-ctrls' + (ctrlsDisabled ? ' disabled' : '')}>
          <div className="rng">
            <span>min</span>
            <input type="range" min="0" max={minCeil}
              step={step} value={safeMin} disabled={ctrlsDisabled}
              onChange={(e) => setMin(parseFloat(e.target.value))} />
            <span className="v">{Math.round(safeMin)}</span>
          </div>
          <div className="rng">
            <span>max</span>
            <input type="range" min={step} max={dataMax}
              step={step} value={safeMax} disabled={ctrlsDisabled}
              onChange={(e) => setMax(parseFloat(e.target.value))} />
            <span className="v">{Math.round(safeMax)}</span>
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
          {/* min/max greyed out for now — visibility and colour still work */}
          {stainChannels.map((c, i) => (
            <ChannelRow key={c.index} c={c} i={i} keyName="name" onChange={onStainChange}
              ctrlsDisabled />
          ))}
          <div className="hint dim">Brightness sliders are disabled for now.</div>
        </div>
      )}

      {geneInfo && channels && (
        <div className="section">
          <div className="section-title">
            Genes ({channels.length}{geneList ? ` of ${geneList.length}` : ''})
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

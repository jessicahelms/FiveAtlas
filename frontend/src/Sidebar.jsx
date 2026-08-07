import { useState } from 'react';

function displayName(feature, idProp) {
  const props = (feature && feature.properties) || {};
  const value = props[idProp] ?? props.name;
  return value == null ? '' : String(value);
}

export default function Sidebar({
  info, fc, sources, selected, onSelectName, onRegionMenu,
  mode, onToggleModify, onToggleBorder, onToggleSplit, onToggleDissolve, onToggleDraw,
  borderPicks, borderMsg, borderShared, onClearBorder, onShareBorders, onMerge,
  splitMsg, drawMsg,
  resample, resampleTol, onResampleTol, onApplyResample, onCancelResample,
  gapFind, gapMsg, onDissolve, onFillGap, onClearGap,
  selectedName,
  moved, canSnap, onSnap, onSave, onReset,
  onUndo, onRedo, canUndo, canRedo,
  propEdit, propRadius, onPropEditChange,
  geomReport, onValidate, onRepair, onDismissReport, onForceExport,
  onRestoreOriginal, orientation, onOrientation,
  onLoadFile, onExport,
  snapInfo, busy, error,
}) {
  const [confirmRestore, setConfirmRestore] = useState(false);
  // Which coordinates to write when the view is rotated/flipped. Default is what
  // is on screen -- that is what has been edited against.
  const [exportFrame, setExportFrame] = useState('displayed');
  const rotated = !!(orientation && (orientation.rot || orientation.flipH || orientation.flipV));
  const idProp = (info && info.idProp) || 'name';
  const names = fc.features.map((f) => displayName(f, idProp) || '(unnamed)');
  // A name carried by several features is ONE multi-part region — every edit acts
  // on the whole body — so the list has to say which rows belong together instead
  // of showing two identical entries that behave as one.
  const partCount = names.reduce((m, nm) => ({ ...m, [nm]: (m[nm] || 0) + 1 }), {});
  const picks = borderPicks || [];
  const pickTag = (i) => (i === 0 ? 'A' : i === 1 ? 'B' : String(i + 1));

  return (
    <div className="pane">
      <h1>FiveAtlas</h1>
      <div className="ds">{info.label}</div>
      <div className="dims">
        {info.width.toLocaleString()}×{info.height.toLocaleString()} px · {info.levels} levels · {info.nZ} z
      </div>

      <div className="row histrow">
        <button className="btn" onClick={onUndo} disabled={!canUndo} title="Undo (Ctrl+Z)">↶ Undo</button>
        <button className="btn" onClick={onRedo} disabled={!canRedo} title="Redo (Ctrl+Shift+Z)">↷ Redo</button>
      </div>

      {sources && sources.sources && (
        <div className="section">
          <div className="section-title">Detected</div>
          <ul className="detected">
            {sources.sources.morphology && <li>✓ morphology (DAPI pyramid)</li>}
            {sources.sources.morphology_focus && (
              <li>✓ focus stains — {sources.sources.morphology_focus.channels.length} ch</li>
            )}
            {sources.sources.genes && <li>✓ genes — {sources.sources.genes.names.length}</li>}
            {sources.sources.regions && <li>✓ regions — {sources.sources.regions.length} file(s)</li>}
            {sources.sources.transcripts && <li>✓ transcripts.zarr</li>}
          </ul>
        </div>
      )}

      <div className="section">
        <div className="section-title">
          Regions ({names.length}){mode === 'border' ? ' — click to pick' : ''}
        </div>
        <ul className="regions">
          {names.map((nm, i) => {
            const pi = picks.indexOf(nm);
            const parts = partCount[nm] || 1;
            const part = parts > 1 ? names.slice(0, i + 1).filter((n) => n === nm).length : 0;
            const cls = [
              selected[0] === i ? 'sel' : '',
              moved.has(nm) ? 'moved' : '',
              pi === 0 ? 'pickA' : pi === 1 ? 'pickB' : pi > 1 ? 'pickX' : '',
            ].filter(Boolean).join(' ');
            return (
              <li
                key={i}
                className={cls}
                onClick={() => onSelectName(nm)}
                onContextMenu={(e) => {
                  if (!onRegionMenu) return;
                  e.preventDefault();
                  onRegionMenu({ index: i, name: nm, x: e.clientX, y: e.clientY });
                }}
                title={parts > 1
                  ? `one region in ${parts} parts — edits act on all of them; right-click to rename or delete`
                  : 'right-click to rename or delete'}
              >
                <span>{nm}{parts > 1 ? <span className="dim"> · part {part}/{parts}</span> : null}</span>
                <span>{pi >= 0 ? pickTag(pi) : (moved.has(nm) ? '●' : '')}</span>
              </li>
            );
          })}
        </ul>
      </div>

      <div className="section">
        <div className="section-title">Edit</div>

        <button className={mode === 'modify' ? 'btn on' : 'btn'} onClick={onToggleModify} disabled={!selectedName && mode !== 'modify'}>
          {mode === 'modify' ? '✎ Editing points' : 'Edit points'}
          {selectedName ? ` · ${selectedName}` : ''}
        </button>

        {/* vertex-count control (arrow keys / +−) disabled — paused per request */}

        <button className={mode === 'border' ? 'btn on' : 'btn'} onClick={onToggleBorder}>
          {mode === 'border' ? '⇹ Editing shared borders' : 'Edit shared borders'}
        </button>

        {mode === 'border' && (
          <div className="border-pick">
            <div className="picks">
              {picks.length === 0 && <span className="hint dim">click two touching regions to drag their border, or several to tile them</span>}
              {picks.map((nm, i) => (
                <span key={nm} className={`chip pick${i === 0 ? 'A' : i === 1 ? 'B' : 'X'}`}>
                  <span className="tag">{pickTag(i)}</span>{nm}
                </span>
              ))}
            </div>
            <div className="row">
              {picks.length > 0 && <button className="btn sm" onClick={onClearBorder}>{borderShared ? 'Done / pick again' : 'Clear'}</button>}
              {picks.length >= 2 && !borderShared && (
                <button className="btn sm primary" onClick={onShareBorders} disabled={busy}>
                  Share borders ({picks.length})
                </button>
              )}
            </div>
            {picks.length >= 2 && !borderShared && (
              <button className="btn sm" onClick={onMerge} disabled={busy}>
                ⨝ Merge into one ({picks.length})
              </button>
            )}
            {picks.length >= 2 && borderShared && (
              <div className="prop">
                <div className="pts-head">Resample points</div>
                <div className="rng">
                  <span>fine</span>
                  <input
                    type="range" min="20" max="600" step="10"
                    value={resampleTol}
                    onChange={(e) => onResampleTol && onResampleTol(Number(e.target.value))}
                    disabled={busy}
                  />
                  <span>coarse</span>
                </div>
                <div className="prop-readout">
                  <span>spacing</span>
                  <span className="v">{resampleTol} px</span>
                </div>
                {resample && resample.counts && (
                  <>
                    {resample.handles && (
                      <div className="pts-head">
                        drag points on the border:{' '}
                        <b>{resample.handles.before} → {resample.handles.after}</b>
                      </div>
                    )}
                    <ul className="notes">
                      {resample.counts.map((c) => (
                        <li key={c.region} className="dim">
                          {c.region} outline {c.before.toLocaleString()} → {c.after.toLocaleString()} pts
                        </li>
                      ))}
                    </ul>
                    <div className="row">
                      <button className="btn sm primary" onClick={onApplyResample} disabled={busy}>Apply</button>
                      <button className="btn sm" onClick={onCancelResample} disabled={busy}>Cancel</button>
                    </div>
                  </>
                )}
                <div className="hint dim">
                  The handles on the border update as you drag the slider — Apply
                  keeps them, Cancel puts the old ones back. Only the shared border
                  moves, so nothing opens up against regions you didn't pick.
                </div>
              </div>
            )}
            {borderMsg && <div className="hint">{borderMsg}</div>}
          </div>
        )}

        <button className={mode === 'split' ? 'btn on' : 'btn'} onClick={onToggleSplit}>
          {mode === 'split' ? '✂ Splitting a region' : 'Split a region'}
        </button>

        {mode === 'split' && (
          <div className="border-pick">
            <div className="pts-head">
              Target: {selectedName ? <b>{selectedName}</b> : <span className="dim">click a region in the list above</span>}
            </div>
            <div className="hint dim">
              Draw a line across it on the image — click each point, then <b>right-click
              to finish</b> (or Enter). <b>Esc</b> throws the line away.
            </div>
            {splitMsg && <div className="hint">{splitMsg}</div>}
          </div>
        )}

        <button className={mode === 'draw' ? 'btn on' : 'btn'} onClick={onToggleDraw}>
          {mode === 'draw' ? '✚ Drawing a new region' : 'Add a new region'}
        </button>

        {mode === 'draw' && (
          <div className="border-pick">
            <div className="hint dim">
              Trace the outline on the image — click each point, then <b>right-click
              to finish</b> (or Enter). <b>Esc</b> throws the outline away.
              Anything it covers is taken from the region underneath.
            </div>
            {drawMsg && <div className="hint">{drawMsg}</div>}
          </div>
        )}

        <button className={mode === 'dissolve' ? 'btn on' : 'btn'} onClick={onToggleDissolve}>
          {mode === 'dissolve' ? '⬤ Fixing gaps' : 'Fix a gap'}
        </button>

        {mode === 'dissolve' && (
          <div className="border-pick">
            <div className="hint dim">
              Click inside a leftover gap on the image. It's outlined in red, then
              either <b>Dissolve</b> it into the regions around it, or turn it into
              a <b>New region</b> of its own.
            </div>
            {gapFind && (
              <>
                <div className="pts-head">
                  {Math.round(gapFind.area).toLocaleString()} px²
                  {gapFind.kind === 'notch' ? ' · open notch' : ' · enclosed'}
                </div>
                <div className="pick-row">
                  <span className="pick-val">→ {(gapFind.regions || []).join(' + ')}</span>
                </div>
                <div className="row">
                  <button className="btn sm primary" onClick={onDissolve} disabled={busy}>Dissolve</button>
                  <button className="btn sm primary" onClick={onFillGap} disabled={busy}>New region</button>
                  <button className="btn sm" onClick={onClearGap} disabled={busy}>Cancel</button>
                </div>
              </>
            )}
            {gapMsg && <div className="hint">{gapMsg}</div>}
          </div>
        )}

        {mode !== 'border' && mode !== 'split' && mode !== 'dissolve' && mode !== 'draw' && (
          <div className="hint">
            <b>Edit points</b>: select a region, drag its outline.
            <b> Shared borders</b>: pick regions to drag a border, tile, or merge them.
          </div>
        )}

        {/* modifier for the editing tools above, so it sits under all of them */}
        <div className="prop">
          <label className="chk">
            <input
              type="checkbox"
              checked={Boolean(propEdit)}
              onChange={(e) => onPropEditChange && onPropEditChange(e.target.checked)}
              disabled={busy}
            />
            <span>Proportional editing</span>
          </label>
          <div className="prop-readout">
            <span>Radius</span>
            <span className="v">{Math.round(propRadius)} px</span>
          </div>
          <div className="hint dim">Scroll the mouse wheel while dragging a point to resize it.</div>
        </div>

        <button className="btn primary" onClick={onSnap} disabled={busy || !canSnap}>
          Snap neighbors{canSnap && moved.size ? ` (${moved.size} moved)` : ''}
        </button>
        <div className="row">
          <button className="btn" onClick={onSave} disabled={busy}>Save</button>
          <button className="btn" onClick={onReset} disabled={busy}>Reset</button>
        </div>
      </div>

      <div className="section">
        <div className="section-title">Orientation</div>
        <div className="hint dim">
          For a slide that was imaged upside down. Moves the image and the regions
          together, so they stay lined up.
        </div>
        <div className="row">
          <button className="btn sm" disabled={busy || !onOrientation}
            title="rotate anticlockwise"
            onClick={() => onOrientation((o) => ({ ...o, rot: (o.rot + 270) % 360 }))}>↺ 90°</button>
          <button className="btn sm" disabled={busy || !onOrientation}
            title="rotate clockwise"
            onClick={() => onOrientation((o) => ({ ...o, rot: (o.rot + 90) % 360 }))}>↻ 90°</button>
        </div>
        <div className="row">
          <button className={`btn sm${orientation && orientation.flipH ? ' on' : ''}`}
            disabled={busy || !onOrientation} title="mirror left to right"
            onClick={() => onOrientation((o) => ({ ...o, flipH: !o.flipH }))}>⇄ Flip L/R</button>
          <button className={`btn sm${orientation && orientation.flipV ? ' on' : ''}`}
            disabled={busy || !onOrientation} title="mirror top to bottom"
            onClick={() => onOrientation((o) => ({ ...o, flipV: !o.flipV }))}>⇅ Flip T/B</button>
        </div>
        {rotated ? (
          <>
            <div className="prop-readout">
              <span>now</span>
              <span className="v">
                {orientation.rot}°{orientation.flipH ? ' ⇄' : ''}{orientation.flipV ? ' ⇅' : ''}
              </span>
            </div>
            <button className="btn sm" disabled={busy}
              onClick={() => onOrientation({ rot: 0, flipH: false, flipV: false })}>
              Back to as-imaged
            </button>
            <div className="hint dim">
              Edit in this frame if you like — Export below chooses whether to write
              these coordinates or the ones the original file used.
            </div>
          </>
        ) : null}
      </div>

      <div className="section">
        <div className="section-title">Data</div>
        <button className="btn" onClick={onLoadFile} disabled={busy}>📂 Load regions file…</button>
        <button className="btn" onClick={onValidate} disabled={busy}>✓ Check geometry</button>

        {!confirmRestore ? (
          <button className="btn" onClick={() => setConfirmRestore(true)} disabled={busy}>
            ↺ Restore original GeoJSON
          </button>
        ) : (
          <div className="border-pick">
            <div className="pts-head">Go back to the original file?</div>
            <div className="hint dim">
              Your current work is snapshotted first, so this can be undone from the
              versions folder. The dataset's own file is only read, never written.
            </div>
            <div className="row">
              <button className="btn sm danger" disabled={busy}
                onClick={() => { setConfirmRestore(false); onRestoreOriginal && onRestoreOriginal(); }}>
                Restore
              </button>
              <button className="btn sm" onClick={() => setConfirmRestore(false)} disabled={busy}>
                Cancel
              </button>
            </div>
          </div>
        )}
        <div className="slabel">Export</div>
        {rotated && (
          <>
            <div className="hint dim">Write the coordinates…</div>
            <div className="row">
              <button className={`btn sm${exportFrame === 'displayed' ? ' on' : ''}`}
                disabled={busy} title="the rotated / flipped coordinates you have been editing"
                onClick={() => setExportFrame('displayed')}>
                As you see it ({orientation.rot}°{orientation.flipH ? ' ⇄' : ''}{orientation.flipV ? ' ⇅' : ''})
              </button>
              <button className={`btn sm${exportFrame === 'original' ? ' on' : ''}`}
                disabled={busy} title="rotate and flip every edit back to the as-imaged frame"
                onClick={() => setExportFrame('original')}>
                As imaged (0°)
              </button>
            </div>
            <div className="hint dim">
              {exportFrame === 'original'
                ? 'Your edits are rotated and flipped back, so the file lines up with the'
                  + ' original image. The file is written without an orientation marker.'
                : 'Written exactly as displayed, marked with the orientation so it can be'
                  + ' turned back later.'}
              {' '}The filename says which.
            </div>
          </>
        )}
        <div className="row">
          <button className="btn" onClick={() => onExport('merged', { frame: exportFrame })}
            disabled={busy}>Merged .geojson</button>
          <button className="btn" onClick={() => onExport('separate', { frame: exportFrame })}
            disabled={busy}>Separate .zip</button>
        </div>
        <div className="hint dim">Merged = one file, all regions. Separate = one file per region, zipped.</div>

        {geomReport && (
          <div className="geomrep">
            <div className="pts-head">{geomReport.title}</div>
            <div className="hint dim">
              {geomReport.counts.errors || 0} error(s), {geomReport.counts.warnings || 0} warning(s)
              {geomReport.counts.features != null ? ` across ${geomReport.counts.features} regions` : ''}
            </div>
            {geomReport.problems.length > 0 && (
              <ul className="notes">
                {geomReport.problems.slice(0, 20).map((p, i) => (
                  <li key={i} className={p.level === 'error' ? 'lvl-err' : 'lvl-warn'}>
                    <b>{p.region}</b> — {p.kind.replace(/_/g, ' ')}: {p.detail}
                  </li>
                ))}
                {geomReport.problems.length > 20 && (
                  <li className="dim">…and {geomReport.problems.length - 20} more</li>
                )}
              </ul>
            )}
            <div className="row">
              {(geomReport.counts.errors || 0) > 0 && (
                <button className="btn sm primary" onClick={onRepair} disabled={busy}>Repair</button>
              )}
              {geomReport.mode && (
                <button className="btn sm" disabled={busy}
                  onClick={() => onForceExport(geomReport.mode,
                                               geomReport.frame || 'displayed')}>
                  Export anyway
                </button>
              )}
              <button className="btn sm" onClick={onDismissReport} disabled={busy}>Dismiss</button>
            </div>
            {(geomReport.counts.errors || 0) > 0 && (
              <div className="hint dim">Repair heals invalid/empty shapes only — a missing name has to
                be fixed by hand (right-click a region → Rename). A repeated name is fine: it's
                one region in several parts.</div>
            )}
          </div>
        )}
      </div>

      {snapInfo && (
        <div className="section">
          <div className="section-title">Last operation</div>
          {snapInfo.movers && snapInfo.movers.length > 0 && (
            <div className="mono">moved: {snapInfo.movers.join(', ')}</div>
          )}
          {snapInfo.notes && snapInfo.notes.length > 0 && (
            <ul className="notes">
              {snapInfo.notes.slice(0, 8).map((n, i) => <li key={i}>{n}</li>)}
            </ul>
          )}
          {snapInfo.saved && <div className="mono ok">{snapInfo.saved}</div>}
        </div>
      )}

      {error && <div className="err">{error}</div>}
      {busy && <div className="busy">working…</div>}
    </div>
  );
}

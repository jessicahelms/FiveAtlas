import { useState } from 'react';
import { parseName, nextNumber, formatName } from './damage';

function displayName(feature, idProp) {
  const props = (feature && feature.properties) || {};
  const value = props[idProp] ?? props.name;
  return value == null ? '' : String(value);
}

export default function Sidebar({
  info, fc, sources, selected, onSelectName, onRegionMenu,
  mode, onToggleModify, onToggleBorder, onToggleSplit, onToggleDissolve, onToggleDraw,
  borderPicks, borderMsg, borderShared, onClearBorder, onShareBorders, onMerge,
  splitMsg, drawMsg, drawKind, onDrawKind, designations, damageAsk, onAnswerDamage,
  resample, resampleTol, onResampleTol, onApplyResample, onCancelResample,
  gapFind, gapMsg, onDissolve, onFillGap, onClearGap,
  selectedName,
  moved, canSnap, onSnap, onSave, onReset,
  onUndo, onRedo, canUndo, canRedo,
  propEdit, propRadius, onPropEditChange,
  geomReport, onValidate, onRepair, onDismissReport, onForceExport,
  onRestoreOriginal, orientation, onOrientation,
  onAnswerReportDamage,
  regionsOff, onToggleRegionOff, outlinePreview, onOutline, onDismissOutline,
  bordersOff, onToggleBorderOff, fillsOff, onToggleFillOff, onAllFaces,
  onToggleClean, cleanFound, cleanMsg, onApplyClean, onCancelClean,
  cellsReport, cellSep, cellsAll, onLoadCells, onDismissCells, onCellSep,
  onCellsAll, onRegionDamage,
  notesInfo, notesReport, notesScope, onNotesScope, onNotesPreview, onNotesSave,
  onNotesCreate, onDismissNotes, identity, onIdentity,
  onLoadFile, onExport,
  snapInfo, busy, error, onQuit,
}) {
  const [confirmRestore, setConfirmRestore] = useState(false);
  const [copiedCell, setCopiedCell] = useState(null);
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

  // Damage is an ordinary region in this list; the designation is just shown
  // beside it, so `separation.4` reads as damage while `PAL.1` — anatomy that
  // happens to end in a number — does not.
  const aliases = (designations && designations.aliases) || null;
  const desigList = (designations && designations.designations) || [];
  const labelOf = desigList.reduce((m, d) => ({ ...m, [d.tag]: d.label }), {});
  const damageOf = (nm) => (aliases ? parseName(nm, aliases).tag : null);
  // drawn: true = always a shape, null = the annotator's call, false = never —
  // those have nothing to trace and can only be a tag on the region.
  const drawable = desigList.filter((d) => d.drawn !== false);
  const tagOnly = desigList.filter((d) => d.drawn === false);
  const nextDamageName = drawKind && drawKind !== 'region' && aliases
    ? formatName(drawKind, nextNumber(names, drawKind, aliases))
    : null;
  const off = regionsOff || new Set();
  const bOff = bordersOff || new Set();
  const fOff = fillsOff || new Set();

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
        {/* faces all at once: the common move while gap-filling is "hide every
            face, keep every border", and 23 clicks is not a control */}
        <div className="row">
          <button className="btn sm" disabled={busy}
            title="hide every region's face — borders stay; nothing else changes"
            onClick={() => onAllFaces && onAllFaces(false)}>
            Faces off
          </button>
          <button className="btn sm" disabled={busy}
            title="show every region's face again"
            onClick={() => onAllFaces && onAllFaces(true)}>
            Faces on
          </button>
        </div>
        {off.size > 0 && (
          <div className="hint dim">
            {[...off].join(', ')} switched off — still in the file and exported,
            but ignored by gap-finding and Check geometry, and hidden on the map.
          </div>
        )}
        <ul className="regions">
          {names.map((nm, i) => {
            const pi = picks.indexOf(nm);
            const parts = partCount[nm] || 1;
            const part = parts > 1 ? names.slice(0, i + 1).filter((n) => n === nm).length : 0;
            const cls = [
              selected[0] === i ? 'sel' : '',
              moved.has(nm) ? 'moved' : '',
              off.has(nm) ? 'off' : '',
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
                <span>
                  {nm}
                  {parts > 1 ? <span className="dim"> · part {part}/{parts}</span> : null}
                  {damageOf(nm) ? <span className="dim"> · {labelOf[damageOf(nm)]}</span> : null}
                </span>
                <span className="rowend">
                  {pi >= 0 ? pickTag(pi) : (moved.has(nm) ? '●' : '')}
                  {/* display-only: face and border. Operations see the region
                      exactly as before -- unlike the eye. */}
                  <button
                    className="eye" disabled={busy}
                    title={fOff.has(nm)
                      ? 'face hidden — click to fill it in again'
                      : 'hide the face: keep the border, see the imagery through it'}
                    onClick={(e) => {
                      e.stopPropagation();
                      onToggleFillOff && onToggleFillOff(nm);
                    }}
                  >{fOff.has(nm) ? '▢' : '▩'}</button>
                  <button
                    className="eye" disabled={busy}
                    title={bOff.has(nm)
                      ? 'border hidden — click to draw it again'
                      : 'hide the border: keep the face, no outline'}
                    onClick={(e) => {
                      e.stopPropagation();
                      onToggleBorderOff && onToggleBorderOff(nm);
                    }}
                  >{bOff.has(nm) ? '◌' : '◯'}</button>
                  {/* Switch a region off: it stays in the file, but the
                      operations that assume a clean partition stop seeing it.
                      Without this, `hemi` covering everything means no gap can
                      ever be found. */}
                  <button
                    className="eye" disabled={busy}
                    title={off.has(nm)
                      ? 'switched off — click to use it again'
                      : 'switch off: hide it and leave it out of gap-finding and Check geometry'}
                    onClick={(e) => {
                      e.stopPropagation();
                      onToggleRegionOff && onToggleRegionOff(nm);
                    }}
                  >{off.has(nm) ? '◍' : '◉'}</button>
                </span>
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
          {mode === 'draw'
            ? (nextDamageName ? `✚ Drawing ${labelOf[drawKind] || drawKind}` : '✚ Drawing a new region')
            : 'Add a new region or damage'}
        </button>

        {mode === 'draw' && (
          <div className="border-pick">
            <label className="field">
              <span>What are you drawing?</span>
              <select
                value={drawKind || 'region'}
                disabled={busy || !onDrawKind}
                onChange={(e) => onDrawKind && onDrawKind(e.target.value)}
              >
                <option value="region">Anatomical region</option>
                {drawable.length > 0 && (
                  <optgroup label="Damage — drawn as a shape">
                    {drawable.map((d) => (
                      <option key={d.tag} value={d.tag}>
                        {d.label}{d.drawn === null ? ' (if you want it drawn)' : ''}
                      </option>
                    ))}
                  </optgroup>
                )}
                {/* Shown but not selectable: without them half the vocabulary
                    looks missing; offering them would invite a shape that the
                    SOP says has nothing to trace. */}
                {tagOnly.length > 0 && (
                  <optgroup label="Damage — recorded per region, never drawn">
                    {tagOnly.map((d) => (
                      <option key={d.tag} value={d.tag} disabled>{d.label}</option>
                    ))}
                  </optgroup>
                )}
              </select>
            </label>

            {nextDamageName ? (
              <>
                <div className="pick-row">
                  <span>Will be named</span>
                  <span className="pick-val">{nextDamageName}</span>
                </div>
                <div className="hint dim">
                  Trace the damaged area — click each point, then <b>right-click to
                  finish</b> (or Enter). <b>Esc</b> throws it away. It becomes a
                  region like any other, but it sits <b>inside</b> the region it
                  covers: nothing is taken away, and the two overlap on purpose.
                  Numbering runs across the whole file, not per region.
                </div>
              </>
            ) : (
              <div className="hint dim">
                Trace the outline on the image — click each point, then <b>right-click
                to finish</b> (or Enter). <b>Esc</b> throws the outline away.
                Anything it covers is taken from the region underneath.
              </div>
            )}
            {!designations && (
              <div className="hint dim">
                (damage designations didn't load — restart the backend to draw damage)
              </div>
            )}
            {drawMsg && <div className="hint">{drawMsg}</div>}

            {/* A shape that reaches into a second region. Damage goes to the one
                region holding most of it, so this is a judgement call and the
                annotator makes it now, not the geometry. */}
            {damageAsk && (damageAsk.candidates || []).length > 1 && (
              <div className="pts">
                <div className="pts-head">
                  Which region is <b>{damageAsk.name}</b> in?
                </div>
                <div className="hint dim">
                  It sits in {damageAsk.candidates.length} regions — most of it in{' '}
                  <b>{damageAsk.dominant}</b>. Recording it in both puts it in both
                  their notes, which is what the SOP asks for when two annotators
                  each own one side.
                </div>
                <div className="row">
                  {damageAsk.candidates.map((nm) => (
                    <button
                      key={nm} disabled={busy}
                      className={nm === damageAsk.dominant ? 'btn sm primary' : 'btn sm'}
                      onClick={() => onAnswerDamage && onAnswerDamage([nm])}
                    >
                      {nm}{nm === damageAsk.dominant ? ' (most of it)' : ''}
                    </button>
                  ))}
                  <button className="btn sm" disabled={busy}
                    onClick={() => onAnswerDamage && onAnswerDamage([...damageAsk.candidates])}>
                    {damageAsk.candidates.length > 2 ? `All ${damageAsk.candidates.length}` : 'Both'}
                  </button>
                </div>
                <div className="hint dim">
                  Leave it alone and it goes to {damageAsk.dominant}; you'll be shown
                  it again in the SmartSheet review.
                </div>
              </div>
            )}
          </div>
        )}

        {/* The hemisection outline, built from the regions rather than traced by
            hand around all 22 of them. */}
        <button className="btn" disabled={busy || !onOutline}
          onClick={() => onOutline && onOutline({})}>
          ⬭ Build the hemisection outline…
        </button>

        {outlinePreview && (
          <div className="border-pick">
            <div className="pts-head">
              {outlinePreview.exists ? 'Rebuild' : 'Create'} “hemi” —{' '}
              {Math.round(outlinePreview.area).toLocaleString()} px²
            </div>
            <div className="hint dim">
              Wrapped around {outlinePreview.sources.length} regions
              {outlinePreview.exists && ' (the existing one is not used as input, '
                + 'so rebuilding does not creep outwards)'}. Damage shapes are left
              out — they sit inside the tissue and would only pull it in.
            </div>
            {/* No radius or method controls, on purpose. Measured on the real
                file: 40 px → 2000 px moves the outline area by 0.8% in total,
                because the regions already tile the section tightly and there is
                almost nothing left for a wider bridge to close. The knob had
                nothing to turn, so it went. The convex hull went with it — it
                claims 3.4% more ground by cutting across every notch.
                `method` and `radius` are still on the route if that ever
                changes. */}
            <div className="row">
              <button className="btn sm primary" disabled={busy}
                onClick={() => onOutline && onOutline({ apply: true })}>
                {outlinePreview.exists ? 'Replace hemi' : 'Create hemi'}
              </button>
              <button className="btn sm" onClick={onDismissOutline} disabled={busy}>Cancel</button>
            </div>
          </div>
        )}

        <button className={mode === 'dissolve' ? 'btn on' : 'btn'} onClick={onToggleDissolve}>
          {mode === 'dissolve' ? '⬤ Fixing gaps' : 'Fix a gap'}
        </button>

        <button className={mode === 'clean' ? 'btn on' : 'btn'} onClick={onToggleClean}>
          {mode === 'clean' ? '◌ Circling stray lines' : 'Clean up stray lines'}
        </button>

        {mode === 'clean' && (
          <div className="border-pick">
            <div className="hint dim">
              <b>Hold the left button and sweep a circle</b> around the leftover
              hairlines — release to finish, <b>Esc</b> to throw it away.
              Everything sliver-thin inside is removed and the ground goes to
              the healthy neighbours. Fat, healthy regions are never touched,
              and neither are damage shapes.
            </div>
            {cleanFound && (
              <>
                <div className="pts-head">
                  {Math.round(cleanFound.area).toLocaleString()} px² of stray lines
                </div>
                {cleanFound.removed.length > 0 && (
                  <div className="hint dim">
                    {cleanFound.removed.map((r) => (
                      <div key={r.region}>
                        {r.region}: {r.parts} piece{r.parts > 1 ? 's' : ''}
                        {' '}({Math.round(r.area).toLocaleString()} px²)
                      </div>
                    ))}
                  </div>
                )}
                {cleanFound.deleted.length > 0 && (
                  <div className="hint lvl-warn">
                    {cleanFound.deleted.join(', ')} {cleanFound.deleted.length > 1
                      ? 'are hairline all over and will be deleted whole'
                      : 'is hairline all over and will be deleted whole'}
                  </div>
                )}
                {cleanFound.filled.length > 0 && (
                  <div className="pick-row">
                    <span className="pick-val">→ {cleanFound.filled.join(' + ')}</span>
                  </div>
                )}
                <div className="row">
                  <button className="btn sm primary" onClick={onApplyClean} disabled={busy}>
                    Apply
                  </button>
                  <button className="btn sm" onClick={onCancelClean} disabled={busy}>
                    Cancel
                  </button>
                </div>
              </>
            )}
            {cleanMsg && <div className="hint">{cleanMsg}</div>}
          </div>
        )}

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

        {mode !== 'border' && mode !== 'split' && mode !== 'dissolve' && mode !== 'draw'
          && mode !== 'clean' && (
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
        {/* The sheet's Damage column is a MULTI-SELECT dropdown — one cell holds
            several chips — so what is worth copying is a cell per region, not a
            row. `Done` rides in the same cell; it is the annotator's tick, not a
            designation, so it never reaches the YAML. */}
        <div className="slabel">SmartSheet damage</div>
        <button className="btn" onClick={onLoadCells} disabled={busy}>
          ⎘ Damage cells, region by region…
        </button>

        {cellsReport && (
          <div className="geomrep">
            <div className="hint dim">
              One box per region — copy it, click that region's Damage cell in
              SmartSheet, paste. The names match the dropdown's own options.
            </div>
            <div className="row">
              {(cellsReport.separators || []).map((s) => (
                <button key={s} disabled={busy}
                  className={`btn sm${cellSep === s ? ' on' : ''}`}
                  title={s === 'cell' ? 'quoted, so a grid keeps it in ONE cell'
                    : s === 'lines' ? 'one value per line, unquoted'
                      : 'comma separated'}
                  onClick={() => onCellSep && onCellSep(s)}>
                  {s === 'cell' ? 'One cell' : s === 'lines' ? 'Lines' : 'Commas'}
                </button>
              ))}
            </div>
            <div className="hint dim">
              If a paste lands as separate <b>rows</b> instead of chips in one
              cell, switch format and paste again — SmartSheet is picky and which
              one it wants is quicker to try than to look up.
            </div>
            <label className="chk">
              <input type="checkbox" checked={!!cellsAll} disabled={busy}
                onChange={(e) => onCellsAll && onCellsAll(e.target.checked)} />
              <span>Show every region, not only those with damage</span>
            </label>

            {/* Shapes still waiting on an answer. Promised when one was drawn
                and left alone — this is where it comes back. */}
            {(cellsReport.needsChoice || []).map((s) => (
              <div className="pts" key={`ask-${s.name}`}>
                <div className="pts-head">Which region is <b>{s.name}</b> in?</div>
                <div className="hint dim">
                  {(s.overlaps || []).filter((o) => s.candidates.includes(o.region))
                    .map((o) => `${o.region} ${(o.frac * 100).toFixed(0)}%`).join(' · ')}
                  {' — '}unanswered, so it is counted in <b>{s.dominant}</b>.
                </div>
                <div className="row">
                  {(s.candidates || []).map((nm) => (
                    <button key={nm} disabled={busy}
                      className={nm === s.dominant ? 'btn sm primary' : 'btn sm'}
                      onClick={() => onAnswerReportDamage && onAnswerReportDamage([nm], s.name)}>
                      {nm}
                    </button>
                  ))}
                  <button className="btn sm" disabled={busy}
                    onClick={() => onAnswerReportDamage
                      && onAnswerReportDamage([...(s.candidates || [])], s.name)}>
                    {(s.candidates || []).length > 2 ? `All ${s.candidates.length}` : 'Both'}
                  </button>
                </div>
              </div>
            ))}
            {(cellsReport.unassigned || []).length > 0 && (
              <div className="hint lvl-warn">
                {cellsReport.unassigned.length} shape(s) overlap no region, so they
                reach no cell: {cellsReport.unassigned.map((s) => s.name).join(', ')}
              </div>
            )}

            {(cellsReport.cells || []).length === 0 && (
              <div className="hint dim">
                No region has damage yet — draw some, or tick a type below after
                turning on “show every region”.
              </div>
            )}

            {(cellsReport.cells || []).map((c) => {
              const spare = (cellsReport.designations || [])
                .filter((d) => !c.tags.includes(d.tag));
              return (
                <div className="pts" key={c.region}>
                  <div className="pts-head">{c.region}</div>
                  <label className="chk">
                    <input type="checkbox" checked={!!c.done} disabled={busy}
                      onChange={(e) => onRegionDamage
                        && onRegionDamage(c.region, { done: e.target.checked })} />
                    <span>{cellsReport.doneLabel || 'Done'}</span>
                  </label>
                  <div className="picks">
                    {c.tags.map((t) => {
                      const drawn = c.drawn.includes(t);
                      const lab = (cellsReport.designations
                        .find((d) => d.tag === t) || {}).label || t;
                      return (
                        <span key={t} className="chip" title={drawn
                          ? 'from a shape you drew — delete the shape to remove it'
                          : 'ticked by hand'}>
                          {lab}
                          {drawn ? <span className="dim"> ◆</span> : (
                            <button className="x" disabled={busy}
                              title="remove"
                              onClick={() => onRegionDamage && onRegionDamage(
                                c.region, { removeExtra: t })}>
                              ×
                            </button>
                          )}
                        </span>
                      );
                    })}
                    {c.tags.length === 0 && <span className="hint dim">no damage yet</span>}
                  </div>
                  {spare.length > 0 && (
                    <select value="" disabled={busy}
                      onChange={(e) => {
                        if (!e.target.value) return;
                        onRegionDamage && onRegionDamage(c.region,
                          { addExtra: e.target.value });
                      }}>
                      <option value="">+ add a damage type…</option>
                      {spare.map((d) => (
                        <option key={d.tag} value={d.tag}>
                          {d.label}{d.drawn === false ? ' (never drawn)' : ''}
                        </option>
                      ))}
                    </select>
                  )}
                  <textarea className="pathin" readOnly rows={Math.min(6, Math.max(2, c.values.length + 1))}
                    value={c.text}
                    style={{ width: '100%', fontFamily: 'monospace', fontSize: '11px' }}
                    onFocus={(e) => e.target.select()} />
                  <button className="btn sm primary" disabled={busy}
                    onClick={() => {
                      // clipboard writes need the gesture itself, not a later
                      // continuation, so this happens in the handler
                      navigator.clipboard.writeText(c.text)
                        .then(() => setCopiedCell(c.region))
                        .catch(() => setCopiedCell(null));
                    }}>
                    {copiedCell === c.region ? 'Copied ✓' : `Copy ${c.region}`}
                  </button>
                  {c.voids.length > 0 && (
                    <div className="hint dim">shapes: {c.voids.join(', ')}</div>
                  )}
                </div>
              );
            })}
            <div className="row">
              <button className="btn sm" onClick={onDismissCells} disabled={busy}>Close</button>
            </div>
          </div>
        )}


        {/* The two YAMLs. They live in the dataset folder — which everything
            else here only reads — and they are shared, so the panel always says
            the exact path, previews as a diff, and never creates a file by
            itself. */}
        <div className="slabel">Annotation notes</div>
        {!notesInfo ? (
          <div className="hint dim">looking in the dataset folder…</div>
        ) : (
          <>
            <div className="mono">{notesInfo.files.notes.path}</div>
            {!notesInfo.files.notes.found ? (
              <>
                <div className="hint lvl-warn">
                  No annotation.notes.yaml in this folder. It is <b>shared per
                  sample</b>, so if one exists elsewhere, open that folder rather
                  than making a second copy here.
                </div>
                <button className="btn" onClick={onNotesCreate} disabled={busy}>
                  Create it here
                </button>
              </>
            ) : (
              <>
                <div className="hint dim">
                  {(notesInfo.files.notes.regions || []).length} regions in the file
                  {notesInfo.files.metadata.found
                    ? ` · metadata.yml roster: ${(notesInfo.files.metadata.annotators || []).join(', ') || 'empty'}`
                    : ' · no metadata.yml'}
                </div>
                <label className="field">
                  <span>Annotator</span>
                  <input
                    type="text" value={(identity && identity.name) || ''}
                    disabled={busy}
                    placeholder="your name (blank = don't write one)"
                    title="Written into the annotator fields and the metadata.yml roster. Left blank, those are not touched — only damage and voids are written."
                    onChange={(e) => onIdentity && onIdentity(e.target.value, false)}
                    onBlur={(e) => onIdentity && onIdentity(e.target.value, true)}
                  />
                </label>
                <div className="row">
                  <button className={`btn sm${notesScope === 'mine' ? ' on' : ''}`}
                    disabled={busy} title="only the regions your edits touched"
                    onClick={() => onNotesScope && onNotesScope('mine')}>
                    Only what I edited
                  </button>
                  <button className={`btn sm${notesScope === 'all' ? ' on' : ''}`}
                    disabled={busy} title="every region in the geometry"
                    onClick={() => onNotesScope && onNotesScope('all')}>
                    All regions
                  </button>
                </div>
                <button className="btn" onClick={onNotesPreview} disabled={busy}>
                  Preview notes changes…
                </button>
              </>
            )}
          </>
        )}

        {notesReport && (
          <div className="geomrep">
            {(notesReport.written || []).length > 0 && (
              <div className="hint mono ok">
                written: {notesReport.written.map((w) => w.path).join(', ')}
              </div>
            )}
            {(notesReport.skipped || []).map((s, i) => (
              <div className="hint lvl-warn" key={i}>
                {s.file} not written — {
                  s.reason === 'changed-on-disk'
                    ? 'it changed on disk since this preview. Someone else has saved '
                      + 'to it. Preview again to see their version first.'
                    : s.reason === 'unexpected-changes'
                      ? 'the round-trip would rewrite lines nobody edited, so the '
                        + 'save was refused rather than reformatting a shared file.'
                      : s.reason === 'nothing-to-write' ? 'nothing to change.'
                        : s.reason}
              </div>
            ))}
            {/* The commonest confusing case: scope is "mine", but this file
                carries no edits of yours yet, so there is correctly nothing to
                write. Say that, rather than a bare "no changes". */}
            {notesScope === 'mine' && (notesReport.workedOn || []).length === 0 && (
              <div className="hint dim">
                Nothing in this file is recorded as yours yet — the trail it
                carries names no edits from your account. Edit a region or draw
                damage first, or switch to <b>All regions</b>.
              </div>
            )}
            {Object.entries(notesReport.files || {}).map(([key, f]) => (
              <div key={key} className="pts">
                <div className="pts-head">{key === 'notes' ? 'annotation.notes.yaml' : 'metadata.yml'}</div>
                <div className="mono">{f.path}</div>
                {!f.found ? (
                  <div className="hint dim">not in this folder — nothing written</div>
                ) : f.unreadable ? (
                  <div className="hint lvl-warn">
                    This file is empty or not valid YAML, so nothing can be written
                    to it. Open it and check before saving — it may have been
                    truncated by an interrupted copy.
                  </div>
                ) : f.unchanged ? (
                  <div className="hint dim">no changes</div>
                ) : (
                  <>
                    <div className="hint dim">
                      {(f.changes || []).length} change(s)
                      {key === 'notes' && notesReport.workedOn
                        ? ` · scope: ${notesScope === 'all' ? 'all regions'
                          : `${notesReport.workedOn.length} region(s) you edited`}`
                        : ''}
                    </div>
                    {(f.unexpected || []).length > 0 && (
                      <div className="hint lvl-warn">
                        {f.unexpected.length} line(s) changed that no edit accounts
                        for — saving is refused. This means the round-trip is
                        rewriting the file, not that your edit is wrong.
                      </div>
                    )}
                    {(f.missingRegions || []).length > 0 && (
                      <div className="hint lvl-warn">
                        not in the YAML, so not written: {f.missingRegions.join(', ')}
                      </div>
                    )}
                    <pre className="diff">
                      {(f.diff || []).map((ln, i) => (
                        <div key={i} className={
                          ln.startsWith('+++') || ln.startsWith('---') ? 'dim'
                            : ln.startsWith('+') ? 'add'
                              : ln.startsWith('-') ? 'del'
                                : ln.startsWith('@@') ? 'hunk' : ''
                        }>{ln}</div>
                      ))}
                    </pre>
                  </>
                )}
              </div>
            ))}
            <div className="row">
              <button className="btn sm primary" onClick={() => onNotesSave && onNotesSave()}
                disabled={busy || !Object.values(notesReport.files || {})
                  .some((f) => f.found && !f.unchanged && !(f.unexpected || []).length)}>
                Save to the dataset folder
              </button>
              <button className="btn sm" onClick={onDismissNotes} disabled={busy}>Close</button>
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

      {/* The app has no window of its own; on a Mac there is not even a
          console to close. This is how it stops. */}
      <div className="section">
        <button className="btn" disabled={busy} onClick={onQuit}
          title="Stop the FiveAtlas server. Save first -- unsaved edits are lost.">
          ⏻ Quit FiveAtlas
        </button>
      </div>
    </div>
  );
}

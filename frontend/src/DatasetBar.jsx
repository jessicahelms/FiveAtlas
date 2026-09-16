import { useState } from 'react';

export default function DatasetBar({
  datasets, activeId, onSelect, onOpenFolder, onOpenPath, busy,
  canSave, dirty, busySave, onSave, onReset, onUndo, onRedo, canUndo, canRedo,
  saveResult, onDismissSave, onRevealSave, onSaveAs, saveTarget, onForgetSaveTarget,
}) {
  const shown = saveResult && (saveResult.exported || saveResult.path);
  const warn = saveResult && (saveResult.refused || saveResult.failed || saveResult.exportError);
  const [path, setPath] = useState('');
  return (
    <div className="dsbar">
      <span className="dsbar-title">Dataset</span>
      <select value={activeId || ''} onChange={(e) => onSelect(e.target.value)}>
        {(!activeId || !datasets.some((d) => d.id === activeId)) && <option value="">—</option>}
        {datasets.map((d) => (
          <option key={d.id} value={d.id}>{d.label}</option>
        ))}
      </select>
      <button className="btn sm" onClick={onOpenFolder} disabled={busy}>📂 Open folder…</button>
      <input
        className="pathin"
        placeholder="…or paste a folder path and press Enter"
        value={path}
        onChange={(e) => setPath(e.target.value)}
        onKeyDown={(e) => { if (e.key === 'Enter' && path.trim()) onOpenPath(path.trim()); }}
      />
      {busy && <span className="dsbar-busy">scanning…</span>}

      {/* The things you reach for constantly live here, in one place that never
          scrolls away: undo, redo, and Save -- which used to be at the bottom of
          a long sidebar section called "Edit". */}
      <div className="dsbar-right">
        <button className="btn sm" onClick={onUndo} disabled={!canUndo}
          title="Undo (Ctrl+Z)">↶</button>
        <button className="btn sm" onClick={onRedo} disabled={!canRedo}
          title="Redo (Ctrl+Shift+Z)">↷</button>
        {canSave && (
          <span className={dirty ? 'dsbar-dirty' : 'dsbar-clean'}
            title={dirty
              ? 'there are edits that are not written to disk yet'
              : 'everything is written to disk'}>
            {dirty ? '● unsaved' : 'saved'}
          </span>
        )}
        {saveTarget && (
          <span className="dsbar-target" title={`Save writes ${saveTarget}`}>
            → {saveTarget.split(/[\\/]/).pop()}
          </span>
        )}
        <button className={dirty ? 'btn sm primary' : 'btn sm'}
          onClick={onSave} disabled={!canSave || busySave}
          title={saveTarget
            ? `write the regions to ${saveTarget} (and a backup in the app's own folder)`
            : "write the edited regions to the app's own folder (the dataset's files are never touched)"}>
          Save
        </button>
        <button className="btn sm" onClick={() => onSaveAs && onSaveAs()}
          disabled={!canSave || busySave}
          title="choose the file to save the regions in; later Saves update the same file">
          Save as…
        </button>
        <button className="btn sm" onClick={onReset} disabled={!canSave || busySave}
          title="throw away every edit since the last save and reload">
          Reset
        </button>
      </div>

      {/* Right under Save, so the answer to "did it save, and where?" is where
          you are already looking. It stays until you close it or edit again. */}
      {/* A refusal or a failure shows even with unsaved edits -- those are
          exactly the moments the answer matters. */}
      {saveResult && (saveResult.refused || saveResult.failed || !dirty) && (
        <div className={warn ? 'savetoast warn' : 'savetoast'} role="status">
          <div className="savetoast-head">
            <b>{saveResult.refused ? 'Not saved — that is an original file'
              : saveResult.failed ? 'Not saved'
                : saveResult.exportError ? '✓ Saved — but not to your chosen file'
                  : '✓ Saved'}</b>
            <button className="savetoast-x" onClick={onDismissSave} title="close">×</button>
          </div>

          {saveResult.refused && (
            <>
              <div className="savetoast-note">{saveResult.message}</div>
              <div className="savetoast-note">Save beside it instead, as:</div>
              <div className="savetoast-path" title={saveResult.suggested}>{saveResult.suggested}</div>
              <div className="row">
                <button className="btn sm primary"
                  onClick={() => onSaveAs && onSaveAs(saveResult.suggested)}>
                  Save there
                </button>
                <button className="btn sm" onClick={() => onSaveAs && onSaveAs()}>
                  Choose another place…
                </button>
              </div>
            </>
          )}

          {saveResult.failed && <div className="savetoast-note">{saveResult.message}</div>}

          {!saveResult.refused && !saveResult.failed && (
            <>
              <div className="savetoast-path" title={shown}>{shown}</div>
              {saveResult.exportError && (
                <div className="savetoast-note">{saveResult.exportError}</div>
              )}
              {saveResult.exported && saveResult.originalFrame && (
                <div className="savetoast-note">
                  Written in the slide's original orientation, so it lines up with the
                  files beside it.
                </div>
              )}
              <div className="savetoast-note">
                {saveResult.exported
                  ? 'A backup is also kept in the app’s own folder, with a snapshot of every save.'
                  : `A copy of this save was also kept in the versions folder beside it${
                    saveResult.versions ? ` (${saveResult.versions} so far)` : ''
                  } — earlier saves are never overwritten.`}
              </div>
              <div className="row">
                <button className="btn sm primary" onClick={() => onRevealSave && onRevealSave(shown)}>
                  Open folder
                </button>
                <button className="btn sm"
                  onClick={() => navigator.clipboard && navigator.clipboard.writeText(shown)}>
                  Copy path
                </button>
                {saveTarget && (
                  <button className="btn sm"
                    title="later Saves go only to the app's own folder again; the file itself is left alone"
                    onClick={() => onForgetSaveTarget && onForgetSaveTarget()}>
                    Stop saving here
                  </button>
                )}
              </div>
            </>
          )}
        </div>
      )}
    </div>
  );
}

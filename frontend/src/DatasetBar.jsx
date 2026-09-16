import { useState } from 'react';

export default function DatasetBar({
  datasets, activeId, onSelect, onOpenFolder, onOpenPath, busy,
  canSave, dirty, busySave, onSave, onReset, onUndo, onRedo, canUndo, canRedo,
  saveResult, onDismissSave, onRevealSave,
}) {
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
        <button className={dirty ? 'btn sm primary' : 'btn sm'}
          onClick={onSave} disabled={!canSave || busySave}
          title="write the edited regions to your working copy (the dataset's own file is never touched)">
          Save
        </button>
        <button className="btn sm" onClick={onReset} disabled={!canSave || busySave}
          title="throw away every edit since the last save and reload">
          Reset
        </button>
      </div>

      {/* Right under Save, so the answer to "did it save, and where?" is where
          you are already looking. It stays until you close it or edit again. */}
      {saveResult && !dirty && (
        <div className="savetoast" role="status">
          <div className="savetoast-head">
            <b>✓ Saved</b>
            <button className="savetoast-x" onClick={onDismissSave} title="close">×</button>
          </div>
          <div className="savetoast-path" title={saveResult.path}>{saveResult.path}</div>
          <div className="savetoast-note">
            A copy of this save was also kept in the <b>versions</b> folder beside it
            {saveResult.versions ? ` (${saveResult.versions} so far)` : ''} — earlier
            saves are never overwritten.
          </div>
          <div className="row">
            <button className="btn sm primary" onClick={() => onRevealSave && onRevealSave(saveResult.path)}>
              Open folder
            </button>
            <button className="btn sm"
              onClick={() => navigator.clipboard && navigator.clipboard.writeText(saveResult.path)}>
              Copy path
            </button>
          </div>
        </div>
      )}
    </div>
  );
}

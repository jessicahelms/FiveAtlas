import { useState } from 'react';
import SuggestionBox from './SuggestionBox';

export default function DatasetBar({
  datasets, activeId, onSelect, onOpenFolder, onOpenPath, busy,
  canSave, dirty, busySave, onSave, onReset, onUndo, onRedo, canUndo, canRedo,
  saveResult, onDismissSave, onRevealSave, onSaveAs, saveTarget,
  version, installedVersion, canRestart, onRestart, restartState,
  updateReady, onApplyUpdate, updateApplyState,
}) {
  const shown = saveResult && (saveResult.exported || saveResult.path);
  const warn = saveResult && (saveResult.refused || saveResult.failed || saveResult.exportError);
  const [path, setPath] = useState('');
  return (
    <>
    <div className="dsbar">
      {/* Which FiveAtlas this is, always in view: after an update, the first
          question is "am I on the new one?" and there was nowhere to look. */}
      {version && (
        <span className="dsbar-version" title="the FiveAtlas version you are running">
          FiveAtlas {version}
        </span>
      )}
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
        {canSave && saveTarget && (
          <>
            <span className="dsbar-target" title={saveTarget}>
              Saving to {saveTarget.split(/[\\/]/).pop()}
            </span>
            <button className="linkbtn" disabled={busySave}
              title="choose a different file; later Saves go there instead"
              onClick={() => onSaveAs && onSaveAs()}>(change)</button>
          </>
        )}
        <button className={dirty ? 'btn sm primary' : 'btn sm'}
          onClick={onSave} disabled={!canSave || busySave}
          title={saveTarget
            ? `write the regions to ${saveTarget}`
            : 'save the regions -- the first time, you choose where'}>
          Save
        </button>
        <button className="btn sm" onClick={onReset} disabled={!canSave || busySave}
          title="throw away every edit since the last save and reload">
          Reset
        </button>
        <SuggestionBox version={version} />
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

              </div>
            </>
          )}
        </div>
      )}
    </div>

    {/* A different FiveAtlas was installed while this one kept running (Mac:
        Finder replaces a running app, and opening it again only wakes this old
        process). Say so across the top, and offer the one click that switches. */}
    {installedVersion && (
      <div className="update-strip" role="status">
        {restartState === 'restarting' ? (
          <span>
            Restarting — FiveAtlas {installedVersion} opens in a new tab in a few
            seconds. You can close this tab.
          </span>
        ) : (
          <>
            <span>
              <b>FiveAtlas {installedVersion} has been installed</b>
              {version ? ` (this tab is still ${version})` : ''}.{' '}
              {canRestart
                ? 'Save your work, then restart to switch to it.'
                : 'Save your work, then close FiveAtlas and open it again to switch.'}
            </span>
            {canRestart && (
              <button className="btn sm primary" onClick={onRestart}>
                Restart FiveAtlas
              </button>
            )}
            {restartState && <span className="update-strip-note">{restartState}</span>}
          </>
        )}
      </div>
    )}

    {/* A newer release has been downloaded in the background (selfupdate.py).
        One click switches; left alone, it switches the next time FiveAtlas is
        opened. Not shown when the Mac strip above is already offering one. */}
    {updateReady && !installedVersion && (
      <div className="update-strip" role="status">
        {updateApplyState === 'restarting' ? (
          <span>
            Updating — FiveAtlas {updateReady.version} opens in a new tab in a few
            seconds. You can close this tab.
          </span>
        ) : (
          <>
            <span>
              <b>FiveAtlas {updateReady.version} is ready</b>
              {updateReady.headline ? <> — {updateReady.headline}</> : null}.{' '}
              Save your work, then restart to update (or it updates the next time
              you open FiveAtlas).
            </span>
            <button className="btn sm primary" onClick={onApplyUpdate}>
              Restart to update
            </button>
            {updateApplyState && <span className="update-strip-note">{updateApplyState}</span>}
          </>
        )}
      </div>
    )}
    </>
  );
}

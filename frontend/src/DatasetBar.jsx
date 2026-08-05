import { useState } from 'react';

export default function DatasetBar({ datasets, activeId, onSelect, onOpenFolder, onOpenPath, busy }) {
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
    </div>
  );
}

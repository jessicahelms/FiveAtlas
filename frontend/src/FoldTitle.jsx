// A section heading that opens and closes its section. The whole bar is the
// button -- not just the little arrow -- so it is one easy click anywhere on it.
export default function FoldTitle({ open, onToggle, small = false, children }) {
  return (
    <button
      type="button"
      className={`${small ? 'slabel' : 'section-title'} fold`}
      aria-expanded={open}
      title={open ? 'click to close this section' : 'click to open this section'}
      onClick={onToggle}
    >
      <span className="fold-label">{children}</span>
      <span className="arr" aria-hidden="true">{open ? '▾' : '▸'}</span>
    </button>
  );
}

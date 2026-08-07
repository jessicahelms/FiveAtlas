// Reading damage names on the client.
//
// `backend/damage.py` is the source of truth for the vocabulary AND for the
// spellings that resolve to it; both arrive from /api/damage/designations, so
// nothing here is a second copy of the list. What IS mirrored is the split of a
// name into stem + number, kept identical to `parse_name()` there.
//
// The trap: **a trailing number is not a damage marker.** This dataset's ordinary
// anatomy includes PAL.1, sAMY.2 and VL.1. Only a stem that resolves to a
// designation counts; anything else is a region and must be left alone.

// Case, spaces, underscores and hyphens are all ignored, so "Small Void",
// "small_void" and "VoidSmall" are one thing. Same as _key() in damage.py.
export const fold = (s) => String(s ?? '').replace(/[\s_-]+/g, '').toLowerCase();

// { tag, number } for a damage name, { tag: null, number: null } for anything
// else. `aliases` is the folded -> tag map served by the backend.
export function parseName(name, aliases) {
  const raw = String(name ?? '').trim();
  const m = raw.match(/^(.*?)[.\s_-]*(\d+)\s*$/);
  const stem = m ? m[1] : raw;
  const number = m ? parseInt(m[2], 10) : null;
  const tag = aliases ? aliases[fold(stem)] : null;
  return tag ? { tag, number } : { tag: null, number: null };
}

export function isDamage(name, aliases) {
  return parseName(name, aliases).tag != null;
}

// The next free number for a designation. Global across the whole file, not per
// region — the lab's own numbering runs separation.4/.5/.6 in one region and .7
// in another. Preview only: the authoritative number comes back from the server
// when the shape is created.
export function nextNumber(names, tag, aliases) {
  const used = new Set();
  (names || []).forEach((nm) => {
    const p = parseName(nm, aliases);
    if (p.tag === tag && p.number != null) used.add(p.number);
  });
  let n = 1;
  while (used.has(n)) n += 1;
  return n;
}

export function formatName(tag, n) {
  return `${tag}.${n}`;
}

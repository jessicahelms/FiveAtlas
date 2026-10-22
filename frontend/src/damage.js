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

// Case, spaces, underscores, hyphens and dots are all ignored, so "Small Void",
// "small_void" and "VoidSmall" are one thing — and so is the trailing dot a
// merge used to leave behind ("separation."). Same as _key() in damage.py.
export const fold = (s) => String(s ?? '').replace(/[\s_.-]+/g, '').toLowerCase();

// { tag, number } for a damage name, { tag: null, number: null } for anything
// else. `aliases` is the folded -> tag map served by the backend.
// Trailing number groups are peeled ONE AT A TIME, mirroring parse_name(). The
// app used to create names with two of them — a split of separation.7 was called
// "separation.7 2", a merge of separation.1 and .2 was called "separation." — and
// neither resolved, so the shape stopped counting as damage and the clean-up
// tools deleted it. Fixed where the names are made, but such names are already
// saved in people's files. Peeling more than one group loses track of which
// shape it is, so the number comes back null.
export function parseName(name, aliases) {
  const raw = String(name ?? '').trim();
  let stem = raw;
  const peeled = [];
  for (let i = 0; i < 4; i += 1) {
    const tag = aliases ? aliases[fold(stem)] : null;
    if (tag) return { tag, number: peeled.length === 1 ? peeled[0] : null };
    const m = stem.match(/^(.*?)[.\s_-]*(\d+)\s*$/);
    if (!m) break;
    stem = m[1];
    peeled.push(parseInt(m[2], 10));
  }
  return { tag: null, number: null };
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

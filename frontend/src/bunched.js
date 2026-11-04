// Points of a region's outline that sit right next to the point before or after
// them -- the clusters annotators hate dragging one by one. Drawn orange on the
// map and counted in the Tidy points box. BUNCHED_PX matches the backend's
// _RS_MIN_GAP: Tidy points leaves no two points closer than this where it can.
export const BUNCHED_PX = 40;

export function bunchedPoints(feature, px = BUNCHED_PX) {
  const g = feature && feature.geometry;
  if (!g) return [];
  const polys = g.type === 'Polygon' ? [g.coordinates]
    : (g.type === 'MultiPolygon' ? g.coordinates : []);
  const out = [];
  for (const poly of polys) {
    for (const ring of poly) {
      const n = ring.length - 1;            // closed: last point repeats the first
      if (n < 3) continue;
      const near = new Array(n).fill(false);
      for (let i = 0; i < n; i += 1) {
        const a = ring[i];
        const b = ring[(i + 1) % n];
        if (Math.hypot(b[0] - a[0], b[1] - a[1]) < px) {
          near[i] = true;
          near[(i + 1) % n] = true;
        }
      }
      for (let i = 0; i < n; i += 1) if (near[i]) out.push(ring[i]);
    }
  }
  return out;
}

export const pointKey = (c) => `${Number(c[0]).toFixed(3)},${Number(c[1]).toFixed(3)}`;

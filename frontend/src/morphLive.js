// Live morphology window (brightness/contrast), kept outside React state on
// purpose -- the same reason stainLive exists. The morphology is windowed on the
// GPU now, so a slider can repaint every tick; committing each tick to App state
// would re-render the whole app. The slider writes here (only the Viewer
// subscribes), commits to App state when the hand pauses, and clears its live
// entry when that value comes back.
const listeners = new Set();
let live = null;          // {lo?, hi?, gamma?} or null

export function setMorphLive(patch) {
  live = { ...(live || {}), ...patch };
  listeners.forEach((l) => l());
}

export function clearMorphLive() {
  if (live === null) return;
  live = null;
  listeners.forEach((l) => l());
}

export function subscribeMorphLive(listener) {
  listeners.add(listener);
  return () => listeners.delete(listener);
}

export function getMorphLive() {
  return live;
}

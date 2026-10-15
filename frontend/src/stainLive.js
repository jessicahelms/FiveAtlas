// Live stain slider values, kept outside React state on purpose.
//
// The stain composite is now done on the GPU, so a slider can repaint on every
// tick -- but committing each tick to App state would re-render the whole app
// (sidebar, region list...) 60 times a second, which is its own lag on a slow
// laptop. Instead the slider writes here; only the Viewer subscribes. The
// channel row still commits to App state once the hand pauses, and clears its
// live entry when that value comes back.
const listeners = new Set();
let live = {};          // channel index -> {min, max}

export function setStainLive(index, patch) {
  live = { ...live, [index]: { ...live[index], ...patch } };
  listeners.forEach((l) => l());
}

export function clearStainLive(index) {
  if (!(index in live)) return;
  const { [index]: _gone, ...rest } = live;
  live = rest;
  listeners.forEach((l) => l());
}

export function subscribeStainLive(listener) {
  listeners.add(listener);
  return () => listeners.delete(listener);
}

export function getStainLive() {
  return live;
}

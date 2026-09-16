// Fetching one image tile for a deck.gl TileLayer without leaving holes.
//
// When getTileData fails, deck marks that tile as loaded with nothing in it and
// never asks for it again. One slow or interrupted read -- a network drive, Box
// streaming a file on demand, a request that raced a dataset switch -- used to
// leave a permanently blank square in the stains until the page was reloaded:
// "only part of the stain loads". Here a failed tile is asked for again, a
// little later each time. Only a cancel stops it early: deck cancels tiles that
// scrolled out of view, and asks for them again when they come back.

const RETRY_WAITS_MS = [500, 1500, 3000, 6000];

// A wait that ends at once, as a cancel, when deck cancels the tile.
export function sleep(ms, signal) {
  return new Promise((resolve, reject) => {
    const abort = () => {
      clearTimeout(timer);
      reject(new DOMException('Aborted', 'AbortError'));
    };
    const timer = setTimeout(() => {
      if (signal) signal.removeEventListener('abort', abort);
      resolve();
    }, ms);
    if (signal) {
      if (signal.aborted) abort();
      else signal.addEventListener('abort', abort, { once: true });
    }
  });
}

// Resolves to the decoded image, or null when the server says the tile is empty
// (204). Rejects only when deck cancelled it, or when every retry failed.
export async function fetchTile(url, {
  signal,
  fetchImpl = (u, o) => fetch(u, o),
  decode = (blob) => createImageBitmap(blob),
  waits = RETRY_WAITS_MS,
} = {}) {
  for (let attempt = 0; ; attempt += 1) {
    let why = 'network error';
    try {
      const r = await fetchImpl(url, { signal });
      if (r.status === 204) return null;              // nothing there: empty, not failed
      if (r.ok) return await decode(await r.blob());
      why = `HTTP ${r.status}`;
    } catch (e) {
      if (signal && signal.aborted) throw e;          // cancelled by deck: stop quietly
      // a dropped connection, or a body that would not decode: try again
    }
    if (attempt >= waits.length) throw new Error(`tile failed after ${attempt + 1} tries (${why}): ${url}`);
    await sleep(waits[attempt], signal);
  }
}

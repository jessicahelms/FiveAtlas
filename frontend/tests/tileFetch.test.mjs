// node frontend/tests/tileFetch.test.mjs
//
// A tile that fails is asked for again instead of being kept blank for good,
// an empty tile (204) is not retried, and a cancel stops everything at once.
// Checked without a browser: fetch and the image decoder are stand-ins.
import { fetchTile, sleep } from '../src/tileFetch.js';

let fails = 0;
const check = (label, cond, detail = '') => {
  console.log(`  ${cond ? 'ok  ' : 'FAIL'}  ${label}${detail ? '  ' + detail : ''}`);
  if (!cond) fails += 1;
};

const reply = (status) => ({ status, ok: status >= 200 && status < 300, blob: async () => `png-${status}` });
const decode = async (b) => `decoded(${b})`;
const WAITS = [5, 5, 5, 5];

// a fetch that plays back a script of outcomes: a status, or 'drop' to throw
function scripted(steps) {
  const calls = [];
  const impl = async (url, { signal } = {}) => {
    calls.push(url);
    if (signal && signal.aborted) throw new DOMException('Aborted', 'AbortError');
    const step = steps[Math.min(calls.length - 1, steps.length - 1)];
    if (step === 'drop') throw new TypeError('Failed to fetch');
    return reply(step);
  };
  return { impl, calls };
}

console.log('\na tile that fails, then works, is loaded -- not left blank');
{
  const f = scripted([503, 'drop', 200]);
  const img = await fetchTile('/t/1', { fetchImpl: f.impl, decode, waits: WAITS });
  check('the image arrives', img === 'decoded(png-200)', String(img));
  check('it took three tries', f.calls.length === 3, `${f.calls.length} calls`);
}

console.log('\nan empty tile (204) is empty, and is not asked for again');
{
  const f = scripted([204]);
  const img = await fetchTile('/t/2', { fetchImpl: f.impl, decode, waits: WAITS });
  check('null, no image', img === null);
  check('one request only', f.calls.length === 1, `${f.calls.length} calls`);
}

console.log('\na tile that never works gives up after the retries');
{
  const f = scripted([500]);
  let err = null;
  try { await fetchTile('/t/3', { fetchImpl: f.impl, decode, waits: WAITS }); } catch (e) { err = e; }
  check('it fails in the end', err instanceof Error && /HTTP 500/.test(err.message), err && err.message);
  check('one try plus four retries', f.calls.length === 5, `${f.calls.length} calls`);
}

console.log('\nan image that will not decode is tried again');
{
  const f = scripted([200]);
  let n = 0;
  const flaky = async (b) => { n += 1; if (n === 1) throw new Error('bad image'); return `decoded(${b})`; };
  const img = await fetchTile('/t/4', { fetchImpl: f.impl, decode: flaky, waits: WAITS });
  check('decoded on the second try', img === 'decoded(png-200)' && f.calls.length === 2, `${f.calls.length} calls`);
}

console.log('\ncancelling stops at once, even in the middle of a wait');
{
  const f = scripted([503]);
  const ac = new AbortController();
  const t0 = Date.now();
  const p = fetchTile('/t/5', { signal: ac.signal, fetchImpl: f.impl, decode, waits: [10000, 10000] });
  setTimeout(() => ac.abort(), 30);
  let err = null;
  try { await p; } catch (e) { err = e; }
  check('rejects as a cancel', err && err.name === 'AbortError', err && err.name);
  check('without sitting out the 10 s wait', Date.now() - t0 < 2000, `${Date.now() - t0} ms`);
  check('and asks no more', f.calls.length === 1, `${f.calls.length} calls`);
}

console.log('\na tile cancelled before it starts is not fetched as a failure');
{
  const f = scripted([200]);
  const ac = new AbortController();
  ac.abort();
  let err = null;
  try { await fetchTile('/t/6', { signal: ac.signal, fetchImpl: f.impl, decode, waits: WAITS }); } catch (e) { err = e; }
  check('rejects as a cancel', err && err.name === 'AbortError', err && err.name);
  check('no retries', f.calls.length === 1, `${f.calls.length} calls`);
}

console.log('\nsleep resolves on time when nothing cancels it');
{
  const t0 = Date.now();
  await sleep(20, new AbortController().signal);
  check('waited', Date.now() - t0 >= 15, `${Date.now() - t0} ms`);
}

console.log('\n' + (fails ? `${fails} FAILED` : 'all checks passed'));
process.exit(fails ? 1 : 0);

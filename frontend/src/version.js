// What to SHOW as the version. The server's version string is exact on purpose
// -- region files are stamped with it -- but a copy run from source reports what
// git says, "1.3.2-2-gfe42ee6-dirty", which only confuses people. Shown: the
// release number, plus a note when this is not a release build. The exact
// string stays available as the label's tooltip.
export function displayVersion(v) {
  const s = String(v || '').trim();
  const m = s.match(/^v?(\d+\.\d+\.\d+)(.*)$/);
  if (!m) return s === 'dev' ? 'development copy' : s;
  const [, num, rest] = m;
  if (!rest) return num;                                  // a release: 1.3.2
  if (/^-\d+-g[0-9a-f]+/.test(rest) || /dirty/.test(rest)) {
    return `${num} (development copy)`;                   // from a git checkout
  }
  if (/^-test/i.test(rest)) return `${num} (test build)`;
  return num;
}

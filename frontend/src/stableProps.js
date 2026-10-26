// Memoising a panel that App re-renders on every drag frame.
//
// App passes the sidebar ~70 props, many of them handlers that are new functions
// on every render (inline closures, or useCallbacks keyed on `fc`), so a plain
// React.memo never skips anything: during a vertex drag the whole region list
// re-rendered at frame rate. withStableHandlers(C) hands C a stable proxy for
// every function prop -- one per prop name for the life of the panel -- that
// always calls the LATEST handler, so nothing can go stale, and memoises C so it
// re-renders only when a non-function prop actually changes. `equal` can supply
// a looser comparison for particular props (e.g. a FeatureCollection whose
// geometry moves while everything the panel reads stays the same).
import { createElement, memo, useRef } from 'react';

function shallowEqualWith(custom) {
  return (a, b) => {
    const ka = Object.keys(a);
    if (ka.length !== Object.keys(b).length) return false;
    for (const k of ka) {
      if (a[k] === b[k]) continue;
      const eq = custom && custom[k];
      if (!eq || !eq(a[k], b[k])) return false;
    }
    return true;
  };
}

export function withStableHandlers(Component, equal) {
  const Inner = memo(Component, shallowEqualWith(equal));
  function Stable(props) {
    const latest = useRef(props);
    latest.current = props;
    const proxies = useRef({});
    const out = {};
    Object.keys(props).forEach((k) => {
      const v = props[k];
      if (typeof v === 'function') {
        if (!proxies.current[k]) proxies.current[k] = (...args) => latest.current[k](...args);
        out[k] = proxies.current[k];
      } else {
        out[k] = v;
      }
    });
    return createElement(Inner, out);
  }
  Stable.displayName = `Stable(${Component.displayName || Component.name || 'Component'})`;
  return Stable;
}

// Two FeatureCollections are the same TO A LIST OF REGIONS when they hold the
// same features in the same order with the same properties objects. A vertex
// drag rebuilds geometry but keeps every properties object, so the sidebar can
// skip those frames; any rename, recolour, add, delete or reorder changes one.
export function sameFeatureProperties(a, b) {
  const fa = a && a.features;
  const fb = b && b.features;
  if (!fa || !fb || fa.length !== fb.length) return false;
  for (let i = 0; i < fa.length; i += 1) {
    if ((fa[i] && fa[i].properties) !== (fb[i] && fb[i].properties)) return false;
  }
  return true;
}

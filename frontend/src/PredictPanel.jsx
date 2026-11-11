// Predict regions from the transcripts: its own group in the top bar, beside where
// a dataset is opened. One click and the regions are on the map. The model needs to
// know how the section lies (hippocampus up, medial edge right is what it was made
// on), so those settings are kept from last time, under the ▾, and the app warns
// when the result looks mirrored or upside down.
import { useEffect, useMemo, useRef, useState } from 'react';
import * as api from './api';
import { isDamage } from './damage';

const SAVED = 'fiveatlas.predict';
const readSaved = () => {
  try { return JSON.parse(localStorage.getItem(SAVED)) || {}; } catch { return {}; }
};
const nameOf = (f, idProp) => String((f.properties || {})[idProp] ?? '');
const cleanError = (e) => String(e.message || e).replace(/^\d+:\s*/, '').replace(/^\{"detail":"|"\}$/g, '');
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

// A finished job stays on the server until the next one, so a dataset opened again
// would be offered the same result again. The result's own stamp names it: once it
// was placed or discarded it is not offered again.
const SEEN = (ds) => `fiveatlas.predict.seen.${ds}`;
const resultKey = (s) => {
  const p = s && s.fc && s.fc._provenance;
  const e = Array.isArray(p) && p.length ? p[p.length - 1] : null;
  return e ? `${e.t}|${s.report && s.report.model}|${s.report && s.report.outline}` : null;
};
const wasSeen = (ds, s) => {
  try { const k = resultKey(s); return !!k && localStorage.getItem(SEEN(ds)) === k; } catch { return false; }
};
const markSeen = (ds, s) => {
  try { const k = resultKey(s); if (k) localStorage.setItem(SEEN(ds), k); } catch { /* only a convenience */ }
};

const RESTARTED = 'The prediction stopped (the app restarted). Run it again.';
const STATUS_RETRY_MS = [2000, 5000, 15000, 30000];
const POLL_TRIES = 6;

export default function PredictPanel({ dsId, fc, idProp, aliases, busy, onResult }) {
  const saved = useRef(readSaved()).current;
  const [status, setStatus] = useState(null);
  const [checking, setChecking] = useState(false);
  const [checkTick, setCheckTick] = useState(0);
  const [showWhy, setShowWhy] = useState(false);
  const [model, setModel] = useState(saved.model || '');
  const [open, setOpen] = useState(false);
  const [roi, setRoi] = useState(null);                 // null = not yet chosen
  const [medial, setMedial] = useState(saved.medial === 'left' ? 'left' : 'right');
  const [dorsal, setDorsal] = useState(saved.dorsal === 'down' ? 'down' : 'up');
  const [smooth, setSmooth] = useState(saved.smooth !== false);
  const [trim, setTrim] = useState(false);
  const [pieces, setPieces] = useState(false);
  const [job, setJob] = useState(null);                 // {stage, elapsed, retrying} while running
  const [problem, setProblem] = useState(null);
  const [lost, setLost] = useState(false);              // stopped polling; the job may still run
  const [done, setDone] = useState(null);               // {count, report}
  const [ask, setAsk] = useState(null);                 // {result, resumed}
  const live = useRef(true);
  const polling = useRef(false);

  // Can this install predict? Asked again on request, and by itself a few times
  // when the app could not be reached at all.
  useEffect(() => {
    let gone = false;
    let timer = null;
    const check = (n) => {
      setChecking(true);
      api.predictStatus().then((s) => {
        if (gone) return;
        setStatus(s); setChecking(false);
      }).catch((e) => {
        if (gone) return;
        setStatus({ available: false, failed: true, retrying: n < STATUS_RETRY_MS.length, models: [],
          reason: `The app could not be asked whether it can predict (${cleanError(e)}).` });
        setChecking(false);
        if (n < STATUS_RETRY_MS.length) timer = setTimeout(() => check(n + 1), STATUS_RETRY_MS[n]);
      });
    };
    check(0);
    return () => { gone = true; clearTimeout(timer); };
  }, [checkTick]);
  const recheck = () => setCheckTick((t) => t + 1);

  useEffect(() => {
    try {
      localStorage.setItem(SAVED, JSON.stringify({ model, medial, dorsal, smooth }));
    } catch { /* only a convenience */ }
  }, [model, medial, dorsal, smooth]);

  const outlines = useMemo(() => {
    const seen = new Set();
    for (const f of (fc && fc.features) || []) {
      const n = nameOf(f, idProp);
      if (n && f.geometry && !isDamage(n, aliases)) seen.add(n);
    }
    return [...seen];
  }, [fc, idProp, aliases]);

  const models = (status && status.models) || [];
  const usable = models.filter((m) => m.usable);
  const chosenModel = usable.some((m) => m.id === model) ? model : (status && status.default) || '';
  useEffect(() => {
    if (dsId && chosenModel) api.preparePredict(dsId, chosenModel).catch(() => { /* Predict reads it instead */ });
  }, [dsId, chosenModel]);
  const hemi = outlines.find((n) => n.trim().toLowerCase() === 'hemi');
  const chosen = roi === null || (roi && !outlines.includes(roi)) ? (hemi || '') : roi;
  const running = !!job;
  const unavailable = !!(status && !status.available);
  const whyOpen = showWhy && unavailable;              // closes by itself once it can run
  const why = !dsId ? 'Open a dataset first'
    : !status ? 'Checking for the region model…'
      : unavailable ? status.reason : '';

  // resumed: found on the server rather than started here -- always asked, never
  // placed unasked
  // poll/resume are long-running loops started by an earlier render: they reach the
  // app through this ref, so it judges the result against the map, orientation and
  // settings as they are NOW, not as they were when the job started.
  const onResultRef = useRef(onResult);
  onResultRef.current = onResult;
  const place = (result, choice, resumed) => {
    const out = onResultRef.current(result, choice || (resumed ? 'count' : undefined)) || {};
    if (out.wait) {
      // the dataset is still loading: nothing can be judged yet, and nothing is
      // refused or marked seen -- try again shortly
      setTimeout(() => { if (live.current) place(result, choice, resumed); }, 400);
      return;
    }
    if (out.error) { setAsk(null); setProblem(out.error); markSeen(dsId, result); return; }
    if (out.others != null) { setAsk({ result, resumed: !!resumed }); return; }
    setAsk(null); markSeen(dsId, result);
    setDone({ count: out.placed != null ? out.placed : (result.fc.features || []).length,
      renamed: out.renamed || 0, added: choice === 'add', report: result.report });
  };
  const discard = () => { if (ask) markSeen(dsId, ask.result); setAsk(null); };

  // Follow the job to its end. One failed request is not the end of it: asked again
  // a few times before giving up, and even then it can be picked up again.
  const poll = async () => {
    if (polling.current) return;
    polling.current = true;
    let misses = 0;
    try {
      while (live.current) {
        let s;
        try {
          s = await api.predictState(dsId);
          misses = 0;
        } catch (e) {
          if (!live.current) return;
          misses += 1;
          if (misses >= POLL_TRIES) {
            setJob(null); setLost(true);
            setProblem(`Lost touch with the app while predicting (${cleanError(e)}). It may still be running.`);
            return;
          }
          setJob((j) => ({ ...(j || { stage: 'Predicting' }), retrying: true }));
          await sleep(1000 * misses);
          continue;
        }
        if (!live.current) return;
        if (s.state === 'running') {
          setJob({ stage: s.stage, elapsed: s.elapsed });
          await sleep(500);
        } else {
          setJob(null);
          if (s.state === 'failed') setProblem(s.error);
          else if (s.state === 'done') place(s);
          else setProblem(RESTARTED);              // 'idle': the server forgot the job
          return;
        }
      }
    } finally { polling.current = false; }
  };

  // A prediction this dataset already has on the server: one still running is
  // followed again, a finished one not yet answered is offered.
  const resume = async (fromMount) => {
    let s = null;
    for (let i = 0; i < (fromMount ? 3 : 1) && live.current; i += 1) {
      try { s = await api.predictState(dsId); break; } catch (e) {
        if (!fromMount) { setProblem(`The app could not be reached (${cleanError(e)}).`); return; }
        await sleep(1500);
      }
    }
    if (!s || !live.current) return;
    if (s.state === 'running') {
      setProblem(null); setLost(false);
      setJob({ stage: s.stage, elapsed: s.elapsed });
      poll();
    } else if (s.state === 'done') {
      if (fromMount && wasSeen(dsId, s)) return;
      setProblem(null); setLost(false);
      place(s, undefined, fromMount);
    } else if (!fromMount) {
      setLost(false);
      setProblem(s.state === 'failed' ? s.error : RESTARTED);
    }
  };

  useEffect(() => {
    live.current = true;
    if (dsId) resume(true);
    return () => { live.current = false; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [dsId]);

  const start = async () => {
    if (!status || !status.available) { setShowWhy(true); recheck(); return; }
    setJob({ stage: 'Starting' }); setProblem(null); setLost(false); setDone(null); setAsk(null);
    setOpen(false); setShowWhy(false);
    try {
      const geoms = chosen
        ? fc.features.filter((f) => nameOf(f, idProp) === chosen && f.geometry).map((f) => f.geometry)
        : null;
      await api.startPredict(dsId, {
        roiName: chosen || null, roiGeometries: geoms, medial, dorsal, trim, pieces, smooth, model: chosenModel,
      });
    } catch (e) {
      if (!live.current) return;
      // one is already running on this dataset (started before a reload): follow it
      if (/^409\b/.test(String(e.message || e))) { await poll(); return; }
      setJob(null); setProblem(cleanError(e));
      if (/^422\b/.test(String(e.message || e))) recheck();   // the model may have gone
      return;
    }
    await poll();
  };

  // The question's count follows the map while it is open (worked out again on every
  // map change, not every render), and once more when the answer is given.
  const askNow = useMemo(() => (ask ? (onResultRef.current(ask.result, 'count') || {}) : null),
    [ask, fc]);
  const askOthers = askNow && askNow.others ? askNow.others : 0;

  const warnings = (done && done.report.warnings) || [];
  const goTitle = running
    ? (job.retrying ? 'The app did not answer: asking again' : job.stage)
    : why || 'Predict the regions and put them on the map';
  return (
    <div className="dsbar-predict">
      {usable.length > 1 && (
        <select value={chosenModel} disabled={running} title="prediction model"
          onChange={(e) => setModel(e.target.value)}>
          {models.map((m) => (
            <option key={m.id} value={m.id} disabled={!m.usable} title={m.title}>{m.label}</option>
          ))}
        </select>
      )}
      <button className={unavailable ? 'btn sm predict-go' : 'btn sm primary predict-go'}
        disabled={busy || running || !dsId || !status}
        title={unavailable ? `${status.reason} Click to check again.` : goTitle}
        onClick={start}>
        {running
          ? (job.retrying ? 'Reconnecting…'
            : `${job.elapsed ? `${Math.round(job.elapsed)} s · ` : ''}${job.stage}…`)
          : 'Predict regions'}
      </button>
      {unavailable ? (
        <button type="button" className={whyOpen ? 'predict-note on' : 'predict-note'}
          title={`${status.reason} Click for details.`} onClick={() => setShowWhy((w) => !w)}>
          ⚠ {status.failed
            ? `Prediction: the app did not answer${status.retrying ? ', retrying' : ''}`
            : status.reason}
        </button>
      ) : (
        <button className={open ? 'btn sm on' : 'btn sm'} disabled={running || !!why}
          title={why || 'Prediction options: outline and which way the section faces'}
          onClick={() => setOpen((o) => !o)}>▾</button>
      )}
      {done && !warnings.length && (
        <span className="predict-done" title="A model's map, not an annotation: check it before saving. Ctrl+Z takes it back.">
          ✓ {done.count} regions{done.added ? ' added' : ''}{done.renamed ? ` (${done.renamed} renamed so no name is used twice)` : ''}, {Math.round(done.report.seconds)} s
        </span>
      )}

      {whyOpen && (
        <div className="predict-pop warn" role="alert">
          <div className="savetoast-head">
            <b>{status.failed ? 'The app did not answer' : 'Region prediction is not available'}</b>
            <button className="savetoast-x" title="close" onClick={() => setShowWhy(false)}>×</button>
          </div>
          <div className="hint">{status.reason}</div>
          {status.retrying && <div className="hint dim">It is asked again by itself in a few seconds.</div>}
          <div className="row">
            <button className="btn sm" disabled={checking} onClick={recheck}>
              {checking ? 'Checking…' : 'Check again'}
            </button>
          </div>
        </div>
      )}

      {open && !unavailable && (
        <div className="predict-pop">
          <label className="hint">Section outline
            <select value={chosen} onChange={(e) => setRoi(e.target.value)}>
              <option value="">Whole slide (one section only)</option>
              {outlines.map((n) => <option key={n} value={n}>{n}</option>)}
            </select>
          </label>
          <div className="hint">On screen, the section's medial edge is on the</div>
          <div className="row">
            {['right', 'left'].map((v) => (
              <button key={v} className={medial === v ? 'btn sm on' : 'btn sm'} onClick={() => setMedial(v)}>{v}</button>
            ))}
          </div>
          <div className="hint">and its hippocampus (dorsal side) is</div>
          <div className="row">
            {['up', 'down'].map((v) => (
              <button key={v} className={dorsal === v ? 'btn sm on' : 'btn sm'} onClick={() => setDorsal(v)}>{v}</button>
            ))}
          </div>
          <div className="hint dim">If the section lies on its side, rotate the view first.</div>
          <label className="hint predict-check" title="Off: the exact pixel outlines, as in the files on Box">
            <input type="checkbox" checked={smooth} onChange={(e) => setSmooth(e.target.checked)} /> Smooth edges
          </label>
          <label className="hint predict-check">
            <input type="checkbox" checked={pieces} onChange={(e) => setPieces(e.target.checked)} /> Section is in separate pieces
          </label>
          <label className="hint predict-check" title="Only for a neighbouring section on the same scan: the prediction can run onto it">
            <input type="checkbox" checked={trim} disabled={!chosen} onChange={(e) => setTrim(e.target.checked)} /> Another section touches this one: trim to the outline
          </label>
        </div>
      )}

      {!open && !whyOpen && ask && (
        <div className="predict-pop warn">
          <div className="pts-head">
            {askNow.error || (
              `${ask.resumed ? 'A prediction made earlier is ready.' : 'The prediction is ready.'}${askOthers
                ? ` The map already has ${askOthers} region${askOthers === 1 ? '' : 's'} that it didn't make.` : ''}`)}
          </div>
          <div className="row">
            {!askNow.error && askOthers > 0 && (<>
              <button className="btn sm primary" onClick={() => place(ask.result, 'replace')}>Replace them</button>
              <button className="btn sm" onClick={() => place(ask.result, 'add')}>Add alongside</button>
            </>)}
            {!askNow.error && !askOthers && (
              <button className="btn sm primary" onClick={() => place(ask.result, 'replace')}>Put it on the map</button>
            )}
            <button className="btn sm" onClick={discard}>Discard</button>
          </div>
          {!askNow.error && (
            <div className="hint dim">
              {askOthers ? 'Replace keeps the outline it ran inside and any damage shapes. ' : ''}Ctrl+Z undoes it.
            </div>
          )}
        </div>
      )}

      {!open && !whyOpen && !ask && (problem || warnings.length > 0) && (
        <div className="predict-pop warn" role="alert">
          <div className="savetoast-head">
            <b>{problem
              ? (lost ? 'Prediction not followed' : problem === RESTARTED ? 'Prediction stopped' : 'Prediction failed')
              : `${done.count} regions placed: worth a look`}</b>
            <button className="savetoast-x" title="close" onClick={() => { setProblem(null); setLost(false); setDone(null); }}>×</button>
          </div>
          {problem && <div className="hint">{problem}</div>}
          {warnings.map((w) => <div className="hint" key={w}>{w}</div>)}
          {lost && (
            <div className="row">
              <button className="btn sm" onClick={() => resume(false)}>Check again</button>
            </div>
          )}
          {warnings.length > 0 && (
            <div className="row">
              <button className="btn sm" onClick={() => setOpen(true)}>Change options</button>
            </div>
          )}
        </div>
      )}
    </div>
  );
}

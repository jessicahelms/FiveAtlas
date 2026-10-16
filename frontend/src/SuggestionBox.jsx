import { useState } from 'react';
import { WEB3FORMS_KEY, SUGGESTION_EMAIL } from './config';

// "Sug-Jess-tions": the suggestion box for Jess. A button in the top bar that opens
// a small form. With WEB3FORMS_KEY set the suggestion is sent from the page and
// arrives as an email; without it (or if sending fails) the person's own email
// app opens with the message already written, and there is a Copy button for
// machines with no email app at all. Only what they type is sent, plus the app
// version and the kind of computer -- never paths, datasets or regions.
export default function SuggestionBox({ version }) {
  const [open, setOpen] = useState(false);
  const [text, setText] = useState('');
  const [name, setName] = useState('');
  const [state, setState] = useState(null); // null | 'sending' | 'sent' | {fallback, why}

  const platform = (() => {
    const ua = navigator.userAgent || '';
    if (/Mac OS X/.test(ua)) return 'Mac';
    if (/Windows/.test(ua)) return 'Windows';
    return navigator.platform || 'unknown';
  })();
  const subject = `Sug-Jess-tion from FiveAtlas${name.trim() ? ` (${name.trim()})` : ''}`;
  const body = `${text.trim()}\n\n--\nFrom: ${name.trim() || '(no name given)'}\n`
    + `FiveAtlas ${version || '?'} on ${platform}`;

  const openEmailApp = () => {
    window.location.href = `mailto:${SUGGESTION_EMAIL}?subject=${encodeURIComponent(subject)}`
      + `&body=${encodeURIComponent(body)}`;
  };

  const send = async () => {
    if (!text.trim()) return;
    if (!WEB3FORMS_KEY) {
      openEmailApp();
      setState({ fallback: true, why: null });
      return;
    }
    setState('sending');
    try {
      const r = await fetch('https://api.web3forms.com/submit', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', Accept: 'application/json' },
        body: JSON.stringify({
          access_key: WEB3FORMS_KEY,
          subject,
          from_name: name.trim() || 'FiveAtlas user',
          message: body,
        }),
      });
      const j = await r.json().catch(() => ({}));
      if (!r.ok || !j.success) throw new Error(j.message || `HTTP ${r.status}`);
      setState('sent');
      setText('');
    } catch (e) {
      openEmailApp();
      setState({ fallback: true, why: `It could not be sent from here (${e.message || 'no connection'}).` });
    }
  };

  const close = () => { setOpen(false); if (state === 'sent') setState(null); };

  return (
    <>
      <button className="btn sm" onClick={() => setOpen((o) => !o)}
        title="Suggestion box for Jess: ideas, bugs, anything">
        💡 Sug-Jess-tions
      </button>
      {open && (
        <div className="savetoast suggestbox" role="dialog" aria-label="Suggestion box">
          <div className="savetoast-head">
            <span><b>Sug-Jess-tions</b></span>
            <button className="savetoast-x" onClick={close} title="close">×</button>
          </div>
          {state === 'sent' ? (
            <p className="suggestbox-note">Thank you — it's on its way to Jess.</p>
          ) : (
            <>
              <textarea className="suggestbox-text" rows={5} autoFocus
                placeholder={'Hi Jess!\n\nI have an issue with...'}
                value={text} onChange={(e) => { setText(e.target.value); if (state && state !== 'sending') setState(null); }} />
              <input className="suggestbox-name" placeholder="Your name (optional)"
                value={name} onChange={(e) => setName(e.target.value)} />
              <div className="suggestbox-row">
                <span className="suggestbox-small">Sends what you type, plus FiveAtlas {version || ''} on {platform}.</span>
                <button className="btn sm primary" onClick={send}
                  disabled={!text.trim() || state === 'sending'}>
                  {state === 'sending' ? 'Sending…' : 'Send'}
                </button>
              </div>
              {state && state.fallback && (
                <p className="suggestbox-note">
                  {state.why ? `${state.why} ` : ''}
                  Your email app should have opened with it written out — press Send
                  there. No email app? <button className="linkbtn" onClick={() => {
                    navigator.clipboard && navigator.clipboard.writeText(`${subject}\n\n${body}`);
                  }}>Copy it</button> and email it to {SUGGESTION_EMAIL}.
                  {' '}<button className="linkbtn" onClick={openEmailApp}>Open it again</button>
                </p>
              )}
            </>
          )}
        </div>
      )}
    </>
  );
}

// Same-origin: Vite dev server proxies /api -> http://127.0.0.1:8000
export const API = '/api';

// SugJESStions (SuggestionBox.jsx). With a Web3Forms access key a suggestion is
// sent straight from the page and arrives as an email; the key stands in for
// the address, so it is fine for it to be in this public code. Without one, the
// box opens the person's own email app, addressed here, instead.
export const WEB3FORMS_KEY = '';
export const SUGGESTION_EMAIL = 'helmsj@wustl.edu';

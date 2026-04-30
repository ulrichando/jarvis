// actions.js — DOM action handlers for jarvis-screen v3.0.
// All handlers return {ok: bool, ...} envelopes. Errors are caught
// and returned, never thrown — the bridge expects deterministic JSON.

function ok(extra = {}) { return Object.assign({ ok: true }, extra); }
function err(msg) { return { ok: false, error: msg }; }

// ── Navigation (content.js side) ─────────────────────────────────────
// ext_navigate / ext_back / ext_forward / ext_close_tab are dispatched
// in background.js using chrome.tabs / chrome.history APIs.
// content.js only handles the content-side observers.

function ext_get_url() {
  return ok({ url: location.href, title: document.title });
}

function ext_close_tab() {
  // Actual close is done in background.js via chrome.tabs.remove.
  // From content.js the handler just acknowledges so the dispatcher
  // can return synchronously — close is fire-and-forget.
  return ok({ action: 'close_requested' });
}

// ── Page reading ─────────────────────────────────────────────────────

const TEXT_CAP = 4096;

function _safeQS(selector) {
  try { return document.querySelectorAll(selector); }
  catch { return null; }
}

function ext_extract_text(args = {}) {
  const sel = args.selector || 'body';
  const nodes = _safeQS(sel);
  if (nodes === null) return err('invalid selector');
  if (!nodes.length) return err('no element matched');
  const text = Array.from(nodes)
    .map(n => (n.innerText || n.textContent || '').trim())
    .filter(Boolean)
    .join('\n')
    .slice(0, TEXT_CAP);
  return ok({ text });
}

function ext_find_by_text(args = {}) {
  const target = (args.text || '').toLowerCase();
  if (!target) return err('text arg required');
  const matches = [];
  document.querySelectorAll('a,button,input,[role="button"],[role="link"],h1,h2,h3,h4,li,span,p,div')
    .forEach(el => {
      const t = (el.innerText || el.textContent || '').trim().toLowerCase();
      if (t === target || t.includes(target)) {
        matches.push(_describeElement(el));
      }
      if (matches.length >= 20) return;
    });
  return ok({ matches });
}

function _describeElement(el) {
  // Build a stable selector. Prefer id, then aria-label, then position.
  if (el.id) {
    const escaped = typeof CSS !== 'undefined' && CSS.escape ? CSS.escape(el.id) : el.id.replace(/[^a-zA-Z0-9_-]/g, '\\$&');
    return { selector: `#${escaped}`, text: (el.innerText||'').slice(0,80), tag: el.tagName.toLowerCase() };
  }
  const aria = el.getAttribute && el.getAttribute('aria-label');
  if (aria) return { selector: `[aria-label="${aria.replace(/"/g,'\\"')}"]`, text: aria, tag: el.tagName.toLowerCase() };
  const parent = el.parentElement;
  if (parent) {
    const sibs = Array.from(parent.children).filter(c => c.tagName === el.tagName);
    const idx = sibs.indexOf(el);
    return { selector: `${el.tagName.toLowerCase()}:nth-of-type(${idx+1})`, text: (el.innerText||'').slice(0,80), tag: el.tagName.toLowerCase() };
  }
  return { selector: el.tagName.toLowerCase(), text: (el.innerText||'').slice(0,80), tag: el.tagName.toLowerCase() };
}

function ext_dom_summary() {
  const headings = Array.from(document.querySelectorAll('h1,h2,h3,h4,h5,h6'))
    .slice(0, 30)
    .map(h => ({ level: parseInt(h.tagName[1]), text: (h.textContent||'').trim().slice(0,120) }));
  const actionable_elements = Array.from(
    document.querySelectorAll('a,button,input,select,textarea,[role="button"],[role="link"]')
  ).slice(0, 30).map(el => {
    const desc = _describeElement(el);
    return {
      ...desc,
      role: el.getAttribute('role') || el.tagName.toLowerCase(),
      label: el.getAttribute('aria-label') || el.getAttribute('placeholder') || (el.innerText||'').trim().slice(0,80),
    };
  });
  return ok({ headings, actionable_elements });
}

function ext_screenshot() {
  // Screenshot capture requires chrome.tabs.captureVisibleTab,
  // which content.js can't call directly. Background.js intercepts
  // this action and substitutes the real implementation. From here
  // we just acknowledge to keep the message-routing protocol uniform.
  return ok({ delegated_to_background: true });
}

// ── Mouse ────────────────────────────────────────────────────────────

function _findOne(selector) {
  if (!selector) return null;
  try { return document.querySelector(selector); } catch { return null; }
}

function ext_click(args = {}) {
  const el = _findOne(args.selector);
  if (!el) return err(`selector not found: ${args.selector}`);
  el.click();
  return ok();
}

function ext_right_click(args = {}) {
  const el = _findOne(args.selector);
  if (!el) return err(`selector not found: ${args.selector}`);
  const rect = el.getBoundingClientRect();
  el.dispatchEvent(new MouseEvent('contextmenu', {
    bubbles: true, cancelable: true,
    button: 2, clientX: rect.left + rect.width/2, clientY: rect.top + rect.height/2,
  }));
  return ok();
}

function ext_hover(args = {}) {
  const el = _findOne(args.selector);
  if (!el) return err(`selector not found: ${args.selector}`);
  ['mouseenter', 'mouseover', 'mousemove'].forEach(type => {
    el.dispatchEvent(new MouseEvent(type, { bubbles: true, cancelable: true }));
  });
  return ok();
}

function ext_drag(args = {}) {
  const src = _findOne(args.from_selector);
  const tgt = _findOne(args.to_selector);
  if (!src) return err(`from_selector not found: ${args.from_selector}`);
  if (!tgt) return err(`to_selector not found: ${args.to_selector}`);
  // DataTransfer and DragEvent may not be available in test environments
  let dt;
  try {
    dt = new DataTransfer();
  } catch {
    dt = {}; // fallback: empty object for tests
  }
  // Try DragEvent first; fall back to MouseEvent for jsdom compatibility
  const dragEventClass = typeof DragEvent !== 'undefined' ? DragEvent : MouseEvent;
  const eventOptions = { bubbles: true, dataTransfer: dt };
  src.dispatchEvent(new dragEventClass('dragstart', eventOptions));
  tgt.dispatchEvent(new dragEventClass('dragenter', eventOptions));
  tgt.dispatchEvent(new dragEventClass('dragover',  eventOptions));
  tgt.dispatchEvent(new dragEventClass('drop',      eventOptions));
  src.dispatchEvent(new dragEventClass('dragend',   eventOptions));
  return ok();
}

function ext_select(args = {}) {
  const el = _findOne(args.selector);
  if (!el) return err(`selector not found: ${args.selector}`);
  if (el.tagName !== 'SELECT') return err('not a <select> element');
  const opts = Array.from(el.options);
  const byValue = opts.find(o => o.value === args.value);
  const byText  = opts.find(o => o.text  === args.value);
  const opt = byValue || byText;
  if (!opt) return err(`option not found: ${args.value}`);
  el.value = opt.value;
  el.dispatchEvent(new Event('change', { bubbles: true }));
  return ok();
}

module.exports = Object.assign(module.exports || {}, {
  ext_get_url, ext_close_tab,
  ext_extract_text, ext_find_by_text, ext_dom_summary, ext_screenshot,
  ext_click, ext_right_click, ext_hover, ext_drag, ext_select,
});

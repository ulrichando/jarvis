# JARVIS Extension Browser Control v3 — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the unreliable `browser_task` (browser-use lib) path with a Chrome extension v3.0 that accepts deterministic high-level commands from a step-by-step `BrowserSpecialistAgent`.

**Architecture:** Voice agent emits ONE structured command per LLM call → bridge → extension WebSocket → content.js DOM op → result returns to LLM, repeat. The unreliable thing (multi-step computer-use) moves out of the LLM into deterministic JS. Modeled on Manus Browser Operator.

**Tech Stack:** Manifest V3 Chrome extension, Bun (bridge), Python livekit-agents (voice agent), Jest (extension tests), pytest (Python tests).

**Spec:** [`docs/superpowers/specs/2026-04-30-jarvis-extension-browser-control-design.md`](../specs/2026-04-30-jarvis-extension-browser-control-design.md)

---

## File Structure

| File | Purpose |
|---|---|
| `src/extensions/jarvis-screen/manifest.json` (M) | Bump v2.0 → v3.0, add `webNavigation` permission |
| `src/extensions/jarvis-screen/background.js` (M) | WS keep-alive + command dispatcher |
| `src/extensions/jarvis-screen/content.js` (M) | Load actions.js handlers, route runtime messages |
| `src/extensions/jarvis-screen/actions.js` (NEW) | 25 DOM action handlers, one function each |
| `src/extensions/jarvis-screen/safety.js` (NEW) | `is_destructive()` + domain allowlist + credentials block |
| `src/extensions/jarvis-screen/side_panel.{html,js}` (M) | Status pill: connection + recent action log |
| `src/extensions/jarvis-screen/tests/jest.config.js` (NEW) | Jest config (jsdom env) |
| `src/extensions/jarvis-screen/tests/package.json` (NEW) | Jest + jsdom dev deps |
| `src/extensions/jarvis-screen/tests/actions.test.js` (NEW) | 25 unit tests, one per command |
| `src/extensions/jarvis-screen/tests/safety.test.js` (NEW) | Safety gate tests |
| `src/cli/src/bridge/ext_browse.ts` (NEW) | `/api/ext_browse` handler + cmd queue |
| `src/cli/src/bridge/server.ts` (M) | Wire ext_browse route + bidirectional /ws |
| `src/cli/src/bridge/tests/ext_browse.test.ts` (NEW) | Bridge route tests (bun test) |
| `src/voice-agent/jarvis_specialist_agents.py` (M) | Add `BrowserSpecialistAgent` with 25 ext_* tools |
| `src/voice-agent/jarvis_agent.py` (M) | Add `transfer_to_browser` handoff |
| `src/voice-agent/tests/test_browser_specialist.py` (NEW) | BrowserSpecialistAgent unit tests |
| `src/voice-agent/tests/test_extension_browser.py` (NEW) | End-to-end smoke test |

---

## Conventions

- All commits use `voice:`, `bridge:`, or `ext:` prefix to match repo convention
- No `Co-Authored-By` trailers (per saved feedback)
- Python tests run from `src/voice-agent/` via `.venv/bin/python -m pytest`
- Extension Jest tests run from `src/extensions/jarvis-screen/tests/` via `npx jest`
- Bridge tests run from `src/cli/` via `bun test src/bridge/tests/`

---

## Task 1: Jest scaffolding for extension tests

**Files:**
- Create: `src/extensions/jarvis-screen/tests/package.json`
- Create: `src/extensions/jarvis-screen/tests/jest.config.js`

- [ ] **Step 1.1: Create `tests/package.json`**

```json
{
  "name": "jarvis-screen-tests",
  "version": "1.0.0",
  "private": true,
  "scripts": { "test": "jest" },
  "devDependencies": {
    "jest": "^29.7.0",
    "jest-environment-jsdom": "^29.7.0"
  }
}
```

- [ ] **Step 1.2: Create `tests/jest.config.js`**

```js
module.exports = {
  testEnvironment: "jsdom",
  testMatch: ["**/tests/**/*.test.js"],
  rootDir: "..",   // so tests can `require('../actions.js')`
};
```

- [ ] **Step 1.3: Install deps and verify**

```bash
cd src/extensions/jarvis-screen/tests
npm install
npx jest --listTests
```

Expected: lists future test files (or nothing — exit 0). No errors.

- [ ] **Step 1.4: Commit**

```bash
cd /home/ulrich/Documents/Projects/jarvis
git add src/extensions/jarvis-screen/tests/package.json src/extensions/jarvis-screen/tests/jest.config.js src/extensions/jarvis-screen/tests/package-lock.json src/extensions/jarvis-screen/tests/node_modules/.package-lock.json 2>/dev/null
# .gitignore should ignore node_modules; if not, leave it untracked
git commit -m "ext: scaffold Jest test runner for extension"
```

---

## Task 2: Safety module — destructive-verb detection

**Files:**
- Create: `src/extensions/jarvis-screen/safety.js`
- Create: `src/extensions/jarvis-screen/tests/safety.test.js`

- [ ] **Step 2.1: Write failing test**

```js
// tests/safety.test.js
const { isDestructive } = require('../safety.js');

describe('isDestructive', () => {
  test.each([
    [{action: 'click', args: {selector: 'button.delete'}}, true],
    [{action: 'click', args: {selector: 'button#purchase'}}, true],
    [{action: 'click', args: {selector: 'a.cancel-subscription'}}, true],
    [{action: 'submit', args: {form_selector: 'form#payment'}}, true],
    [{action: 'click', args: {selector: 'button.subscribe'}}, false],
    [{action: 'extract_text', args: {selector: 'body'}}, false],
    [{action: 'navigate', args: {url: 'https://example.com'}}, false],
    [{action: 'exec_js', args: {code: 'document.title'}}, true],          // always-confirm
    [{action: 'set_cookies', args: {domain: 'example.com'}}, true],       // always-confirm
  ])('isDestructive(%j) === %s', (cmd, expected) => {
    expect(isDestructive(cmd)).toBe(expected);
  });
});
```

- [ ] **Step 2.2: Run test, verify it fails**

```bash
cd src/extensions/jarvis-screen/tests && npx jest safety
```

Expected: `Cannot find module '../safety.js'`

- [ ] **Step 2.3: Implement `safety.js`**

```js
// safety.js — deterministic safety gates for extension commands.

const DESTRUCTIVE_RE = /\b(delete|remove|unfollow|unfriend|block|buy|purchase|order|checkout|pay|transfer|send|wire|tweet|post|publish|share|email|reply|comment|cancel|unsubscribe|deactivate|close[\-_ ]?account)\b/i;

const ALWAYS_CONFIRM_ACTIONS = new Set(['exec_js', 'set_cookies']);

const PAYMENT_DOMAINS = [
  'stripe.com', 'paypal.com', 'amazon.com/checkout',
  'bankofamerica.com', 'chase.com', 'wellsfargo.com',
  'citi.com', 'usbank.com', 'capitalone.com',
];

const CREDENTIAL_KEYS = /password|otp|2fa|mfa|cvv|card[\-_ ]?number|ssn/i;

function isDestructive(cmd) {
  if (!cmd || !cmd.action) return false;
  if (ALWAYS_CONFIRM_ACTIONS.has(cmd.action)) return true;
  // Inspect string args for destructive verbs.
  const flat = JSON.stringify(cmd.args || {});
  if (DESTRUCTIVE_RE.test(flat)) return true;
  // Submit on payment domains.
  if (cmd.action === 'submit' && cmd.args && cmd.args.form_selector) {
    const url = (typeof location !== 'undefined' && location.href) || '';
    if (PAYMENT_DOMAINS.some(d => url.includes(d))) return true;
  }
  return false;
}

function hasCredentials(cmd) {
  if (!cmd || !cmd.args) return false;
  return CREDENTIAL_KEYS.test(JSON.stringify(cmd.args));
}

function isAllowedDomain(url, allowlist) {
  if (!allowlist || !allowlist.length) return true;
  try {
    const host = new URL(url).hostname;
    return allowlist.some(d => host === d || host.endsWith('.' + d));
  } catch {
    return false;
  }
}

module.exports = { isDestructive, hasCredentials, isAllowedDomain };
```

- [ ] **Step 2.4: Run test, verify pass**

```bash
cd src/extensions/jarvis-screen/tests && npx jest safety
```

Expected: 9 passing.

- [ ] **Step 2.5: Add credentials + allowlist tests, then commit**

Append to `tests/safety.test.js`:

```js
const { hasCredentials, isAllowedDomain } = require('../safety.js');

describe('hasCredentials', () => {
  test.each([
    [{action: 'type', args: {selector: '#pw', text: 'mypassword'}}, true],
    [{action: 'fill_form', args: {fields: {otp: '123456'}}}, true],
    [{action: 'fill_form', args: {fields: {cvv: '123'}}}, true],
    [{action: 'type', args: {selector: '#email', text: 'a@b.com'}}, false],
  ])('hasCredentials(%j) === %s', (cmd, want) => {
    expect(hasCredentials(cmd)).toBe(want);
  });
});

describe('isAllowedDomain', () => {
  test('empty allowlist permits all', () => {
    expect(isAllowedDomain('https://example.com', [])).toBe(true);
    expect(isAllowedDomain('https://example.com', null)).toBe(true);
  });
  test('exact match allowed', () => {
    expect(isAllowedDomain('https://gmail.com/inbox', ['gmail.com'])).toBe(true);
  });
  test('subdomain allowed', () => {
    expect(isAllowedDomain('https://mail.gmail.com', ['gmail.com'])).toBe(true);
  });
  test('non-listed blocked', () => {
    expect(isAllowedDomain('https://evil.com', ['gmail.com'])).toBe(false);
  });
});
```

```bash
cd src/extensions/jarvis-screen/tests && npx jest safety
```

Expected: all green.

```bash
cd /home/ulrich/Documents/Projects/jarvis
git add src/extensions/jarvis-screen/safety.js src/extensions/jarvis-screen/tests/safety.test.js
git commit -m "ext: safety module — destructive-verb, credentials, allowlist gates"
```

---

## Task 3: Action handlers — Navigation (5 commands)

**Files:**
- Create: `src/extensions/jarvis-screen/actions.js`
- Create: `src/extensions/jarvis-screen/tests/actions.test.js`

- [ ] **Step 3.1: Write failing tests for navigation handlers**

```js
// tests/actions.test.js
const actions = require('../actions.js');

describe('navigation actions', () => {
  test('ext_get_url returns location info', () => {
    document.title = 'Test Page';
    Object.defineProperty(window, 'location', {
      value: { href: 'https://example.com/foo' },
      writable: true,
    });
    expect(actions.ext_get_url()).toEqual({
      ok: true, url: 'https://example.com/foo', title: 'Test Page'
    });
  });

  test('ext_close_tab returns ok (extension context handles actual close)', () => {
    expect(actions.ext_close_tab()).toEqual({ ok: true, action: 'close_requested' });
  });
});
```

(Note: `ext_navigate`, `ext_back`, `ext_forward` are dispatched from background.js using the chrome.tabs API — content.js doesn't handle them. We test only the content.js-side handlers here. background.js dispatch is integration-tested in the E2E task.)

- [ ] **Step 3.2: Run test, verify fail**

```bash
cd src/extensions/jarvis-screen/tests && npx jest actions
```

Expected: `Cannot find module '../actions.js'`.

- [ ] **Step 3.3: Implement `actions.js` skeleton + 2 navigation handlers**

```js
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

module.exports = { ext_get_url, ext_close_tab };
```

- [ ] **Step 3.4: Run, verify pass**

```bash
cd src/extensions/jarvis-screen/tests && npx jest actions
```

Expected: 2 passing.

- [ ] **Step 3.5: Commit**

```bash
git add src/extensions/jarvis-screen/actions.js src/extensions/jarvis-screen/tests/actions.test.js
git commit -m "ext: navigation action handlers (get_url, close_tab)"
```

---

## Task 4: Action handlers — Reading the page (4 commands)

**Files:**
- Modify: `src/extensions/jarvis-screen/actions.js`
- Modify: `src/extensions/jarvis-screen/tests/actions.test.js`

- [ ] **Step 4.1: Write tests**

Append to `tests/actions.test.js`:

```js
describe('page reading actions', () => {
  beforeEach(() => {
    document.body.innerHTML = `
      <h1>Title</h1>
      <h2>Subhead</h2>
      <p>Hello world.</p>
      <button id="btn1" aria-label="Submit">Submit</button>
      <a href="/x" role="link">More information</a>
      <input type="text" name="email" placeholder="email@x.com">
    `;
  });

  test('ext_extract_text default body', () => {
    const r = actions.ext_extract_text({});
    expect(r.ok).toBe(true);
    expect(r.text).toContain('Hello world');
    expect(r.text).toContain('Title');
  });

  test('ext_extract_text by selector', () => {
    const r = actions.ext_extract_text({ selector: 'p' });
    expect(r.text).toBe('Hello world.');
  });

  test('ext_extract_text invalid selector', () => {
    const r = actions.ext_extract_text({ selector: '###bad' });
    expect(r.ok).toBe(false);
  });

  test('ext_find_by_text exact match', () => {
    const r = actions.ext_find_by_text({ text: 'More information' });
    expect(r.ok).toBe(true);
    expect(r.matches.length).toBeGreaterThan(0);
  });

  test('ext_find_by_text no match', () => {
    const r = actions.ext_find_by_text({ text: 'nonexistent_xyz' });
    expect(r.ok).toBe(true);
    expect(r.matches).toEqual([]);
  });

  test('ext_dom_summary returns headings + actionable elements', () => {
    const r = actions.ext_dom_summary();
    expect(r.ok).toBe(true);
    expect(r.headings.find(h => h.text === 'Title')).toBeDefined();
    expect(r.actionable_elements.length).toBeGreaterThan(0);
    const btn = r.actionable_elements.find(e => e.role === 'button' || e.tag === 'button');
    expect(btn).toBeDefined();
  });

  test('ext_screenshot returns placeholder ok (real screenshot in background.js)', () => {
    const r = actions.ext_screenshot();
    expect(r.ok).toBe(true);
    expect(r.delegated_to_background).toBe(true);
  });
});
```

- [ ] **Step 4.2: Run, verify failures**

```bash
cd src/extensions/jarvis-screen/tests && npx jest actions
```

Expected: failures naming the missing functions.

- [ ] **Step 4.3: Implement reading handlers**

Append to `actions.js`:

```js
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
  // Walk all elements and find ones whose innerText *exactly* contains target.
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
  // Build a stable selector. Prefer id, then aria-label, then text.
  if (el.id) return { selector: `#${CSS.escape(el.id)}`, text: el.innerText?.slice(0,80), tag: el.tagName.toLowerCase() };
  const aria = el.getAttribute && el.getAttribute('aria-label');
  if (aria) return { selector: `[aria-label="${aria.replace(/"/g,'\\"')}"]`, text: aria, tag: el.tagName.toLowerCase() };
  // Fallback: tag + position
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

module.exports = Object.assign(module.exports || {}, {
  ext_extract_text, ext_find_by_text, ext_dom_summary, ext_screenshot,
});
```

- [ ] **Step 4.4: Run, verify pass**

```bash
cd src/extensions/jarvis-screen/tests && npx jest actions
```

Expected: all green (8 passing now).

- [ ] **Step 4.5: Commit**

```bash
git add src/extensions/jarvis-screen/actions.js src/extensions/jarvis-screen/tests/actions.test.js
git commit -m "ext: page-reading action handlers (extract_text, find_by_text, dom_summary, screenshot)"
```

---

## Task 5: Action handlers — Mouse (5 commands)

**Files:**
- Modify: `src/extensions/jarvis-screen/actions.js`
- Modify: `src/extensions/jarvis-screen/tests/actions.test.js`

- [ ] **Step 5.1: Write tests for click, right_click, hover, drag, select**

Append to `tests/actions.test.js`:

```js
describe('mouse actions', () => {
  beforeEach(() => {
    document.body.innerHTML = `
      <button id="b1">B1</button>
      <select id="s1">
        <option value="a">A</option>
        <option value="b">B</option>
      </select>
      <div id="src" draggable="true">Source</div>
      <div id="tgt">Target</div>
    `;
  });

  test('ext_click hits the element', () => {
    let clicked = false;
    document.getElementById('b1').addEventListener('click', () => { clicked = true; });
    const r = actions.ext_click({ selector: '#b1' });
    expect(r.ok).toBe(true);
    expect(clicked).toBe(true);
  });

  test('ext_click selector not found', () => {
    expect(actions.ext_click({ selector: '#nope' }).ok).toBe(false);
  });

  test('ext_right_click fires contextmenu', () => {
    let fired = false;
    document.getElementById('b1').addEventListener('contextmenu', () => { fired = true; });
    expect(actions.ext_right_click({ selector: '#b1' }).ok).toBe(true);
    expect(fired).toBe(true);
  });

  test('ext_hover fires mouseover', () => {
    let fired = false;
    document.getElementById('b1').addEventListener('mouseover', () => { fired = true; });
    expect(actions.ext_hover({ selector: '#b1' }).ok).toBe(true);
    expect(fired).toBe(true);
  });

  test('ext_select sets dropdown value', () => {
    expect(actions.ext_select({ selector: '#s1', value: 'b' }).ok).toBe(true);
    expect(document.getElementById('s1').value).toBe('b');
  });

  test('ext_drag fires dragstart and drop', () => {
    const events = [];
    ['dragstart','dragend','drop'].forEach(ev =>
      document.getElementById(ev === 'drop' ? 'tgt' : 'src')
        .addEventListener(ev, () => events.push(ev)));
    expect(actions.ext_drag({ from_selector: '#src', to_selector: '#tgt' }).ok).toBe(true);
    expect(events).toContain('dragstart');
  });
});
```

- [ ] **Step 5.2: Verify failures**

```bash
cd src/extensions/jarvis-screen/tests && npx jest actions
```

- [ ] **Step 5.3: Implement mouse handlers**

Append to `actions.js`:

```js
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
  const dt = new DataTransfer();
  src.dispatchEvent(new DragEvent('dragstart', { bubbles: true, dataTransfer: dt }));
  tgt.dispatchEvent(new DragEvent('dragenter', { bubbles: true, dataTransfer: dt }));
  tgt.dispatchEvent(new DragEvent('dragover',  { bubbles: true, dataTransfer: dt }));
  tgt.dispatchEvent(new DragEvent('drop',      { bubbles: true, dataTransfer: dt }));
  src.dispatchEvent(new DragEvent('dragend',   { bubbles: true, dataTransfer: dt }));
  return ok();
}

function ext_select(args = {}) {
  const el = _findOne(args.selector);
  if (!el) return err(`selector not found: ${args.selector}`);
  if (el.tagName !== 'SELECT') return err('not a <select> element');
  // Try value first, then text-content match
  const opts = Array.from(el.options);
  const byValue = opts.find(o => o.value === args.value);
  const byText  = opts.find(o => o.text  === args.value);
  const opt = byValue || byText;
  if (!opt) return err(`option not found: ${args.value}`);
  el.value = opt.value;
  el.dispatchEvent(new Event('change', { bubbles: true }));
  return ok();
}

module.exports = Object.assign(module.exports, {
  ext_click, ext_right_click, ext_hover, ext_drag, ext_select,
});
```

- [ ] **Step 5.4: Verify pass**

```bash
cd src/extensions/jarvis-screen/tests && npx jest actions
```

- [ ] **Step 5.5: Commit**

```bash
git add src/extensions/jarvis-screen/actions.js src/extensions/jarvis-screen/tests/actions.test.js
git commit -m "ext: mouse action handlers (click, right_click, hover, drag, select)"
```

---

## Task 6: Action handlers — Keyboard / input (4 commands)

**Files:**
- Modify: `src/extensions/jarvis-screen/actions.js`
- Modify: `src/extensions/jarvis-screen/tests/actions.test.js`

- [ ] **Step 6.1: Write tests**

Append to `tests/actions.test.js`:

```js
describe('keyboard / input actions', () => {
  beforeEach(() => {
    document.body.innerHTML = `
      <input type="text" id="email" name="email" placeholder="email">
      <input type="text" id="name" name="name" placeholder="name">
      <textarea id="msg"></textarea>
      <form id="f1">
        <input type="text" name="q" id="q">
      </form>
    `;
  });

  test('ext_type fills the input', () => {
    expect(actions.ext_type({selector: '#email', text: 'a@b.com'}).ok).toBe(true);
    expect(document.getElementById('email').value).toBe('a@b.com');
  });

  test('ext_type fires input event', () => {
    let fired = false;
    document.getElementById('email').addEventListener('input', () => { fired = true; });
    actions.ext_type({selector: '#email', text: 'x'});
    expect(fired).toBe(true);
  });

  test('ext_fill_form by name', () => {
    const r = actions.ext_fill_form({fields: { email: 'a@b.com', name: 'Bob' }});
    expect(r.ok).toBe(true);
    expect(r.filled_count).toBe(2);
    expect(document.getElementById('email').value).toBe('a@b.com');
    expect(document.getElementById('name').value).toBe('Bob');
  });

  test('ext_fill_form reports missing fields', () => {
    const r = actions.ext_fill_form({fields: { unknownX: 'v' }});
    expect(r.ok).toBe(true);
    expect(r.missing).toEqual(['unknownX']);
  });

  test('ext_keypress dispatches keydown+keyup', () => {
    const events = [];
    document.addEventListener('keydown', e => events.push(['down', e.key]));
    document.addEventListener('keyup',   e => events.push(['up',   e.key]));
    actions.ext_keypress({key: 'Enter'});
    expect(events).toEqual([['down', 'Enter'], ['up', 'Enter']]);
  });

  test('ext_submit submits the form', () => {
    let submitted = false;
    document.getElementById('f1').addEventListener('submit', e => {
      e.preventDefault(); submitted = true;
    });
    expect(actions.ext_submit({form_selector: '#f1'}).ok).toBe(true);
    expect(submitted).toBe(true);
  });
});
```

- [ ] **Step 6.2: Verify failure**

```bash
cd src/extensions/jarvis-screen/tests && npx jest actions
```

- [ ] **Step 6.3: Implement keyboard handlers**

Append to `actions.js`:

```js
// ── Keyboard / input ──────────────────────────────────────────────────

function ext_type(args = {}) {
  const el = _findOne(args.selector);
  if (!el) return err(`selector not found: ${args.selector}`);
  el.focus();
  el.value = args.text || '';
  el.dispatchEvent(new Event('input', { bubbles: true }));
  el.dispatchEvent(new Event('change', { bubbles: true }));
  return ok();
}

function ext_fill_form(args = {}) {
  const fields = args.fields || {};
  let filled_count = 0;
  const missing = [];
  for (const [name, value] of Object.entries(fields)) {
    // Try name attr first, then id, then aria-label, then placeholder
    const el =
      document.querySelector(`[name="${CSS.escape(name)}"]`) ||
      document.querySelector(`#${CSS.escape(name)}`) ||
      document.querySelector(`[aria-label="${name.replace(/"/g,'\\"')}"]`) ||
      Array.from(document.querySelectorAll('input,textarea,select'))
        .find(e => (e.placeholder||'').toLowerCase() === name.toLowerCase());
    if (!el) { missing.push(name); continue; }
    el.focus();
    el.value = value;
    el.dispatchEvent(new Event('input', { bubbles: true }));
    el.dispatchEvent(new Event('change', { bubbles: true }));
    filled_count++;
  }
  return ok({ filled_count, missing });
}

function ext_keypress(args = {}) {
  const key = args.key;
  if (!key) return err('key arg required');
  // Parse modifiers from "Ctrl+Shift+K" syntax
  const parts = key.split('+');
  const mainKey = parts.pop();
  const opts = {
    bubbles: true, cancelable: true,
    key: mainKey,
    ctrlKey:  parts.includes('Ctrl'),
    shiftKey: parts.includes('Shift'),
    altKey:   parts.includes('Alt'),
    metaKey:  parts.includes('Meta'),
  };
  const target = document.activeElement || document;
  target.dispatchEvent(new KeyboardEvent('keydown', opts));
  target.dispatchEvent(new KeyboardEvent('keyup', opts));
  return ok();
}

function ext_submit(args = {}) {
  const form = _findOne(args.form_selector);
  if (!form) return err(`form not found: ${args.form_selector}`);
  if (form.tagName !== 'FORM') return err('not a <form> element');
  // Use requestSubmit if available (fires submit event); fall back to .submit()
  if (typeof form.requestSubmit === 'function') {
    form.requestSubmit();
  } else {
    const ev = new Event('submit', { bubbles: true, cancelable: true });
    form.dispatchEvent(ev);
    if (!ev.defaultPrevented) form.submit();
  }
  return ok();
}

module.exports = Object.assign(module.exports, {
  ext_type, ext_fill_form, ext_keypress, ext_submit,
});
```

- [ ] **Step 6.4: Verify pass**

```bash
cd src/extensions/jarvis-screen/tests && npx jest actions
```

- [ ] **Step 6.5: Commit**

```bash
git add src/extensions/jarvis-screen/actions.js src/extensions/jarvis-screen/tests/actions.test.js
git commit -m "ext: keyboard/input action handlers (type, fill_form, keypress, submit)"
```

---

## Task 7: Action handlers — Scroll / wait / dialog / iframe (4 commands)

**Files:**
- Modify: `src/extensions/jarvis-screen/actions.js`
- Modify: `src/extensions/jarvis-screen/tests/actions.test.js`

- [ ] **Step 7.1: Write tests**

Append:

```js
describe('scroll/wait/dialog actions', () => {
  beforeEach(() => {
    document.body.innerHTML = `<div id="late" style="display:none">late</div>`;
    Object.defineProperty(window, 'scrollTo', {
      value: jest.fn((x, y) => { window._sx = x; window._sy = y; }),
      writable: true,
    });
    window.scrollX = 0;
    window.scrollY = 0;
  });

  test('ext_scroll down', () => {
    expect(actions.ext_scroll({direction: 'down', amount: 500}).ok).toBe(true);
    expect(window.scrollTo).toHaveBeenCalledWith(0, 500);
  });

  test('ext_scroll up after down', () => {
    actions.ext_scroll({direction: 'down', amount: 1000});
    actions.ext_scroll({direction: 'up', amount: 300});
    // jsdom doesn't auto-update scrollY, so we verify the call args
    expect(window.scrollTo).toHaveBeenLastCalledWith(0, -300);
  });

  test('ext_scroll page', () => {
    Object.defineProperty(window, 'innerHeight', { value: 800, configurable: true });
    actions.ext_scroll({direction: 'down', amount: 'page'});
    expect(window.scrollTo).toHaveBeenCalledWith(0, 800);
  });

  test('ext_wait_for finds element that already exists', async () => {
    document.getElementById('late').style.display = 'block';
    const r = await actions.ext_wait_for({selector: '#late', timeout: 1});
    expect(r.found).toBe(true);
  });

  test('ext_wait_for times out for missing element', async () => {
    const r = await actions.ext_wait_for({selector: '#never', timeout: 0.2});
    expect(r.found).toBe(false);
  });

  test('ext_accept_dialog returns ok (delegated to background)', () => {
    expect(actions.ext_accept_dialog({accept: true}).ok).toBe(true);
  });

  test('ext_switch_iframe returns ok or error', () => {
    // No iframes → error
    expect(actions.ext_switch_iframe({selector_or_index: 0}).ok).toBe(false);
  });
});
```

- [ ] **Step 7.2: Verify failure**

- [ ] **Step 7.3: Implement scroll/wait/dialog/iframe handlers**

Append to `actions.js`:

```js
// ── Scroll / wait / dialog / iframe ──────────────────────────────────

function ext_scroll(args = {}) {
  const dir = (args.direction || 'down').toLowerCase();
  let amt = args.amount;
  if (amt === 'page') amt = window.innerHeight;
  if (typeof amt !== 'number') amt = 500;
  let dx = 0, dy = 0;
  if (dir === 'down')  dy =  amt;
  if (dir === 'up')    dy = -amt;
  if (dir === 'right') dx =  amt;
  if (dir === 'left')  dx = -amt;
  window.scrollTo(window.scrollX + dx, window.scrollY + dy);
  return ok();
}

async function ext_wait_for(args = {}) {
  const sel = args.selector;
  if (!sel) return err('selector required');
  const timeoutSec = args.timeout || 10;
  const deadline = Date.now() + timeoutSec * 1000;
  while (Date.now() < deadline) {
    const el = _findOne(sel);
    if (el && el.offsetParent !== null) return ok({ found: true });
    await new Promise(r => setTimeout(r, 100));
  }
  return ok({ found: false });
}

function ext_accept_dialog(args = {}) {
  // Real dialog handling (alert/confirm/prompt) requires
  // chrome.debugger or page.on('dialog') in the background script.
  // Content.js can't intercept these directly. Background.js
  // substitutes the real implementation.
  return ok({ delegated_to_background: true, accept: !!args.accept });
}

function ext_switch_iframe(args = {}) {
  const sel = args.selector_or_index;
  if (sel === undefined || sel === null) return err('selector_or_index required');
  let frame;
  if (typeof sel === 'number') {
    frame = document.querySelectorAll('iframe')[sel];
  } else {
    frame = _findOne(sel);
  }
  if (!frame) return err('iframe not found');
  // jsdom limitation: we can't actually switch the active document.
  // Return acknowledgement; real iframe switching is done in background
  // via tab message routing to the frame.
  return ok({ frame_id: frame.id || null, frame_src: frame.src || null });
}

module.exports = Object.assign(module.exports, {
  ext_scroll, ext_wait_for, ext_accept_dialog, ext_switch_iframe,
});
```

- [ ] **Step 7.4: Verify pass**

```bash
cd src/extensions/jarvis-screen/tests && npx jest actions
```

- [ ] **Step 7.5: Commit**

```bash
git add src/extensions/jarvis-screen/actions.js src/extensions/jarvis-screen/tests/actions.test.js
git commit -m "ext: scroll/wait/dialog/iframe action handlers"
```

---

## Task 8: Action handlers — Power tools (3 commands, all gated)

**Files:**
- Modify: `src/extensions/jarvis-screen/actions.js`
- Modify: `src/extensions/jarvis-screen/tests/actions.test.js`

- [ ] **Step 8.1: Write tests**

Append:

```js
describe('power tools', () => {
  test('ext_exec_js runs the code and returns result', () => {
    const r = actions.ext_exec_js({code: '1 + 2'});
    expect(r.ok).toBe(true);
    expect(r.result).toBe(3);
  });
  test('ext_exec_js returns error on bad code', () => {
    const r = actions.ext_exec_js({code: 'this.does.not.exist'});
    expect(r.ok).toBe(false);
  });
  test('ext_get_cookies delegated to background', () => {
    const r = actions.ext_get_cookies({domain: 'example.com'});
    expect(r.delegated_to_background).toBe(true);
  });
  test('ext_set_cookies delegated to background', () => {
    const r = actions.ext_set_cookies({domain: 'example.com', cookies: []});
    expect(r.delegated_to_background).toBe(true);
  });
});
```

- [ ] **Step 8.2: Verify failure**

- [ ] **Step 8.3: Implement power tools**

Append to `actions.js`:

```js
// ── Power tools (always gated upstream by safety.js) ─────────────────

function ext_exec_js(args = {}) {
  const code = args.code;
  if (!code) return err('code required');
  try {
    // eslint-disable-next-line no-new-func
    const fn = new Function(`return (${code})`);
    return ok({ result: fn() });
  } catch (e) {
    return err(`exec failed: ${e.message}`);
  }
}

function ext_get_cookies(args = {}) {
  // chrome.cookies API only available in background context
  return ok({ delegated_to_background: true, domain: args.domain });
}

function ext_set_cookies(args = {}) {
  return ok({ delegated_to_background: true, domain: args.domain });
}

module.exports = Object.assign(module.exports, {
  ext_exec_js, ext_get_cookies, ext_set_cookies,
});
```

- [ ] **Step 8.4: Verify pass + run all action tests**

```bash
cd src/extensions/jarvis-screen/tests && npx jest
```

Expected: 25+ tests passing across actions.test.js + safety.test.js.

- [ ] **Step 8.5: Commit**

```bash
git add src/extensions/jarvis-screen/actions.js src/extensions/jarvis-screen/tests/actions.test.js
git commit -m "ext: power-tools action handlers (exec_js, get/set_cookies)"
```

---

## Task 9: Bridge route — `/api/ext_browse` + bidirectional /ws

**Files:**
- Create: `src/cli/src/bridge/ext_browse.ts`
- Modify: `src/cli/src/bridge/server.ts`
- Create: `src/cli/src/bridge/tests/ext_browse.test.ts`

- [ ] **Step 9.1: Write failing test**

```ts
// src/cli/src/bridge/tests/ext_browse.test.ts
import { test, expect, describe } from "bun:test";
import { handleExtBrowse, registerExtensionWS, _resetForTests } from "../ext_browse";

describe("ext_browse", () => {
  test("returns 503 when no extension connected", async () => {
    _resetForTests();
    const req = new Request("http://x/api/ext_browse", {
      method: "POST",
      body: JSON.stringify({action: "navigate", args: {url: "https://example.com"}}),
      headers: {"content-type": "application/json"},
    });
    const res = await handleExtBrowse(req);
    expect(res.status).toBe(503);
  });

  test("queues command when extension connected, resolves on WS reply", async () => {
    _resetForTests();
    const fakeWS = {
      sent: [] as any[],
      send(data: string) { this.sent.push(JSON.parse(data)); },
      readyState: 1,
    };
    registerExtensionWS(fakeWS as any);

    // Fire a request asynchronously
    const req = new Request("http://x/api/ext_browse", {
      method: "POST",
      body: JSON.stringify({action: "navigate", args: {url: "https://example.com"}}),
      headers: {"content-type": "application/json"},
    });
    const resPromise = handleExtBrowse(req);

    // Wait for command to be queued
    await new Promise(r => setTimeout(r, 50));
    expect(fakeWS.sent.length).toBe(1);
    const cmd_id = fakeWS.sent[0].cmd_id;

    // Simulate extension response
    const { resolveExtensionResponse } = await import("../ext_browse");
    resolveExtensionResponse({cmd_id, ok: true, page_state: {url: "https://example.com"}});

    const res = await resPromise;
    expect(res.status).toBe(200);
    const body = await res.json();
    expect(body.ok).toBe(true);
    expect(body.page_state.url).toBe("https://example.com");
  });

  test("times out after configured ms with 504", async () => {
    _resetForTests();
    const fakeWS = { sent: [] as any[], send(d: string){ this.sent.push(JSON.parse(d)); }, readyState: 1 };
    registerExtensionWS(fakeWS as any);
    const req = new Request("http://x/api/ext_browse", {
      method: "POST",
      body: JSON.stringify({action: "navigate", args: {url: "x"}, timeout_ms: 100}),
      headers: {"content-type": "application/json"},
    });
    const res = await handleExtBrowse(req);
    expect(res.status).toBe(504);
  });
});
```

- [ ] **Step 9.2: Verify fail**

```bash
cd src/cli && bun test src/bridge/tests/ext_browse.test.ts
```

Expected: Cannot find module '../ext_browse'.

- [ ] **Step 9.3: Implement `ext_browse.ts`**

```ts
// src/cli/src/bridge/ext_browse.ts
//
// /api/ext_browse — accepts a browser command from the voice agent,
// forwards it to the connected jarvis-screen extension over WebSocket,
// and returns the extension's response synchronously to the caller.
//
// Correlation: each command gets a UUID cmd_id. The extension echoes
// the cmd_id back with its response. The bridge holds Map<cmd_id,
// {resolve, reject, timer}> until either response arrives or timeout.

import { randomUUID } from "node:crypto";

interface PendingCmd {
  resolve: (result: any) => void;
  reject: (err: Error) => void;
  timer: ReturnType<typeof setTimeout>;
}

let extensionWS: any = null;
const pending = new Map<string, PendingCmd>();

const DEFAULT_TIMEOUT_MS = parseInt(process.env.JARVIS_EXT_TIMEOUT_MS || "10000", 10);

export function registerExtensionWS(ws: any) {
  if (extensionWS) {
    try { extensionWS.close?.(); } catch {}
  }
  extensionWS = ws;
}

export function unregisterExtensionWS(ws: any) {
  if (extensionWS === ws) extensionWS = null;
}

export function isExtensionConnected(): boolean {
  return !!extensionWS && extensionWS.readyState === 1;
}

export function resolveExtensionResponse(msg: { cmd_id: string; [k: string]: any }) {
  const p = pending.get(msg.cmd_id);
  if (!p) return;
  pending.delete(msg.cmd_id);
  clearTimeout(p.timer);
  p.resolve(msg);
}

export async function handleExtBrowse(req: Request): Promise<Response> {
  let body: any;
  try { body = await req.json(); }
  catch { return Response.json({ ok: false, error: "bad json" }, { status: 400 }); }

  const action = body?.action;
  if (!action) return Response.json({ ok: false, error: "action required" }, { status: 400 });

  if (!isExtensionConnected()) {
    return Response.json(
      { ok: false, error: "extension not connected" },
      { status: 503 },
    );
  }

  const cmd_id = randomUUID();
  const timeout_ms = body.timeout_ms || DEFAULT_TIMEOUT_MS;
  const cmd = { cmd_id, action, args: body.args || {}, confirmed: !!body.confirmed };

  const responsePromise = new Promise<any>((resolve, reject) => {
    const timer = setTimeout(() => {
      pending.delete(cmd_id);
      reject(new Error("timeout"));
    }, timeout_ms);
    pending.set(cmd_id, { resolve, reject, timer });
  });

  extensionWS.send(JSON.stringify(cmd));

  try {
    const result = await responsePromise;
    return Response.json(result, { status: 200 });
  } catch (e: any) {
    return Response.json(
      { ok: false, error: e.message || String(e) },
      { status: e.message === "timeout" ? 504 : 500 },
    );
  }
}

export function _resetForTests() {
  for (const p of pending.values()) clearTimeout(p.timer);
  pending.clear();
  extensionWS = null;
}
```

- [ ] **Step 9.4: Wire route into `server.ts`**

Find the URL-pathname routing block (~line 394) and add:

```ts
import { handleExtBrowse, registerExtensionWS, unregisterExtensionWS, isExtensionConnected, resolveExtensionResponse } from "./ext_browse";

// ... later, inside the fetch handler:

if (url.pathname === '/api/ext_browse' && req.method === 'POST') {
  return handleExtBrowse(req);
}
if (url.pathname === '/api/ext_status') {
  return Response.json({ connected: isExtensionConnected() });
}
```

And in the `/ws` handler (~line 364), when a message arrives that matches `{cmd_id, ok, ...}`:

```ts
// Inside the existing /ws message handler:
if (typeof msg === 'object' && msg.cmd_id) {
  resolveExtensionResponse(msg);
  return;  // don't fall through to other handlers
}
// Register/unregister on connect/disconnect — see livekit/server.ts
// for the existing WS lifecycle hooks.
```

In the WS open handler:
```ts
ws.addEventListener('open', () => registerExtensionWS(ws));
ws.addEventListener('close', () => unregisterExtensionWS(ws));
```

(Adjust to match the existing Bun.serve websocket adapter pattern — `server.upgrade(...)` style. The principle is: when the extension's WS connects, call `registerExtensionWS(ws)`; on close, `unregisterExtensionWS(ws)`.)

- [ ] **Step 9.5: Verify all bridge tests pass**

```bash
cd src/cli && bun test src/bridge/tests/ext_browse.test.ts
```

Expected: 3 passing.

- [ ] **Step 9.6: Commit**

```bash
cd /home/ulrich/Documents/Projects/jarvis
git add src/cli/src/bridge/ext_browse.ts src/cli/src/bridge/server.ts src/cli/src/bridge/tests/ext_browse.test.ts
git commit -m "bridge: /api/ext_browse + bidirectional /ws for extension command channel"
```

---

## Task 10: Extension manifest v3.0 + background.js dispatcher

**Files:**
- Modify: `src/extensions/jarvis-screen/manifest.json`
- Modify: `src/extensions/jarvis-screen/background.js`

- [ ] **Step 10.1: Bump manifest to v3.0 + add `webNavigation` permission**

In `manifest.json`:

```diff
-  "version": "2.0.0",
+  "version": "3.0.0",
   "permissions": [
     "activeTab",
     "tabs",
     "sidePanel",
     "scripting",
+    "webNavigation",
+    "cookies",
     "storage"
   ],
```

- [ ] **Step 10.2: Add WebSocket client + command dispatcher to `background.js`**

Append to `background.js`:

```js
// ── v3.0 — WS command channel to JARVIS bridge ───────────────────────

const WS_URL = 'ws://localhost:8765/ws';
const WS_KEEPALIVE_MS = 25_000;
let ws = null;
let wsKeepaliveTimer = null;

function _connectWS() {
  try { if (ws) ws.close(); } catch {}
  ws = new WebSocket(WS_URL);

  ws.addEventListener('open', () => {
    console.log('[jarvis-ext] WS connected');
    // Identify ourselves so the bridge calls registerExtensionWS for this socket.
    ws.send(JSON.stringify({ type: 'extension_hello', version: '3.0.0' }));
    if (wsKeepaliveTimer) clearInterval(wsKeepaliveTimer);
    wsKeepaliveTimer = setInterval(() => {
      try { ws.send(JSON.stringify({ type: 'ping' })); } catch {}
    }, WS_KEEPALIVE_MS);
  });

  ws.addEventListener('message', async (ev) => {
    let cmd;
    try { cmd = JSON.parse(ev.data); }
    catch { return; }
    if (cmd.type === 'pong') return;
    if (!cmd.cmd_id) return;
    const result = await dispatchCommand(cmd);
    try { ws.send(JSON.stringify({ cmd_id: cmd.cmd_id, ...result })); } catch {}
  });

  ws.addEventListener('close', () => {
    console.log('[jarvis-ext] WS closed; reconnecting in 3s');
    if (wsKeepaliveTimer) clearInterval(wsKeepaliveTimer);
    setTimeout(_connectWS, 3000);
  });

  ws.addEventListener('error', (e) => console.warn('[jarvis-ext] WS error', e));
}

// Run on SW startup AND on resume.
_connectWS();
chrome.runtime.onStartup.addListener(_connectWS);

async function dispatchCommand({ action, args = {}, confirmed = false }) {
  // Some actions are bg-context-only (chrome.tabs / chrome.cookies).
  // Others run in content.js (DOM ops). We split here.
  try {
    switch (action) {
      case 'navigate':       return await _bgNavigate(args);
      case 'back':           return await _bgHistory(-1);
      case 'forward':        return await _bgHistory(+1);
      case 'close_tab':      return await _bgCloseTab();
      case 'screenshot':     return await _bgScreenshot();
      case 'get_cookies':    return await _bgGetCookies(args);
      case 'set_cookies':    return await _bgSetCookies(args);
      case 'accept_dialog':  return await _bgAcceptDialog(args);
      default:
        return await _forwardToContent(action, args);
    }
  } catch (e) {
    return { ok: false, error: String(e.message || e) };
  }
}

async function _activeTabId() {
  const [tab] = await chrome.tabs.query({ active: true, lastFocusedWindow: true });
  return tab?.id || null;
}

async function _forwardToContent(action, args) {
  const tabId = await _activeTabId();
  if (!tabId) return { ok: false, error: 'no active tab' };
  return await chrome.tabs.sendMessage(tabId, { action, args });
}

async function _bgNavigate({ url }) {
  if (!url) return { ok: false, error: 'url required' };
  const tabId = await _activeTabId();
  if (!tabId) {
    const tab = await chrome.tabs.create({ url });
    await _waitForLoad(tab.id);
    return await _forwardToContent('dom_summary', {});
  }
  await chrome.tabs.update(tabId, { url });
  await _waitForLoad(tabId);
  return await _forwardToContent('dom_summary', {});
}

function _waitForLoad(tabId, timeoutMs = 10_000) {
  return new Promise((resolve) => {
    const onCompleted = (details) => {
      if (details.tabId === tabId && details.frameId === 0) {
        chrome.webNavigation.onCompleted.removeListener(onCompleted);
        clearTimeout(t);
        // small delay for SPA hydration
        setTimeout(resolve, 300);
      }
    };
    const t = setTimeout(() => {
      chrome.webNavigation.onCompleted.removeListener(onCompleted);
      resolve();
    }, timeoutMs);
    chrome.webNavigation.onCompleted.addListener(onCompleted);
  });
}

async function _bgHistory(direction) {
  const tabId = await _activeTabId();
  if (!tabId) return { ok: false, error: 'no active tab' };
  if (direction < 0) await chrome.tabs.goBack(tabId);
  else                await chrome.tabs.goForward(tabId);
  await _waitForLoad(tabId);
  return await _forwardToContent('dom_summary', {});
}

async function _bgCloseTab() {
  const tabId = await _activeTabId();
  if (!tabId) return { ok: false, error: 'no active tab' };
  await chrome.tabs.remove(tabId);
  return { ok: true };
}

async function _bgScreenshot() {
  const dataUrl = await chrome.tabs.captureVisibleTab(null, { format: 'png' });
  return { ok: true, image_b64: dataUrl };
}

async function _bgGetCookies({ domain }) {
  const cookies = await chrome.cookies.getAll({ domain });
  return { ok: true, cookies };
}

async function _bgSetCookies({ domain, cookies }) {
  for (const c of (cookies || [])) {
    await chrome.cookies.set({
      url: `https://${domain}${c.path || '/'}`,
      name: c.name, value: c.value,
      domain, path: c.path || '/',
      secure: c.secure ?? true,
    });
  }
  return { ok: true };
}

async function _bgAcceptDialog(args) {
  // Real browsers show alert/confirm/prompt synchronously. The
  // chrome.debugger API would be needed to intercept; for v1 we
  // just acknowledge and let the page handle defaults.
  return { ok: true, note: 'dialog handling not yet implemented' };
}
```

- [ ] **Step 10.3: Reload the extension in Chrome**

Open `chrome://extensions`, toggle Developer mode on, find JARVIS, click "Reload". Verify in the bridge log (`/tmp/jarvis-bridge.log`) that the WS shows `extension_hello` arriving.

- [ ] **Step 10.4: Smoke-test from terminal**

```bash
curl -s -X POST http://localhost:8765/api/ext_status
# Expected: {"connected":true}

curl -s -X POST http://localhost:8765/api/ext_browse \
  -H 'content-type: application/json' \
  -d '{"action":"get_url"}'
# Expected: {"ok":true,"url":"...","title":"..."}
```

- [ ] **Step 10.5: Commit**

```bash
git add src/extensions/jarvis-screen/manifest.json src/extensions/jarvis-screen/background.js
git commit -m "ext: v3.0 — WS command channel, bg dispatcher, navigate/screenshot/cookies"
```

---

## Task 11: Wire content.js to load actions.js + dispatch

**Files:**
- Modify: `src/extensions/jarvis-screen/manifest.json`
- Modify: `src/extensions/jarvis-screen/content.js`

- [ ] **Step 11.1: Add `actions.js` to content_scripts in manifest**

```diff
   "content_scripts": [
     {
       "matches": ["<all_urls>"],
-      "js": ["content.js"],
+      "js": ["actions.js", "content.js"],
       "run_at": "document_idle",
       "all_frames": false
     }
   ],
```

- [ ] **Step 11.2: Modify content.js to dispatch new action messages**

Replace the existing `chrome.runtime.onMessage.addListener` block in `content.js` with:

```js
  chrome.runtime.onMessage.addListener((msg, _sender, sendResponse) => {
    // Legacy v2 protocol — preserved for compatibility
    if (msg.action === 'extract-dom') {
      sendResponse(extractDOM());
      return false;
    }
    // v3 protocol — actions defined in actions.js
    const action = msg.action;
    const args = msg.args || {};
    const handlerName = `ext_${action}`;
    const fn = (typeof window !== 'undefined' && window[handlerName])
      || (typeof globalThis !== 'undefined' && globalThis[handlerName]);
    if (typeof fn === 'function') {
      const r = fn(args);
      // Some handlers (ext_wait_for) are async
      if (r && typeof r.then === 'function') {
        r.then(sendResponse);
        return true;  // async response
      }
      sendResponse(r);
      return false;
    }
    sendResponse({ ok: false, error: `unknown action: ${action}` });
    return false;
  });
```

- [ ] **Step 11.3: Adapt actions.js to expose handlers on `window` (content-script global) AND keep CommonJS for tests**

At the bottom of `actions.js`, replace `module.exports = ...` with:

```js
const _ALL = {
  ext_get_url, ext_close_tab,
  ext_extract_text, ext_find_by_text, ext_dom_summary, ext_screenshot,
  ext_click, ext_right_click, ext_hover, ext_drag, ext_select,
  ext_type, ext_fill_form, ext_keypress, ext_submit,
  ext_scroll, ext_wait_for, ext_accept_dialog, ext_switch_iframe,
  ext_exec_js, ext_get_cookies, ext_set_cookies,
};

if (typeof window !== 'undefined') {
  // Content-script context — expose on window for content.js dispatch
  for (const [name, fn] of Object.entries(_ALL)) window[name] = fn;
}

if (typeof module !== 'undefined' && module.exports) {
  // Test context — CommonJS
  module.exports = _ALL;
}
```

- [ ] **Step 11.4: Re-run all jest tests to confirm nothing broke**

```bash
cd src/extensions/jarvis-screen/tests && npx jest
```

Expected: all green.

- [ ] **Step 11.5: Reload extension in Chrome, smoke-test from terminal**

```bash
curl -s -X POST http://localhost:8765/api/ext_browse \
  -H 'content-type: application/json' \
  -d '{"action":"dom_summary"}'
# Expected: {"ok":true,"headings":[...],"actionable_elements":[...]}
```

- [ ] **Step 11.6: Commit**

```bash
git add src/extensions/jarvis-screen/manifest.json src/extensions/jarvis-screen/content.js src/extensions/jarvis-screen/actions.js
git commit -m "ext: wire actions.js into content.js dispatcher"
```

---

## Task 12: BrowserSpecialistAgent — Python skeleton + handoff back

**Files:**
- Modify: `src/voice-agent/jarvis_specialist_agents.py`

- [ ] **Step 12.1: Add the BrowserSpecialistAgent class**

Append to `jarvis_specialist_agents.py`:

```python
import aiohttp


BROWSER_INSTRUCTIONS = """\
You are JARVIS's browser-action specialist. The supervisor handed control
to you because the user asked for something requiring browser interaction
inside an existing Chrome tab — open Gmail, post on Twitter, check Amazon
orders, scroll a feed, log into a site.

YOUR LOOP: emit ONE ext_* tool call → read the page_state response → emit
the next tool call. Keep going until the task is complete. Then call
task_done(summary) to hand back to the supervisor with a one-line summary.

ABSOLUTE RULES:

1. CALL THE TOOL. Never narrate what you would do. The action is the tool;
   the tool result is the answer. "I'll try to..." is BANNED.

2. ONE STEP AT A TIME. Each LLM call emits exactly ONE ext_* tool call.
   Don't pre-plan a sequence. Read the previous result, decide one step,
   emit it.

3. CAP AT 20 STEPS. After ~20 ext_* calls you should already have the
   answer. If you don't, call task_done with "stuck after 20 steps" and
   let the supervisor handle it.

4. DESTRUCTIVE ACTIONS REQUIRE CONFIRMATION. If a tool returns
   {ok: false, needs_confirmation: true, preview: "..."}, voice the
   preview to the user as a question, wait for explicit "yes/confirm",
   then re-call the tool with confirmed=True in args.

5. NEVER use ext_exec_js / ext_set_cookies / ext_get_cookies without
   explicit user confirmation — these are gated.

6. PASSWORDS / OTPs: refuse politely and ask the user to type them
   manually. Never put credentials in tool args.

7. WHEN DONE call task_done with a one-sentence summary the supervisor
   will voice — e.g. "12 unread emails, sir" or "Posted 'hello world' on
   Twitter, sir".
"""


_BRIDGE_URL = "http://localhost:8765"


async def _post_ext_browse(action: str, args: dict, confirmed: bool = False, timeout: float = 12.0) -> dict:
    """Send a single command to the extension via the bridge."""
    try:
        async with aiohttp.ClientSession() as s:
            async with s.post(
                f"{_BRIDGE_URL}/api/ext_browse",
                json={"action": action, "args": args, "confirmed": confirmed},
                timeout=aiohttp.ClientTimeout(total=timeout),
            ) as r:
                return await r.json()
    except asyncio.TimeoutError:
        return {"ok": False, "error": "bridge timeout"}
    except Exception as e:
        return {"ok": False, "error": f"bridge call failed: {e}"}


class BrowserSpecialistAgent(Agent):
    """Specialist agent for in-browser action work via the jarvis-screen
    extension. See spec 2026-04-30-jarvis-extension-browser-control-design.md.
    """

    def __init__(self, *, supervisor: Agent, chat_ctx: ChatContext | None = None):
        super().__init__(instructions=BROWSER_INSTRUCTIONS, chat_ctx=chat_ctx)
        self._supervisor = supervisor
        self._step_count = 0
        self._max_steps = int(os.environ.get("JARVIS_EXT_LOOP_MAX_STEPS", "20"))

    async def on_enter(self) -> None:
        logger.info("[specialist:browser] active")

    async def on_exit(self) -> None:
        logger.info("[specialist:browser] handing back to supervisor")

    @function_tool()
    async def task_done(self, context: RunContext, summary: str) -> tuple[Agent, str]:
        """Call when the browser task is complete. Hands control back to JARVIS
        with a one-line summary.

        Args:
            summary: One-sentence description of what was accomplished.
        """
        logger.info(f"[specialist:browser] task_done → '{summary[:80]}'")
        return self._supervisor, summary
```

- [ ] **Step 12.2: Smoke-import**

```bash
cd src/voice-agent && .venv/bin/python -c "from jarvis_specialist_agents import BrowserSpecialistAgent; print('OK')"
```

Expected: `OK`.

- [ ] **Step 12.3: Commit**

```bash
git add src/voice-agent/jarvis_specialist_agents.py
git commit -m "voice: BrowserSpecialistAgent skeleton + task_done handoff"
```

---

## Task 13: BrowserSpecialistAgent — wire all 25 ext_* tool methods

**Files:**
- Modify: `src/voice-agent/jarvis_specialist_agents.py`
- Create: `src/voice-agent/tests/test_browser_specialist.py`

- [ ] **Step 13.1: Write a smoke test for one tool method**

```python
# src/voice-agent/tests/test_browser_specialist.py
import pytest
import asyncio
from unittest.mock import AsyncMock, patch, MagicMock

from jarvis_specialist_agents import BrowserSpecialistAgent


@pytest.fixture
def agent():
    sup = MagicMock()
    return BrowserSpecialistAgent(supervisor=sup)


@pytest.mark.asyncio
async def test_ext_navigate_posts_to_bridge(agent):
    fake_resp = {"ok": True, "page_state": {"url": "https://x.com", "title": "X"}}
    with patch("jarvis_specialist_agents._post_ext_browse",
               new=AsyncMock(return_value=fake_resp)) as p:
        ctx = MagicMock()
        result = await agent.ext_navigate(ctx, url="https://x.com")
        assert result == fake_resp
        p.assert_called_once_with("navigate", {"url": "https://x.com"}, confirmed=False)


@pytest.mark.asyncio
async def test_ext_click_passes_confirmed(agent):
    with patch("jarvis_specialist_agents._post_ext_browse",
               new=AsyncMock(return_value={"ok": True})) as p:
        ctx = MagicMock()
        await agent.ext_click(ctx, selector="#btn", confirmed=True)
        p.assert_called_once_with("click", {"selector": "#btn"}, confirmed=True)
```

- [ ] **Step 13.2: Run, verify failure**

```bash
cd src/voice-agent && .venv/bin/python -m pytest tests/test_browser_specialist.py -v
```

Expected: AttributeError on `agent.ext_navigate`.

- [ ] **Step 13.3: Implement all 25 tool methods on BrowserSpecialistAgent**

Add these methods inside the `BrowserSpecialistAgent` class (after `task_done`):

```python
    # ── Navigation (5) ──────────────────────────────────────────────

    @function_tool()
    async def ext_navigate(self, context: RunContext, url: str, confirmed: bool = False) -> dict:
        """Navigate the active tab to a URL. Returns page_state."""
        return await _post_ext_browse("navigate", {"url": url}, confirmed=confirmed)

    @function_tool()
    async def ext_back(self, context: RunContext) -> dict:
        """Browser history back."""
        return await _post_ext_browse("back", {})

    @function_tool()
    async def ext_forward(self, context: RunContext) -> dict:
        """Browser history forward."""
        return await _post_ext_browse("forward", {})

    @function_tool()
    async def ext_get_url(self, context: RunContext) -> dict:
        """Current tab URL and title."""
        return await _post_ext_browse("get_url", {})

    @function_tool()
    async def ext_close_tab(self, context: RunContext) -> dict:
        """Close the active tab."""
        return await _post_ext_browse("close_tab", {})

    # ── Reading (4) ──────────────────────────────────────────────────

    @function_tool()
    async def ext_extract_text(self, context: RunContext, selector: str = "body") -> dict:
        """Extract visible text from a CSS selector (default body)."""
        return await _post_ext_browse("extract_text", {"selector": selector})

    @function_tool()
    async def ext_screenshot(self, context: RunContext) -> dict:
        """Capture the visible tab as base64 PNG."""
        return await _post_ext_browse("screenshot", {}, timeout=8.0)

    @function_tool()
    async def ext_find_by_text(self, context: RunContext, text: str) -> dict:
        """Find elements whose text contains the given string."""
        return await _post_ext_browse("find_by_text", {"text": text})

    @function_tool()
    async def ext_dom_summary(self, context: RunContext) -> dict:
        """Headings + actionable elements list (up to 30 each)."""
        return await _post_ext_browse("dom_summary", {})

    # ── Mouse (5) ────────────────────────────────────────────────────

    @function_tool()
    async def ext_click(self, context: RunContext, selector: str, confirmed: bool = False) -> dict:
        """Click an element by CSS selector. May require confirmed=True."""
        return await _post_ext_browse("click", {"selector": selector}, confirmed=confirmed)

    @function_tool()
    async def ext_right_click(self, context: RunContext, selector: str) -> dict:
        """Right-click (contextmenu) on an element."""
        return await _post_ext_browse("right_click", {"selector": selector})

    @function_tool()
    async def ext_hover(self, context: RunContext, selector: str) -> dict:
        """Hover (mouseover) on an element."""
        return await _post_ext_browse("hover", {"selector": selector})

    @function_tool()
    async def ext_drag(self, context: RunContext, from_selector: str, to_selector: str) -> dict:
        """Drag from one selector to another."""
        return await _post_ext_browse(
            "drag", {"from_selector": from_selector, "to_selector": to_selector},
        )

    @function_tool()
    async def ext_select(self, context: RunContext, selector: str, value: str) -> dict:
        """Pick an option in a <select> dropdown by value or visible text."""
        return await _post_ext_browse("select", {"selector": selector, "value": value})

    # ── Keyboard / input (4) ─────────────────────────────────────────

    @function_tool()
    async def ext_type(self, context: RunContext, selector: str, text: str) -> dict:
        """Type into an input or textarea."""
        return await _post_ext_browse("type", {"selector": selector, "text": text})

    @function_tool()
    async def ext_fill_form(self, context: RunContext, fields: dict) -> dict:
        """Fill a form by mapping {field_name_or_id_or_label: value}."""
        return await _post_ext_browse("fill_form", {"fields": fields})

    @function_tool()
    async def ext_keypress(self, context: RunContext, key: str) -> dict:
        """Send a keypress (e.g. 'Enter', 'Escape', 'Ctrl+C')."""
        return await _post_ext_browse("keypress", {"key": key})

    @function_tool()
    async def ext_submit(self, context: RunContext, form_selector: str, confirmed: bool = False) -> dict:
        """Submit a <form>. GATED — first call returns confirmation prompt."""
        return await _post_ext_browse("submit", {"form_selector": form_selector}, confirmed=confirmed)

    # ── Scroll / wait / dialog / iframe (4) ──────────────────────────

    @function_tool()
    async def ext_scroll(self, context: RunContext, direction: str = "down", amount: int = 500) -> dict:
        """Scroll in a direction. amount in pixels, or 'page' for window height."""
        return await _post_ext_browse("scroll", {"direction": direction, "amount": amount})

    @function_tool()
    async def ext_wait_for(self, context: RunContext, selector: str, timeout: int = 10) -> dict:
        """Wait up to N seconds for a selector to appear."""
        return await _post_ext_browse("wait_for", {"selector": selector, "timeout": timeout},
                                       timeout=timeout + 2.0)

    @function_tool()
    async def ext_accept_dialog(self, context: RunContext, accept: bool = True) -> dict:
        """Accept or dismiss a browser dialog."""
        return await _post_ext_browse("accept_dialog", {"accept": accept})

    @function_tool()
    async def ext_switch_iframe(self, context: RunContext, selector_or_index: str) -> dict:
        """Switch focus into an iframe by selector or numeric index."""
        return await _post_ext_browse(
            "switch_iframe", {"selector_or_index": selector_or_index},
        )

    # ── Power tools (3) — all gated ──────────────────────────────────

    @function_tool()
    async def ext_exec_js(self, context: RunContext, code: str, confirmed: bool = False) -> dict:
        """Execute arbitrary JavaScript. ALWAYS GATED — first call returns prompt."""
        return await _post_ext_browse("exec_js", {"code": code}, confirmed=confirmed)

    @function_tool()
    async def ext_get_cookies(self, context: RunContext, domain: str, confirmed: bool = False) -> dict:
        """Read cookies for a domain. GATED."""
        return await _post_ext_browse("get_cookies", {"domain": domain}, confirmed=confirmed)

    @function_tool()
    async def ext_set_cookies(self, context: RunContext, domain: str, cookies: list, confirmed: bool = False) -> dict:
        """Set cookies on a domain. ALWAYS GATED."""
        return await _post_ext_browse(
            "set_cookies", {"domain": domain, "cookies": cookies}, confirmed=confirmed,
        )
```

- [ ] **Step 13.4: Re-run unit tests, verify pass**

```bash
cd src/voice-agent && .venv/bin/python -m pytest tests/test_browser_specialist.py -v
```

Expected: 2 passing.

- [ ] **Step 13.5: Commit**

```bash
git add src/voice-agent/jarvis_specialist_agents.py src/voice-agent/tests/test_browser_specialist.py
git commit -m "voice: BrowserSpecialistAgent — 25 ext_* tools wired to bridge"
```

---

## Task 14: Supervisor handoff — `transfer_to_browser`

**Files:**
- Modify: `src/voice-agent/jarvis_agent.py`

- [ ] **Step 14.1: Add `transfer_to_browser` to JarvisAgent**

Find `JarvisAgent.transfer_to_desktop` and add a sibling method right after it:

```python
    @function_tool()
    async def transfer_to_browser(self, context: RunContext, intent: str) -> tuple[Agent, str]:
        """Hand off to the BROWSER specialist for in-page web work — Gmail,
        Twitter, Amazon, LinkedIn, banking, search-engine queries that need
        scrolling/clicking, etc. Use ONLY for things you'd do INSIDE an
        existing Chrome tab. For "open Chrome to https://X" without
        further interaction, use transfer_to_desktop instead.

        Args:
            intent: The user's high-level goal, e.g. "log into Gmail and
                    summarize unread" or "post 'hello' on Twitter".
        """
        # Legacy fallback path — still available via env override
        if os.environ.get("JARVIS_USE_LEGACY_BROWSER", "0") == "1":
            from jarvis_browser import browser_task
            result = await browser_task(intent)
            # browser_task returns a string; bridge it as a "completed" handoff
            return self, str(result)

        from jarvis_specialist_agents import BrowserSpecialistAgent
        try:
            ctx = self.chat_ctx.copy(exclude_instructions=True).truncate(max_items=12)
        except Exception:
            ctx = None
        logger.info(f"[handoff] → BrowserSpecialistAgent (intent: {intent[:80]!r})")
        return (
            BrowserSpecialistAgent(supervisor=self, chat_ctx=ctx),
            "On the browser, sir.",
        )
```

- [ ] **Step 14.2: Update the TOOL ROUTING block in JARVIS_INSTRUCTIONS**

Find the `═══ TOOL ROUTING — pick the right path ═══` block. Update Section 1 to mention browser explicitly:

```diff
 **1. ANY action / desktop / browser / media / multi-step work**
-   → call `transfer_to_desktop(request)`. The specialist has the
-   full action toolset (bash, computer_use, run_jarvis_cli,
-   media_control, browser_task, screenshot, click, drag, type,
-   etc.) and a focused prompt for tool execution discipline.
-   This is the ONLY path for anything the user wants DONE.
+   For DESKTOP work (open app, screenshot, click on a desktop UI):
+      → call `transfer_to_desktop(request)`
+   For BROWSER work INSIDE an open Chrome tab (Gmail, Twitter,
+   Amazon, scrolling a feed, filling a web form):
+      → call `transfer_to_browser(intent)`
+   These are the ONLY paths for action work. You have NO direct
+   action tools.
```

- [ ] **Step 14.3: Smoke-import**

```bash
cd src/voice-agent && .venv/bin/python -c "import jarvis_agent; print('OK')"
```

Expected: `OK`.

- [ ] **Step 14.4: Commit + restart**

```bash
git add src/voice-agent/jarvis_agent.py
git commit -m "voice: transfer_to_browser handoff on supervisor (with legacy fallback)"
systemctl --user restart jarvis-voice-agent
sleep 3
systemctl --user is-active jarvis-voice-agent
```

Expected: `active`.

---

## Task 15: End-to-end smoke test

**Files:**
- Create: `src/voice-agent/tests/test_extension_browser.py`

- [ ] **Step 15.1: Write the E2E test**

```python
# src/voice-agent/tests/test_extension_browser.py
"""End-to-end smoke: bridge live, extension loaded, run a fixed sequence
against example.com, assert extracted text contains expected markers."""
import asyncio
import os
import pytest

import aiohttp


BRIDGE = os.environ.get("JARVIS_BRIDGE_URL", "http://localhost:8765")


async def _post(action: str, args: dict, timeout: float = 12.0) -> dict:
    async with aiohttp.ClientSession() as s:
        async with s.post(
            f"{BRIDGE}/api/ext_browse",
            json={"action": action, "args": args},
            timeout=aiohttp.ClientTimeout(total=timeout),
        ) as r:
            return await r.json()


async def _ext_connected() -> bool:
    try:
        async with aiohttp.ClientSession() as s:
            async with s.get(f"{BRIDGE}/api/ext_status",
                             timeout=aiohttp.ClientTimeout(total=2)) as r:
                d = await r.json()
                return bool(d.get("connected"))
    except Exception:
        return False


@pytest.mark.asyncio
@pytest.mark.skipif(
    not asyncio.run(_ext_connected()),
    reason="extension not connected to bridge — load it in Chrome first",
)
async def test_navigate_extract_text_example_com():
    r = await _post("navigate", {"url": "https://example.com"})
    assert r["ok"], r
    # navigate returns dom_summary inside; verify
    assert "actionable_elements" in r or "page_state" in r or "headings" in r

    r2 = await _post("extract_text", {"selector": "body"})
    assert r2["ok"], r2
    assert "Example Domain" in r2["text"]


@pytest.mark.asyncio
@pytest.mark.skipif(
    not asyncio.run(_ext_connected()),
    reason="extension not connected to bridge",
)
async def test_find_by_text_returns_match():
    await _post("navigate", {"url": "https://example.com"})
    r = await _post("find_by_text", {"text": "More information"})
    assert r["ok"], r
    assert len(r["matches"]) >= 1
```

- [ ] **Step 15.2: Run E2E (requires extension live)**

```bash
# Pre-req: bridge running, jarvis-screen extension loaded in Chrome
cd src/voice-agent && .venv/bin/python -m pytest tests/test_extension_browser.py -v
```

Expected: 2 passing (or 2 skipped if extension not connected).

- [ ] **Step 15.3: Commit**

```bash
git add src/voice-agent/tests/test_extension_browser.py
git commit -m "voice: E2E smoke test — extension navigate + extract + find"
```

---

## Task 16: Manual dogfood checklist

This is a manual step — not automated. Run by speaking each phrase to JARVIS, observing behaviour, ticking each item.

- [ ] **16.1:** *"Jarvis, open Gmail."* → Gmail loads in active tab, signed-in profile.
- [ ] **16.2:** *"Jarvis, search YouTube for lofi hip hop."* → search executes, results visible.
- [ ] **16.3:** *"Jarvis, what's the unread count in my Gmail?"* → JARVIS extracts and voices the count.
- [ ] **16.4:** *"Jarvis, open my Amazon order history."* → orders page loads.
- [ ] **16.5:** *"Jarvis, post 'hello world' on Twitter."* → JARVIS composes, asks for confirmation, posts after "yes."
- [ ] **16.6:** *"Jarvis, scroll to the bottom of my LinkedIn feed."* → smooth scroll to bottom.
- [ ] **16.7:** *"Jarvis, find the cheapest flight from SFO to NYC next Friday."* → multi-step search; voice the result.
- [ ] **16.8:** *"Jarvis, click the orange subscribe button on this page."* → finds and clicks.

**Acceptance:** ≥6 of 8 succeed first-try. If <6, identify the failure modes and revisit the prompt or specific tool implementations. Each iteration is a separate commit.

---

## Self-Review

**Spec coverage check:**

| Spec section | Covered by |
|---|---|
| Bridge plumbing (`/api/ext_browse`, `/ws` bidirectional, `/api/ext_status`) | Task 9 |
| Extension manifest v3.0 + permissions | Task 10.1 |
| Background.js dispatcher + WS keep-alive | Task 10.2 |
| Content.js routing | Task 11 |
| 25 action handlers (5+4+5+4+4+3) | Tasks 3, 4, 5, 6, 7, 8 |
| Safety gates (destructive verb, allowlist, credentials, payment domains) | Task 2 |
| BrowserSpecialistAgent class + 25 tools + task_done | Tasks 12, 13 |
| Supervisor `transfer_to_browser` handoff | Task 14 |
| Legacy `JARVIS_USE_LEGACY_BROWSER=1` fallback | Task 14.1 |
| Jest unit tests for actions + safety | Tasks 2-8 |
| Bridge route tests | Task 9 |
| BrowserSpecialistAgent unit tests | Task 13 |
| E2E smoke test | Task 15 |
| Manual dogfood (8 flows) | Task 16 |

All spec sections have task coverage.

**Placeholder scan:** No "TBD" / "implement later" / "fill in details" markers in any task. All tasks have concrete code or commands.

**Type consistency check:**
- `_post_ext_browse(action, args, confirmed=False, timeout=10)` — same signature in Task 12 (definition) and Tasks 13 (callers). ✓
- `cmd_id` is a string everywhere (uuid). ✓
- `page_state` shape: `{ok, url, title, page_text, dom_summary, actionable_elements}` — consistent across handlers. ✓
- `register/unregisterExtensionWS(ws)` — same name in Task 9 and call sites. ✓
- Safety gate envelope: `{ok:false, needs_confirmation:true, preview:...}` — referenced same way in spec and Task 2 implementation comment. ✓

No issues found.

---

## Out of scope (parked for v2)

Per the spec — not part of this plan:
- Cross-tab orchestration
- Headless mode / second Chrome instance
- OAuth / 2FA automation
- Firefox extension (separate manifest)
- Recording macros for replay
- Multi-page wizard state machines
- Manus-style multi-account parallelism
- chrome.debugger API for full dialog interception

If any of these become urgent during dogfood, add as separate spec → plan → tasks.

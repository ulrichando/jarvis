// actions.js — content-side DOM action handlers for jarvis-screen v3.0.
//
// Each ext_* function runs IN THE PAGE (content-script world) and returns a
// plain { ok, ... } result. Tab/history/screenshot/cookie actions do NOT live
// here — background.js handles those via chrome.* APIs. content.js routes an
// incoming { action, args } to HANDLERS[action].
//
// Dual-loadable: content script (globalThis.JARVIS_ACTIONS) + Node/Jest
// (module.exports) so every handler is unit-tested under jsdom without Chrome.

(function (root) {
  const TEXT_CAP = 4096;
  const LIST_CAP = 40;

  const ok = (extra) => ({ ok: true, ...(extra || {}) });
  const err = (message) => ({ ok: false, error: String(message) });

  // Resolve a selector to one element or throw a friendly error.
  function _one(selector) {
    if (!selector) throw new Error("selector required");
    let el;
    try {
      el = document.querySelector(selector);
    } catch {
      throw new Error(`invalid selector: ${selector}`);
    }
    if (!el) throw new Error(`no element matches: ${selector}`);
    return el;
  }

  function _label(el) {
    const t = (el.getAttribute("aria-label") || el.textContent || el.value || el.getAttribute("name") || el.id || "").trim();
    return t.replace(/\s+/g, " ").slice(0, 80);
  }

  // Authoritative destructive-click gate: runs on the RESOLVED element, so it
  // catches a "Delete account" button whose selector never mentions delete
  // (the background's selector pre-check can't). Returns a needs_confirmation
  // refusal when the element looks destructive and the user hasn't confirmed,
  // else null. Safety module may be absent in a bare unit test — then skip.
  function _confirmGate(label, ctx) {
    if (ctx && ctx.confirmed) return null;
    const S = (typeof globalThis !== "undefined" && globalThis.JARVIS_SAFETY) || null;
    if (S && typeof S.isDestructiveText === "function" && S.isDestructiveText(label)) {
      return { ok: false, needs_confirmation: true, error: `"${label}" looks destructive — needs confirmation` };
    }
    return null;
  }

  // ── Reading ────────────────────────────────────────────────────────
  function ext_get_url() {
    return ok({ url: location.href, title: document.title });
  }

  function ext_extract_text(args = {}) {
    try {
      const el = args.selector ? _one(args.selector) : document.body;
      const text = (el.innerText || el.textContent || "").replace(/\s+\n/g, "\n").trim().slice(0, TEXT_CAP);
      return ok({ text, truncated: text.length >= TEXT_CAP });
    } catch (e) {
      return err(e.message);
    }
  }

  function ext_find_by_text(args = {}) {
    const needle = (args.text || "").trim().toLowerCase();
    if (!needle) return err("text required");
    const els = [...document.querySelectorAll("a,button,[role=button],input,summary,label,h1,h2,h3,li,td,span")];
    const hits = els.filter((el) => (el.innerText || el.textContent || el.value || "").trim().toLowerCase().includes(needle));
    return ok({
      found: hits.length > 0,
      count: hits.length,
      matches: hits.slice(0, LIST_CAP).map((el) => ({ tag: el.tagName.toLowerCase(), text: _label(el) })),
    });
  }

  function ext_dom_summary() {
    const pick = (sel, n) => [...document.querySelectorAll(sel)].slice(0, n).map(_label).filter(Boolean);
    return ok({
      url: location.href,
      title: document.title,
      headings: pick("h1,h2,h3", LIST_CAP),
      links: pick("a[href]", LIST_CAP),
      buttons: pick("button,[role=button],input[type=submit],input[type=button]", LIST_CAP),
      inputs: [...document.querySelectorAll("input,textarea,select")]
        .slice(0, LIST_CAP)
        .map((el) => ({ type: (el.getAttribute("type") || el.tagName.toLowerCase()), name: el.getAttribute("name") || el.id || "", label: _label(el) })),
    });
  }

  // Recent console messages captured by console_hook.js (MAIN world) → content.js.
  function ext_read_console(args = {}) {
    const buf = (typeof globalThis !== "undefined" && globalThis.__jarvisConsole) || [];
    const rows = (args.level ? buf.filter((m) => m.level === args.level) : buf).slice(-50);
    return ok({ messages: rows, count: rows.length });
  }

  // Recent network requests via the Resource Timing API (no permissions needed).
  function ext_read_network() {
    try {
      const entries = (typeof performance !== "undefined" && performance.getEntriesByType)
        ? performance.getEntriesByType("resource") : [];
      const rows = entries.slice(-60).map((e) => ({
        url: e.name, type: e.initiatorType, ms: Math.round(e.duration || 0), size: e.transferSize || 0,
      }));
      return ok({ requests: rows, count: rows.length });
    } catch (e) { return err(e.message); }
  }

  // ── Mouse ──────────────────────────────────────────────────────────
  function _fire(el, type, init) {
    el.dispatchEvent(new MouseEvent(type, { bubbles: true, cancelable: true, view: window, ...(init || {}) }));
  }

  function ext_click(args = {}, ctx = {}) {
    try {
      const el = _one(args.selector);
      const label = _label(el);
      const blocked = _confirmGate(label, ctx);
      if (blocked) return blocked;
      try { el.scrollIntoView({ block: "center" }); } catch {} // best-effort; never fail the click on it
      _fire(el, "mousedown"); _fire(el, "mouseup");
      if (typeof el.click === "function") el.click(); else _fire(el, "click");
      return ok({ clicked: label });
    } catch (e) { return err(e.message); }
  }

  function ext_right_click(args = {}, ctx = {}) {
    try {
      const el = _one(args.selector);
      const blocked = _confirmGate(_label(el), ctx);
      if (blocked) return blocked;
      el.dispatchEvent(new MouseEvent("contextmenu", { bubbles: true, cancelable: true }));
      return ok();
    } catch (e) { return err(e.message); }
  }

  // Click the first interactive element whose visible text matches — the
  // reliable way to "click the Buy now button" without guessing a CSS selector.
  function ext_click_text(args = {}, ctx = {}) {
    try {
      const needle = (args.text || "").trim().toLowerCase();
      if (!needle) return err("text required");
      const els = [...document.querySelectorAll("a,button,[role=button],input[type=submit],input[type=button],summary,label,[onclick]")];
      const el = els.find((e) => (e.innerText || e.textContent || e.value || e.getAttribute("aria-label") || "").trim().toLowerCase().includes(needle));
      if (!el) return err(`no clickable element with text: ${args.text}`);
      const label = _label(el);
      const blocked = _confirmGate(label, ctx);
      if (blocked) return blocked;
      try { el.scrollIntoView({ block: "center" }); } catch {}
      _fire(el, "mousedown"); _fire(el, "mouseup");
      if (typeof el.click === "function") el.click(); else _fire(el, "click");
      return ok({ clicked: label });
    } catch (e) { return err(e.message); }
  }

  function ext_hover(args = {}) {
    try { const el = _one(args.selector); _fire(el, "mouseover"); _fire(el, "mouseenter"); return ok(); }
    catch (e) { return err(e.message); }
  }

  function ext_drag(args = {}) {
    try {
      const from = _one(args.from_selector), to = _one(args.to_selector);
      const dt = typeof DataTransfer !== "undefined" ? new DataTransfer() : undefined;
      const mk = (type, node) => node.dispatchEvent(new (typeof DragEvent !== "undefined" ? DragEvent : Event)(type, { bubbles: true, cancelable: true, dataTransfer: dt }));
      mk("dragstart", from); mk("dragover", to); mk("drop", to); mk("dragend", from);
      return ok();
    } catch (e) { return err(e.message); }
  }

  function ext_select(args = {}) {
    try {
      const el = _one(args.selector);
      el.value = args.value;
      el.dispatchEvent(new Event("change", { bubbles: true }));
      return ok({ value: el.value });
    } catch (e) { return err(e.message); }
  }

  // ── Keyboard / input ───────────────────────────────────────────────
  function _setValue(el, text) {
    const proto = el.tagName === "TEXTAREA" ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype;
    const setter = Object.getOwnPropertyDescriptor(proto, "value")?.set;
    if (setter) setter.call(el, text); else el.value = text;
    el.dispatchEvent(new Event("input", { bubbles: true }));
    el.dispatchEvent(new Event("change", { bubbles: true }));
  }

  function ext_type(args = {}) {
    try {
      const el = _one(args.selector);
      el.focus();
      _setValue(el, args.text != null ? String(args.text) : "");
      return ok();
    } catch (e) { return err(e.message); }
  }

  function ext_fill_form(args = {}) {
    const fields = args.fields || {};
    const filled = [];
    const errors = [];
    for (const [key, value] of Object.entries(fields)) {
      try {
        const el = document.querySelector(key) ||
          document.querySelector(`[name="${key}"]`) ||
          document.getElementById(key);
        if (!el) { errors.push(`no field: ${key}`); continue; }
        el.focus(); _setValue(el, String(value)); filled.push(key);
      } catch (e) { errors.push(`${key}: ${e.message}`); }
    }
    return { ok: errors.length === 0, filled, ...(errors.length ? { errors } : {}) };
  }

  function ext_submit(args = {}) {
    try {
      const form = _one(args.form_selector || args.selector || "form");
      if (typeof form.requestSubmit === "function") form.requestSubmit();
      else form.submit();
      return ok();
    } catch (e) { return err(e.message); }
  }

  function ext_press_key(args = {}) {
    try {
      const el = args.selector ? _one(args.selector) : (document.activeElement || document.body);
      const key = args.key || "Enter";
      for (const type of ["keydown", "keypress", "keyup"]) {
        el.dispatchEvent(new KeyboardEvent(type, { key, bubbles: true, cancelable: true }));
      }
      return ok({ key });
    } catch (e) { return err(e.message); }
  }

  // ── Scroll / wait ──────────────────────────────────────────────────
  function ext_scroll(args = {}) {
    try {
      if (args.selector) { const el = _one(args.selector); try { el.scrollIntoView({ block: "center", behavior: "instant" }); } catch {} return ok(); }
      const amount = Number(args.amount) || Math.round((window.innerHeight || 800) * 0.85);
      const dir = args.direction || "down";
      const dy = dir === "up" ? -amount : dir === "top" ? -1e9 : dir === "bottom" ? 1e9 : amount;
      window.scrollBy(0, dy);
      return ok({ scrollY: window.scrollY });
    } catch (e) { return err(e.message); }
  }

  function ext_wait_for(args = {}) {
    const selector = args.selector;
    if (!selector) return Promise.resolve(err("selector required"));
    const timeout = Number(args.timeout_ms) || 5000;
    const started = Date.now();
    return new Promise((resolve) => {
      const tick = () => {
        let el = null;
        try { el = document.querySelector(selector); } catch { return resolve(err(`invalid selector: ${selector}`)); }
        if (el) return resolve(ok({ waited_ms: Date.now() - started }));
        if (Date.now() - started >= timeout) return resolve(err(`timeout waiting for: ${selector}`));
        setTimeout(tick, 120);
      };
      tick();
    });
  }

  // NOTE: exec_js (arbitrary in-page eval) is deliberately NOT implemented in
  // v1 — it's a code-injection surface even when confirmation-gated. Parked for
  // v2 behind an explicit per-install opt-in + chrome.scripting MAIN-world exec.

  // close_tab is acknowledged here; background.js does the real chrome.tabs.remove.
  function ext_close_tab() {
    return ok({ action: "close_requested" });
  }

  // ── Workflow recording ────────────────────────────────────────────
  // Records the user's clicks + field edits (as replayable click/type/select
  // steps with computed selectors). Single-page for v1 — a navigation reloads
  // the content script and ends the recording.
  let _recording = false;
  let _recSteps = [];
  const _esc = (s) => (typeof CSS !== "undefined" && CSS.escape ? CSS.escape(s) : String(s).replace(/[^\w-]/g, "\\$&"));
  function _cssSelector(el) {
    if (!el || el.nodeType !== 1) return "";
    if (el.id) return "#" + _esc(el.id);
    const parts = [];
    let cur = el;
    while (cur && cur.nodeType === 1 && parts.length < 5) {
      if (cur.id) { parts.unshift("#" + _esc(cur.id)); break; }
      let sel = cur.tagName.toLowerCase();
      if (cur.classList && cur.classList.length) sel += "." + [...cur.classList].slice(0, 2).map(_esc).join(".");
      const parent = cur.parentElement;
      if (parent) {
        const sibs = [...parent.children].filter((s) => s.tagName === cur.tagName);
        if (sibs.length > 1) sel += `:nth-of-type(${sibs.indexOf(cur) + 1})`;
      }
      parts.unshift(sel);
      cur = parent;
    }
    return parts.join(" > ");
  }
  function _onRecClick(e) {
    if (!_recording) return;
    const sel = _cssSelector(e.target);
    if (sel) _recSteps.push({ action: "click", args: { selector: sel } });
  }
  function _onRecChange(e) {
    if (!_recording) return;
    const el = e.target, sel = _cssSelector(el);
    if (!sel) return;
    if (el.tagName === "SELECT") _recSteps.push({ action: "select", args: { selector: sel, value: el.value } });
    else if (el.tagName === "INPUT" || el.tagName === "TEXTAREA") _recSteps.push({ action: "type", args: { selector: sel, text: el.value } });
  }
  function ext_record_start() {
    _recording = true; _recSteps = [];
    document.addEventListener("click", _onRecClick, true);
    document.addEventListener("change", _onRecChange, true);
    return ok();
  }
  function ext_record_stop() {
    _recording = false;
    document.removeEventListener("click", _onRecClick, true);
    document.removeEventListener("change", _onRecChange, true);
    return ok({ steps: _recSteps });
  }

  const HANDLERS = {
    get_url: ext_get_url,
    extract_text: ext_extract_text,
    find_by_text: ext_find_by_text,
    dom_summary: ext_dom_summary,
    read_console: ext_read_console,
    read_network: ext_read_network,
    click: ext_click,
    click_text: ext_click_text,
    right_click: ext_right_click,
    hover: ext_hover,
    drag: ext_drag,
    select: ext_select,
    type: ext_type,
    fill_form: ext_fill_form,
    submit: ext_submit,
    press_key: ext_press_key,
    scroll: ext_scroll,
    wait_for: ext_wait_for,
    close_tab: ext_close_tab,
    record_start: ext_record_start,
    record_stop: ext_record_stop,
  };

  const api = {
    HANDLERS,
    ext_get_url, ext_extract_text, ext_find_by_text, ext_dom_summary,
    ext_read_console, ext_read_network,
    ext_click, ext_click_text, ext_right_click, ext_hover, ext_drag, ext_select,
    ext_type, ext_fill_form, ext_submit, ext_press_key,
    ext_scroll, ext_wait_for, ext_close_tab,
    ext_record_start, ext_record_stop,
  };
  if (typeof module !== "undefined" && module.exports) module.exports = api;
  if (root) root.JARVIS_ACTIONS = api;
})(typeof globalThis !== "undefined" ? globalThis : this);

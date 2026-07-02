// safety.js — decides which commands need explicit human confirmation before
// jarvis-screen executes them. The bridge forwards `confirmed:true` once the
// user has approved; until then a destructive command is refused with
// needs_confirmation so Jarvis can ask first.
//
// Dual-loadable: as a content script (attaches to globalThis.JARVIS_SAFETY) and
// under Node/Jest (module.exports) so the logic is unit-tested without a browser.

(function (root) {
  // Power tools are ALWAYS confirmed — arbitrary code + cookie writes.
  const ALWAYS_CONFIRM_ACTIONS = new Set(["exec_js", "set_cookies"]);

  // Selector fragments that imply an irreversible / costly click.
  const DESTRUCTIVE_SELECTOR_RE =
    /\b(delete|remove|purchase|buy|pay|checkout|order|confirm|cancel-subscription|unsubscribe|deactivate|close-account|wire|transfer)\b/i;

  // Credential / sensitive field hints — typing into these is confirmed.
  const SENSITIVE_FIELD_RE = /\b(password|passwd|pwd|otp|2fa|cvv|cvc|ssn|card-?number|pin)\b/i;

  // Forms that look like payment/checkout — submitting is confirmed.
  const PAYMENT_FORM_RE = /\b(payment|checkout|billing|card|purchase|order)\b/i;

  function _s(v) {
    return typeof v === "string" ? v : "";
  }

  // Returns true if the command must be human-confirmed before running.
  function isDestructive(cmd) {
    if (!cmd || !cmd.action) return false;
    if (ALWAYS_CONFIRM_ACTIONS.has(cmd.action)) return true;
    const a = cmd.args || {};

    if (cmd.action === "click" || cmd.action === "right_click") {
      if (DESTRUCTIVE_SELECTOR_RE.test(_s(a.selector))) return true;
    }
    if (cmd.action === "submit") {
      if (PAYMENT_FORM_RE.test(_s(a.form_selector)) || PAYMENT_FORM_RE.test(_s(a.selector))) return true;
    }
    if (cmd.action === "type") {
      if (SENSITIVE_FIELD_RE.test(_s(a.selector))) return true;
    }
    if (cmd.action === "fill_form") {
      const keys = Object.keys(a.fields || {}).join(" ");
      if (SENSITIVE_FIELD_RE.test(keys)) return true;
    }
    return false;
  }

  // Gate: given a command (with cmd.confirmed set by the bridge/user), return
  // either { allow:true } or a { ok:false, needs_confirmation:true } refusal.
  function gate(cmd) {
    if (isDestructive(cmd) && !cmd.confirmed) {
      return {
        ok: false,
        needs_confirmation: true,
        error: `"${cmd.action}" needs confirmation before it runs`,
      };
    }
    return { allow: true };
  }

  const api = { isDestructive, gate, ALWAYS_CONFIRM_ACTIONS };
  if (typeof module !== "undefined" && module.exports) module.exports = api;
  if (root) root.JARVIS_SAFETY = api;
})(typeof globalThis !== "undefined" ? globalThis : this);

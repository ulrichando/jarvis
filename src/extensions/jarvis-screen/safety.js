// safety.js — decides which commands need explicit human confirmation before
// jarvis-screen executes them. The bridge forwards `confirmed:true` once the
// user has approved; until then a destructive command is refused with
// needs_confirmation so Jarvis can ask first.
//
// Dual-loadable: as a content script (attaches to globalThis.JARVIS_SAFETY) and
// under Node/Jest (module.exports) so the logic is unit-tested without a browser.

(function (root) {
  // Power tools are ALWAYS confirmed — arbitrary code, cookie writes, and
  // cookie READS (get_cookies returns session/auth cookies for any domain,
  // an exfiltration path, so it's gated like the writes).
  const ALWAYS_CONFIRM_ACTIONS = new Set(["exec_js", "set_cookies", "get_cookies"]);

  // Verbs that imply an irreversible / costly action. Matched against BOTH the
  // selector string (cheap pre-check, background) AND the resolved element's
  // visible text / aria-label (authoritative, content-side — see actions.js).
  // `[-\s]?` so both the selector form (cancel-subscription) and the element's
  // visible-text form (Cancel subscription) match the same compound verbs.
  const DESTRUCTIVE_SELECTOR_RE =
    /\b(delete|remove|purchase|buy|pay|checkout|order|confirm|cancel[-\s]?subscription|unsubscribe|deactivate|close[-\s]?account|wire|transfer)\b/i;

  // Credential / sensitive field hints — typing into these is confirmed.
  const SENSITIVE_FIELD_RE = /\b(password|passwd|pwd|otp|2fa|cvv|cvc|ssn|card-?number|pin)\b/i;

  // Forms that look like payment/checkout — submitting is confirmed.
  const PAYMENT_FORM_RE = /\b(payment|checkout|billing|card|purchase|order)\b/i;

  function _s(v) {
    return typeof v === "string" ? v : "";
  }

  // Free-text destructive check. Content-side callers pass a resolved
  // element's visible text / aria-label here — the selector string is an
  // unreliable proxy for intent (a "Delete account" button can have a generic
  // selector like button:nth-child(3), and a benign button can sit under a
  // .delete-icon container). Judging the element's own text closes that gap.
  function isDestructiveText(text) {
    return DESTRUCTIVE_SELECTOR_RE.test(_s(text));
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

  // ── Site permissions (#3 per-site allow/block, #4 default blocked categories) ─
  // Starter heuristic matched loosely on the hostname — NOT exhaustive. The
  // user's per-site block/allow list is the precise control; these are just sane
  // defaults (mirrors Claude for Chrome blocking financial/adult/pirated sites).
  // ponytail: substring/keyword match; upgrade to a curated category feed if
  // false positives bite (a user allow-override fixes any in the meantime).
  const BLOCKED_CATEGORY_PATTERNS = {
    financial: /paypal|venmo|coinbase|binance|kraken|robinhood|\bchase\b|wellsfargo|bankofamerica|citibank|capitalone|americanexpress|\bamex\b|barclays|\bhsbc\b|revolut|schwab|fidelity|vanguard|\bbank\b/i,
    adult: /porn|xvideos|pornhub|xhamster|\bxnxx\b|redtube|onlyfans|brazzers|adultfriendfinder|\bxxx\b/i,
    pirated: /thepiratebay|1337x|\brarbg\b|torrentz|kickass|\byts\b|fitgirl|\bnyaa\b|limetorrents|\btorrent\b/i,
  };

  function matchBlockedCategory(host) {
    for (const cat in BLOCKED_CATEGORY_PATTERNS) {
      if (BLOCKED_CATEGORY_PATTERNS[cat].test(host)) return cat;
    }
    return null;
  }

  // A host matches a list entry if it equals it or is a subdomain of it.
  function hostInList(host, list) {
    return Array.isArray(list) && list.some((h) => host === h || host.endsWith("." + h));
  }

  // Pure decision: given a hostname + the user's permission record, allow or
  // refuse Jarvis from acting on it. User allow-list OVERRIDES a blocked
  // category; user block-list is absolute. Empty host (chrome://, blank tab) →
  // allow (there's nothing web to act on; the command fails downstream anyway).
  function siteDecision(host, perms) {
    host = _s(host).toLowerCase();
    perms = perms || {};
    if (!host) return { allow: true };
    // Explicitly allowed → the site gate passes. (This does NOT auto-confirm
    // destructive actions — the safety gate still applies; see background.js.)
    if (hostInList(host, perms.allowed)) return { allow: true };
    if (hostInList(host, perms.blocked)) return { allow: false, reason: `Jarvis is blocked on ${host} (per your settings).` };
    if (perms.blockCategories === true) { // opt-in; off unless the user turned it on
      const cat = matchBlockedCategory(host);
      if (cat) return { allow: false, reason: `Jarvis won't operate on ${cat} sites by default (${host}) — allow it in Settings to override.` };
    }
    return { allow: true };
  }

  const api = { isDestructive, isDestructiveText, gate, siteDecision, matchBlockedCategory, ALWAYS_CONFIRM_ACTIONS };
  if (typeof module !== "undefined" && module.exports) module.exports = api;
  if (root) root.JARVIS_SAFETY = api;
})(typeof globalThis !== "undefined" ? globalThis : this);

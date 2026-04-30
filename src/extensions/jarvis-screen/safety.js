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
  // Submit on payment domains or if form contains payment keywords.
  if (cmd.action === 'submit' && cmd.args && cmd.args.form_selector) {
    // Check if form_selector contains payment keywords
    if (/payment|checkout|purchase|billing|credit[\-_ ]?card/i.test(cmd.args.form_selector)) {
      return true;
    }
    // Check if on a payment domain
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

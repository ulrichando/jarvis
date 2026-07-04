const safety = require("../safety.js");

describe("safety.isDestructive", () => {
  test("exec_js + set_cookies + get_cookies are always confirmed", () => {
    expect(safety.isDestructive({ action: "exec_js" })).toBe(true);
    expect(safety.isDestructive({ action: "set_cookies", args: { domain: "x.com" } })).toBe(true);
    // get_cookies reads session/auth cookies for any domain — gated like writes.
    expect(safety.isDestructive({ action: "get_cookies", args: { domain: "x.com" } })).toBe(true);
  });
  test("destructive click selectors are confirmed", () => {
    for (const sel of ["button.delete", "button#purchase", "a.cancel-subscription", ".unsubscribe-btn"]) {
      expect(safety.isDestructive({ action: "click", args: { selector: sel } })).toBe(true);
    }
  });
  test("benign clicks are not confirmed", () => {
    expect(safety.isDestructive({ action: "click", args: { selector: "a.more-info" } })).toBe(false);
    // subscribe is benign (only unsubscribe/cancel-subscription are destructive) — matches the plan.
    expect(safety.isDestructive({ action: "click", args: { selector: "button.subscribe" } })).toBe(false);
    expect(safety.isDestructive({ action: "click", args: { selector: "a.unsubscribe" } })).toBe(true);
  });
  test("credential field typing is confirmed", () => {
    expect(safety.isDestructive({ action: "type", args: { selector: "#password" } })).toBe(true);
    expect(safety.isDestructive({ action: "type", args: { selector: "input[name=cvv]" } })).toBe(true);
    expect(safety.isDestructive({ action: "type", args: { selector: "#email" } })).toBe(false);
  });
  test("fill_form with sensitive keys is confirmed", () => {
    expect(safety.isDestructive({ action: "fill_form", args: { fields: { otp: "123456" } } })).toBe(true);
    expect(safety.isDestructive({ action: "fill_form", args: { fields: { name: "Ada" } } })).toBe(false);
  });
  test("payment form submit is confirmed", () => {
    expect(safety.isDestructive({ action: "submit", args: { form_selector: "form#payment" } })).toBe(true);
    expect(safety.isDestructive({ action: "submit", args: { form_selector: "form#newsletter" } })).toBe(false);
  });
});

describe("safety.isDestructiveText (element-text, content-side)", () => {
  test("flags destructive verbs in an element's visible text / aria-label", () => {
    expect(safety.isDestructiveText("Delete account")).toBe(true);
    expect(safety.isDestructiveText("Cancel subscription")).toBe(true);
    expect(safety.isDestructiveText("Confirm purchase")).toBe(true);
  });
  test("passes benign labels", () => {
    expect(safety.isDestructiveText("Read more")).toBe(false);
    expect(safety.isDestructiveText("Continue")).toBe(false);
    expect(safety.isDestructiveText("")).toBe(false);
    expect(safety.isDestructiveText(null)).toBe(false);
  });
});

describe("safety.gate", () => {
  test("refuses an unconfirmed destructive command", () => {
    const g = safety.gate({ action: "exec_js" });
    expect(g.ok).toBe(false);
    expect(g.needs_confirmation).toBe(true);
  });
  test("allows the same command when confirmed", () => {
    expect(safety.gate({ action: "exec_js", confirmed: true }).allow).toBe(true);
  });
  test("allows benign commands outright", () => {
    expect(safety.gate({ action: "get_url" }).allow).toBe(true);
    expect(safety.gate({ action: "click", args: { selector: "a.more" } }).allow).toBe(true);
  });
});

describe("safety.siteDecision (#3 per-site perms, #4 blocked categories)", () => {
  const perms = (o) => ({ blocked: [], allowed: [], ...o }); // mirrors the real default: categories OFF unless set
  test("allows an ordinary site by default", () => {
    expect(safety.siteDecision("example.com", perms()).allow).toBe(true);
  });
  test("category blocking is OPT-IN — off by default (even for a bank)", () => {
    expect(safety.siteDecision("chase.com", perms()).allow).toBe(true);
  });
  test("refuses a user-blocked site (and its subdomains)", () => {
    expect(safety.siteDecision("news.ycombinator.com", perms({ blocked: ["ycombinator.com"] })).allow).toBe(false);
    expect(safety.siteDecision("ycombinator.com", perms({ blocked: ["ycombinator.com"] })).allow).toBe(false);
  });
  test("refuses category sites WHEN enabled (financial/adult/pirated)", () => {
    const on = { blockCategories: true };
    expect(safety.siteDecision("chase.com", perms(on)).allow).toBe(false);
    expect(safety.siteDecision("www.pornhub.com", perms(on)).allow).toBe(false);
    expect(safety.siteDecision("thepiratebay.org", perms(on)).allow).toBe(false);
    expect(safety.matchBlockedCategory("bankofamerica.com")).toBe("financial");
  });
  test("user allow-list OVERRIDES an enabled blocked category", () => {
    expect(safety.siteDecision("chase.com", perms({ blockCategories: true, allowed: ["chase.com"] })).allow).toBe(true);
  });
  test("SECURITY: an 'always allowed' site still confirms destructive/sensitive actions", () => {
    // The site gate passes on an allowed host, and carries NO auto-confirm flag...
    const r = safety.siteDecision("github.com", perms({ allowed: ["github.com"] }));
    expect(r.allow).toBe(true);
    expect(r.autoConfirm).toBeUndefined();
    // ...and the safety gate is independent: destructive + cookie/exec actions
    // still require per-action confirmation regardless of site trust.
    expect(safety.gate({ action: "click", args: { selector: "button.delete" } }).needs_confirmation).toBe(true);
    expect(safety.gate({ action: "get_cookies", args: { domain: "chase.com" } }).needs_confirmation).toBe(true);
    expect(safety.gate({ action: "exec_js" }).needs_confirmation).toBe(true);
  });
  test("empty host (chrome://, blank tab) is allowed — nothing web to act on", () => {
    expect(safety.siteDecision("", perms({ blockCategories: true })).allow).toBe(true);
  });
  test("no false-positive on a word-boundary miss (foodbank ≠ bank)", () => {
    expect(safety.siteDecision("foodbank.org", perms({ blockCategories: true })).allow).toBe(true);
  });
});

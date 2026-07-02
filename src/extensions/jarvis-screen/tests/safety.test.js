const safety = require("../safety.js");

describe("safety.isDestructive", () => {
  test("exec_js + set_cookies are always confirmed", () => {
    expect(safety.isDestructive({ action: "exec_js" })).toBe(true);
    expect(safety.isDestructive({ action: "set_cookies", args: { domain: "x.com" } })).toBe(true);
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

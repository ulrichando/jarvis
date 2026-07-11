import { describe, it, expect } from "vitest";
import { isSessionWithinAbsoluteCap } from "@/lib/auth-helpers";

const DAY = 24 * 60 * 60 * 1000;

// Absolute cap: 7 days for the single-user personal box (see auth-helpers.ts
// ABSOLUTE_CAP_MS). These pin that policy — tighten both the constant and
// these bounds together if the box ever goes multi-user.
describe("session absolute cap (7 days)", () => {
  it("accepts a session created 6 days ago", () => {
    const created = new Date(Date.now() - 6 * DAY);
    expect(isSessionWithinAbsoluteCap(created)).toBe(true);
  });
  it("rejects a session created 8 days ago", () => {
    const created = new Date(Date.now() - 8 * DAY);
    expect(isSessionWithinAbsoluteCap(created)).toBe(false);
  });
});

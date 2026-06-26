import { describe, it, expect } from "vitest";
import { isSessionWithinAbsoluteCap } from "@/lib/auth-helpers";

const DAY = 24 * 60 * 60 * 1000;

// Absolute cap lengthened from 8h to 30 days for the single-user personal box
// (see auth-helpers.ts ABSOLUTE_CAP_MS). These pin that policy — tighten both
// the constant and these bounds together if the box ever goes multi-user.
describe("session absolute cap (30 days)", () => {
  it("accepts a session created 29 days ago", () => {
    const created = new Date(Date.now() - 29 * DAY);
    expect(isSessionWithinAbsoluteCap(created)).toBe(true);
  });
  it("rejects a session created 31 days ago", () => {
    const created = new Date(Date.now() - 31 * DAY);
    expect(isSessionWithinAbsoluteCap(created)).toBe(false);
  });
});

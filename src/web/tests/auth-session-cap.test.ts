import { describe, it, expect } from "vitest";
import { isSessionWithinAbsoluteCap } from "@/lib/auth-helpers";

const HOUR = 60 * 60 * 1000;

describe("session absolute cap (8 hours)", () => {
  it("accepts a session created 7 hours ago", () => {
    const created = new Date(Date.now() - 7 * HOUR);
    expect(isSessionWithinAbsoluteCap(created)).toBe(true);
  });
  it("rejects a session created 9 hours ago", () => {
    const created = new Date(Date.now() - 9 * HOUR);
    expect(isSessionWithinAbsoluteCap(created)).toBe(false);
  });
});

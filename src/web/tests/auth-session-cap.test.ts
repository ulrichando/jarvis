import { describe, it, expect } from "vitest";
import { isSessionWithinAbsoluteCap } from "@/lib/auth-helpers";

describe("session absolute cap", () => {
  it("accepts a session created 29 days ago", () => {
    const created = new Date(Date.now() - 29 * 864e5);
    expect(isSessionWithinAbsoluteCap(created)).toBe(true);
  });
  it("rejects a session created 31 days ago", () => {
    const created = new Date(Date.now() - 31 * 864e5);
    expect(isSessionWithinAbsoluteCap(created)).toBe(false);
  });
});

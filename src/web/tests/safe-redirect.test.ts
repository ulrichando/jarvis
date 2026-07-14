import { describe, it, expect } from "vitest";
import { safeNextPath } from "@/lib/safe-redirect";

// Regression guard for the live open-redirect bypass shipped in PR #230:
// /login?next=/%09//evil.com decodes (searchParams.get) to "/<TAB>//evil.com".
// The old inline guard's startsWith("//") saw TAB at char 2 and PASSED it,
// but the WHATWG URL parser strips tab/newline/CR, so router.push received
// "//evil.com" — protocol-relative — and hard-navigated off-origin.
// safeNextPath strips those chars BEFORE the checks; these vectors must
// never regress.

const TAB = String.fromCharCode(9);
const LF = String.fromCharCode(10);
const CR = String.fromCharCode(13);
const DEL = String.fromCharCode(127);

describe("safeNextPath", () => {
  describe("control-char smuggling bypasses (the PR #230 bug)", () => {
    it("rejects the tab bypass /<TAB>//evil.com (decoded /%09//evil.com)", () => {
      expect(safeNextPath("/" + TAB + "//evil.com")).toBe("/chat");
    });
    it("rejects the newline bypass /<LF>/evil.com (decoded /%0A/evil.com)", () => {
      expect(safeNextPath("/" + LF + "/evil.com")).toBe("/chat");
    });
    it("rejects the CR bypass /<CR>/evil.com (decoded /%0D/evil.com)", () => {
      expect(safeNextPath("/" + CR + "/evil.com")).toBe("/chat");
    });
    it("rejects newline/CR variants that collapse to //host", () => {
      expect(safeNextPath("/" + LF + "//evil.com")).toBe("/chat");
      expect(safeNextPath("/" + CR + "//evil.com")).toBe("/chat");
    });
    it("rejects other C0 controls and DEL used as separators", () => {
      expect(safeNextPath("/" + String.fromCharCode(0) + "//evil.com")).toBe("/chat");
      expect(safeNextPath("/" + DEL + "//evil.com")).toBe("/chat");
    });
    it("rejects a control char hiding a backslash trick", () => {
      expect(safeNextPath("/" + TAB + "\\evil.com")).toBe("/chat");
    });
  });

  describe("classic open-redirect vectors", () => {
    it("rejects protocol-relative //evil.com", () => {
      expect(safeNextPath("//evil.com")).toBe("/chat");
    });
    it("rejects the backslash variant /\\evil.com", () => {
      expect(safeNextPath("/\\evil.com")).toBe("/chat");
    });
    it("rejects absolute URLs", () => {
      expect(safeNextPath("https://evil.com")).toBe("/chat");
      expect(safeNextPath("http://evil.com/chat")).toBe("/chat");
    });
    it("rejects scheme tricks", () => {
      expect(safeNextPath("javascript:alert(1)")).toBe("/chat");
    });
    it("rejects non-rooted paths", () => {
      expect(safeNextPath("evil.com")).toBe("/chat");
      expect(safeNextPath("chat")).toBe("/chat");
    });
    it("rejects backslashes anywhere in the path (defense-in-depth)", () => {
      expect(safeNextPath("/chat\\..\\evil")).toBe("/chat");
    });
  });

  describe("empty / missing input", () => {
    it("falls back to /chat for null", () => {
      expect(safeNextPath(null)).toBe("/chat");
    });
    it("falls back to /chat for empty string", () => {
      expect(safeNextPath("")).toBe("/chat");
    });
    it("falls back to /chat for control-chars-only input", () => {
      expect(safeNextPath(TAB + LF + CR)).toBe("/chat");
    });
  });

  describe("legitimate same-origin paths pass through unchanged", () => {
    it("returns /chat unchanged", () => {
      expect(safeNextPath("/chat")).toBe("/chat");
    });
    it("returns a deep path with query + hash unchanged", () => {
      expect(safeNextPath("/code/session_x?a=b#h")).toBe("/code/session_x?a=b#h");
    });
    it("returns other app routes unchanged", () => {
      expect(safeNextPath("/settings")).toBe("/settings");
      expect(safeNextPath("/artifacts/123")).toBe("/artifacts/123");
    });
  });
});

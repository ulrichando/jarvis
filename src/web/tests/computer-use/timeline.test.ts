import { describe, it, expect } from "vitest";
import { eventToPart, formatStepTime, formatElapsed, computeThumbSize } from "@/lib/computer-use/timeline";

describe("eventToPart", () => {
  it("maps text", () => {
    expect(eventToPart({ type: "text", text: "hi" }, 1000)).toEqual({ kind: "text", text: "hi", ts: 1000 });
  });
  it("maps action", () => {
    expect(eventToPart({ type: "action", summary: "Clicked Firefox" }, 5)).toEqual({ kind: "action", text: "Clicked Firefox", ts: 5 });
  });
  it("maps permission_request with fallback label", () => {
    expect(eventToPart({ type: "permission_request", id: "r1", summary: "type x" }, 7))
      .toEqual({ kind: "permission", reqId: "r1", label: "this action", text: "type x", ts: 7 });
  });
  it("maps done/blocked/error", () => {
    expect(eventToPart({ type: "done" }, 9)).toEqual({ kind: "done", text: "Done", ts: 9 });
    expect(eventToPart({ type: "blocked", summary: "nope" }, 9)?.kind).toBe("blocked");
    expect(eventToPart({ type: "error", error: "boom" }, 9)?.kind).toBe("error");
  });
  it("drops non-rendered frames", () => {
    expect(eventToPart({ type: "ping" }, 1)).toBeNull();
    expect(eventToPart({ type: "start" }, 1)).toBeNull();
    expect(eventToPart({ type: "denied", summary: "x" }, 1)).toBeNull();
    expect(eventToPart({ type: "action" }, 1)).toBeNull();
  });
});

describe("formatStepTime", () => {
  it("HH:MM:SS, zero-padded", () => {
    const ts = new Date(2026, 0, 1, 9, 4, 7).getTime();
    expect(formatStepTime(ts)).toBe("09:04:07");
  });
});

describe("formatElapsed", () => {
  it("m:ss", () => {
    expect(formatElapsed(0)).toBe("0:00");
    expect(formatElapsed(38_000)).toBe("0:38");
    expect(formatElapsed(125_000)).toBe("2:05");
    expect(formatElapsed(-50)).toBe("0:00");
  });
});

describe("computeThumbSize", () => {
  it("keeps small canvases, downscales large ones preserving aspect", () => {
    expect(computeThumbSize(100, 60, 128)).toEqual({ w: 100, h: 60 });
    expect(computeThumbSize(256, 160, 128)).toEqual({ w: 128, h: 80 });
    expect(computeThumbSize(0, 0, 128)).toEqual({ w: 0, h: 0 });
  });
});

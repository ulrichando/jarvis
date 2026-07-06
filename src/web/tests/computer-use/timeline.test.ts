import { describe, it, expect } from "vitest";
import { applyEvent, eventToPart, formatStepTime, formatElapsed, computeThumbSize, type ChatMsg } from "@/lib/computer-use/timeline";

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
    expect(eventToPart({ type: "approval_resolved", id: "r1" }, 1)).toBeNull();
  });
  it("maps stopped to a done-style row", () => {
    expect(eventToPart({ type: "stopped" }, 9)).toEqual({ kind: "done", text: "Stopped", ts: 9 });
  });
});

describe("applyEvent", () => {
  it("rebuilds a full turn from a replayed stream (reattach path)", () => {
    let thread: ChatMsg[] = [];
    const events = [
      { type: "start", task: "open firefox" },
      { type: "text", text: "on it" },
      { type: "action", summary: "Clicked Firefox" },
      { type: "done" },
    ] as const;
    for (const evt of events) thread = applyEvent(thread, evt, 1000, true);
    expect(thread).toHaveLength(2);
    expect(thread[0]).toEqual({ role: "user", parts: [{ kind: "text", text: "open firefox", ts: 1000 }] });
    expect(thread[1].parts.map((p) => p.kind)).toEqual(["text", "action", "done"]);
  });
  it("ignores start on the live path (turn already appended optimistically)", () => {
    const thread: ChatMsg[] = [{ role: "user", parts: [] }, { role: "assistant", parts: [] }];
    expect(applyEvent(thread, { type: "start", task: "x" }, 1, false)).toBe(thread);
  });
  it("resolves the matching pending permission card", () => {
    const thread: ChatMsg[] = [
      { role: "user", parts: [] },
      { role: "assistant", parts: [{ kind: "permission", reqId: "r1", text: "" }, { kind: "permission", reqId: "r2", text: "", resolved: "deny" }] },
    ];
    const next = applyEvent(thread, { type: "approval_resolved", id: "r1", decision: "session" }, 1, true);
    expect(next[1].parts[0].resolved).toBe("session");
    expect(next[1].parts[1].resolved).toBe("deny"); // already-resolved cards untouched
  });
  it("attaches a snapshot thumb to action parts", () => {
    const thread: ChatMsg[] = [{ role: "user", parts: [] }, { role: "assistant", parts: [] }];
    const next = applyEvent(thread, { type: "action", summary: "click" }, 1, true, () => "data:thumb");
    expect(next[1].parts[0].thumb).toBe("data:thumb");
  });
  it("drops parts when no assistant turn is open (pre-start replay noise)", () => {
    expect(applyEvent([], { type: "text", text: "hi" }, 1, true)).toEqual([]);
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

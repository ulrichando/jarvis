import { describe, it, expect, afterEach } from "vitest";
import {
  kimiModesEnabled,
  formatKimiError,
  extractMessagesForKimi,
  loadKimiPersona,
} from "@/lib/ai/kimi/shared";
import type { UIMessage } from "ai";

describe("kimiModesEnabled", () => {
  const originalEnv = process.env.KIMI_K2_MODES_ENABLED;
  afterEach(() => {
    if (originalEnv === undefined) delete process.env.KIMI_K2_MODES_ENABLED;
    else process.env.KIMI_K2_MODES_ENABLED = originalEnv;
  });

  it("returns false when env var unset", () => {
    delete process.env.KIMI_K2_MODES_ENABLED;
    expect(kimiModesEnabled()).toBe(false);
  });

  it("returns false when env var is anything other than '1'", () => {
    process.env.KIMI_K2_MODES_ENABLED = "true";
    expect(kimiModesEnabled()).toBe(false);
    process.env.KIMI_K2_MODES_ENABLED = "0";
    expect(kimiModesEnabled()).toBe(false);
    process.env.KIMI_K2_MODES_ENABLED = "";
    expect(kimiModesEnabled()).toBe(false);
  });

  it("returns true when env var is exactly '1'", () => {
    process.env.KIMI_K2_MODES_ENABLED = "1";
    expect(kimiModesEnabled()).toBe(true);
  });
});

describe("formatKimiError", () => {
  it("returns a 502 SSE Response for generic errors with kimi-error data part", async () => {
    const err = new Error("upstream exploded");
    const resp = formatKimiError(err);
    expect(resp.status).toBe(502);
    expect(resp.headers.get("Content-Type")).toBe("text/event-stream");
    const text = await resp.text();
    expect(text).toContain("kimi-error");
    expect(text).toContain("upstream exploded");
    expect(text).toContain("[DONE]");
  });

  it("returns 401 for AuthenticationError", async () => {
    const err: Error & { status?: number } = new Error("invalid key");
    err.status = 401;
    const resp = formatKimiError(err);
    expect(resp.status).toBe(401);
  });

  it("returns 429 for RateLimit with retry-after hint in body", async () => {
    const err: Error & { status?: number } = new Error("Rate limit");
    err.status = 429;
    const resp = formatKimiError(err, { retryAfterSeconds: 10 });
    expect(resp.status).toBe(429);
    const text = await resp.text();
    expect(text).toContain("10");
  });
});

describe("extractMessagesForKimi", () => {
  it("filters out file parts (Kimi text-only) and preserves text parts", () => {
    const filePart = {
      type: "file",
      url: "data:image/png;base64,xxx",
      mediaType: "image/png",
    } as unknown as UIMessage["parts"][number];
    const msgs: UIMessage[] = [
      {
        id: "u1",
        role: "user",
        parts: [{ type: "text", text: "hello" }, filePart],
      },
    ];
    const out = extractMessagesForKimi(msgs);
    expect(out).toHaveLength(1);
    expect(out[0].parts).toEqual([{ type: "text", text: "hello" }]);
  });

  it("drops messages that have no text after filtering", () => {
    const filePart = {
      type: "file",
      url: "data:image/png;base64,xxx",
      mediaType: "image/png",
    } as unknown as UIMessage["parts"][number];
    const msgs: UIMessage[] = [
      { id: "u1", role: "user", parts: [filePart] },
      { id: "u2", role: "user", parts: [{ type: "text", text: "real text" }] },
    ];
    const out = extractMessagesForKimi(msgs);
    expect(out).toHaveLength(1);
    expect(out[0].id).toBe("u2");
  });
});

describe("loadKimiPersona", () => {
  it("returns the JARVIS persona string by default", () => {
    const p = loadKimiPersona();
    expect(p).toContain("JARVIS");
    expect(p.length).toBeGreaterThan(50);
  });

  it("appends custom suffix when passed", () => {
    const p = loadKimiPersona({ suffix: "Be terse." });
    expect(p).toContain("Be terse.");
  });
});

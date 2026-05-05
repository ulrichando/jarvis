import { describe, it, expect, beforeEach, afterEach } from "vitest";
import { routeKimiMode } from "@/lib/ai/kimi";

describe("routeKimiMode dispatcher", () => {
  beforeEach(() => {
    process.env.KIMI_K2_MODES_ENABLED = "1";
    process.env.KIMI_API_KEY = "test-key";
  });
  afterEach(() => {
    delete process.env.KIMI_K2_MODES_ENABLED;
    delete process.env.KIMI_API_KEY;
  });

  it("rejects unknown kimi-k2-* model with 400", async () => {
    const resp = await routeKimiMode({ messages: [] }, "kimi-k2-bogus");
    expect(resp.status).toBe(400);
  });

  it("dispatches kimi-k2-instant", async () => {
    const resp = await routeKimiMode(
      { messages: [{ id: "u", role: "user", parts: [{ type: "text", text: "hi" }] }] },
      "kimi-k2-instant",
    );
    // Either 200 (handler responded) or 501 (stub) — both prove the
    // switch matched. 400 would mean the switch missed.
    expect(resp.status).not.toBe(400);
  });

  it("dispatches kimi-k2-thinking", async () => {
    const resp = await routeKimiMode(
      { messages: [{ id: "u", role: "user", parts: [{ type: "text", text: "hi" }] }] },
      "kimi-k2-thinking",
    );
    expect(resp.status).not.toBe(400);
  });

  it("dispatches kimi-k2-agent", async () => {
    const resp = await routeKimiMode(
      { messages: [{ id: "u", role: "user", parts: [{ type: "text", text: "hi" }] }] },
      "kimi-k2-agent",
    );
    expect(resp.status).not.toBe(400);
  });

  it("dispatches kimi-k2-swarm", async () => {
    const resp = await routeKimiMode(
      { messages: [{ id: "u", role: "user", parts: [{ type: "text", text: "hi" }] }] },
      "kimi-k2-swarm",
    );
    expect(resp.status).not.toBe(400);
  });
});

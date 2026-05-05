import { describe, it, expect, beforeEach, vi } from "vitest";

vi.mock("ai", async () => {
  const actual = await vi.importActual<typeof import("ai")>("ai");
  return {
    ...actual,
    streamText: vi.fn(),
  };
});

vi.mock("@/lib/ai/kimi/shared", async () => {
  const actual = await vi.importActual<typeof import("@/lib/ai/kimi/shared")>(
    "@/lib/ai/kimi/shared",
  );
  return {
    ...actual,
    buildKimiClient: vi.fn(async () => ({
      model: { _mock: "kimi-k2.6" },
      apiKey: "test-key",
      baseURL: "https://api.moonshot.ai/v1",
    })),
  };
});

import { streamText } from "ai";
import { handleThinking } from "@/lib/ai/kimi/thinking";

const mockedStreamText = streamText as unknown as ReturnType<typeof vi.fn>;

function fakeStreamResult() {
  return {
    toUIMessageStreamResponse: (init?: { headers?: Record<string, string> }) =>
      new Response("ok", {
        status: 200,
        headers: { "Content-Type": "text/event-stream", ...(init?.headers ?? {}) },
      }),
    consumeStream: () => undefined,
  };
}

describe("handleThinking", () => {
  beforeEach(() => {
    mockedStreamText.mockReset();
    mockedStreamText.mockReturnValue(fakeStreamResult());
    process.env.KIMI_API_KEY = "test-key";
  });

  it("sends thinking:enabled,keep:all", async () => {
    await handleThinking({
      messages: [{ id: "u", role: "user", parts: [{ type: "text", text: "explain" }] }],
    });
    const args = mockedStreamText.mock.calls[0][0] as Record<string, unknown>;
    const po = args.providerOptions as { kimi?: { thinking?: { type?: string; keep?: string } } };
    expect(po?.kimi?.thinking?.type).toBe("enabled");
    expect(po?.kimi?.thinking?.keep).toBe("all");
  });

  it("uses maxOutputTokens 16000", async () => {
    await handleThinking({
      messages: [{ id: "u", role: "user", parts: [{ type: "text", text: "explain" }] }],
    });
    const args = mockedStreamText.mock.calls[0][0] as Record<string, unknown>;
    expect(args.maxOutputTokens).toBe(16000);
  });

  it("uses temperature 1.0", async () => {
    await handleThinking({
      messages: [{ id: "u", role: "user", parts: [{ type: "text", text: "explain" }] }],
    });
    const args = mockedStreamText.mock.calls[0][0] as Record<string, unknown>;
    expect(args.temperature).toBe(1.0);
  });

  it("retries with maxOutputTokens 8000 when first call rejects '16000 too high'", async () => {
    mockedStreamText.mockReset();
    let calls = 0;
    mockedStreamText.mockImplementation(() => {
      calls++;
      if (calls === 1) {
        throw Object.assign(new Error("max_completion_tokens is above the model's limit"), {
          status: 400,
        });
      }
      return fakeStreamResult();
    });
    await handleThinking({
      messages: [{ id: "u", role: "user", parts: [{ type: "text", text: "explain" }] }],
    });
    expect(calls).toBe(2);
    const secondCallArgs = mockedStreamText.mock.calls[1][0] as Record<string, unknown>;
    expect(secondCallArgs.maxOutputTokens).toBe(8000);
  });

  it("returns 200 SSE", async () => {
    const resp = await handleThinking({
      messages: [{ id: "u", role: "user", parts: [{ type: "text", text: "explain" }] }],
    });
    expect(resp.status).toBe(200);
  });

  it("emits X-Kimi-Mode: thinking response header", async () => {
    const resp = await handleThinking({
      messages: [{ id: "u", role: "user", parts: [{ type: "text", text: "explain" }] }],
    });
    expect(resp.headers.get("X-Kimi-Mode")).toBe("thinking");
  });

  it("returns 401 when KIMI_API_KEY missing", async () => {
    const { buildKimiClient } = await import("@/lib/ai/kimi/shared");
    (buildKimiClient as unknown as ReturnType<typeof vi.fn>).mockRejectedValueOnce(
      Object.assign(new Error("KIMI_API_KEY not configured"), { name: "KimiKeyMissingError" }),
    );
    const resp = await handleThinking({
      messages: [{ id: "u", role: "user", parts: [{ type: "text", text: "explain" }] }],
    });
    expect(resp.status).toBe(401);
  });
});

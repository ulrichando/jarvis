import { describe, it, expect, beforeEach, vi } from "vitest";

vi.mock("ai", async () => {
  const actual = await vi.importActual<typeof import("ai")>("ai");
  return {
    ...actual,
    streamText: vi.fn(),
    stepCountIs: vi.fn((n: number) => ({ _stepLimit: n })),
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
import { handleAgent } from "@/lib/ai/kimi/agent";

const mockedStreamText = streamText as unknown as ReturnType<typeof vi.fn>;

// Honor `headers` passed to toUIMessageStreamResponse so the
// X-Kimi-Mode assertion is structurally observable. Same fix as
// thinking.test.ts.
function fakeStreamResult() {
  return {
    toUIMessageStreamResponse: (init?: { headers?: Record<string, string> }) => {
      const headers = new Headers({ "Content-Type": "text/event-stream" });
      if (init?.headers) {
        for (const [k, v] of Object.entries(init.headers)) headers.set(k, v);
      }
      return new Response("ok", { status: 200, headers });
    },
    consumeStream: () => undefined,
  };
}

describe("handleAgent", () => {
  beforeEach(() => {
    mockedStreamText.mockReset();
    mockedStreamText.mockReturnValue(fakeStreamResult());
    process.env.KIMI_API_KEY = "test-key";
  });

  it("binds webSearch tool", async () => {
    await handleAgent({
      messages: [{ id: "u", role: "user", parts: [{ type: "text", text: "what's the weather in Paris" }] }],
    });
    const args = mockedStreamText.mock.calls[0][0] as Record<string, unknown>;
    const tools = args.tools as Record<string, unknown>;
    expect(tools).toBeDefined();
    expect(tools.webSearch).toBeDefined();
  });

  it("uses thinking:disabled (incompatible with $web_search per Moonshot docs)", async () => {
    await handleAgent({
      messages: [{ id: "u", role: "user", parts: [{ type: "text", text: "weather?" }] }],
    });
    const args = mockedStreamText.mock.calls[0][0] as Record<string, unknown>;
    const po = args.providerOptions as { kimi?: { thinking?: { type?: string } } };
    expect(po?.kimi?.thinking?.type).toBe("disabled");
  });

  it("sets stopWhen to stepCountIs(5)", async () => {
    await handleAgent({
      messages: [{ id: "u", role: "user", parts: [{ type: "text", text: "weather?" }] }],
    });
    const args = mockedStreamText.mock.calls[0][0] as Record<string, unknown>;
    const stopWhen = args.stopWhen as { _stepLimit?: number };
    expect(stopWhen?._stepLimit).toBe(5);
  });

  it("uses maxOutputTokens 4096 (room for tool loop)", async () => {
    await handleAgent({
      messages: [{ id: "u", role: "user", parts: [{ type: "text", text: "weather?" }] }],
    });
    const args = mockedStreamText.mock.calls[0][0] as Record<string, unknown>;
    expect(args.maxOutputTokens).toBe(4096);
  });

  it("returns 200 SSE", async () => {
    const resp = await handleAgent({
      messages: [{ id: "u", role: "user", parts: [{ type: "text", text: "weather?" }] }],
    });
    expect(resp.status).toBe(200);
  });

  it("emits X-Kimi-Mode: agent header", async () => {
    const resp = await handleAgent({
      messages: [{ id: "u", role: "user", parts: [{ type: "text", text: "weather?" }] }],
    });
    expect(resp.headers.get("X-Kimi-Mode")).toBe("agent");
  });

  it("returns 401 on missing key", async () => {
    const { buildKimiClient } = await import("@/lib/ai/kimi/shared");
    (buildKimiClient as unknown as ReturnType<typeof vi.fn>).mockRejectedValueOnce(
      Object.assign(new Error("KIMI_API_KEY not configured"), { name: "KimiKeyMissingError" }),
    );
    const resp = await handleAgent({
      messages: [{ id: "u", role: "user", parts: [{ type: "text", text: "weather?" }] }],
    });
    expect(resp.status).toBe(401);
  });

  it("uses temperature 0.6 (Moonshot K2.6 only accepts 0.6)", async () => {
    await handleAgent({
      messages: [{ id: "u", role: "user", parts: [{ type: "text", text: "weather?" }] }],
    });
    const args = mockedStreamText.mock.calls[0][0] as Record<string, unknown>;
    expect(args.temperature).toBe(0.6);
  });
});

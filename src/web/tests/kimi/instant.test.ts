import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";

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
import { handleInstant } from "@/lib/ai/kimi/instant";

const mockedStreamText = streamText as unknown as ReturnType<typeof vi.fn>;

function fakeStreamResult() {
  return {
    toUIMessageStreamResponse: () =>
      new Response("ok", {
        status: 200,
        headers: { "Content-Type": "text/event-stream" },
      }),
    consumeStream: () => undefined,
  };
}

describe("handleInstant", () => {
  beforeEach(() => {
    mockedStreamText.mockReset();
    mockedStreamText.mockReturnValue(fakeStreamResult());
    process.env.KIMI_API_KEY = "test-key";
  });
  afterEach(() => {
    delete process.env.KIMI_API_KEY;
  });

  it("calls streamText with thinking:disabled in providerOptions.kimi", async () => {
    await handleInstant({
      messages: [{ id: "u", role: "user", parts: [{ type: "text", text: "hi" }] }],
    });
    expect(mockedStreamText).toHaveBeenCalledOnce();
    const args = mockedStreamText.mock.calls[0][0] as Record<string, unknown>;
    const providerOptions = args.providerOptions as { kimi?: { thinking?: { type?: string } } };
    expect(providerOptions?.kimi?.thinking?.type).toBe("disabled");
  });

  it("uses maxOutputTokens 1024", async () => {
    await handleInstant({
      messages: [{ id: "u", role: "user", parts: [{ type: "text", text: "hi" }] }],
    });
    const args = mockedStreamText.mock.calls[0][0] as Record<string, unknown>;
    expect(args.maxOutputTokens).toBe(1024);
  });

  it("uses temperature 0.6", async () => {
    await handleInstant({
      messages: [{ id: "u", role: "user", parts: [{ type: "text", text: "hi" }] }],
    });
    const args = mockedStreamText.mock.calls[0][0] as Record<string, unknown>;
    expect(args.temperature).toBe(0.6);
  });

  it("returns a 200 SSE Response from toUIMessageStreamResponse", async () => {
    const resp = await handleInstant({
      messages: [{ id: "u", role: "user", parts: [{ type: "text", text: "hi" }] }],
    });
    expect(resp.status).toBe(200);
    expect(resp.headers.get("Content-Type")).toBe("text/event-stream");
  });

  it("returns 401 SSE when KIMI_API_KEY missing", async () => {
    delete process.env.KIMI_API_KEY;
    const { buildKimiClient } = await import("@/lib/ai/kimi/shared");
    (buildKimiClient as unknown as ReturnType<typeof vi.fn>).mockRejectedValueOnce(
      Object.assign(new Error("KIMI_API_KEY not configured"), { name: "KimiKeyMissingError" }),
    );
    const resp = await handleInstant({
      messages: [{ id: "u", role: "user", parts: [{ type: "text", text: "hi" }] }],
    });
    expect(resp.status).toBe(401);
  });
});

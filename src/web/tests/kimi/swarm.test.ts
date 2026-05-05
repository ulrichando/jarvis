import { describe, it, expect, beforeEach, vi } from "vitest";

vi.mock("ai", async () => {
  const actual = await vi.importActual<typeof import("ai")>("ai");
  return {
    ...actual,
    streamText: vi.fn(),
    generateText: vi.fn(),
    generateObject: vi.fn(),
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

vi.mock("@/lib/ai/kimi/budget", () => ({
  reserveSwarmBudget: vi.fn(async () => ({ ok: true, remaining: 5 })),
  recordSwarmSpend: vi.fn(async () => undefined),
}));

import { streamText, generateText, generateObject } from "ai";
import { handleSwarm } from "@/lib/ai/kimi/swarm";
import { reserveSwarmBudget } from "@/lib/ai/kimi/budget";

const mockedStream = streamText as unknown as ReturnType<typeof vi.fn>;
const mockedGenText = generateText as unknown as ReturnType<typeof vi.fn>;
const mockedGenObj = generateObject as unknown as ReturnType<typeof vi.fn>;
const mockedReserve = reserveSwarmBudget as unknown as ReturnType<typeof vi.fn>;

function fakeStreamResult() {
  return {
    // Empty-plan fallback path uses this directly.
    toUIMessageStreamResponse: (opts?: { headers?: Record<string, string> }) =>
      new Response("ok", {
        status: 200,
        headers: {
          "Content-Type": "text/event-stream",
          ...(opts?.headers ?? {}),
        },
      }),
    // Composite path merges this stream into the createUIMessageStream
    // writer. Empty stream is fine — assertions check headers / call
    // counts, not body chunks (e2e.test.ts covers the body content).
    toUIMessageStream: () =>
      new ReadableStream({
        start(controller) {
          controller.close();
        },
      }),
    consumeStream: () => undefined,
  };
}

describe("handleSwarm", () => {
  beforeEach(() => {
    mockedStream.mockReset();
    mockedGenText.mockReset();
    mockedGenObj.mockReset();
    mockedReserve.mockReset();
    mockedReserve.mockResolvedValue({ ok: true, remaining: 5 });
    mockedStream.mockReturnValue(fakeStreamResult());
    mockedGenText.mockResolvedValue({
      text: "sub-agent reply",
      usage: { inputTokens: 100, outputTokens: 50 },
    });
    mockedGenObj.mockResolvedValue({
      object: {
        subtasks: [
          { role: "researcher-A", prompt: "research aspect A" },
          { role: "researcher-B", prompt: "research aspect B" },
          { role: "researcher-C", prompt: "research aspect C" },
        ],
      },
    });
    process.env.KIMI_API_KEY = "test-key";
  });

  it("calls generateObject to decompose first", async () => {
    const resp = await handleSwarm({
      messages: [{ id: "u", role: "user", parts: [{ type: "text", text: "compare frameworks" }] }],
    });
    await resp.text();
    expect(mockedGenObj).toHaveBeenCalledOnce();
  });

  it("fans out generateText calls — one per subtask", async () => {
    const resp = await handleSwarm({
      messages: [{ id: "u", role: "user", parts: [{ type: "text", text: "compare frameworks" }] }],
    });
    await resp.text();
    expect(mockedGenText).toHaveBeenCalledTimes(3);
  });

  it("passes prompt_cache_key for shared input cache", async () => {
    const resp = await handleSwarm({
      messages: [{ id: "u", role: "user", parts: [{ type: "text", text: "compare frameworks" }] }],
    });
    await resp.text();
    const firstCall = mockedGenText.mock.calls[0][0] as Record<string, unknown>;
    const po = firstCall.providerOptions as { kimi?: { prompt_cache_key?: string } };
    expect(po?.kimi?.prompt_cache_key).toMatch(/^swarm-/);
  });

  it("calls streamText to aggregate after fan-out", async () => {
    const resp = await handleSwarm({
      messages: [{ id: "u", role: "user", parts: [{ type: "text", text: "compare frameworks" }] }],
    });
    // Composite stream's start() runs lazily on body consumption — drive
    // it by reading the response body before asserting streamText fired.
    await resp.text();
    expect(mockedStream).toHaveBeenCalledOnce();
  });

  it("falls back to Instant when decompose returns empty subtasks", async () => {
    mockedGenObj.mockResolvedValueOnce({ object: { subtasks: [] } });
    const resp = await handleSwarm({
      messages: [{ id: "u", role: "user", parts: [{ type: "text", text: "trivial question" }] }],
    });
    await resp.text();
    expect(mockedGenText).not.toHaveBeenCalled();
    expect(mockedStream).toHaveBeenCalledOnce();
    const args = mockedStream.mock.calls[0][0] as Record<string, unknown>;
    const po = args.providerOptions as { kimi?: { thinking?: { type?: string } } };
    expect(po?.kimi?.thinking?.type).toBe("disabled");
  });

  it("survives when one sub-agent rejects (Promise.allSettled)", async () => {
    mockedGenText
      .mockResolvedValueOnce({ text: "result A", usage: { inputTokens: 50, outputTokens: 25 } })
      .mockRejectedValueOnce(new Error("transient API blip"))
      .mockResolvedValueOnce({ text: "result C", usage: { inputTokens: 50, outputTokens: 25 } });
    const resp = await handleSwarm({
      messages: [{ id: "u", role: "user", parts: [{ type: "text", text: "compare frameworks" }] }],
    });
    await resp.text();
    expect(mockedStream).toHaveBeenCalledOnce();
    const args = mockedStream.mock.calls[0][0] as Record<string, unknown>;
    const messages = args.messages as Array<{ content?: unknown }>;
    const aggInput = JSON.stringify(messages);
    expect(aggInput).toContain("result A");
    expect(aggInput).toContain("result C");
    expect(aggInput).toMatch(/sub-agent failed/i);
  });

  it("returns budget-exceeded SSE when reserveSwarmBudget denies", async () => {
    mockedReserve.mockResolvedValueOnce({
      ok: false,
      reason: "Per-day Swarm budget ($5.00) reached. Current spend: $5.01.",
      remaining: 0,
    });
    const resp = await handleSwarm({
      messages: [{ id: "u", role: "user", parts: [{ type: "text", text: "compare frameworks" }] }],
    });
    expect(resp.status).toBe(429);
    const body = await resp.text();
    expect(body).toMatch(/budget/i);
  });

  it("emits X-Kimi-Mode: swarm response header", async () => {
    const resp = await handleSwarm({
      messages: [{ id: "u", role: "user", parts: [{ type: "text", text: "compare frameworks" }] }],
    });
    expect(resp.headers.get("X-Kimi-Mode")).toBe("swarm");
  });

  it("returns 401 on missing key", async () => {
    const { buildKimiClient } = await import("@/lib/ai/kimi/shared");
    (buildKimiClient as unknown as ReturnType<typeof vi.fn>).mockRejectedValueOnce(
      Object.assign(new Error("KIMI_API_KEY not configured"), { name: "KimiKeyMissingError" }),
    );
    const resp = await handleSwarm({
      messages: [{ id: "u", role: "user", parts: [{ type: "text", text: "compare frameworks" }] }],
    });
    expect(resp.status).toBe(401);
  });

  it("aggregator system prompt instructs synthesis only from sources", async () => {
    const resp = await handleSwarm({
      messages: [{ id: "u", role: "user", parts: [{ type: "text", text: "compare frameworks" }] }],
    });
    await resp.text();
    const args = mockedStream.mock.calls[0][0] as Record<string, unknown>;
    expect(args.system).toMatch(/synthesize.*these.*sources/i);
  });
});

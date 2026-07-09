import { describe, it, expect } from "vitest";

// Characterization tests for finding #13b — the chat.tsx SSE stream loop.
//
// chat.tsx reads /api/chat's response body as a byte stream, decodes it,
// splits on newlines, and classifies each `data: …` line into a small set of
// event shapes (text-delta, reasoning-delta, tool-output-available, finish,
// message-metadata). That classification, the decode-buffer split, the
// per-event guards, the usage reducer, and the length/complete decision are
// all PURE — they were extracted verbatim into src/lib/chat/sse-stream.ts and
// the component's reader loop now calls them. These tests PIN the current
// behavior so the upcoming useChatStream / useActionRunner extraction can't
// silently drift the wire protocol handling (which has a documented
// regression history: "I send and nothing appears", builds going off the
// rails, etc.).

import {
  splitSseBuffer,
  parseSseLine,
  isTextDelta,
  isReasoningDelta,
  extractGeneratedImage,
  accumulateUsage,
  decideStreamCompletion,
  type SseEvent,
} from "@/lib/chat/sse-stream";

describe("splitSseBuffer — decode-buffer newline split", () => {
  it("returns complete lines and keeps the trailing partial as `rest`", () => {
    // The reader appends decoded bytes to `buf`; complete lines are dispatched
    // and the final partial stays buffered until the next chunk completes it.
    const { lines, rest } = splitSseBuffer(
      'data: {"type":"text-delta","delta":"a"}\ndata: {"type":"text-delta","de',
    );
    expect(lines).toEqual(['data: {"type":"text-delta","delta":"a"}']);
    // The partial second line is NOT dispatched — it's carried over.
    expect(rest).toBe('data: {"type":"text-delta","de');
  });

  it("a buffer ending in \\n yields an empty trailing rest", () => {
    const { lines, rest } = splitSseBuffer("data: one\ndata: two\n");
    expect(lines).toEqual(["data: one", "data: two"]);
    expect(rest).toBe("");
  });

  it("a buffer with no newline is entirely partial (nothing dispatched)", () => {
    const { lines, rest } = splitSseBuffer("data: partia");
    expect(lines).toEqual([]);
    expect(rest).toBe("data: partia");
  });

  it("an empty buffer yields no lines and empty rest", () => {
    const { lines, rest } = splitSseBuffer("");
    expect(lines).toEqual([]);
    expect(rest).toBe("");
  });

  it("re-splitting the carried rest + next chunk reassembles the split line", () => {
    // Simulates two reader chunks that tore a single SSE line in half.
    const first = splitSseBuffer(
      'data: {"type":"finish","fin',
    );
    expect(first.lines).toEqual([]);
    const second = splitSseBuffer(
      first.rest + 'ishReason":"stop"}\n',
    );
    expect(second.lines).toEqual(['data: {"type":"finish","finishReason":"stop"}']);
    expect(second.rest).toBe("");
    const parsed = parseSseLine(second.lines[0]);
    expect(parsed.kind).toBe("event");
    if (parsed.kind === "event") {
      expect(parsed.event.finishReason).toBe("stop");
    }
  });
});

describe("parseSseLine — per-line classification", () => {
  it("ignores lines that don't start with `data: `", () => {
    expect(parseSseLine(":comment").kind).toBe("ignore");
    expect(parseSseLine("event: message").kind).toBe("ignore");
    expect(parseSseLine("").kind).toBe("ignore");
    // Requires the trailing SPACE after the colon — `data:{…}` is ignored.
    expect(parseSseLine('data:{"type":"text-delta"}').kind).toBe("ignore");
  });

  it("classifies `data: [DONE]` as the done sentinel", () => {
    expect(parseSseLine("data: [DONE]").kind).toBe("done");
  });

  it("parses a well-formed JSON payload into an event", () => {
    const r = parseSseLine('data: {"type":"text-delta","delta":"hi"}');
    expect(r.kind).toBe("event");
    if (r.kind === "event") {
      expect(r.event.type).toBe("text-delta");
      expect(r.event.delta).toBe("hi");
    }
  });

  it("classifies an unparseable payload as malformed (skipped, not thrown)", () => {
    const r = parseSseLine("data: {not json");
    expect(r.kind).toBe("malformed");
    if (r.kind === "malformed") expect(r.raw).toBe("{not json");
  });

  it("slices exactly 6 chars so a payload with a leading space survives", () => {
    // `data:  x` → slice(6) → " x" — that's not `[DONE]` and not JSON, so
    // malformed. Pins the exact slice offset the loop relies on.
    const r = parseSseLine("data:  [DONE]");
    // slice(6) = " [DONE]" which !== "[DONE]" → JSON.parse throws → malformed
    expect(r.kind).toBe("malformed");
  });
});

describe("isTextDelta / isReasoningDelta — delta guards", () => {
  it("isTextDelta accepts only text-delta with a string delta", () => {
    expect(isTextDelta({ type: "text-delta", delta: "x" })).toBe(true);
    // missing delta
    expect(isTextDelta({ type: "text-delta" })).toBe(false);
    // wrong type
    expect(isTextDelta({ type: "reasoning-delta", delta: "x" })).toBe(false);
    // non-string delta (e.g. malformed server payload) is not appended
    expect(
      isTextDelta({ type: "text-delta", delta: 5 as unknown as string }),
    ).toBe(false);
  });

  it("isReasoningDelta accepts only reasoning-delta with a string delta", () => {
    expect(isReasoningDelta({ type: "reasoning-delta", delta: "why" })).toBe(
      true,
    );
    expect(isReasoningDelta({ type: "text-delta", delta: "x" })).toBe(false);
    expect(isReasoningDelta({ type: "reasoning-delta" })).toBe(false);
  });

  it("an empty-string delta still counts (a real zero-length token)", () => {
    expect(isTextDelta({ type: "text-delta", delta: "" })).toBe(true);
  });
});

describe("extractGeneratedImage — tool-output-available", () => {
  it("returns the image only when status ok AND url is a string", () => {
    expect(
      extractGeneratedImage({
        type: "tool-output-available",
        output: { status: "ok", url: "https://x/y.png", prompt: "a cat" },
      }),
    ).toEqual({ url: "https://x/y.png", prompt: "a cat" });
  });

  it("drops errored tool outputs (they flow through the model's own text)", () => {
    expect(
      extractGeneratedImage({
        type: "tool-output-available",
        output: { status: "error", url: "https://x/y.png" },
      }),
    ).toBeNull();
  });

  it("drops an ok output with a missing/non-string url", () => {
    expect(
      extractGeneratedImage({
        type: "tool-output-available",
        output: { status: "ok" },
      }),
    ).toBeNull();
  });

  it("returns null for non-image events", () => {
    expect(extractGeneratedImage({ type: "text-delta", delta: "x" })).toBeNull();
    expect(extractGeneratedImage({ type: "finish" })).toBeNull();
  });

  it("preserves an ok image with no prompt (prompt is optional)", () => {
    expect(
      extractGeneratedImage({
        type: "tool-output-available",
        output: { status: "ok", url: "u" },
      }),
    ).toEqual({ url: "u", prompt: undefined });
  });
});

describe("decideStreamCompletion — length vs complete", () => {
  it("finishReason=length → length", () => {
    expect(decideStreamCompletion("length", false)).toBe("length");
  });

  it("finishReason=stop with a balanced artifact → complete", () => {
    expect(decideStreamCompletion("stop", false)).toBe("complete");
  });

  it("finishReason=stop BUT an open artifact tag → length (truncation recovery)", () => {
    // The load-bearing case: provider said stop, but the model left a
    // <boltArtifact>/<boltAction> open → auto-continue closes it.
    expect(decideStreamCompletion("stop", true)).toBe("length");
  });

  it("undefined finishReason with no open tag → complete", () => {
    expect(decideStreamCompletion(undefined, false)).toBe("complete");
  });

  it("undefined finishReason with an open tag → length", () => {
    expect(decideStreamCompletion(undefined, true)).toBe("length");
  });

  it("finishReason=length always wins even with a balanced artifact", () => {
    expect(decideStreamCompletion("length", false)).toBe("length");
  });
});

describe("accumulateUsage — multi-step usage reducer", () => {
  const meta = (
    u: {
      inputTokens?: number;
      outputTokens?: number;
      reasoningTokens?: number;
    },
    model?: string,
  ): NonNullable<SseEvent["messageMetadata"]> => ({ usage: u, model });

  it("seeds from an empty (undefined) prior", () => {
    expect(
      accumulateUsage(undefined, meta({ inputTokens: 10, outputTokens: 3 }, "m")),
    ).toEqual({
      inputTokens: 10,
      outputTokens: 3,
      reasoningTokens: 0,
      model: "m",
    });
  });

  it("sums input/output/reasoning across steps (auto-continuations)", () => {
    const step1 = accumulateUsage(
      undefined,
      meta({ inputTokens: 100, outputTokens: 20, reasoningTokens: 5 }),
    );
    const step2 = accumulateUsage(
      step1,
      meta({ inputTokens: 50, outputTokens: 10, reasoningTokens: 2 }),
    );
    expect(step2).toEqual({
      inputTokens: 150,
      outputTokens: 30,
      reasoningTokens: 7,
      model: undefined,
    });
  });

  it("a later step's model overrides; a missing model keeps the prior", () => {
    const step1 = accumulateUsage(undefined, meta({ inputTokens: 1 }, "gpt"));
    // no model on step 2 → keep "gpt"
    const step2 = accumulateUsage(step1, meta({ inputTokens: 1 }));
    expect(step2.model).toBe("gpt");
    // step 3 provides a new model → override
    const step3 = accumulateUsage(step2, meta({ inputTokens: 1 }, "claude"));
    expect(step3.model).toBe("claude");
  });

  it("treats missing token fields as zero (never NaN)", () => {
    const r = accumulateUsage(undefined, meta({}));
    expect(r).toEqual({
      inputTokens: 0,
      outputTokens: 0,
      reasoningTokens: 0,
      model: undefined,
    });
  });
});

describe("integration — a full SSE chunk driven line-by-line", () => {
  // Feeds a representative chunk through the same split → parse → guard
  // pipeline the reader loop runs, asserting the derived assistant text,
  // reasoning text, collected images, usage, and completion decision.
  it("reconstructs assistant/reasoning/images/usage/finish from a stream", () => {
    const chunk = [
      'data: {"type":"reasoning-delta","delta":"let me think"}',
      'data: {"type":"text-delta","delta":"Hello"}',
      'data: {"type":"text-delta","delta":" world"}',
      ':a comment line the parser ignores',
      'data: {"type":"tool-output-available","output":{"status":"ok","url":"img://1","prompt":"p"}}',
      'data: {not-json}',
      'data: {"type":"message-metadata","messageMetadata":{"usage":{"inputTokens":42,"outputTokens":8},"model":"m"}}',
      'data: {"type":"finish","finishReason":"stop"}',
      "data: [DONE]",
      "", // trailing empty line
    ].join("\n");

    const { lines } = splitSseBuffer(chunk + "\n");

    let assistantText = "";
    let reasoningText = "";
    const images: { url: string; prompt?: string }[] = [];
    let usage:
      | ReturnType<typeof accumulateUsage>
      | undefined;
    let finishReason: string | undefined;
    let sawDone = false;
    let malformed = 0;

    for (const line of lines) {
      const parsed = parseSseLine(line);
      if (parsed.kind === "ignore") continue;
      if (parsed.kind === "done") {
        sawDone = true;
        continue;
      }
      if (parsed.kind === "malformed") {
        malformed++;
        continue;
      }
      const evt = parsed.event;
      if (isTextDelta(evt)) assistantText += evt.delta;
      else if (isReasoningDelta(evt)) reasoningText += evt.delta;
      else if (evt.type === "tool-output-available") {
        const img = extractGeneratedImage(evt);
        if (img) images.push(img);
      } else if (evt.type === "finish") finishReason = evt.finishReason;
      else if (evt.type === "message-metadata" && evt.messageMetadata?.usage) {
        usage = accumulateUsage(usage, evt.messageMetadata);
      }
    }

    expect(assistantText).toBe("Hello world");
    expect(reasoningText).toBe("let me think");
    expect(images).toEqual([{ url: "img://1", prompt: "p" }]);
    expect(usage).toEqual({
      inputTokens: 42,
      outputTokens: 8,
      reasoningTokens: 0,
      model: "m",
    });
    expect(finishReason).toBe("stop");
    expect(sawDone).toBe(true);
    expect(malformed).toBe(1);
    // Balanced text → complete (no open artifact tag).
    expect(decideStreamCompletion(finishReason, false)).toBe("complete");
  });
});

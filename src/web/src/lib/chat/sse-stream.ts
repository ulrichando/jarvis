// Pure SSE-stream parsing helpers extracted from the chat.tsx streaming
// loop (finding #13b: characterization before hook extraction).
//
// The chat component reads /api/chat's response body as a raw byte stream,
// decodes it, splits on newlines, and classifies each `data: …` line into
// one of a handful of event shapes. That classification is PURE — it has no
// dependence on React state — so it lives here where it can be characterized
// in isolation. chat.tsx calls these functions from inside its reader loop;
// the side-effecting dispatch (writing tokens into refs, scheduling flushes)
// stays in the component.
//
// Behavior is a faithful mirror of the previous inline logic. Do not change
// the classification without updating tests/chat/sse-stream.test.ts — the
// chat stream loop has a documented regression history.

import type { UIMessage } from "ai";

// The event shapes the server emits over SSE. `text-delta` carries visible
// reply tokens; `reasoning-delta` carries hidden chain-of-thought;
// `tool-output-available` carries a generateImage result; `finish` carries
// the provider finishReason; `message-metadata` carries per-step usage.
export type SseEvent = {
  type?: string;
  delta?: string;
  finishReason?: string;
  output?: { status?: string; url?: string; prompt?: string };
  messageMetadata?: {
    usage?: {
      inputTokens?: number;
      outputTokens?: number;
      reasoningTokens?: number;
    };
    model?: string;
  };
};

// The result of classifying one raw stream line. `kind` distinguishes the
// three non-event outcomes (a non-`data:` line, the `[DONE]` sentinel, or an
// unparseable payload) from a successfully-parsed `event`. The chat loop
// `continue`s on the first three and dispatches on the fourth.
export type SseLine =
  | { kind: "ignore" } // not a `data: ` line — skip
  | { kind: "done" } // the `[DONE]` sentinel — skip (the loop breaks on reader `done`)
  | { kind: "malformed"; raw: string } // JSON.parse threw — skip
  | { kind: "event"; event: SseEvent };

// Split the decode buffer the same way the reader loop does: break on "\n",
// keep every complete line, and return the trailing partial (which stays in
// the buffer until the next chunk completes it). Mirrors:
//   const lines = buf.split("\n"); buf = lines.pop() ?? "";
export function splitSseBuffer(buf: string): { lines: string[]; rest: string } {
  const lines = buf.split("\n");
  const rest = lines.pop() ?? "";
  return { lines, rest };
}

// Classify one raw line from the stream. Pure mirror of the inline logic:
//   - lines not starting with "data: " are ignored
//   - "data: [DONE]" is the done sentinel
//   - the rest is JSON.parse'd; a throw → malformed (skipped)
export function parseSseLine(line: string): SseLine {
  if (!line.startsWith("data: ")) return { kind: "ignore" };
  const raw = line.slice(6);
  if (raw === "[DONE]") return { kind: "done" };
  try {
    const event = JSON.parse(raw) as SseEvent;
    return { kind: "event", event };
  } catch {
    return { kind: "malformed", raw };
  }
}

// A stream is a text-delta iff it's typed `text-delta` AND carries a string
// delta. Same guard the loop uses before appending to assistantText.
export function isTextDelta(
  evt: SseEvent,
): evt is SseEvent & { delta: string } {
  return evt.type === "text-delta" && typeof evt.delta === "string";
}

// A stream is a reasoning-delta iff typed `reasoning-delta` with a string
// delta. Routed to the collapsible Thoughts panel, not the message body.
export function isReasoningDelta(
  evt: SseEvent,
): evt is SseEvent & { delta: string } {
  return evt.type === "reasoning-delta" && typeof evt.delta === "string";
}

// Extract a successfully-generated image from a tool-output-available event,
// or null when the event isn't a successful image. Mirrors the loop's guard:
//   if (out?.status === "ok" && typeof out.url === "string") …
export function extractGeneratedImage(
  evt: SseEvent,
): { url: string; prompt?: string } | null {
  if (evt.type !== "tool-output-available") return null;
  const out = evt.output;
  if (out?.status === "ok" && typeof out.url === "string") {
    return { url: out.url, prompt: out.prompt };
  }
  return null;
}

// Decide whether the finished stream should auto-continue. The provider's own
// finishReason="length" means it ran out of output budget. But even a "stop"
// finish can leave a `<boltArtifact>`/`<boltAction>` open when the model's
// internal budget ran out without surfacing a length signal — treat that as a
// length finish so the auto-continue loop closes the tag. Mirrors chat.tsx:
//   if (finishReason !== "length" && hasOpenArtifactTag(text)) return "length";
//   return finishReason === "length" ? "length" : "complete";
export function decideStreamCompletion(
  finishReason: string | undefined,
  hasOpenTag: boolean,
): "length" | "complete" {
  if (finishReason !== "length" && hasOpenTag) return "length";
  return finishReason === "length" ? "length" : "complete";
}

// Accumulate per-step usage across a multi-step turn (auto-continuations emit
// several `message-metadata` events for one assistant message). Pure reducer
// mirroring the setUsageById updater in the loop.
export type Usage = {
  inputTokens: number;
  outputTokens: number;
  // Optional to match the chat component's usage-state Map shape (older
  // rows predate reasoning tracking). The reducer always writes a number.
  reasoningTokens?: number;
  model?: string;
};

export function accumulateUsage(
  cur: Usage | undefined,
  incoming: NonNullable<SseEvent["messageMetadata"]>,
): Usage & { reasoningTokens: number } {
  const u = incoming.usage ?? {};
  return {
    inputTokens: (cur?.inputTokens ?? 0) + (u.inputTokens ?? 0),
    outputTokens: (cur?.outputTokens ?? 0) + (u.outputTokens ?? 0),
    reasoningTokens: (cur?.reasoningTokens ?? 0) + (u.reasoningTokens ?? 0),
    model: incoming.model ?? cur?.model,
  };
}

// Re-export the message type so tests importing from this module have the
// same UIMessage the component uses without reaching into `ai` directly.
export type { UIMessage };

import { describe, it, expect } from "vitest";
import type { UIMessage } from "ai";

// Characterization test for finding #5 (chat stage-progression stale closure).
//
// The bug: when a multi-stage build finishes a stage, chat.tsx fires the next
// stage via `queueMicrotask(() => submit(progressPrompt, ...))`. That deferred
// call re-enters the SAME render-closure `submit`, whose captured `messages`
// snapshot predates the stage that just completed (its user prompt + the
// assistant reply carrying the plan were committed via setMessages /
// startTransition and haven't produced a fresh render-closure yet). Because the
// server derives the model's entire context from the request body alone
// (api/chat/route.ts), stage 2 arrived WITHOUT the plan and the model
// restarted / invented a new one ("long builds go off the rails").
//
// The fix threads the finalized stage history into the continuation (the same
// way the auto-continue loop does) via `buildTurnHistory`'s `continuationHistory`
// parameter. These tests pin that seam: with the threaded history the stage-2
// request includes the stage-1 turns; the OLD stale-closure computation drops
// them. Full-component SSE-loop testing is left to the characterization suite a
// later agent adds (the spec's "if component testing is impractical, extract +
// characterize the helper" path).

import { buildTurnHistory } from "@/components/chat/chat";

// A representative stage-1 assistant reply — carries the multi-stage plan the
// model must keep referencing on later stages.
const STAGE1_PLAN =
  '<jarvisplan stages="2">Stage 1: scaffold the app. ' +
  "Stage 2: wire the API and tests.</jarvisplan> Building stage 1 now…";

function userMsg(id: string, text: string): UIMessage {
  return { id, role: "user", parts: [{ type: "text", text }] };
}
function assistantMsg(id: string, text: string): UIMessage {
  return { id, role: "assistant", parts: [{ type: "text", text }] };
}

function joinText(m: UIMessage): string {
  return m.parts
    .map((p) => (p.type === "text" ? p.text : ""))
    .join("");
}

describe("buildTurnHistory — stage-progression continuation", () => {
  // The stale render-closure `messages` captured when the stage-1 turn STARTED.
  // For a fresh multi-stage build this is empty (nothing before stage 1).
  const staleClosureMessages: UIMessage[] = [];

  // The stage-1 request history that submit() actually sent:
  // (prior turns) + the stage-1 user prompt.
  const stage1RequestHistory: UIMessage[] = [
    userMsg("u1", "Build me a two-stage todo app"),
  ];

  // The finalized stage-1 history threaded into the continuation:
  // stage-1 request + the stage-1 assistant reply (the plan).
  const continuationHistory: UIMessage[] = [
    ...stage1RequestHistory,
    assistantMsg("a1", STAGE1_PLAN),
  ];

  // The stage-2 progress prompt submit() appends as the new user message.
  const stage2Prompt = userMsg("u2", "[stage 2 of 2] Continue with the plan.");

  it("stage-2 request includes the stage-1 user prompt AND the assistant plan", () => {
    const history = buildTurnHistory(
      staleClosureMessages,
      stage2Prompt,
      continuationHistory,
    );

    // stage-1 user prompt survives
    expect(history.some((m) => joinText(m).includes("two-stage todo app"))).toBe(
      true,
    );
    // stage-1 assistant PLAN survives — this is the load-bearing bit
    expect(history.some((m) => joinText(m).includes('<jarvisplan stages="2"'))).toBe(
      true,
    );
    // and the new stage-2 prompt is appended last
    expect(history.at(-1)).toBe(stage2Prompt);
    // full ordered shape: [stage1 user, stage1 assistant(plan), stage2 user]
    expect(history.map((m) => m.id)).toEqual(["u1", "a1", "u2"]);
  });

  it("OLD stale-closure behavior would DROP the plan (regression this fix prevents)", () => {
    // Reproduce the pre-fix computation: `[...messages, userMessage]` with the
    // stale render-closure snapshot and NO threaded continuation history.
    const oldHistory = buildTurnHistory(staleClosureMessages, stage2Prompt);

    // The plan is gone → the model would restart / invent a new one.
    expect(
      oldHistory.some((m) => joinText(m).includes('<jarvisplan stages="2"')),
    ).toBe(false);
    // The stage-1 user prompt is gone too.
    expect(
      oldHistory.some((m) => joinText(m).includes("two-stage todo app")),
    ).toBe(false);
    // Only the bare stage-2 prompt reaches the server.
    expect(oldHistory).toEqual([stage2Prompt]);
  });

  it("the threaded history is a strict superset of the old (stale) one", () => {
    const fixed = buildTurnHistory(
      staleClosureMessages,
      stage2Prompt,
      continuationHistory,
    );
    const old = buildTurnHistory(staleClosureMessages, stage2Prompt);
    for (const m of old) {
      expect(fixed).toContain(m);
    }
    expect(fixed.length).toBeGreaterThan(old.length);
  });
});

describe("buildTurnHistory — normal (non-continuation) turns unchanged", () => {
  it("appends the user message to the render-closure messages when no continuation", () => {
    const messages: UIMessage[] = [
      userMsg("u1", "hi"),
      assistantMsg("a1", "hello"),
    ];
    const next = userMsg("u2", "make it blue");

    const history = buildTurnHistory(messages, next);

    expect(history).toEqual([...messages, next]);
    // no mutation of the input array
    expect(messages).toHaveLength(2);
  });

  it("undefined continuation falls back to priorMessages (same as omitting it)", () => {
    const messages: UIMessage[] = [userMsg("u1", "hi")];
    const next = userMsg("u2", "again");

    expect(buildTurnHistory(messages, next, undefined)).toEqual([
      ...messages,
      next,
    ]);
  });

  it("an empty threaded continuation still replaces the (nonempty) stale base", () => {
    // Defensive: `continuationHistory: []` is a deliberate empty base, not a
    // signal to fall back — only `undefined` falls back. This guards the
    // nullish-coalescing choice (?? not ||) so a legitimately-empty threaded
    // history isn't silently swapped for the stale messages.
    const stale: UIMessage[] = [userMsg("stale", "should NOT appear")];
    const next = userMsg("u2", "go");

    const history = buildTurnHistory(stale, next, []);

    expect(history).toEqual([next]);
    expect(history.some((m) => joinText(m).includes("should NOT appear"))).toBe(
      false,
    );
  });
});

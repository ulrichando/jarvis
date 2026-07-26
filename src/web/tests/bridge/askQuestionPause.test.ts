import { describe, expect, test } from "vitest";
import {
  ASK_QUESTION_PAUSE_CLEAR,
  ASK_QUESTION_PENDING_ONLY_CLEAR,
  buildAskQuestionPauseState,
  isAskUserQuestionTool,
  pausedRequestId,
} from "@/lib/bridge/askQuestionPause";

// Mirror of the two predicates in src/components/code/code-session.tsx that
// decide whether the browser renders the QuestionsCard. Replicated here so a
// drift between the server pause payload and the frontend contract fails a test
// instead of silently not rendering the card (the original bug). If these ever
// change in code-session.tsx, update them here too.
type Worker = {
  worker_status?: string;
  external_metadata?: { pending_action?: Record<string, unknown> | null };
  requires_action_details?: Record<string, unknown>;
};
function pendingAction(w: Worker | null) {
  if (!w || w.worker_status !== "requires_action") return null;
  const fromMeta = w.external_metadata?.pending_action ?? undefined;
  const merged = { ...(w.requires_action_details ?? {}), ...(fromMeta ?? {}) };
  if (!merged.request_id) return null;
  return {
    tool_name: (merged.tool_name as string) ?? "tool",
    request_id: merged.request_id as string,
    input: fromMeta?.input as Record<string, unknown> | undefined,
  };
}
function askQuestions(p: ReturnType<typeof pendingAction>) {
  if (!p || p.tool_name !== "AskUserQuestion") return null;
  const qs = (p.input as { questions?: unknown } | undefined)?.questions;
  return Array.isArray(qs) && qs.length ? qs : null;
}

describe("isAskUserQuestionTool", () => {
  test("matches the real name and the snake_case variant", () => {
    expect(isAskUserQuestionTool("AskUserQuestion")).toBe(true);
    expect(isAskUserQuestionTool("ask_user_question")).toBe(true);
    expect(isAskUserQuestionTool("askuserquestion")).toBe(true);
  });
  test("does not match other interactive tools or unrelated tools", () => {
    expect(isAskUserQuestionTool("ExitPlanMode")).toBe(false);
    expect(isAskUserQuestionTool("exit_plan_mode")).toBe(false);
    expect(isAskUserQuestionTool("Bash")).toBe(false);
    expect(isAskUserQuestionTool(undefined)).toBe(false);
  });
});

describe("pause payload → frontend renders the QuestionsCard", () => {
  const input = {
    questions: [
      { question: "What would you like me to fix?", options: [{ label: "A" }] },
    ],
  };

  test("the pause state surfaces the question to the browser", () => {
    const p = pendingAction(buildAskQuestionPauseState("req-1", input) as Worker);
    expect(p).not.toBeNull();
    expect(p?.tool_name).toBe("AskUserQuestion"); // exact — askQuestions compares literally
    expect(p?.request_id).toBe("req-1");
    expect(askQuestions(p)).toHaveLength(1);
  });

  test("non-object input degrades to no questions, never throws", () => {
    const p = pendingAction(buildAskQuestionPauseState("req-2", "oops") as Worker);
    expect(p?.tool_name).toBe("AskUserQuestion");
    expect(askQuestions(p)).toBeNull();
  });
});

describe("clear payload → card disappears, worker resumes", () => {
  test("the clear drops the pending action", () => {
    expect(pendingAction(ASK_QUESTION_PAUSE_CLEAR as Worker)).toBeNull();
    expect((ASK_QUESTION_PAUSE_CLEAR as Worker).worker_status).toBe("running");
  });
});

describe("regression: the old auto-approve behavior surfaced no card", () => {
  test("a running worker with no pending_action → nothing to answer", () => {
    expect(pendingAction({ worker_status: "running" })).toBeNull();
  });
});

describe("pausedRequestId — drives the conditional answer-clear", () => {
  const stateWith = (reqId: string) =>
    JSON.stringify(buildAskQuestionPauseState(reqId, { questions: [] }));

  test("returns the live paused request_id", () => {
    expect(pausedRequestId(stateWith("req-live"))).toBe("req-live");
  });
  test("null/empty/garbage/no-pending → undefined (no false match)", () => {
    expect(pausedRequestId(null)).toBeUndefined();
    expect(pausedRequestId(undefined)).toBeUndefined();
    expect(pausedRequestId("{not json")).toBeUndefined();
    expect(pausedRequestId(JSON.stringify({ worker_status: "running" }))).toBeUndefined();
    expect(
      pausedRequestId(JSON.stringify(ASK_QUESTION_PENDING_ONLY_CLEAR)),
    ).toBeUndefined();
  });
  test("a stale/phantom answer id does NOT match the live id (no stuck spinner)", () => {
    const live = pausedRequestId(stateWith("req-live"));
    expect(live === "req-STALE").toBe(false); // → /messages skips the force-running clear
  });
});

describe("ASK_QUESTION_PENDING_ONLY_CLEAR — abandon without forcing status", () => {
  test("drops the card but does not set worker_status (idle worker stays idle)", () => {
    expect(pendingAction(ASK_QUESTION_PENDING_ONLY_CLEAR as Worker)).toBeNull();
    expect(
      (ASK_QUESTION_PENDING_ONLY_CLEAR as { worker_status?: string })
        .worker_status,
    ).toBeUndefined();
  });
});

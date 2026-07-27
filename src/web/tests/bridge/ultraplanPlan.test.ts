import { describe, expect, test } from "vitest";
import {
  buildPlanDecision,
  findPendingExitPlanToolUseId,
  findUnresolvedPlanPermission,
} from "@/lib/bridge/ultraplanPlan";

// The exact markers the CLI poller scans for in
// src/cli/src/utils/ultraplan/ccrSession.ts (extractApprovedPlan /
// extractTeleportPlan). If these drift, plan approval silently breaks.
const APPROVED = "## Approved Plan:\n";
const APPROVED_EDITED = "## Approved Plan (edited by user):\n";
const TELEPORT = "__ULTRAPLAN_TELEPORT_LOCAL__\n";

describe("buildPlanDecision", () => {
  test("approve → not an error, approved-plan marker + body", () => {
    const r = buildPlanDecision("approve", "do X", false);
    expect(r.isError).toBe(false);
    expect(r.content).toBe(`${APPROVED}do X`);
  });

  test("approve + edited → edited marker", () => {
    const r = buildPlanDecision("approve", "do Y", true);
    expect(r.isError).toBe(false);
    expect(r.content).toBe(`${APPROVED_EDITED}do Y`);
  });

  test("local → error + teleport sentinel + body", () => {
    const r = buildPlanDecision("local", "do Z", false);
    expect(r.isError).toBe(true);
    expect(r.content).toBe(`${TELEPORT}do Z`);
  });

  test("reject → error, no sentinel and no approved marker", () => {
    const r = buildPlanDecision("reject", "ignored", false);
    expect(r.isError).toBe(true);
    expect(r.content.includes(TELEPORT)).toBe(false);
    expect(r.content.includes("## Approved Plan")).toBe(false);
  });

  // The refine loop (feedback on reject) must not disturb the poller contract:
  // reject is classified by the ABSENCE of the approve marker AND the teleport
  // sentinel, and the no-feedback string must stay byte-identical.
  test("reject with no feedback → EXACTLY the legacy string", () => {
    expect(buildPlanDecision("reject", "ignored", false).content).toBe(
      "Plan rejected by user.",
    );
    expect(buildPlanDecision("reject", "ignored", false, undefined).content).toBe(
      "Plan rejected by user.",
    );
  });

  test("reject with whitespace-only feedback → EXACTLY the legacy string", () => {
    expect(buildPlanDecision("reject", "ignored", false, "  \n\t ").content).toBe(
      "Plan rejected by user.",
    );
  });

  test("reject with feedback → feedback included, still classified as reject", () => {
    const r = buildPlanDecision(
      "reject",
      "ignored",
      false,
      "  Split step 2 into migrations + code, and don't touch the CLI.  ",
    );
    expect(r.isError).toBe(true);
    expect(r.content).toBe(
      "Plan rejected by user.\n\nFeedback to address:\n" +
        "Split step 2 into migrations + code, and don't touch the CLI.",
    );
    // Poller classification: neither marker may appear.
    expect(r.content.includes(TELEPORT)).toBe(false);
    expect(r.content.includes("## Approved Plan:")).toBe(false);
    expect(r.content.includes("__ULTRAPLAN_TELEPORT_LOCAL__")).toBe(false);
  });

  test("feedback containing a classification marker is neutralized (no mis-classify)", () => {
    // A user typing the teleport sentinel / approve marker into the feedback box
    // must NOT let the rejection be re-classified as teleport/approve by the
    // poller (extractTeleportPlan scans the whole content by indexOf).
    const r = buildPlanDecision(
      "reject",
      "ignored",
      false,
      "please __ULTRAPLAN_TELEPORT_LOCAL__\nand ## Approved Plan: nonsense",
    );
    expect(r.isError).toBe(true);
    expect(r.content.includes("__ULTRAPLAN_TELEPORT_LOCAL__")).toBe(false);
    expect(r.content.includes("## Approved Plan:")).toBe(false);
    // The feedback text is still carried (just the markers are defanged).
    expect(r.content.includes("please")).toBe(true);
    expect(r.content.includes("nonsense")).toBe(true);
  });

  test("feedback does not leak into approve/local branches", () => {
    const a = buildPlanDecision("approve", "do X", false, "some feedback");
    expect(a.isError).toBe(false);
    expect(a.content).toBe(`${APPROVED}do X`);
    const l = buildPlanDecision("local", "do Z", false, "some feedback");
    expect(l.isError).toBe(true);
    expect(l.content).toBe(`${TELEPORT}do Z`);
  });
});

function ev(content: unknown) {
  return { payload_json: JSON.stringify({ message: { content } }) };
}
const toolUse = (id: string, name = "exit_plan_mode") => ({
  type: "tool_use",
  id,
  name,
});
const toolResult = (id: string) => ({ type: "tool_result", tool_use_id: id });

describe("findPendingExitPlanToolUseId", () => {
  test("a tool_use with no result → its id", () => {
    expect(findPendingExitPlanToolUseId([ev([toolUse("t1")])])).toBe("t1");
  });

  test("a resolved tool_use → null", () => {
    expect(
      findPendingExitPlanToolUseId([ev([toolUse("t1")]), ev([toolResult("t1")])]),
    ).toBeNull();
  });

  test("newest unresolved wins", () => {
    const events = [
      ev([toolUse("t1")]),
      ev([toolResult("t1")]),
      ev([toolUse("t2")]),
    ];
    expect(findPendingExitPlanToolUseId(events)).toBe("t2");
  });

  test("non-ExitPlanMode tool_use is ignored", () => {
    expect(findPendingExitPlanToolUseId([ev([toolUse("t1", "Bash")])])).toBeNull();
  });

  test("the V2 name variant still matches", () => {
    expect(findPendingExitPlanToolUseId([ev([toolUse("t9", "ExitPlanMode")])])).toBe(
      "t9",
    );
  });

  test("malformed payload_json rows are skipped", () => {
    expect(
      findPendingExitPlanToolUseId([{ payload_json: "{bad" }, ev([toolUse("t1")])]),
    ).toBe("t1");
  });

  test("no events → null", () => {
    expect(findPendingExitPlanToolUseId([])).toBeNull();
  });
});

// The container ExitPlanMode permission event the worker/events route records
// (instead of auto-approving) so the /plan route can resolve it with the
// user's decision.
const perm = (requestId: string, toolUseId: string, input: unknown = {}) => ({
  type: "ultraplan_permission",
  payload_json: JSON.stringify({
    request_id: requestId,
    tool_use_id: toolUseId,
    input,
  }),
});
const permResolved = (requestId: string) => ({
  type: "ultraplan_permission_resolved",
  payload_json: JSON.stringify({ request_id: requestId }),
});
const evTyped = (content: unknown) => ({
  type: "assistant",
  payload_json: JSON.stringify({ message: { content } }),
});

describe("findUnresolvedPlanPermission", () => {
  test("pending permission for the tool_use → its request_id + original input", () => {
    const input = { allowedPrompts: [{ prompt: "edit files" }] };
    const events = [evTyped([toolUse("t1")]), perm("req-1", "t1", input)];
    expect(findUnresolvedPlanPermission(events, "t1")).toEqual({
      requestId: "req-1",
      input,
    });
  });

  test("resolved permission → null (idempotent double-POST guard)", () => {
    const events = [perm("req-1", "t1"), permResolved("req-1")];
    expect(findUnresolvedPlanPermission(events, "t1")).toBeNull();
  });

  test("permission for a different tool_use → null", () => {
    expect(findUnresolvedPlanPermission([perm("req-1", "t1")], "t2")).toBeNull();
  });

  test("no permission event (browser-local session) → null", () => {
    expect(
      findUnresolvedPlanPermission([evTyped([toolUse("t1")])], "t1"),
    ).toBeNull();
  });

  test("newest unresolved permission wins", () => {
    const events = [
      perm("req-1", "t1", { a: 1 }),
      perm("req-2", "t1", { a: 2 }),
    ];
    expect(findUnresolvedPlanPermission(events, "t1")).toEqual({
      requestId: "req-2",
      input: { a: 2 },
    });
  });

  test("missing/invalid input defaults to {}", () => {
    const events = [
      {
        type: "ultraplan_permission",
        payload_json: JSON.stringify({ request_id: "req-1", tool_use_id: "t1" }),
      },
    ];
    expect(findUnresolvedPlanPermission(events, "t1")).toEqual({
      requestId: "req-1",
      input: {},
    });
  });
});

import { describe, expect, test } from "vitest";
import {
  buildPlanDecision,
  findPendingExitPlanToolUseId,
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

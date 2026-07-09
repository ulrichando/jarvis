import { describe, expect, test, beforeEach, vi } from "vitest";
import { _resetForTests, getStore } from "@/lib/bridge/db";
import { listInboundSince, listSessionEvents } from "@/lib/bridge/store";

// End-to-end web-side coverage of the /ultraplan teleport-back leg, with the
// REAL route handlers over the in-memory store:
//
//   worker/events (can_use_tool ExitPlanMode) → PAUSE (no auto-approve, #176)
//   POST /plan {local}   → control_response deny + sentinel + interrupt:true
//   POST /plan {reject}  → control_response deny WITHOUT interrupt
//   worker/events (echoed deny tool_result) → persisted for the CLI poller
//   GET /api/v1/sessions/{id}/events → serves the sentinel tool_result
//
// interrupt:true is the 2026-07-09 teleport-back fix: without it the container
// plan-mode agent treats the deny as a revision request and re-calls
// ExitPlanMode within seconds (live capture: 3.5s), so the remote keeps
// working in parallel and the new pending call shadows the teleport
// tool_result in the CLI poller — the plan never returns to the terminal.

vi.mock("@/lib/auth-helpers", () => ({
  getUserId: async () => "00000000-0000-0000-0000-000000000001",
  getUserIdOrSharedLocal: async () => "00000000-0000-0000-0000-000000000001",
}));

const SESSION_ID = "sess_ultra_1";
const SIT = "sit_test_token";
const SENTINEL = "__ULTRAPLAN_TELEPORT_LOCAL__";

beforeEach(() => {
  _resetForTests();
  const store = getStore();
  store.db
    .prepare(
      `INSERT INTO sessions (session_id, environment_id, archived, created_at)
       VALUES (?, NULL, 0, ?)`,
    )
    .run(SESSION_ID, Date.now());
  store.db
    .prepare(
      `UPDATE sessions SET session_token = ?, container_json = '{}', worker_epoch = 1
       WHERE session_id = ?`,
    )
    .run(SIT, SESSION_ID);
  store.db
    .prepare(
      "INSERT INTO bridge_tokens (token, user_id, created_at) VALUES (?, ?, ?)",
    )
    .run("jbr_poller", "00000000-0000-0000-0000-000000000001", Date.now());
});

const ctx = { params: Promise.resolve({ sessionId: SESSION_ID }) };

async function postWorkerEvents(events: unknown[]): Promise<Response> {
  const { POST } = await import(
    "@/app/api/bridge/v1/code/sessions/[sessionId]/worker/events/route"
  );
  return POST(
    new Request("http://127.0.0.1:3000/x", {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        Authorization: `Bearer ${SIT}`,
      },
      body: JSON.stringify({ events: events.map((payload) => ({ payload })) }),
    }),
    ctx,
  );
}

async function postPlanDecision(
  decision: string,
  plan: string,
): Promise<Response> {
  const { POST } = await import(
    "@/app/api/bridge/v1/sessions/[sessionId]/plan/route"
  );
  return POST(
    new Request("http://127.0.0.1:3000/x", {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        Authorization: "Bearer jbr_poller",
      },
      body: JSON.stringify({ decision, plan }),
    }),
    ctx,
  );
}

/** The plan pause: assistant ExitPlanMode tool_use + its can_use_tool. */
async function pauseAtPlan(toolUseId: string, requestId: string): Promise<void> {
  const r = await postWorkerEvents([
    {
      type: "assistant",
      session_id: SESSION_ID,
      uuid: `uuid-${toolUseId}`,
      message: {
        role: "assistant",
        content: [
          {
            type: "tool_use",
            id: toolUseId,
            name: "ExitPlanMode",
            input: { plan: "THE PLAN", planFilePath: "/plans/p.md" },
          },
        ],
      },
    },
    {
      type: "control_request",
      request_id: requestId,
      request: {
        subtype: "can_use_tool",
        tool_name: "ExitPlanMode",
        tool_use_id: toolUseId,
        input: { plan: "THE PLAN", planFilePath: "/plans/p.md" },
      },
    },
  ]);
  expect(r.status).toBe(200);
}

type ControlResponse = {
  type: string;
  response: {
    request_id: string;
    response: { behavior: string; message?: string; interrupt?: boolean };
  };
};

function inboundControlResponses(): ControlResponse[] {
  return listInboundSince(getStore(), SESSION_ID, 0)
    .map((r) => JSON.parse(r.payload_json) as ControlResponse)
    .filter((p) => p.type === "control_response");
}

describe("ultraplan teleport-back flow (container path)", () => {
  test("ExitPlanMode can_use_tool pauses — no auto-approve (PR #176 guard)", async () => {
    await pauseAtPlan("toolu_1", "req_1");
    expect(inboundControlResponses()).toHaveLength(0);
    const perms = listSessionEvents(getStore(), SESSION_ID, 0).filter(
      (e) => e.type === "ultraplan_permission",
    );
    expect(perms).toHaveLength(1);
  });

  test("decision:local → deny + sentinel + interrupt:true (agent stands down)", async () => {
    await pauseAtPlan("toolu_1", "req_1");
    const r = await postPlanDecision("local", "PLAN BODY");
    expect(r.status).toBe(200);
    const responses = inboundControlResponses();
    expect(responses).toHaveLength(1);
    const resp = responses[0].response;
    expect(resp.request_id).toBe("req_1");
    expect(resp.response.behavior).toBe("deny");
    expect(resp.response.message).toBe(`${SENTINEL}\nPLAN BODY`);
    expect(resp.response.interrupt).toBe(true);
  });

  test("decision:reject → deny WITHOUT interrupt (agent revises)", async () => {
    await pauseAtPlan("toolu_1", "req_1");
    const r = await postPlanDecision("reject", "");
    expect(r.status).toBe(200);
    const resp = inboundControlResponses()[0].response;
    expect(resp.response.behavior).toBe("deny");
    expect(resp.response.interrupt).toBeUndefined();
    expect(resp.response.message).not.toContain(SENTINEL);
  });

  test("decision:approve → allow replaying the original input, no interrupt", async () => {
    await pauseAtPlan("toolu_1", "req_1");
    const r = await postPlanDecision("approve", "THE PLAN");
    expect(r.status).toBe(200);
    const resp = inboundControlResponses()[0].response as {
      request_id: string;
      response: { behavior: string; updatedInput?: Record<string, unknown> };
    };
    expect(resp.response.behavior).toBe("allow");
    expect(resp.response.updatedInput).toEqual({
      plan: "THE PLAN",
      planFilePath: "/plans/p.md",
    });
  });

  test("echoed deny tool_result is persisted and served to the CLI poller", async () => {
    await pauseAtPlan("toolu_1", "req_1");
    await postPlanDecision("local", "PLAN BODY");
    // The container CLI's echo of the deny (live capture 2026-07-09: emitted
    // with is_error:true and the raw deny message as content).
    const echo = {
      type: "user",
      session_id: SESSION_ID,
      uuid: "uuid-echo-1",
      message: {
        role: "user",
        content: [
          {
            type: "tool_result",
            tool_use_id: "toolu_1",
            is_error: true,
            content: `${SENTINEL}\nPLAN BODY`,
          },
        ],
      },
    };
    expect((await postWorkerEvents([echo])).status).toBe(200);

    // What pollRemoteSessionEvents receives from the ccr-compat events GET.
    const { GET } = await import("@/app/api/v1/sessions/[sessionId]/events/route");
    const res = await GET(
      new Request("http://127.0.0.1:3000/x?after_id=0", {
        headers: { Authorization: "Bearer jbr_poller" },
      }),
      ctx,
    );
    expect(res.status).toBe(200);
    const { data } = (await res.json()) as { data: Array<Record<string, unknown>> };
    // Apply the poller's own filter (teleport.tsx): typed + session_id present.
    const polled = data.filter(
      (e) => e && typeof e === "object" && "type" in e && "session_id" in e,
    );
    const userEvents = polled.filter((e) => e.type === "user");
    expect(userEvents).toHaveLength(1);
    const block = (
      userEvents[0].message as { content: Array<Record<string, unknown>> }
    ).content[0];
    expect(block.type).toBe("tool_result");
    expect(block.is_error).toBe(true);
    expect(block.content).toBe(`${SENTINEL}\nPLAN BODY`);
  });
});

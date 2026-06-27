import { describe, expect, test } from "vitest";
import { ccrSessionStatus } from "@/lib/bridge/ccrCompat";
import { type SessionRow } from "@/lib/bridge/store";

function row(over: Partial<SessionRow>): SessionRow {
  return {
    session_id: "s",
    environment_id: "e",
    archived: 0,
    created_at: 0,
    archived_at: null,
    title: null,
    session_token: null,
    worker_epoch: 0,
    worker_state_json: null,
    container_json: null,
    pinned: 0,
    read: 0,
    group_id: null,
    autofix: 0,
    autofix_sha: null,
    automerge: 0,
    routine_id: null,
    worker_spec_json: null,
    inbound_floor_seq: 0,
    ...over,
  };
}

describe("ccrSessionStatus", () => {
  test("null session → running", () => {
    expect(ccrSessionStatus(null)).toBe("running");
  });

  test("archived → archived", () => {
    expect(ccrSessionStatus(row({ archived: 1 }))).toBe("archived");
  });

  test("maps the worker's reported status", () => {
    for (const s of ["running", "idle", "requires_action"] as const) {
      expect(
        ccrSessionStatus(
          row({ worker_state_json: JSON.stringify({ worker_status: s }) }),
        ),
      ).toBe(s);
    }
  });

  test("malformed worker_state_json → running", () => {
    expect(ccrSessionStatus(row({ worker_state_json: "{not json" }))).toBe(
      "running",
    );
  });

  test("absent or unknown worker_status → running", () => {
    expect(ccrSessionStatus(row({ worker_state_json: null }))).toBe("running");
    expect(
      ccrSessionStatus(
        row({ worker_state_json: JSON.stringify({ worker_status: "weird" }) }),
      ),
    ).toBe("running");
  });

  test("archived takes precedence over a running worker", () => {
    expect(
      ccrSessionStatus(
        row({
          archived: 1,
          worker_state_json: JSON.stringify({ worker_status: "running" }),
        }),
      ),
    ).toBe("archived");
  });
});

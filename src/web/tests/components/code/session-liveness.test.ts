import { describe, it, expect } from "vitest";
import { shouldAutoOpenSession } from "@/components/code/session-liveness";

describe("shouldAutoOpenSession", () => {
  const replLive = { environment_id: "e1", worker_type: "claude_code_repl", online: true };
  const replDead = { environment_id: "e1", worker_type: "claude_code_repl", online: false };
  const containerAsleep = { environment_id: "e2", worker_type: "container", online: false };
  const session = { session_id: "s1", environment_id: "e1" };

  it("auto-opens a live /remote-control (REPL) session", () => {
    expect(shouldAutoOpenSession(session, replLive)).toBe(true);
  });

  it("does NOT auto-open a dead REPL session — the zombie-session bug", () => {
    // The CLI bridge exited; env went offline. A sticky URL must not reopen the
    // old conversation.
    expect(shouldAutoOpenSession(session, replDead)).toBe(false);
  });

  it("auto-opens a container session even when its sandbox is asleep", () => {
    expect(
      shouldAutoOpenSession({ session_id: "s2", environment_id: "e2" }, containerAsleep),
    ).toBe(true);
  });

  it("does NOT auto-open when the environment is gone/reaped", () => {
    expect(shouldAutoOpenSession(session, undefined)).toBe(false);
  });

  it("does NOT auto-open an unknown session id (not in the list)", () => {
    expect(shouldAutoOpenSession(undefined, replLive)).toBe(false);
  });
});

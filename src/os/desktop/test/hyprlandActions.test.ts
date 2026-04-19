import { test, expect } from "bun:test";
import type { HyprIpc } from "../hyprland/ipc.ts";
import { createActions } from "../hyprland/actions.ts";

function stubIpc(responses: Record<string, string>): { ipc: HyprIpc; calls: string[] } {
  const calls: string[] = [];
  const ipc: HyprIpc = {
    async sendCommand(cmd: string) {
      calls.push(cmd);
      return responses[cmd] ?? "ok";
    },
  };
  return { ipc, calls };
}

test("focus sends the right dispatch", async () => {
  const { ipc, calls } = stubIpc({});
  await createActions(ipc).focus("0xdeadbeef");
  expect(calls).toEqual(["dispatch focuswindow address:0xdeadbeef"]);
});

test("spawn sends dispatch exec", async () => {
  const { ipc, calls } = stubIpc({});
  await createActions(ipc).spawn("firefox");
  expect(calls).toEqual(["dispatch exec firefox"]);
});

test("moveToWorkspace composes the expected command", async () => {
  const { ipc, calls } = stubIpc({});
  await createActions(ipc).moveToWorkspace("0xcafef00d", 3);
  expect(calls).toEqual(["dispatch movetoworkspace 3,address:0xcafef00d"]);
});

test("dispatch passes arbitrary commands through", async () => {
  const { ipc, calls } = stubIpc({});
  await createActions(ipc).dispatch("togglefloating");
  expect(calls).toEqual(["dispatch togglefloating"]);
});

test("listWindows parses JSON response from j/clients", async () => {
  const sample = [{
    address: "0xabc",
    title: "Firefox",
    class: "firefox",
    workspace: { id: 1, name: "1" },
    at: [0, 0],
    size: [1920, 1080],
    focusHistoryID: 0,
  }];
  const { ipc } = stubIpc({ "j/clients": JSON.stringify(sample) });
  const windows = await createActions(ipc).listWindows();
  expect(windows).toHaveLength(1);
  expect(windows[0]!.address).toBe("0xabc");
  expect(windows[0]!.title).toBe("Firefox");
});

test("listWindows throws with helpful error on non-JSON response", async () => {
  const { ipc } = stubIpc({ "j/clients": "not json at all" });
  await expect(createActions(ipc).listWindows()).rejects.toThrow(/failed to parse/);
});

import { test, expect } from "bun:test";
import type { HyprIpc } from "../hyprland/ipc.ts";
import { createHyprlandTool } from "../agent/tools/hyprland.ts";

function stubIpc(respond: (cmd: string) => string): HyprIpc {
  return { async sendCommand(cmd) { return respond(cmd); } };
}

test("hyprland tool forwards focus action", async () => {
  let received = "";
  const tool = createHyprlandTool(() => stubIpc((cmd) => {
    received = cmd;
    return "focused";
  }));
  const result = await tool.run({ action: "focus", args: { address: "0xdead" } });
  expect(result.is_error).toBeFalsy();
  expect(received).toBe("dispatch focuswindow address:0xdead");
  expect(result.output).toBe("focused");
});

test("hyprland tool list_windows returns stringified JSON", async () => {
  const tool = createHyprlandTool(() => stubIpc((cmd) => {
    if (cmd === "j/clients") return JSON.stringify([{ address: "0xabc", title: "X", class: "x", workspace: { id: 1, name: "1" }, at: [0, 0], size: [100, 100], focusHistoryID: 0 }]);
    return "";
  }));
  const result = await tool.run({ action: "list_windows", args: {} });
  expect(result.is_error).toBeFalsy();
  const parsed = JSON.parse(result.output);
  expect(parsed).toHaveLength(1);
  expect(parsed[0].address).toBe("0xabc");
});

test("hyprland tool surfaces IPC errors as is_error", async () => {
  const tool = createHyprlandTool(() => ({
    async sendCommand() { throw new Error("connection refused"); },
  }));
  const result = await tool.run({ action: "focus", args: { address: "0xabc" } });
  expect(result.is_error).toBe(true);
  expect(result.output).toContain("connection refused");
});

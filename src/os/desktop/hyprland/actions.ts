import type { HyprIpc } from "./ipc.ts";

export type HyprWindow = {
  address: string;
  title: string;
  class: string;
  workspace: { id: number; name: string };
  at: [number, number];
  size: [number, number];
  focusHistoryID: number;
};

export type HyprActions = {
  focus(address: string): Promise<string>;
  spawn(exec: string): Promise<string>;
  moveToWorkspace(address: string, workspace: number): Promise<string>;
  listWindows(): Promise<HyprWindow[]>;
  dispatch(cmd: string): Promise<string>;
};

export function createActions(ipc: HyprIpc): HyprActions {
  return {
    focus: (address) => ipc.sendCommand(`dispatch focuswindow address:${address}`),
    spawn: (exec) => ipc.sendCommand(`dispatch exec ${exec}`),
    moveToWorkspace: (address, workspace) =>
      ipc.sendCommand(`dispatch movetoworkspace ${workspace},address:${address}`),
    dispatch: (cmd) => ipc.sendCommand(`dispatch ${cmd}`),
    async listWindows(): Promise<HyprWindow[]> {
      const raw = await ipc.sendCommand("j/clients");
      try {
        return JSON.parse(raw) as HyprWindow[];
      } catch (err) {
        throw new Error(`failed to parse hyprland /clients response: ${String(err)}\n---\n${raw.slice(0, 500)}`);
      }
    },
  };
}

import type { ToolRunner } from "../types.ts";
import { createHyprIpc } from "../../hyprland/ipc.ts";
import { createActions } from "../../hyprland/actions.ts";

type HyprlandInput =
  | { action: "focus"; args: { address: string } }
  | { action: "spawn"; args: { exec: string } }
  | { action: "move_to_workspace"; args: { address: string; workspace: number } }
  | { action: "list_windows"; args?: Record<string, never> }
  | { action: "fullscreen"; args?: { mode?: "maximize" | "fullscreen" } }
  | { action: "dispatch"; args: { cmd: string } };

// Factory so tests can inject a stubbed IPC.
export function createHyprlandTool(ipcFactory: () => import("../../hyprland/ipc.ts").HyprIpc = () => createHyprIpc()): ToolRunner {
  return {
    def: {
      name: "hyprland",
      description: "Control Hyprland window manager. Actions: focus, spawn, move_to_workspace, list_windows, fullscreen, dispatch.",
      input_schema: {
        type: "object",
        properties: {
          action: {
            type: "string",
            enum: ["focus", "spawn", "move_to_workspace", "list_windows", "fullscreen", "dispatch"],
          },
          // args is action-dependent. list_windows needs none; focus/move need address;
          // spawn needs exec; move also needs workspace; dispatch needs cmd. Kept loose to
          // avoid schema validators rejecting valid minimal calls (e.g. list_windows).
          args: { type: "object" },
        },
        required: ["action"],
      },
    },
    async run(input: unknown): Promise<{ output: string; is_error?: boolean }> {
      try {
        const ipc = ipcFactory();
        const actions = createActions(ipc);
        const { action, args } = input as HyprlandInput;
        switch (action) {
          case "focus":
            return { output: await actions.focus(args.address) };
          case "spawn":
            return { output: await actions.spawn(args.exec) };
          case "move_to_workspace":
            return { output: await actions.moveToWorkspace(args.address, args.workspace) };
          case "list_windows": {
            const windows = await actions.listWindows();
            return { output: JSON.stringify(windows, null, 2) };
          }
          case "fullscreen": {
            // Hyprland: `fullscreen 0` = fullscreen (real), `fullscreen 1` = maximize (tile-sized).
            const mode = args?.mode ?? "fullscreen";
            const arg = mode === "maximize" ? "1" : "0";
            return { output: await actions.dispatch(`fullscreen ${arg}`) };
          }
          case "dispatch":
            return { output: await actions.dispatch(args.cmd) };
          default: {
            const exhaustive: never = action;
            return { output: `unknown action: ${String(exhaustive)}`, is_error: true };
          }
        }
      } catch (err) {
        return { output: String(err), is_error: true };
      }
    },
  };
}

export const hyprlandTool: ToolRunner = createHyprlandTool();

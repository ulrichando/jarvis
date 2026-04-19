import { bashTool } from "./bash.ts";
import { hyprlandTool } from "./hyprland.ts";
import type { ToolRegistry } from "../types.ts";

export function defaultTools(): ToolRegistry {
  return {
    [bashTool.def.name]: bashTool,
    [hyprlandTool.def.name]: hyprlandTool,
  };
}

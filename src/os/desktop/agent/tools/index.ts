import { bashTool } from "./bash.ts";
import { hyprlandTool } from "./hyprland.ts";
import { createScreenTool } from "./screen.ts";
import type { ToolRegistry } from "../types.ts";
import type { VisionClient } from "../../providers/types.ts";

export type ToolDeps = {
  visionClient?: VisionClient;
};

export function defaultTools(deps: ToolDeps = {}): ToolRegistry {
  const screenTool = createScreenTool(deps.visionClient);
  return {
    [bashTool.def.name]: bashTool,
    [hyprlandTool.def.name]: hyprlandTool,
    [screenTool.def.name]: screenTool,
  };
}

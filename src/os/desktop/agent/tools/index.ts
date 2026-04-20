import { bashTool } from "./bash.ts";
import { hyprlandTool } from "./hyprland.ts";
import { createScreenTool } from "./screen.ts";
import { createPanelTool } from "./panel.ts";
import { readFileTool, writeFileTool, editFileTool, globTool, grepTool } from "./files.ts";
import { webFetchTool, webSearchTool } from "./web.ts";
import { currentTimeTool } from "./time.ts";
import { sshExecTool, dockerExecTool, distroboxExecTool, envListTool } from "./env.ts";
import type { ToolRegistry } from "../types.ts";
import type { VisionClient } from "../../providers/types.ts";
import type { PanelState } from "../../panels/state.ts";
import type { EventBus } from "../../bridge/events.ts";

export type ToolDeps = {
  visionClient?: VisionClient;
  panelState?: PanelState;
  events?: EventBus;
};

export function defaultTools(deps: ToolDeps = {}): ToolRegistry {
  const screenTool = createScreenTool(deps.visionClient);
  const registry: ToolRegistry = {
    [bashTool.def.name]: bashTool,
    [hyprlandTool.def.name]: hyprlandTool,
    [screenTool.def.name]: screenTool,
    [readFileTool.def.name]: readFileTool,
    [writeFileTool.def.name]: writeFileTool,
    [editFileTool.def.name]: editFileTool,
    [globTool.def.name]: globTool,
    [grepTool.def.name]: grepTool,
    [webFetchTool.def.name]: webFetchTool,
    [webSearchTool.def.name]: webSearchTool,
    [currentTimeTool.def.name]: currentTimeTool,
    [sshExecTool.def.name]: sshExecTool,
    [dockerExecTool.def.name]: dockerExecTool,
    [distroboxExecTool.def.name]: distroboxExecTool,
    [envListTool.def.name]: envListTool,
  };
  if (deps.panelState && deps.events) {
    const panelTool = createPanelTool(deps.panelState, deps.events);
    registry[panelTool.def.name] = panelTool;
  }
  return registry;
}

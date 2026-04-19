import { bashTool } from "./bash.ts";
import type { ToolRegistry } from "../types.ts";

export function defaultTools(): ToolRegistry {
  return {
    [bashTool.def.name]: bashTool,
  };
}

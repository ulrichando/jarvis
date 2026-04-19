export type { Message, ToolDef, LLMResponse, ContentBlock } from "../providers/types.ts";

export type ToolRunner = {
  def: import("../providers/types.ts").ToolDef;
  run(input: unknown): Promise<{ output: string; is_error?: boolean }>;
};

export type ToolRegistry = Record<string, ToolRunner>;

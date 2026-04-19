export type ContentBlock =
  | { type: "text"; text: string }
  | { type: "tool_use"; id: string; name: string; input: unknown }
  | { type: "tool_result"; tool_use_id: string; content: string; is_error?: boolean };

export type Message =
  | { role: "user"; content: string | ContentBlock[] }
  | { role: "assistant"; content: string | ContentBlock[] }
  | { role: "system"; content: string };

export type ToolDef = {
  name: string;
  description: string;
  input_schema: Record<string, unknown>;
};

export type LLMResponse = {
  content: ContentBlock[];
  stop_reason: "end_turn" | "tool_use" | "max_tokens" | "stop_sequence";
};

export interface LLMClient {
  name: string;
  complete(params: { model: string; messages: Message[]; tools?: ToolDef[]; system?: string }): Promise<LLMResponse>;
}

export interface VisionClient {
  name: string;
  /** Describe/answer about an image. `image` is JPEG bytes base64-encoded. */
  describe(params: { imageBase64: string; prompt: string; model?: string }): Promise<string>;
}

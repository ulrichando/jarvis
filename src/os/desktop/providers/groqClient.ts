// Groq client — uses Groq's OpenAI-compatible endpoint (Groq does NOT expose an
// Anthropic-compatible endpoint). Translates between our internal Anthropic-shaped
// ContentBlock union and OpenAI's chat-completions / tool-call format at the boundary.

import type { ContentBlock, LLMClient, LLMResponse, Message, ToolDef } from "./types.ts";

type GroqOpts = { apiKey: string; fetchFn?: typeof fetch };

const ENDPOINT = "https://api.groq.com/openai/v1/chat/completions";

// OpenAI wire types (minimal — only what we use).
type OAIToolCall = { id: string; type: "function"; function: { name: string; arguments: string } };
type OAIMessage =
  | { role: "system"; content: string }
  | { role: "user"; content: string }
  | { role: "assistant"; content: string | null; tool_calls?: OAIToolCall[] }
  | { role: "tool"; tool_call_id: string; content: string };
type OAITool = { type: "function"; function: { name: string; description: string; parameters: Record<string, unknown> } };
type OAIResponse = {
  choices: Array<{
    message: { role: "assistant"; content: string | null; tool_calls?: OAIToolCall[] };
    finish_reason: "stop" | "tool_calls" | "length" | "content_filter" | string;
  }>;
};

export function createGroqClient(opts: GroqOpts): LLMClient {
  const f = opts.fetchFn ?? fetch;
  return {
    name: "groq",
    async complete({ model, messages, tools, system }): Promise<LLMResponse> {
      const systemText = system ?? extractSystem(messages);
      const oaiMessages = buildOpenAIMessages(messages, systemText);
      const body: Record<string, unknown> = {
        model,
        messages: oaiMessages,
        max_tokens: 4096,
      };
      if (tools && tools.length > 0) body.tools = tools.map(toOAITool);

      const resp = await f(ENDPOINT, {
        method: "POST",
        headers: {
          "content-type": "application/json",
          authorization: `Bearer ${opts.apiKey}`,
        },
        body: JSON.stringify(body),
      });
      if (!resp.ok) {
        const errText = await resp.text();
        throw new Error(`groq chat.completions failed (${resp.status}): ${errText.slice(0, 500)}`);
      }
      const data = (await resp.json()) as OAIResponse;
      const choice = data.choices[0];
      if (!choice) throw new Error("groq response had no choices");

      const content: ContentBlock[] = [];
      if (choice.message.content) content.push({ type: "text", text: choice.message.content });
      if (choice.message.tool_calls) {
        for (const tc of choice.message.tool_calls) {
          let input: unknown = {};
          try { input = JSON.parse(tc.function.arguments); } catch { input = { _raw: tc.function.arguments }; }
          content.push({ type: "tool_use", id: tc.id, name: tc.function.name, input });
        }
      }

      return { content, stop_reason: mapFinishReason(choice.finish_reason) };
    },
  };
}

function extractSystem(messages: Message[]): string | undefined {
  const sys = messages.find((m) => m.role === "system");
  return sys && typeof sys.content === "string" ? sys.content : undefined;
}

function buildOpenAIMessages(messages: Message[], system: string | undefined): OAIMessage[] {
  const out: OAIMessage[] = [];
  if (system) out.push({ role: "system", content: system });

  for (const m of messages) {
    if (m.role === "system") continue;

    if (typeof m.content === "string") {
      if (m.role === "assistant") out.push({ role: "assistant", content: m.content });
      else out.push({ role: "user", content: m.content });
      continue;
    }

    if (m.role === "assistant") {
      const textParts: string[] = [];
      const toolCalls: OAIToolCall[] = [];
      for (const b of m.content) {
        if (b.type === "text") textParts.push(b.text);
        else if (b.type === "tool_use") {
          toolCalls.push({ id: b.id, type: "function", function: { name: b.name, arguments: JSON.stringify(b.input ?? {}) } });
        }
      }
      const msg: Extract<OAIMessage, { role: "assistant" }> = {
        role: "assistant",
        content: textParts.length > 0 ? textParts.join("\n") : null,
      };
      if (toolCalls.length > 0) msg.tool_calls = toolCalls;
      out.push(msg);
      continue;
    }

    // user role with blocks — blocks are typically tool_result from the agent loop.
    // Split each tool_result into its own {role:"tool"} message per OpenAI format.
    // Any stray text blocks get collapsed into one user message.
    const userText: string[] = [];
    for (const b of m.content) {
      if (b.type === "tool_result") {
        out.push({ role: "tool", tool_call_id: b.tool_use_id, content: b.content });
      } else if (b.type === "text") {
        userText.push(b.text);
      }
    }
    if (userText.length > 0) out.push({ role: "user", content: userText.join("\n") });
  }
  return out;
}

function toOAITool(t: ToolDef): OAITool {
  return {
    type: "function",
    function: {
      name: t.name,
      description: t.description,
      parameters: t.input_schema,
    },
  };
}

function mapFinishReason(fr: string): LLMResponse["stop_reason"] {
  switch (fr) {
    case "tool_calls": return "tool_use";
    case "length": return "max_tokens";
    case "content_filter":
    case "stop":
    default: return "end_turn";
  }
}

import Anthropic from "@anthropic-ai/sdk";
import type { LLMClient, LLMResponse, Message } from "./types.ts";

type GroqOpts = { apiKey: string };

export function createGroqClient(opts: GroqOpts): LLMClient {
  const anthropic = new Anthropic({
    apiKey: opts.apiKey,
    baseURL: "https://api.groq.com/anthropic/v1",
  });
  return {
    name: "groq",
    async complete({ model, messages, tools, system }): Promise<LLMResponse> {
      const systemText = system ?? extractSystem(messages);
      const nonSystem = messages.filter((m) => m.role !== "system") as Exclude<Message, { role: "system" }>[];

      const resp = await anthropic.messages.create({
        model,
        max_tokens: 4096,
        system: systemText,
        messages: nonSystem.map(toAnthropicMessage),
        tools: tools?.map((t) => ({ name: t.name, description: t.description, input_schema: t.input_schema as Anthropic.Tool["input_schema"] })),
      });

      return {
        content: resp.content.map(fromAnthropicBlock),
        stop_reason: (resp.stop_reason ?? "end_turn") as LLMResponse["stop_reason"],
      };
    },
  };
}

function extractSystem(messages: Message[]): string | undefined {
  const sys = messages.find((m) => m.role === "system");
  return sys && typeof sys.content === "string" ? sys.content : undefined;
}

function toAnthropicMessage(m: Exclude<Message, { role: "system" }>): Anthropic.MessageParam {
  if (typeof m.content === "string") {
    return { role: m.role, content: m.content };
  }
  return { role: m.role, content: m.content.map(toAnthropicBlock) };
}

function toAnthropicBlock(b: { type: string } & Record<string, unknown>): Anthropic.ContentBlockParam {
  return b as unknown as Anthropic.ContentBlockParam;
}

function fromAnthropicBlock(b: Anthropic.ContentBlock): LLMResponse["content"][number] {
  if (b.type === "text") return { type: "text", text: b.text };
  if (b.type === "tool_use") return { type: "tool_use", id: b.id, name: b.name, input: b.input };
  throw new Error(`unexpected anthropic block type: ${b.type}`);
}

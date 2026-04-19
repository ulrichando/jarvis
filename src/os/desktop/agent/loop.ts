import type { LLMClient, Message, ContentBlock } from "../providers/types.ts";
import type { ToolRegistry } from "./types.ts";
import { gate, type ConfirmCallback } from "../risk/gate.ts";

const MAX_ITERATIONS = 10;

export type AgentRunResult = {
  messages: Message[];
  stop_reason: "end_turn" | "max_iterations";
  blocked: { tool: string; input: unknown; reason: string }[];
};

export type RunOpts = {
  client: LLMClient;
  model: string;
  messages: Message[];
  tools: ToolRegistry;
  system?: string;
  confirm?: ConfirmCallback;
};

export async function runAgent(opts: RunOpts): Promise<AgentRunResult> {
  const messages: Message[] = [...opts.messages];
  const blocked: AgentRunResult["blocked"] = [];
  const toolDefs = Object.values(opts.tools).map((t) => t.def);

  for (let i = 0; i < MAX_ITERATIONS; i++) {
    const resp = await opts.client.complete({
      model: opts.model,
      messages,
      tools: toolDefs,
      system: opts.system,
    });

    // Record the assistant turn.
    messages.push({ role: "assistant", content: resp.content });

    if (resp.stop_reason !== "tool_use") {
      return { messages, stop_reason: "end_turn", blocked };
    }

    const toolUses = resp.content.filter((b): b is Extract<ContentBlock, { type: "tool_use" }> => b.type === "tool_use");
    if (toolUses.length === 0) {
      // Malformed upstream: stop_reason was "tool_use" but no tool_use blocks present.
      // Don't push an empty user content array (API rejects); treat as end_turn.
      return { messages, stop_reason: "end_turn", blocked };
    }
    const toolResults: ContentBlock[] = [];

    for (const use of toolUses) {
      const decision = await gate(use.name, use.input, { confirm: opts.confirm });
      if (!decision.allow) {
        blocked.push({ tool: use.name, input: use.input, reason: decision.reason });
        toolResults.push({
          type: "tool_result",
          tool_use_id: use.id,
          content: `[blocked] ${decision.reason}`,
          is_error: true,
        });
        continue;
      }
      const runner = opts.tools[use.name];
      if (!runner) {
        toolResults.push({
          type: "tool_result",
          tool_use_id: use.id,
          content: `unknown tool: ${use.name}`,
          is_error: true,
        });
        continue;
      }
      const out = await runner.run(use.input);
      toolResults.push({
        type: "tool_result",
        tool_use_id: use.id,
        content: out.output,
        is_error: out.is_error,
      });
    }

    // Feed results back to the model as a user turn.
    messages.push({ role: "user", content: toolResults });
  }

  return { messages, stop_reason: "max_iterations", blocked };
}

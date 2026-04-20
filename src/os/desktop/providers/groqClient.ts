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

      // llama-3.3 occasionally emits <function=...> literals instead of OpenAI
      // tool_calls, causing Groq to return tool_use_failed. Retry up to 2x with
      // a small delay before giving up.
      let resp!: Response;
      let errText = "";
      for (let attempt = 0; attempt < 3; attempt++) {
        resp = await f(ENDPOINT, {
          method: "POST",
          headers: {
            "content-type": "application/json",
            authorization: `Bearer ${opts.apiKey}`,
          },
          body: JSON.stringify(body),
        });
        if (resp.ok) break;
        errText = await resp.text();
        const retriable = resp.status === 400 && /tool_use_failed/.test(errText);
        if (!retriable || attempt === 2) break;
        await new Promise((r) => setTimeout(r, 200 * (attempt + 1)));
      }
      if (!resp.ok) {
        // llama on Groq sometimes emits <function=name {...}</function> literals
        // that Groq's strict validator rejects. The failed_generation field
        // contains exactly what the model tried to say — extract the tool call
        // from it and return a synthetic success response so the agent loop
        // continues. The alternative is repeatedly failing the same turn.
        const salvaged = salvageToolCall(errText);
        if (salvaged) {
          return { content: [salvaged], stop_reason: "tool_use" };
        }
        throw new Error(`groq chat.completions failed (${resp.status}): ${errText.slice(0, 500)}`);
      }
      const data = (await resp.json()) as OAIResponse;
      const choice = data.choices[0];
      if (!choice) throw new Error("groq response had no choices");

      const content: ContentBlock[] = [];
      let stopReason = mapFinishReason(choice.finish_reason);
      if (choice.message.content) {
        // llama sometimes ignores tools and outputs <function=name {...}</function>
        // as raw text. Extract those as tool_use blocks so the agent loop
        // actually runs the call instead of speaking the function syntax.
        const extracted = extractInlineToolCalls(choice.message.content);
        if (extracted.length > 0) {
          for (const tu of extracted) content.push(tu);
          stopReason = "tool_use";
        } else {
          content.push({ type: "text", text: choice.message.content });
        }
      }
      if (choice.message.tool_calls) {
        for (const tc of choice.message.tool_calls) {
          let input: unknown = {};
          try { input = JSON.parse(tc.function.arguments); } catch { input = { _raw: tc.function.arguments }; }
          content.push({ type: "tool_use", id: tc.id, name: tc.function.name, input });
        }
      }

      return { content, stop_reason: stopReason };
    },
  };
}

/**
 * Parse Groq's tool_use_failed error body and extract the tool call llama tried
 * to emit. The failed_generation can take several forms:
 *   <function=bash {"command": "ls"}</function>
 *   <function=glob {"pattern": "*.ts"}>
 *   <function=web_search>{"query": "x"}</function>
 * Returns a tool_use ContentBlock or null if the format doesn't match.
 *
 * Also handles schema mismatches: the salvaged tool's own runner will coerce
 * string-typed numbers (e.g. {"limit": "1"}) that Groq's strict validator
 * rejects. So we're permissive about input types here.
 */
/**
 * Extract all <function=name {args}>/<function\name "{args}"></function> patterns
 * from a string and return them as tool_use blocks. Used when llama on Groq
 * ignores the native tool-call channel and speaks the call as regular text.
 */
export function extractInlineToolCalls(text: string): Extract<ContentBlock, { type: "tool_use" }>[] {
  const results: Extract<ContentBlock, { type: "tool_use" }>[] = [];
  // Match either <function=name ...{args}... or <function\name "...{args}..."
  // The args payload is the first {…} block after the name.
  const re = /<function[=\\]([a-zA-Z_][\w-]*)[^{]*(\{[\s\S]*?\})/g;
  let m: RegExpExecArray | null;
  let i = 0;
  while ((m = re.exec(text)) !== null) {
    let input: unknown;
    try { input = JSON.parse(m[2]!); } catch { continue; }
    results.push({ type: "tool_use", id: `inline_${Date.now().toString(36)}_${i++}`, name: m[1]!, input });
  }
  return results;
}

export function salvageToolCall(errText: string): ContentBlock | null {
  try {
    const body = JSON.parse(errText) as { error?: { code?: string; failed_generation?: string } };
    if (body.error?.code !== "tool_use_failed") return null;
    const raw = body.error?.failed_generation;
    if (typeof raw !== "string") return null;
    // Match the function name, then the first JSON object that follows (possibly
    // separated by a '>' or whitespace), regardless of where </function> falls.
    const m = raw.match(/<function=([a-zA-Z_][\w-]*)[^{]*(\{[\s\S]*?\})/);
    if (!m) return null;
    const name = m[1]!;
    let input: unknown;
    try { input = JSON.parse(m[2]!); } catch { return null; }
    const id = `salvaged_${Date.now().toString(36)}`;
    return { type: "tool_use", id, name, input };
  } catch {
    return null;
  }
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

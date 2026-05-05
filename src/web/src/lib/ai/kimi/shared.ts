import "server-only";
import type { UIMessage } from "ai";
import type { LanguageModel } from "ai";
import { resolveApiKey } from "@/lib/ai/models";
import { createOpenAICompatible } from "@ai-sdk/openai-compatible";

// Shared discriminated UI parts emitted by K2.6 mode handlers.
// message.tsx switches on `type` to render the right component.
export type KimiReasoningPart = {
  type: "kimi-reasoning";
  delta: string;
};

export type KimiToolTracePart = {
  type: "kimi-tool-trace";
  toolName: string;
  phase: "call" | "result";
  data: unknown;
};

export type KimiSwarmStatusPart = {
  type: "kimi-swarm-status";
  total: number;
  completed: number;
  current?: string;
};

export type KimiUIPart =
  | KimiReasoningPart
  | KimiToolTracePart
  | KimiSwarmStatusPart;

const KIMI_BASE_URL = "https://api.moonshot.ai/v1";
const KIMI_API_MODEL = "kimi-k2.6";

export function kimiModesEnabled(): boolean {
  return process.env.KIMI_K2_MODES_ENABLED === "1";
}

export class KimiKeyMissingError extends Error {
  constructor() {
    super("KIMI_API_KEY not configured");
    this.name = "KimiKeyMissingError";
  }
}

export async function buildKimiClient(): Promise<{
  model: LanguageModel;
  apiKey: string;
  baseURL: string;
}> {
  const { apiKey, baseURL } = await resolveApiKey("kimi");
  if (!apiKey) throw new KimiKeyMissingError();
  const url = baseURL ?? KIMI_BASE_URL;
  const factory = createOpenAICompatible({
    name: "kimi",
    apiKey,
    baseURL: url,
  });
  return {
    model: factory(KIMI_API_MODEL) as LanguageModel,
    apiKey,
    baseURL: url,
  };
}

// Drop image/file parts (K2.6 is text-only — vision goes through the
// kimi-vision-* models on a different path) and any messages that end
// up empty after stripping.
export function extractMessagesForKimi(msgs: UIMessage[]): UIMessage[] {
  const out: UIMessage[] = [];
  for (const m of msgs) {
    const textParts = m.parts.filter((p) => p.type === "text");
    if (textParts.length === 0) continue;
    out.push({ ...m, parts: textParts });
  }
  return out;
}

export function formatKimiError(
  err: unknown,
  opts: { retryAfterSeconds?: number } = {},
): Response {
  const e = err as Error & { status?: number };
  const status = e.status ?? 502;
  const message = e.message ?? "Kimi request failed";
  const headers: Record<string, string> = {
    "Content-Type": "text/event-stream",
    "Cache-Control": "no-store",
  };
  if (opts.retryAfterSeconds !== undefined) {
    headers["Retry-After"] = String(opts.retryAfterSeconds);
  }
  const body = [
    `data: ${JSON.stringify({
      type: "kimi-error",
      status,
      message,
      retryAfter: opts.retryAfterSeconds,
    })}\n\n`,
    `data: [DONE]\n\n`,
  ].join("");
  return new Response(body, { status, headers });
}

export function loadKimiPersona(opts: { suffix?: string } = {}): string {
  const base = `You are JARVIS, an advanced AI assistant. Answer concisely. \
For complex questions, use markdown with headings, lists, and tables. \
Skip greetings and filler.`;
  return opts.suffix ? `${base}\n\n${opts.suffix}` : base;
}

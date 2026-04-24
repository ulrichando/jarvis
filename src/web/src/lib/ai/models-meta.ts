/**
 * Client-safe model metadata. No SDK imports — safe to ship to the browser.
 * Server uses `@/lib/ai/models` which resolves these ids to LanguageModel instances.
 */

export type Provider =
  | "anthropic"
  | "openai"
  | "google"
  | "deepseek"
  | "kimi"
  | "groq";

export type ModelMeta = {
  id: string;
  label: string;
  description: string;
  provider: Provider;
  contextWindow: number;
  /** Small pill badge in the model picker (e.g., "Beta", "New"). */
  badge?: string;
};

export const PROVIDER_LABEL: Record<Provider, string> = {
  anthropic: "Anthropic",
  openai: "OpenAI",
  google: "Google",
  deepseek: "DeepSeek",
  kimi: "Kimi",
  groq: "Groq",
};

export const MODELS_META: Record<string, ModelMeta> = {
  "claude-opus-4-7": {
    id: "claude-opus-4-7",
    label: "Claude Opus 4.7",
    description: "Most capable Claude. Deep reasoning, long tasks.",
    provider: "anthropic",
    contextWindow: 1_000_000,
  },
  "claude-sonnet-4-6": {
    id: "claude-sonnet-4-6",
    label: "Claude Sonnet 4.6",
    description: "Balanced Claude. Great everyday default.",
    provider: "anthropic",
    contextWindow: 200_000,
  },
  "claude-haiku-4-5": {
    id: "claude-haiku-4-5",
    label: "Claude Haiku 4.5",
    description: "Fastest Claude. Short exchanges, cheap.",
    provider: "anthropic",
    contextWindow: 200_000,
  },

  "gpt-5": {
    id: "gpt-5",
    label: "GPT-5",
    description: "OpenAI flagship. Strong general reasoning.",
    provider: "openai",
    contextWindow: 400_000,
  },
  "gpt-5-mini": {
    id: "gpt-5-mini",
    label: "GPT-5 mini",
    description: "Smaller, faster GPT-5.",
    provider: "openai",
    contextWindow: 400_000,
  },
  "o3": {
    id: "o3",
    label: "o3",
    description: "Reasoning-first OpenAI model.",
    provider: "openai",
    contextWindow: 200_000,
  },

  "gemini-2.5-pro": {
    id: "gemini-2.5-pro",
    label: "Gemini 2.5 Pro",
    description: "Google flagship. Big context, multimodal.",
    provider: "google",
    contextWindow: 2_000_000,
  },
  "gemini-2.5-flash": {
    id: "gemini-2.5-flash",
    label: "Gemini 2.5 Flash",
    description: "Fast Google model.",
    provider: "google",
    contextWindow: 1_000_000,
  },

  "deepseek-chat": {
    id: "deepseek-chat",
    label: "DeepSeek V3",
    description: "Strong open-weight general model.",
    provider: "deepseek",
    contextWindow: 128_000,
  },
  "deepseek-reasoner": {
    id: "deepseek-reasoner",
    label: "DeepSeek R1",
    description: "Reasoning-focused DeepSeek.",
    provider: "deepseek",
    contextWindow: 128_000,
  },

  "kimi-k2-instant": {
    id: "kimi-k2-instant",
    label: "K2.6 Instant",
    description: "Quick response.",
    provider: "kimi",
    contextWindow: 256_000,
  },
  "kimi-k2-thinking": {
    id: "kimi-k2-thinking",
    label: "K2.6 Thinking",
    description: "Deep thinking for complex questions.",
    provider: "kimi",
    contextWindow: 256_000,
  },
  "kimi-k2-agent": {
    id: "kimi-k2-agent",
    label: "K2.6 Agent",
    description: "Research, slides, websites, docs, sheets.",
    provider: "kimi",
    contextWindow: 256_000,
  },
  "kimi-k2-swarm": {
    id: "kimi-k2-swarm",
    label: "K2.6 Agent Swarm",
    description: "Large-scale search, long-form writing, batch tasks.",
    provider: "kimi",
    contextWindow: 256_000,
    badge: "Beta",
  },

  "llama-3.3-70b": {
    id: "llama-3.3-70b",
    label: "Llama 3.3 70B (Groq)",
    description: "Fast Llama on Groq. Free tier.",
    provider: "groq",
    contextWindow: 128_000,
  },
  "kimi-k2-groq": {
    id: "kimi-k2-groq",
    label: "Kimi K2 (Groq)",
    description: "Kimi hosted on Groq. Fast + free tier.",
    provider: "groq",
    contextWindow: 256_000,
  },
  "qwen-qwq-32b": {
    id: "qwen-qwq-32b",
    label: "Qwen QwQ 32B (Groq)",
    description: "Reasoning model on Groq. Free tier.",
    provider: "groq",
    contextWindow: 128_000,
  },
};

export type ModelId = keyof typeof MODELS_META;
export const DEFAULT_MODEL: ModelId = "claude-sonnet-4-6";

export function modelsByProvider(): Array<{
  provider: Provider;
  label: string;
  models: ModelMeta[];
}> {
  const order: Provider[] = [
    "anthropic",
    "openai",
    "google",
    "groq",
    "deepseek",
    "kimi",
  ];
  return order.map((p) => ({
    provider: p,
    label: PROVIDER_LABEL[p],
    models: Object.values(MODELS_META).filter((m) => m.provider === p),
  }));
}

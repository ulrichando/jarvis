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
  | "ollama";

export type ModelMeta = {
  id: string;
  label: string;
  description: string;
  provider: Provider;
  contextWindow: number;
  /** Small pill badge in the model picker (e.g., "Beta", "New"). */
  badge?: string;
  /** Reasoning-mode model: spends most of its output budget on hidden
   *  thinking tokens. Great for analysis tasks but BAD for code-heavy
   *  generation (design, workbench) where every output token should be
   *  going to the actual artifact. The chat route auto-substitutes a
   *  non-reasoning sibling for design mode. */
  reasoning?: boolean;
  /** Sibling model id to fall back to when reasoning is unsuitable
   *  (e.g. design mode). Same provider, no thinking-token overhead. */
  nonReasoningFallback?: string;
};

// Conservative vision check — used to warn when an image is attached to a
// text-only model (otherwise the model just replies "I don't see a picture"
// and the user has no idea why). Errs toward "no" so we warn rather than
// silently fail: the big multimodal providers (Anthropic/OpenAI/Google) are
// trusted; Kimi is pattern-gated; DeepSeek/Ollama assumed text-only.
export function modelSupportsVision(id: string): boolean {
  const lid = id.toLowerCase();
  const provider = MODELS_META[id]?.provider;
  if (provider === "anthropic" || provider === "openai" || provider === "google")
    return true;
  if (provider === "kimi") return lid.includes("vision");
  return false; // deepseek, ollama, unknown → assume text-only
}

export const PROVIDER_LABEL: Record<Provider, string> = {
  anthropic: "Anthropic",
  openai: "OpenAI",
  google: "Google",
  deepseek: "DeepSeek",
  kimi: "Kimi",
  ollama: "Local (Ollama)",
};

export const MODELS_META: Record<string, ModelMeta> = {
  "claude-fable-5": {
    id: "claude-fable-5",
    label: "Fable 5",
    description: "Newest Claude. Built for long-running, complex agentic work.",
    provider: "anthropic",
    contextWindow: 1_000_000,
    badge: "New",
  },
  "claude-opus-4-8": {
    id: "claude-opus-4-8",
    label: "Claude Opus 4.8",
    description: "Most capable Claude. Deep reasoning, long agentic tasks.",
    provider: "anthropic",
    contextWindow: 1_000_000,
  },
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
    reasoning: true,
    nonReasoningFallback: "gpt-5-mini",
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
    reasoning: true,
    nonReasoningFallback: "deepseek-chat",
  },
  "deepseek-v4-pro": {
    id: "deepseek-v4-pro",
    label: "DeepSeek V4 Pro",
    description: "Top model. Used as the JARVIS CLI tool model and the design default.",
    provider: "deepseek",
    contextWindow: 128_000,
    // NOT flagged as reasoning. With 16K maxOutputTokens, v4-pro produces
    // full multi-file artifacts in a single shot even with its thinking
    // overhead. Substituting it to v4-flash was overcautious — restore
    // the user's explicit pick.
  },
  "deepseek-v4-flash": {
    id: "deepseek-v4-flash",
    label: "DeepSeek V4 Flash",
    description: "Fast variant for low-latency work.",
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
    reasoning: true,
    nonReasoningFallback: "kimi-k2-instant",
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

  "kimi-vision-8k": {
    id: "kimi-vision-8k",
    label: "Kimi Vision 8k",
    description: "Vision-capable Moonshot model. Compact context.",
    provider: "kimi",
    contextWindow: 8_000,
  },
  "kimi-vision-32k": {
    id: "kimi-vision-32k",
    label: "Kimi Vision 32k",
    description: "Vision-capable. Sweet-spot context for screen + page grounding.",
    provider: "kimi",
    contextWindow: 32_000,
  },
  "kimi-vision-128k": {
    id: "kimi-vision-128k",
    label: "Kimi Vision 128k",
    description: "Vision-capable. Long context — multi-image reasoning, doc + screen analysis.",
    provider: "kimi",
    contextWindow: 128_000,
  },

  // Local (Ollama) — on-device, no API key. Served from the local ollama
  // daemon at :11434. qwen3-30b-a3b is the CPU sweet spot (MoE, ~3B active);
  // gpt-oss-120b is heavier + slower on CPU.
  "ollama-qwen3-30b-a3b": {
    id: "ollama-qwen3-30b-a3b",
    label: "Qwen3 30B-A3B (Local)",
    description: "On-device via Ollama. MoE, fast on CPU.",
    provider: "ollama",
    contextWindow: 40_000,
  },
  "ollama-gpt-oss-120b": {
    id: "ollama-gpt-oss-120b",
    label: "gpt-oss 120B (Local)",
    description: "On-device via Ollama. Heavy, slow on CPU.",
    provider: "ollama",
    contextWindow: 128_000,
  },
};

export type ModelId = keyof typeof MODELS_META;
// Default = DeepSeek chat. Set 2026-06-29 (full-Groq-eradication pass);
// was llama-3.3-70b on Groq — a model Groq discontinued AND a provider
// we removed. DeepSeek is cheap, reliable, and the box's de-facto primary.
export const DEFAULT_MODEL: ModelId = "deepseek-chat";

export function modelsByProvider(): Array<{
  provider: Provider;
  label: string;
  models: ModelMeta[];
}> {
  const order: Provider[] = [
    "anthropic",
    "openai",
    "google",
    "deepseek",
    "kimi",
    "ollama",
  ];
  return order.map((p) => ({
    provider: p,
    label: PROVIDER_LABEL[p],
    models: Object.values(MODELS_META).filter((m) => m.provider === p),
  }));
}

// ── Dynamic Ollama models ──────────────────────────────────────────────────
// The two MODELS_META entries above are static. Models the user `ollama pull`s
// beyond those are discovered at runtime (see ollama-discovery.ts) and given
// ids of the form "ollama:<tag>", which encode the tag so the server can route
// them with no static registry entry (see models.ts::getModel).

/** Static ollama entry id → its exact ollama tag. Lets the picker dedupe
 *  discovered models against the curated ones (whose labels are nicer). */
export const OLLAMA_STATIC_TAGS: Record<string, string> = {
  "ollama-qwen3-30b-a3b": "qwen3:30b-a3b",
  "ollama-gpt-oss-120b": "gpt-oss:120b",
};

const OLLAMA_DYNAMIC_ID_PREFIX = "ollama:";

/** True for both static ("ollama-*") and discovered ("ollama:*") ids. */
export function isOllamaId(id: string): boolean {
  return (
    id.startsWith(OLLAMA_DYNAMIC_ID_PREFIX) ||
    MODELS_META[id]?.provider === "ollama"
  );
}

/** Resolve any ollama model id (static or discovered) to its ollama tag. */
export function ollamaIdToTag(id: string): string | null {
  if (id.startsWith(OLLAMA_DYNAMIC_ID_PREFIX)) {
    const tag = id.slice(OLLAMA_DYNAMIC_ID_PREFIX.length);
    return tag || null;
  }
  return OLLAMA_STATIC_TAGS[id] ?? null;
}

/** Synthesize client-safe metadata for a discovered (non-static) ollama tag. */
export function buildOllamaMeta(tag: string): ModelMeta {
  return {
    id: `${OLLAMA_DYNAMIC_ID_PREFIX}${tag}`,
    label: `${tag} (Local)`,
    description: `On-device via Ollama: ${tag}.`,
    provider: "ollama",
    // Unknown without an /api/show round-trip; a neutral display-only default.
    contextWindow: 32_000,
  };
}

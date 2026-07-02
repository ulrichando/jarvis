import "server-only";

import { createAnthropic } from "@ai-sdk/anthropic";
import { createOpenAI } from "@ai-sdk/openai";
import { createGoogleGenerativeAI } from "@ai-sdk/google";
import { createDeepSeek } from "@ai-sdk/deepseek";
import { createOpenAICompatible } from "@ai-sdk/openai-compatible";
import type { LanguageModel } from "ai";
import {
  DEFAULT_MODEL,
  MODELS_META,
  buildOllamaMeta,
  isOllamaId,
  ollamaIdToTag,
  type ModelId,
  type ModelMeta,
  type Provider,
} from "./models-meta";
import { loadSettings } from "@/lib/settings/store";
import { providerEnvKey } from "./provider-keys";

export class MissingApiKeyError extends Error {
  constructor(public provider: Provider) {
    super(`No API key configured for provider "${provider}"`);
    this.name = "MissingApiKeyError";
  }
}

const MODEL_IDS: Record<string, { provider: Provider; modelId: string }> = {
  "claude-fable-5": { provider: "anthropic", modelId: "claude-fable-5" },
  "claude-opus-4-8": { provider: "anthropic", modelId: "claude-opus-4-8" },
  "claude-opus-4-7": { provider: "anthropic", modelId: "claude-opus-4-7" },
  "claude-sonnet-4-6": { provider: "anthropic", modelId: "claude-sonnet-4-6" },
  "claude-haiku-4-5": { provider: "anthropic", modelId: "claude-haiku-4-5-20251001" },

  "gpt-5": { provider: "openai", modelId: "gpt-5" },
  "gpt-5-mini": { provider: "openai", modelId: "gpt-5-mini" },
  "o3": { provider: "openai", modelId: "o3" },

  "gemini-2.5-pro": { provider: "google", modelId: "gemini-2.5-pro" },
  "gemini-2.5-flash": { provider: "google", modelId: "gemini-2.5-flash" },

  "deepseek-chat": { provider: "deepseek", modelId: "deepseek-chat" },
  "deepseek-reasoner": { provider: "deepseek", modelId: "deepseek-reasoner" },
  "deepseek-v4-pro": { provider: "deepseek", modelId: "deepseek-v4-pro" },
  "deepseek-v4-flash": { provider: "deepseek", modelId: "deepseek-v4-flash" },

  // K2.6 family — all four UI modes hit the same API model (`kimi-k2.6`).
  // Moonshot exposes ONE K2.6 endpoint; the "Instant / Thinking / Agent /
  // Swarm" differentiation on kimi.com is built ON TOP of the model with
  // different system prompts, tool sets, and parallelism — those presets
  // are implemented on the JARVIS side, not switched at the modelId
  // level. Verified live via /v1/models 2026-05-04.
  // NOTE: K2.6 returns a separate `reasoning_content` field on every
  // response (same shape as DeepSeek-R1). The chat route + voice agent
  // adapters must strip / suppress it before TTS — otherwise JARVIS
  // narrates his own chain-of-thought (the `<think>` tag bug surfaces
  // here in a different shape).
  "kimi-k2-instant": { provider: "kimi", modelId: "kimi-k2.6" },
  "kimi-k2-thinking": { provider: "kimi", modelId: "kimi-k2.6" },
  "kimi-k2-agent": { provider: "kimi", modelId: "kimi-k2.6" },
  "kimi-k2-swarm": { provider: "kimi", modelId: "kimi-k2.6" },

  // Moonshot vision family. K2.6 itself is text-only — these are the
  // separate vision-capable models. Image input must be base64
  // (Moonshot rejects external URLs as of 2026-05-04). Useful for
  // computer-use/screen-grounding flows alongside K2.6 text.
  "kimi-vision-8k": { provider: "kimi", modelId: "moonshot-v1-8k-vision-preview" },
  "kimi-vision-32k": { provider: "kimi", modelId: "moonshot-v1-32k-vision-preview" },
  "kimi-vision-128k": { provider: "kimi", modelId: "moonshot-v1-128k-vision-preview" },

  // Local Ollama — upstream model is the exact ollama tag, routed to :11434/v1.
  "ollama-qwen3-30b-a3b": { provider: "ollama", modelId: "qwen3:30b-a3b" },
  "ollama-gpt-oss-120b": { provider: "ollama", modelId: "gpt-oss:120b" },
};

export function buildProvider(
  provider: Provider,
  apiKey: string,
  baseURL?: string,
) {
  switch (provider) {
    case "anthropic":
      return createAnthropic({ apiKey });
    case "openai":
      return createOpenAI({ apiKey, baseURL });
    case "google":
      return createGoogleGenerativeAI({ apiKey });
    case "deepseek":
      return createDeepSeek({ apiKey });
    case "kimi":
      return createOpenAICompatible({
        name: "kimi",
        apiKey,
        baseURL: baseURL ?? "https://api.moonshot.ai/v1",
      });
    case "ollama":
      return createOpenAICompatible({
        name: "ollama",
        apiKey,
        baseURL: baseURL ?? "http://localhost:11434/v1",
      });
  }
}

export async function resolveApiKey(provider: Provider): Promise<{
  apiKey?: string;
  baseURL?: string;
}> {
  const settings = await loadSettings();
  // ollama isn't a key-configurable provider in settings, so index defensively
  // (returns undefined → falls back to envFallback, which hands ollama a
  // placeholder key + the local baseURL).
  const p = (settings.providers as Partial<
    Record<Provider, { apiKey?: string; baseURL?: string }>
  >)[provider];
  return {
    apiKey: p?.apiKey ?? providerEnvKey(provider),
    baseURL: p?.baseURL,
  };
}

export async function getModel(id: string): Promise<{
  meta: ModelMeta;
  model: LanguageModel;
}> {
  // Discovered (non-static) Ollama model: the id carries the tag, so route it
  // to the local ollama daemon without needing a static MODEL_IDS entry.
  if (!MODELS_META[id] && isOllamaId(id)) {
    const tag = ollamaIdToTag(id);
    if (tag) {
      const { apiKey, baseURL } = await resolveApiKey("ollama");
      const clientFactory = buildProvider("ollama", apiKey ?? "ollama", baseURL);
      return {
        meta: buildOllamaMeta(tag),
        model: clientFactory(tag) as LanguageModel,
      };
    }
  }

  const resolvedId = MODELS_META[id] ? id : DEFAULT_MODEL;
  const entry = MODEL_IDS[resolvedId];
  const { apiKey, baseURL } = await resolveApiKey(entry.provider);
  if (!apiKey) throw new MissingApiKeyError(entry.provider);
  const clientFactory = buildProvider(entry.provider, apiKey, baseURL);
  return {
    meta: MODELS_META[resolvedId],
    model: clientFactory(entry.modelId) as LanguageModel,
  };
}

export { DEFAULT_MODEL, type ModelId };

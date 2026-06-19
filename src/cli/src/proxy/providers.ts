import {
  getDefaultJarvisProvider,
  getJarvisModel,
  getJarvisProviderConfig,
  type JarvisProviderName,
} from '../utils/model/jarvisModelRegistry.js'

export type Provider = {
  name: string
  baseUrl: string
  apiKey: string
  model: string
  supportsToolChoice: boolean
  maxTools?: number
  maxOutputTokens: number
  // True for thinking-mode models (e.g. deepseek-v4-pro) that require
  // every prior assistant message in a multi-turn request to carry a
  // non-empty reasoning_content field. Convert.ts injects a placeholder
  // when the cache misses so the upstream API doesn't 400.
  requiresReasoning: boolean
  // True when the upstream model accepts OpenAI-shape image_url content
  // parts. Plumbed from JarvisModelDefinition.supportsVision (defaults
  // to false when unset). Convert.ts uses this to decide whether to
  // emit image bytes vs flatten to the literal "[image]" placeholder.
  supportsVision: boolean
  // Jarvis model id used to look up this provider — needed to resolve
  // the fallback chain when the primary fails.
  jarvisModelId: string | null
  // Ordered list of jarvis model ids to try if this one fails after
  // retries. Consumed inline by executeWithFallback() in server.ts.
  fallback: readonly string[]
}

function resolveApiKey(
  name: JarvisProviderName,
  envVar: string | readonly string[] | undefined,
): string {
  if (name === 'ollama') return 'ollama'
  if (!envVar) {
    throw new Error(`Provider "${name}" has no apiKeyEnvVar configured in the model registry`)
  }
  const candidates = typeof envVar === 'string' ? [envVar] : envVar
  for (const candidate of candidates) {
    const key = (process.env[candidate] ?? '').trim()
    if (key) return key
  }
  throw new Error(
    `Missing ${candidates.join(' / ')} in proxy environment — cannot route to ${name}. ` +
    `Start via src/cli/scripts/start.sh (or bin/jarvis-desktop) so .env.local is loaded.`,
  )
}

function buildProvider(
  name: JarvisProviderName,
  upstreamModel: string,
  requiresReasoning: boolean,
  supportsVision: boolean,
  jarvisModelId: string | null,
  fallback: readonly string[],
  modelMaxOutputTokens: number | undefined,
): Provider {
  const config = getJarvisProviderConfig(name)
  return {
    name,
    baseUrl: config.baseUrl,
    apiKey: resolveApiKey(name, config.apiKeyEnvVar),
    model:
      name === 'ollama' && upstreamModel === 'ollama'
        // Bare 'ollama' placeholder (the provider default when no model is
        // pinned). OLLAMA_MODEL wins if set; otherwise fall back to a model
        // that's actually a sane default — 'llama3' was a tag most boxes don't
        // have pulled (→ 404 on first message). qwen3:30b-a3b is the CPU sweet
        // spot and is the curated default pull.
        ? (process.env.OLLAMA_MODEL ?? 'qwen3:30b-a3b')
        : upstreamModel,
    supportsToolChoice: config.supportsToolChoice,
    maxTools: config.maxTools,
    // Per-model maxOutputTokens overrides the provider default. Used for
    // models with a stricter API cap than their provider family (e.g.
    // gpt-4o = 16K under the OpenAI provider's 32K default; llama-4-scout
    // = 8K under the Groq provider's 32K default).
    maxOutputTokens: modelMaxOutputTokens ?? config.maxOutputTokens,
    requiresReasoning,
    supportsVision,
    jarvisModelId,
    fallback,
  }
}

/** Resolve provider from an explicit model name sent by the CLI (e.g. via /model). */
export function getProviderForModel(modelName: string): Provider | null {
  const model = getJarvisModel(modelName)
  if (!model) return null
  const requiresReasoning = model.capabilities.includes('thinking')
  const supportsVision = model.supportsVision ?? false
  return buildProvider(
    model.provider,
    model.upstreamModel,
    requiresReasoning,
    supportsVision,
    model.id,
    model.fallback ?? [],
    model.maxOutputTokens,
  )
}

/** Default provider from JARVIS_PROVIDER env var. */
export function getProvider(): Provider {
  const name = getDefaultJarvisProvider()
  const config = getJarvisProviderConfig(name)
  const defaultModel = getJarvisModel(config.defaultModel)
  const requiresReasoning = defaultModel?.capabilities.includes('thinking') ?? false
  const supportsVision = defaultModel?.supportsVision ?? false
  return buildProvider(
    name,
    config.defaultModel,
    requiresReasoning,
    supportsVision,
    defaultModel?.id ?? null,
    defaultModel?.fallback ?? [],
    defaultModel?.maxOutputTokens,
  )
}

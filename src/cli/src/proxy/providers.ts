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
  // Jarvis model id used to look up this provider — needed to resolve
  // the fallback chain when the primary fails.
  jarvisModelId: string | null
  // Ordered list of jarvis model ids to try if this one fails after
  // retries. Resolved on demand by getFallbackProvider().
  fallback: readonly string[]
}

function resolveApiKey(name: JarvisProviderName, envVar: string | undefined): string {
  if (name === 'ollama') return 'ollama'
  if (!envVar) {
    throw new Error(`Provider "${name}" has no apiKeyEnvVar configured in the model registry`)
  }
  const key = (process.env[envVar] ?? '').trim()
  if (!key) {
    throw new Error(
      `Missing ${envVar} in proxy environment — cannot route to ${name}. ` +
      `Start via src/cli/scripts/start.sh (or bin/jarvis-desktop) so .env.local is loaded.`,
    )
  }
  return key
}

function buildProvider(
  name: JarvisProviderName,
  upstreamModel: string,
  requiresReasoning: boolean,
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
        ? (process.env.OLLAMA_MODEL ?? 'llama3')
        : upstreamModel,
    supportsToolChoice: config.supportsToolChoice,
    maxTools: config.maxTools,
    // Per-model maxOutputTokens overrides the provider default. Used for
    // models with a stricter API cap than their provider family (e.g.
    // gpt-4o = 16K under the OpenAI provider's 32K default; llama-4-scout
    // = 8K under the Groq provider's 32K default).
    maxOutputTokens: modelMaxOutputTokens ?? config.maxOutputTokens,
    requiresReasoning,
    jarvisModelId,
    fallback,
  }
}

/** Resolve provider from an explicit model name sent by the CLI (e.g. via /model). */
export function getProviderForModel(modelName: string): Provider | null {
  const model = getJarvisModel(modelName)
  if (!model) return null
  const requiresReasoning = model.capabilities.includes('thinking')
  return buildProvider(
    model.provider,
    model.upstreamModel,
    requiresReasoning,
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
  return buildProvider(
    name,
    config.defaultModel,
    requiresReasoning,
    defaultModel?.id ?? null,
    defaultModel?.fallback ?? [],
    defaultModel?.maxOutputTokens,
  )
}

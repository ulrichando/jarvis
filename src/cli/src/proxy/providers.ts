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
    maxOutputTokens: config.maxOutputTokens,
  }
}

/** Resolve provider from an explicit model name sent by the CLI (e.g. via /model). */
export function getProviderForModel(modelName: string): Provider | null {
  const model = getJarvisModel(modelName)
  return model ? buildProvider(model.provider, model.upstreamModel) : null
}

/** Default provider from JARVIS_PROVIDER env var. */
export function getProvider(): Provider {
  const name = getDefaultJarvisProvider()
  const config = getJarvisProviderConfig(name)
  return buildProvider(name, config.defaultModel)
}

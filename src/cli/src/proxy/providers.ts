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

function buildProvider(
  name: JarvisProviderName,
  upstreamModel: string,
): Provider {
  const config = getJarvisProviderConfig(name)
  return {
    name,
    baseUrl: config.baseUrl,
    apiKey:
      name === 'ollama'
        ? 'ollama'
        : (config.apiKeyEnvVar && process.env[config.apiKeyEnvVar]) ?? '',
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

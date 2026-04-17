export type JarvisProviderName =
  | 'deepseek'
  | 'groq'
  | 'gemini'
  | 'openai'
  | 'ollama'

export type JarvisModelTier =
  | 'default'
  | 'balanced'
  | 'fast'
  | 'reasoning'
  | 'orchestration'
  | 'long_context'

export type JarvisModelCapability =
  | 'effort'
  | 'max_effort'
  | 'thinking'
  | 'adaptive_thinking'
  | 'interleaved_thinking'

export type JarvisProviderDefinition = {
  baseUrl: string
  apiKeyEnvVar?: string
  defaultModel: string
  supportsToolChoice: boolean
  maxTools?: number
  maxOutputTokens: number
}

export type JarvisModelDefinition = {
  id: string
  label: string
  description: string
  provider: JarvisProviderName
  upstreamModel: string
  tiers: readonly JarvisModelTier[]
  capabilities: readonly JarvisModelCapability[]
  visibleInPicker?: boolean
}

const JARVIS_PROVIDER_DEFINITIONS: Record<
  JarvisProviderName,
  JarvisProviderDefinition
> = {
  deepseek: {
    baseUrl: 'https://api.deepseek.com/v1',
    apiKeyEnvVar: 'DEEPSEEK_API_KEY',
    defaultModel: 'deepseek-chat',
    supportsToolChoice: true,
    maxOutputTokens: 8000,
  },
  groq: {
    baseUrl: 'https://api.groq.com/openai/v1',
    apiKeyEnvVar: 'GROQ_API_KEY',
    defaultModel: 'qwen/qwen3-32b',
    supportsToolChoice: true,
    maxTools: 20,
    maxOutputTokens: 8000,
  },
  gemini: {
    baseUrl: 'https://generativelanguage.googleapis.com/v1beta/openai',
    apiKeyEnvVar: 'GOOGLE_API_KEY',
    defaultModel: 'gemini-2.0-flash',
    supportsToolChoice: true,
    maxOutputTokens: 8192,
  },
  openai: {
    baseUrl: 'https://api.openai.com/v1',
    apiKeyEnvVar: 'OPENAI_API_KEY',
    defaultModel: 'gpt-4o',
    supportsToolChoice: true,
    maxOutputTokens: 16384,
  },
  ollama: {
    baseUrl: (process.env.OLLAMA_BASE_URL ?? 'http://localhost:11434') + '/v1',
    defaultModel: 'ollama',
    supportsToolChoice: false,
    maxOutputTokens: 4096,
  },
}

const JARVIS_MODEL_DEFINITIONS: readonly JarvisModelDefinition[] = [
  {
    id: 'deepseek-chat',
    label: 'DeepSeek Chat',
    description: 'Default fast model',
    provider: 'deepseek',
    upstreamModel: 'deepseek-chat',
    tiers: ['default', 'balanced'],
    capabilities: ['effort'],
    visibleInPicker: true,
  },
  {
    id: 'deepseek-reasoner',
    label: 'DeepSeek Reasoner',
    description: 'R1 · Complex reasoning',
    provider: 'deepseek',
    upstreamModel: 'deepseek-reasoner',
    tiers: ['reasoning'],
    capabilities: ['effort'],
    visibleInPicker: true,
  },
  {
    id: 'qwen/qwen3-32b',
    label: 'Groq Qwen3 32B',
    description: 'Primary · Best for everyday tasks',
    provider: 'groq',
    upstreamModel: 'qwen/qwen3-32b',
    tiers: ['default', 'balanced', 'orchestration'],
    capabilities: [],
    visibleInPicker: true,
  },
  {
    id: 'llama-3.3-70b-versatile',
    label: 'Groq Llama 3.3 70B',
    description: 'Chat & reasoning · limited tool use',
    provider: 'groq',
    upstreamModel: 'llama-3.3-70b-versatile',
    tiers: ['reasoning'],
    capabilities: [],
    visibleInPicker: true,
  },
  {
    id: 'meta-llama/llama-4-scout-17b-16e-instruct',
    label: 'Groq Llama 4 Scout',
    description: 'Fast · Llama 4 lightweight',
    provider: 'groq',
    upstreamModel: 'meta-llama/llama-4-scout-17b-16e-instruct',
    tiers: ['fast'],
    capabilities: [],
    visibleInPicker: true,
  },
  {
    id: 'gemini-flash',
    label: 'Gemini Flash',
    description: 'Gemini 2.0 Flash',
    provider: 'gemini',
    upstreamModel: 'gemini-2.0-flash',
    tiers: ['fast'],
    capabilities: [],
    visibleInPicker: false,
  },
  {
    id: 'gemini-2.0-flash',
    label: 'Gemini Flash',
    description: 'Gemini 2.0 Flash',
    provider: 'gemini',
    upstreamModel: 'gemini-2.0-flash',
    tiers: ['fast'],
    capabilities: [],
    visibleInPicker: false,
  },
  {
    id: 'gemini-pro',
    label: 'Gemini Pro',
    description: 'Gemini 2.5 Pro',
    provider: 'gemini',
    upstreamModel: 'gemini-2.5-pro-preview-03-25',
    tiers: ['reasoning'],
    capabilities: [],
    visibleInPicker: false,
  },
  {
    id: 'gemini-2.5-pro',
    label: 'Gemini Pro',
    description: 'Gemini 2.5 Pro',
    provider: 'gemini',
    upstreamModel: 'gemini-2.5-pro-preview-03-25',
    tiers: ['reasoning'],
    capabilities: [],
    visibleInPicker: false,
  },
  {
    id: 'gpt-4o',
    label: 'OpenAI GPT-4o',
    description: 'Balanced multimodal model',
    provider: 'openai',
    upstreamModel: 'gpt-4o',
    tiers: ['balanced'],
    capabilities: [],
    visibleInPicker: false,
  },
  {
    id: 'gpt-4o-mini',
    label: 'OpenAI GPT-4o Mini',
    description: 'Fast OpenAI model',
    provider: 'openai',
    upstreamModel: 'gpt-4o-mini',
    tiers: ['fast'],
    capabilities: [],
    visibleInPicker: false,
  },
  {
    id: 'ollama',
    label: 'Ollama',
    description: 'Local model via OLLAMA_MODEL',
    provider: 'ollama',
    upstreamModel: 'ollama',
    tiers: ['default'],
    capabilities: [],
    visibleInPicker: false,
  },
] as const

export function isJarvisModelRegistryEnabled(): boolean {
  return process.env.JARVIS_MODEL_REGISTRY_ENABLED === '1'
}

export function getDefaultJarvisProvider(): JarvisProviderName {
  const provider = process.env.JARVIS_PROVIDER
  if (provider && provider in JARVIS_PROVIDER_DEFINITIONS) {
    return provider as JarvisProviderName
  }
  return 'deepseek'
}

export function getJarvisProviderConfig(
  provider: JarvisProviderName,
): JarvisProviderDefinition {
  return JARVIS_PROVIDER_DEFINITIONS[provider]
}

export function getJarvisModels(): readonly JarvisModelDefinition[] {
  return JARVIS_MODEL_DEFINITIONS
}

export function getJarvisPickerModels(): readonly JarvisModelDefinition[] {
  return JARVIS_MODEL_DEFINITIONS.filter(model => model.visibleInPicker !== false)
}

export function getJarvisModel(
  modelId: string | null | undefined,
): JarvisModelDefinition | undefined {
  if (!modelId) {
    return undefined
  }
  const normalized = modelId.trim().toLowerCase()
  return JARVIS_MODEL_DEFINITIONS.find(
    model => model.id.toLowerCase() === normalized,
  )
}

export function isJarvisModelId(modelId: string): boolean {
  return getJarvisModel(modelId) !== undefined
}

export function getJarvisModelDisplayName(
  modelId: string | null | undefined,
): string | undefined {
  return getJarvisModel(modelId)?.label
}

export function getJarvisModelCapabilityOverride(
  model: string,
  capability: JarvisModelCapability,
): boolean | undefined {
  const entry = getJarvisModel(model)
  if (!entry) {
    return undefined
  }
  return entry.capabilities.includes(capability)
}

export function getJarvisModelsWithCapability(
  capability: JarvisModelCapability,
): readonly JarvisModelDefinition[] {
  return JARVIS_MODEL_DEFINITIONS.filter(model =>
    model.capabilities.includes(capability),
  )
}

export function formatJarvisModelLabels(
  labels: readonly string[],
): string | undefined {
  if (labels.length === 0) {
    return undefined
  }
  if (labels.length === 1) {
    return labels[0]
  }
  if (labels.length === 2) {
    return `${labels[0]} and ${labels[1]}`
  }
  return `${labels.slice(0, -1).join(', ')}, and ${labels[labels.length - 1]}`
}

export function getPreferredJarvisModelForTier(
  tier: JarvisModelTier,
  preferredProvider: JarvisProviderName = getDefaultJarvisProvider(),
): JarvisModelDefinition | undefined {
  return (
    JARVIS_MODEL_DEFINITIONS.find(
      model =>
        model.provider === preferredProvider && model.tiers.includes(tier),
    ) ?? JARVIS_MODEL_DEFINITIONS.find(model => model.tiers.includes(tier))
  )
}

export function getJarvisDefaultModel(): JarvisModelDefinition {
  const providerDefault = getJarvisModel(
    getJarvisProviderConfig(getDefaultJarvisProvider()).defaultModel,
  )
  if (providerDefault) {
    return providerDefault
  }
  return getPreferredJarvisModelForTier('default') ?? JARVIS_MODEL_DEFINITIONS[0]
}

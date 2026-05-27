export type JarvisProviderName =
  | 'deepseek'
  | 'groq'
  | 'gemini'
  | 'openai'
  | 'ollama'
  | 'kimi'
  | 'anthropic'

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
  // Per-model override of provider.maxOutputTokens. Used when a model's
  // API-level cap is stricter than the provider family default (e.g.
  // gpt-4o tops at 16K while gpt-5 supports up to 128K under the same
  // OpenAI provider). If unset, the provider default applies.
  maxOutputTokens?: number
  visibleInPicker?: boolean
  // Models to try (in order) if this one's upstream is unreachable or
  // returns 5xx/429 after retries. Entries are jarvis model ids.
  // Capabilities may differ across the chain (e.g. thinking → non-thinking)
  // — that's accepted as a graceful-degradation tradeoff vs. surfacing a
  // hard error to the CLI.
  fallback?: readonly string[]
}

const JARVIS_PROVIDER_DEFINITIONS: Record<
  JarvisProviderName,
  JarvisProviderDefinition
> = {
  deepseek: {
    baseUrl: 'https://api.deepseek.com/v1',
    apiKeyEnvVar: 'DEEPSEEK_API_KEY',
    defaultModel: 'deepseek-v4-pro',
    supportsToolChoice: true,
    // 64K — DeepSeek's own pricing page lists 384K MAXIMUM for v4-* but
    // that's a soft ceiling, not a per-request cap they recommend. 64K
    // gives v4-pro plenty of headroom for thinking-mode reasoning_content
    // + visible output + tool args. Bumped from 32K on 2026-05-27 after
    // doc verification.
    maxOutputTokens: 65536,
  },
  groq: {
    baseUrl: 'https://api.groq.com/openai/v1',
    apiKeyEnvVar: 'GROQ_API_KEY',
    defaultModel: 'qwen/qwen3-32b',
    supportsToolChoice: true,
    maxTools: 20,
    // 32K covers the majority of Groq-hosted models per console.groq.com/docs/models
    // (qwen3-32b=40K, llama-3.3-70b=32K, llama-3.1-8b=131K, gpt-oss-120b=64K).
    // llama-4-scout (8K model-side cap) uses a per-model override below
    // so its requests don't carry a too-high max_tokens that Groq would
    // 400 on. Bumped from 8K on 2026-05-27.
    maxOutputTokens: 32768,
  },
  gemini: {
    baseUrl: 'https://generativelanguage.googleapis.com/v1beta/openai',
    apiKeyEnvVar: 'GOOGLE_API_KEY',
    defaultModel: 'gemini-2.0-flash',
    supportsToolChoice: true,
    // 32K — verified live 2026-05-27 that Google's OpenAI-compat layer
    // accepts up to 16K (responded normally to a max_tokens=16384 request).
    // ai.google.dev docs list 65,536 as the native API cap; setting 32K
    // as a conservative middle. Bumped from 8K (which was the legacy
    // gemini-1.x cap and no longer applies on the 2.5 family).
    maxOutputTokens: 32768,
  },
  openai: {
    baseUrl: 'https://api.openai.com/v1',
    apiKeyEnvVar: 'OPENAI_API_KEY',
    defaultModel: 'gpt-4o',
    supportsToolChoice: true,
    // 32K — GPT-5 family supports up to 128K per OpenAI's per-model
    // docs; 32K doubles the GPT-5 reasoning headroom without going to
    // silly numbers. gpt-4o / gpt-4o-mini cap at 16K model-side, handled
    // via per-model override below. Bumped from 16K on 2026-05-27.
    maxOutputTokens: 32768,
  },
  ollama: {
    baseUrl: (process.env.OLLAMA_BASE_URL ?? 'http://localhost:11434') + '/v1',
    defaultModel: 'ollama',
    supportsToolChoice: false,
    maxOutputTokens: 4096,
  },
  kimi: {
    // Moonshot Kimi (K2.6 + vision). OpenAI-compatible endpoint.
    // K2.6 emits a separate `reasoning_content` field on every response
    // (DeepSeek-R1 shape) — the proxy / consumer should strip it from
    // user-voiced output if Kimi ever lands on the voice path. Today
    // it's CLI-only.
    baseUrl: 'https://api.moonshot.ai/v1',
    apiKeyEnvVar: 'KIMI_API_KEY',
    defaultModel: 'kimi-k2.6-instant',
    supportsToolChoice: true,
    maxTools: 16,
    // 32K — Moonshot's own docs don't publish a per-call output ceiling
    // for K2.6 (only the 256K context window). OpenRouter's mirror lists
    // ~49K as the per-step cap. 32K is a safe middle that doubles
    // reasoning headroom over the old 16K. Bumped 2026-05-27.
    maxOutputTokens: 32768,
  },
  anthropic: {
    // Anthropic Claude — native Messages API. The CLI's openai-shaped
    // chat client treats this as an OpenAI-compatible endpoint via the
    // anthropic provider; the @anthropic-ai/sdk is already in the dep
    // graph (vendored Claude Code shape). Sonnet 4.6 is the workhorse:
    // 1M ctx, 128K output, strong agentic tool calls, $3/$15 per M.
    // Haiku 4.5 is also wired but it's voice-only (see voice-agent's
    // SPEECH_MODELS); CLI's tool chains are too deep for it.
    //
    // NOTE: this value is METADATA only for Anthropic. The proxy uses
    // byte-for-byte passthrough (src/proxy/anthropicPassthrough.ts) so
    // max_tokens comes from the CLI's @anthropic-ai/sdk, which caps at
    // 64K via src/utils/context.ts::MAX_OUTPUT_TOKENS_UPPER_LIMIT.
    // The registry value here aligns with that cap for accuracy +
    // future-proofing if the architecture changes. Bumped from 32K on
    // 2026-05-27 to match the CLI's own UPPER_LIMIT and Anthropic's
    // documented 64K output cap for Sonnet 4.6 / Haiku 4.5 (Opus 4.7
    // can do 128K natively but the CLI clamps to 64K anyway).
    baseUrl: 'https://api.anthropic.com/v1',
    apiKeyEnvVar: 'ANTHROPIC_API_KEY',
    defaultModel: 'claude-sonnet-4-6',
    supportsToolChoice: true,
    maxTools: 64,
    maxOutputTokens: 65536,
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
    id: 'deepseek-v4-flash',
    label: 'DeepSeek V4 Flash',
    description: 'V4 · Fast everyday model',
    provider: 'deepseek',
    upstreamModel: 'deepseek-v4-flash',
    tiers: ['fast', 'balanced'],
    capabilities: [],
    fallback: ['qwen/qwen3-32b'],
    visibleInPicker: true,
  },
  {
    id: 'deepseek-v4-pro',
    label: 'DeepSeek V4 Pro',
    description: 'V4 · Strongest reasoning',
    provider: 'deepseek',
    upstreamModel: 'deepseek-v4-pro',
    tiers: ['reasoning', 'long_context'],
    capabilities: ['effort', 'thinking'],
    visibleInPicker: true,
    fallback: ['deepseek-v4-flash', 'qwen/qwen3-32b'],
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
    fallback: ['deepseek-v4-flash'],
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
    // Scout's model-side cap is 8192 per Groq docs (lower than the rest
    // of the Groq family). Pin here so requests don't carry max_tokens
    // > 8K (which Groq would 400 on).
    maxOutputTokens: 8192,
    visibleInPicker: true,
  },
  {
    // Arbiter / gating model — tiny, fast, cheap. Not exposed in /model
    // picker since it's a utility target, not meant for main chat.
    id: 'llama-3.1-8b-instant',
    label: 'Groq Llama 3.1 8B Instant',
    description: 'Small · Fast · Gating / classification',
    provider: 'groq',
    upstreamModel: 'llama-3.1-8b-instant',
    tiers: ['fast'],
    capabilities: [],
    visibleInPicker: false,
  },
  {
    id: 'openai/gpt-oss-120b',
    label: 'Groq GPT-OSS 120B',
    description: 'Open-source GPT · strong reasoning',
    provider: 'groq',
    upstreamModel: 'openai/gpt-oss-120b',
    tiers: ['reasoning'],
    capabilities: ['effort', 'max_effort'],
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
  // gpt-4o family — model-side caps at 16K per OpenAI's developer docs
  // (https://developers.openai.com/api/docs/models/gpt-4o). The OpenAI
  // provider default (32K) is right for the GPT-5 family but too high
  // for 4o, so we pin a per-model override on both.
  {
    id: 'gpt-4o',
    label: 'OpenAI GPT-4o',
    description: 'Balanced multimodal model',
    provider: 'openai',
    upstreamModel: 'gpt-4o',
    tiers: ['balanced'],
    capabilities: [],
    maxOutputTokens: 16384,
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
    maxOutputTokens: 16384,
    visibleInPicker: false,
  },
  // OpenAI GPT-5 family — supports `reasoning_effort` (minimal/low/medium/high)
  // on the OpenAI Responses API. `max` is NOT a valid tier — utils/effort.ts
  // downgrades 'max' → 'high' for any model without `max_effort` capability,
  // which is correct for OpenAI: 'high' is the strongest tier the API accepts.
  // Voice tray (voice_client_tray_config.py) advertises the same five IDs.
  {
    id: 'gpt-5-nano',
    label: 'OpenAI GPT-5 Nano',
    description: 'GPT-5 nano · cheapest, fastest',
    provider: 'openai',
    upstreamModel: 'gpt-5-nano',
    tiers: ['fast'],
    capabilities: ['effort'],
    visibleInPicker: false,
  },
  {
    id: 'gpt-5-mini',
    label: 'OpenAI GPT-5 Mini',
    description: 'GPT-5 mini · balanced cost vs. quality',
    provider: 'openai',
    upstreamModel: 'gpt-5-mini',
    tiers: ['balanced', 'fast'],
    capabilities: ['effort'],
    visibleInPicker: true,
  },
  {
    id: 'gpt-5',
    label: 'OpenAI GPT-5',
    description: 'GPT-5 · reasoning + general purpose',
    provider: 'openai',
    upstreamModel: 'gpt-5',
    tiers: ['reasoning', 'balanced'],
    capabilities: ['effort'],
    visibleInPicker: true,
  },
  {
    id: 'gpt-5.1',
    label: 'OpenAI GPT-5.1',
    description: 'GPT-5.1 · adaptive reasoning',
    provider: 'openai',
    upstreamModel: 'gpt-5.1',
    tiers: ['reasoning', 'balanced', 'long_context'],
    capabilities: ['effort'],
    visibleInPicker: true,
  },
  {
    id: 'gpt-5.1-chat-latest',
    label: 'OpenAI GPT-5.1 Chat',
    description: 'GPT-5.1 chat variant · non-reasoning',
    provider: 'openai',
    upstreamModel: 'gpt-5.1-chat-latest',
    tiers: ['balanced'],
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
  // Kimi K2.6 family — all four UI modes hit the same upstream API
  // model `kimi-k2.6`. The Instant/Thinking/Agent/Swarm split is a
  // CLIENT-side preset (different system prompt + tools), not a
  // separate API endpoint. Verified live via /v1/models 2026-05-04.
  {
    id: 'kimi-k2.6-instant',
    label: 'Kimi K2.6 Instant',
    description: 'Quick response. Default Kimi model.',
    provider: 'kimi',
    upstreamModel: 'kimi-k2.6',
    tiers: ['fast', 'default'],
    capabilities: [],
    visibleInPicker: true,
  },
  {
    id: 'kimi-k2.6-thinking',
    label: 'Kimi K2.6 Thinking',
    description: 'Deep reasoning. Returns reasoning_content.',
    provider: 'kimi',
    upstreamModel: 'kimi-k2.6',
    tiers: ['reasoning'],
    capabilities: ['thinking'],
    visibleInPicker: true,
    fallback: ['kimi-k2.6-instant'],
  },
  {
    id: 'kimi-k2.6-agent',
    label: 'Kimi K2.6 Agent',
    description: 'Research / orchestration with tools.',
    provider: 'kimi',
    upstreamModel: 'kimi-k2.6',
    tiers: ['orchestration', 'balanced'],
    capabilities: [],
    visibleInPicker: true,
  },
  {
    id: 'kimi-k2.6-swarm',
    label: 'Kimi K2.6 Swarm',
    description: 'Long-form / batch tasks.',
    provider: 'kimi',
    upstreamModel: 'kimi-k2.6',
    tiers: ['long_context'],
    capabilities: [],
    visibleInPicker: true,
  },
  // Anthropic Claude — added 2026-05-11. Three tiers mirroring the
  // Claude Code /model picker shape: Opus (most capable, complex work),
  // Sonnet (everyday workhorse), Haiku (fastest, simple tasks). All
  // three support adaptive thinking. ANTHROPIC_API_KEY required.
  // Capabilities are mirrored from the /v1/models response on this key
  // (2026-05-11): all three current-gen Claude tiers support `effort`
  // at low/medium/high/max, and `thinking: adaptive`. Exposing every
  // level here makes the CLI's EffortPicker show the four-option list
  // for all three models instead of falling back to the substring
  // heuristic in utils/effort.ts (which excludes anything matching
  // 'haiku'/'sonnet'/'opus' for non-1P).
  {
    id: 'claude-opus-4-7',
    label: 'Claude Opus 4.7',
    description: 'Opus 4.7 with 1M context · Most capable for complex work',
    provider: 'anthropic',
    upstreamModel: 'claude-opus-4-7',
    tiers: ['reasoning', 'long_context', 'orchestration'],
    capabilities: ['adaptive_thinking', 'effort', 'max_effort'],
    // Opus 4.7's published max_output is 128K per Anthropic's models
    // overview (https://platform.claude.com/docs/en/about-claude/models/overview).
    // Sonnet 4.6 + Haiku 4.5 cap at 64K (Anthropic provider default).
    // METADATA only — the proxy uses passthrough for Anthropic, so the
    // value here doesn't reach the wire.
    maxOutputTokens: 131072,
    visibleInPicker: true,
    fallback: ['claude-sonnet-4-6', 'deepseek-v4-pro'],
  },
  {
    id: 'claude-sonnet-4-6',
    label: 'Claude Sonnet 4.6',
    description: 'Sonnet 4.6 · Best for everyday tasks',
    provider: 'anthropic',
    upstreamModel: 'claude-sonnet-4-6',
    tiers: ['default', 'balanced', 'reasoning', 'long_context', 'orchestration'],
    capabilities: ['adaptive_thinking', 'effort', 'max_effort'],
    visibleInPicker: true,
    fallback: ['deepseek-v4-pro', 'qwen/qwen3-32b'],
  },
  {
    id: 'claude-haiku-4-5',
    label: 'Claude Haiku 4.5',
    description: 'Haiku 4.5 · Fastest for quick answers',
    provider: 'anthropic',
    upstreamModel: 'claude-haiku-4-5',
    tiers: ['fast', 'balanced'],
    // Haiku 4.5 supports adaptive thinking but NOT the explicit
    // effort/max_effort tiers (verified 2026-05-11 against
    // /v1/messages — the API returns 'This model does not support
    // the effort parameter' when output_config.effort is set).
    capabilities: ['adaptive_thinking'],
    visibleInPicker: true,
    fallback: ['claude-sonnet-4-6', 'deepseek-v4-flash'],
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

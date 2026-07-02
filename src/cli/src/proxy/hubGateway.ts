import { getProvider, getProviderForModel, type Provider } from './providers.js'

export type ChatCompletionsRoute =
  | { kind: 'route'; provider: Provider }
  | { kind: 'reject'; status: number; message: string }

/**
 * Decide where an OpenAI-shaped /v1/chat/completions request goes.
 *
 * Anthropic models are rejected here: the proxy's `anthropic` provider speaks
 * native /v1/messages, not /chat/completions, so an OpenAI-shaped request for a
 * Claude model can't be served on this path. No real client does this — voice
 * uses the Anthropic plugin (→ /v1/messages) for Claude — so it's a defensive
 * 400, not a supported route. (OpenAI-ingress→Anthropic-upstream is explicitly
 * out of scope for sub-project 1.)
 */
// Diagnostic: which providers have a key on this host + the default route. No
// secrets are returned — only booleans. The provider→envvar map is the small
// stable set in ~/.jarvis/keys.env; add a line when a provider is added.
const _DIAG_PROVIDER_KEYS: Record<string, string> = {
  deepseek: 'DEEPSEEK_API_KEY',
  anthropic: 'ANTHROPIC_API_KEY',
  openai: 'OPENAI_API_KEY',
  kimi: 'KIMI_API_KEY',
  google: 'GOOGLE_API_KEY',
}

export function buildHubConfig(): {
  status: string
  default_provider: string | null
  default_model: string | null
  providers: Record<string, boolean>
} {
  const providers: Record<string, boolean> = {}
  for (const [name, envVar] of Object.entries(_DIAG_PROVIDER_KEYS)) {
    providers[name] = Boolean((process.env[envVar] ?? '').trim())
  }
  let default_provider: string | null = null
  let default_model: string | null = null
  try { const p = getProvider(); default_provider = p.name; default_model = p.model } catch {}
  return { status: 'ok', default_provider, default_model, providers }
}

export function classifyChatCompletionsRequest(
  model: string | undefined,
): ChatCompletionsRoute {
  const provider = (model ? getProviderForModel(model) : null) ?? getProvider()
  if (provider.name === 'anthropic') {
    return {
      kind: 'reject',
      status: 400,
      message:
        'Anthropic models must use the /v1/messages endpoint, not /v1/chat/completions',
    }
  }
  return { kind: 'route', provider }
}

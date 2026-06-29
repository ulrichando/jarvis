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

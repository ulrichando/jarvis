/**
 * Stage 2 offline parity — local last-resort failover tests.
 *
 * The load-bearing properties pinned here:
 *   1. A genuine cloud OUTAGE (connection error / 5xx after chain
 *      exhaustion) reroutes the request to local Ollama.
 *   2. Definitive cloud answers (401 bad key, 429 quota/rate) NEVER
 *      reroute — they surface to the CLI exactly as before.
 *   3. When the cloud is healthy, the local endpoint is never contacted
 *      (online path unchanged).
 *   4. Flag off / local down → prior behavior, original cloud error.
 */
import { describe, test, expect } from 'bun:test'
import {
  attemptLocalFailover,
  buildLocalFailoverProvider,
  cloudFailureAllowsLocal,
  localFailoverEnabled,
  localFailoverModelTag,
} from './localFailover.js'
import { executeWithFallback } from './execute.js'
import type { Provider } from './providers.js'
import type { FetchAttemptResult } from './retry.js'

const ENV_ON: NodeJS.ProcessEnv = {}
const ENV_OFF: NodeJS.ProcessEnv = { JARVIS_LOCAL_LLM_FAILOVER: '0' }

function cloudProvider(overrides: Partial<Provider> = {}): Provider {
  return {
    name: 'deepseek',
    baseUrl: 'https://api.deepseek.com/v1',
    apiKey: 'test-key',
    model: 'deepseek-chat',
    supportsToolChoice: true,
    maxOutputTokens: 8192,
    requiresReasoning: false,
    supportsVision: false,
    jarvisModelId: 'deepseek-chat',
    fallback: [],
    ...overrides,
  }
}

const okBody = JSON.stringify({
  choices: [{ message: { role: 'assistant', content: 'hi' }, finish_reason: 'stop' }],
  usage: { prompt_tokens: 1, completion_tokens: 1 },
})

function fetcherReturning(result: () => FetchAttemptResult): typeof import('./retry.js').fetchWithRetry {
  return (async () => result()) as any
}

const connFailure = (): FetchAttemptResult => ({
  response: null,
  error: new Error('Unable to connect. Is the computer able to access the url? (ECONNREFUSED)'),
  retriesUsed: 3,
  ttfbMs: null,
})

const statusFailure = (status: number): FetchAttemptResult => ({
  response: new Response(JSON.stringify({ error: { message: `upstream ${status}` } }), { status }),
  error: null,
  retriesUsed: 0,
  ttfbMs: 5,
})

describe('flag + config plumbing', () => {
  test('failover defaults ON; explicit falsy values disable', () => {
    expect(localFailoverEnabled({})).toBe(true)
    expect(localFailoverEnabled({ JARVIS_LOCAL_LLM_FAILOVER: '1' })).toBe(true)
    for (const off of ['0', 'false', 'no', 'off']) {
      expect(localFailoverEnabled({ JARVIS_LOCAL_LLM_FAILOVER: off })).toBe(false)
    }
  })

  test('model tag defaults to qwen2.5:7b, overridable', () => {
    expect(localFailoverModelTag({})).toBe('qwen2.5:7b')
    expect(localFailoverModelTag({ JARVIS_LOCAL_LLM_MODEL: 'llama3:8b' })).toBe('llama3:8b')
  })

  test('provider builds via the ollama registry synthesis; URL override honored', () => {
    const p = buildLocalFailoverProvider(ENV_ON)
    expect(p).not.toBeNull()
    expect(p!.name).toBe('ollama')
    expect(p!.model).toBe('qwen2.5:7b')
    expect(p!.baseUrl.endsWith('/v1')).toBe(true)

    const custom = buildLocalFailoverProvider({
      JARVIS_LOCAL_LLM_URL: 'http://127.0.0.1:11434/v1/',
    })
    expect(custom!.baseUrl).toBe('http://127.0.0.1:11434/v1')

    expect(buildLocalFailoverProvider(ENV_OFF)).toBeNull()
  })
})

describe('cloudFailureAllowsLocal — which failures count as an outage', () => {
  test('conn error (no HTTP status) and 5xx/408 are outages', () => {
    expect(cloudFailureAllowsLocal(null)).toBe(true)
    expect(cloudFailureAllowsLocal(408)).toBe(true)
    expect(cloudFailureAllowsLocal(500)).toBe(true)
    expect(cloudFailureAllowsLocal(503)).toBe(true)
    expect(cloudFailureAllowsLocal(529)).toBe(true)
  })
  test('429 (quota/rate) and definitive 4xx are NOT outages', () => {
    expect(cloudFailureAllowsLocal(429)).toBe(false)
    expect(cloudFailureAllowsLocal(400)).toBe(false)
    expect(cloudFailureAllowsLocal(401)).toBe(false)
    expect(cloudFailureAllowsLocal(403)).toBe(false)
  })
})

describe('attemptLocalFailover', () => {
  test('serves the request from ollama and reports the local provider', async () => {
    let calledUrl = ''
    let sentBody: any = null
    const attempt = await attemptLocalFailover({
      openaiReq: { model: 'deepseek-chat', messages: [{ role: 'user', content: 'hi' }], max_tokens: 999999 },
      triedProviders: [cloudProvider()],
      env: ENV_ON,
      fetchImpl: (async (url: any, init: any) => {
        calledUrl = String(url)
        sentBody = JSON.parse(init.body)
        return new Response(okBody, { status: 200 })
      }) as any,
    })
    expect(attempt).not.toBeNull()
    expect(attempt!.provider.name).toBe('ollama')
    expect(calledUrl.endsWith('/v1/chat/completions')).toBe(true)
    // Request is re-targeted at the local model and clamped to its caps.
    expect(sentBody.model).toBe('qwen2.5:7b')
    expect(sentBody.max_tokens).toBeLessThanOrEqual(32768)
  })

  test('returns null (never throws) when ollama is down', async () => {
    const attempt = await attemptLocalFailover({
      openaiReq: { messages: [] },
      triedProviders: [cloudProvider()],
      env: ENV_ON,
      fetchImpl: (async () => { throw new Error('ECONNREFUSED 127.0.0.1:11434') }) as any,
    })
    expect(attempt).toBeNull()
  })

  test('skips when the chain already tried ollama (no double-local)', async () => {
    let fetched = 0
    const attempt = await attemptLocalFailover({
      openaiReq: { messages: [] },
      triedProviders: [cloudProvider({ name: 'ollama' })],
      env: ENV_ON,
      fetchImpl: (async () => { fetched++; return new Response(okBody) }) as any,
    })
    expect(attempt).toBeNull()
    expect(fetched).toBe(0)
  })

  test('disabled flag → null, ollama never contacted', async () => {
    let fetched = 0
    const attempt = await attemptLocalFailover({
      openaiReq: { messages: [] },
      triedProviders: [cloudProvider()],
      env: ENV_OFF,
      fetchImpl: (async () => { fetched++; return new Response(okBody) }) as any,
    })
    expect(attempt).toBeNull()
    expect(fetched).toBe(0)
  })
})

describe('executeWithFallback + local tail (end-to-end chain semantics)', () => {
  test('ONLINE UNCHANGED: healthy cloud → local endpoint never contacted', async () => {
    let localCalls = 0
    const outcome = await executeWithFallback(
      cloudProvider(),
      { messages: [] },
      {
        fetcher: fetcherReturning(() => ({
          response: new Response(okBody, { status: 200 }),
          error: null,
          retriesUsed: 0,
          ttfbMs: 3,
        })),
        localFetch: (async () => { localCalls++; return new Response(okBody) }) as any,
        env: ENV_ON,
      },
    )
    expect(outcome.response?.ok).toBe(true)
    expect(outcome.provider.name).toBe('deepseek')
    expect(outcome.fallbackUsed).toBe(false)
    expect(localCalls).toBe(0)
  })

  test('cloud CONNECTION failure → request rerouted to local ollama', async () => {
    const outcome = await executeWithFallback(
      cloudProvider(),
      { messages: [{ role: 'user', content: 'hi' }] },
      {
        fetcher: fetcherReturning(connFailure),
        localFetch: (async () => new Response(okBody, { status: 200 })) as any,
        env: ENV_ON,
      },
    )
    expect(outcome.response?.ok).toBe(true)
    expect(outcome.provider.name).toBe('ollama')
    expect(outcome.provider.model).toBe('qwen2.5:7b')
    expect(outcome.fallbackUsed).toBe(true)
    // The primary's failure is still recorded for observability.
    expect(outcome.primaryError).toContain('ECONNREFUSED')
  })

  test('cloud 5xx exhaustion → rerouted to local ollama', async () => {
    const outcome = await executeWithFallback(
      cloudProvider(),
      { messages: [] },
      {
        fetcher: fetcherReturning(() => statusFailure(503)),
        localFetch: (async () => new Response(okBody, { status: 200 })) as any,
        env: ENV_ON,
      },
    )
    expect(outcome.response?.ok).toBe(true)
    expect(outcome.provider.name).toBe('ollama')
  })

  test('401 bad key → surfaces immediately, local NEVER contacted', async () => {
    let localCalls = 0
    const outcome = await executeWithFallback(
      cloudProvider(),
      { messages: [] },
      {
        fetcher: fetcherReturning(() => statusFailure(401)),
        localFetch: (async () => { localCalls++; return new Response(okBody) }) as any,
        env: ENV_ON,
      },
    )
    expect(outcome.response).toBeNull()
    expect(outcome.upstreamStatus).toBe(401)
    expect(outcome.errorMessage).toContain('HTTP 401')
    expect(localCalls).toBe(0)
  })

  test('429 quota/rate exhaustion → surfaces, local NEVER contacted', async () => {
    let localCalls = 0
    const outcome = await executeWithFallback(
      cloudProvider(),
      { messages: [] },
      {
        fetcher: fetcherReturning(() => statusFailure(429)),
        localFetch: (async () => { localCalls++; return new Response(okBody) }) as any,
        env: ENV_ON,
      },
    )
    expect(outcome.response).toBeNull()
    expect(outcome.errorMessage).toContain('HTTP 429')
    expect(localCalls).toBe(0)
  })

  test('flag off → conn failure surfaces exactly as before (no local attempt)', async () => {
    let localCalls = 0
    const outcome = await executeWithFallback(
      cloudProvider(),
      { messages: [] },
      {
        fetcher: fetcherReturning(connFailure),
        localFetch: (async () => { localCalls++; return new Response(okBody) }) as any,
        env: ENV_OFF,
      },
    )
    expect(outcome.response).toBeNull()
    expect(outcome.errorMessage).toContain('ECONNREFUSED')
    expect(localCalls).toBe(0)
  })

  test('cloud AND local down → the ORIGINAL cloud error surfaces', async () => {
    const outcome = await executeWithFallback(
      cloudProvider(),
      { messages: [] },
      {
        fetcher: fetcherReturning(connFailure),
        localFetch: (async () => { throw new Error('local ollama not running') }) as any,
        env: ENV_ON,
      },
    )
    expect(outcome.response).toBeNull()
    expect(outcome.errorMessage).toContain('ECONNREFUSED')
  })

  test('primary already ollama → no recursive local tail', async () => {
    let localCalls = 0
    const outcome = await executeWithFallback(
      cloudProvider({ name: 'ollama', model: 'qwen2.5:7b', baseUrl: 'http://127.0.0.1:11434/v1' }),
      { messages: [] },
      {
        fetcher: fetcherReturning(connFailure),
        localFetch: (async () => { localCalls++; return new Response(okBody) }) as any,
        env: ENV_ON,
      },
    )
    expect(outcome.response).toBeNull()
    expect(localCalls).toBe(0)
  })
})

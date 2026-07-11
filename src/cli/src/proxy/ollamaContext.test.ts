import { beforeEach, describe, expect, test } from 'bun:test'
import {
  DEFAULT_OLLAMA_NUM_CTX,
  _resetOllamaContextCacheForTests,
  derivedOllamaModelName,
  ensureOllamaNumCtx,
  ollamaNumCtxDisabled,
  resolveOllamaNumCtx,
} from './ollamaContext.js'
import type { Provider } from './providers.js'

function makeProvider(overrides: Partial<Provider> = {}): Provider {
  return {
    name: 'ollama',
    baseUrl: 'http://localhost:11434/v1',
    apiKey: 'ollama',
    model: 'qwen3:4b-instruct-2507-q4_K_M',
    supportsToolChoice: false,
    maxOutputTokens: 32768,
    requiresReasoning: false,
    supportsVision: false,
    jarvisModelId: 'ollama:qwen3:4b-instruct-2507-q4_K_M',
    fallback: [],
    ...overrides,
  }
}

type FetchCall = { url: string; body?: any }

function makeFetchMock(opts: { createStatus?: number } = {}) {
  const calls: FetchCall[] = []
  const fetchImpl = (async (url: any, init?: any) => {
    const u = String(url)
    const body = init?.body ? JSON.parse(init.body as string) : undefined
    calls.push({ url: u, body })
    if (u.endsWith('/api/create')) {
      const status = opts.createStatus ?? 200
      return new Response(
        status === 200 ? '{"status":"success"}' : '{"error":"boom"}',
        { status },
      )
    }
    if (u.endsWith('/api/tags')) {
      return new Response(JSON.stringify({ models: [] }), { status: 200 })
    }
    if (u.endsWith('/api/delete')) {
      return new Response('{}', { status: 200 })
    }
    return new Response('{}', { status: 200 })
  }) as typeof fetch
  return { fetchImpl, calls }
}

beforeEach(() => {
  _resetOllamaContextCacheForTests()
})

describe('resolveOllamaNumCtx', () => {
  test('defaults to 32768 when unset', () => {
    expect(resolveOllamaNumCtx({} as NodeJS.ProcessEnv)).toBe(
      DEFAULT_OLLAMA_NUM_CTX,
    )
    expect(DEFAULT_OLLAMA_NUM_CTX).toBe(32768)
  })

  test('honors a positive integer override', () => {
    expect(
      resolveOllamaNumCtx({ JARVIS_OLLAMA_NUM_CTX: '12288' } as any),
    ).toBe(12288)
  })

  test('falls back to the default for garbage or sub-2048 values', () => {
    expect(resolveOllamaNumCtx({ JARVIS_OLLAMA_NUM_CTX: 'lots' } as any)).toBe(
      DEFAULT_OLLAMA_NUM_CTX,
    )
    expect(resolveOllamaNumCtx({ JARVIS_OLLAMA_NUM_CTX: '512' } as any)).toBe(
      DEFAULT_OLLAMA_NUM_CTX,
    )
    expect(resolveOllamaNumCtx({ JARVIS_OLLAMA_NUM_CTX: '-1' } as any)).toBe(
      DEFAULT_OLLAMA_NUM_CTX,
    )
  })

  test('disable values are recognized', () => {
    for (const v of ['0', 'off', 'false', 'no', 'OFF']) {
      expect(ollamaNumCtxDisabled({ JARVIS_OLLAMA_NUM_CTX: v } as any)).toBe(
        true,
      )
    }
    expect(ollamaNumCtxDisabled({} as any)).toBe(false)
    expect(
      ollamaNumCtxDisabled({ JARVIS_OLLAMA_NUM_CTX: '16384' } as any),
    ).toBe(false)
  })
})

describe('derivedOllamaModelName', () => {
  test('flattens ":" and "/" and appends the marker + ctx tag', () => {
    expect(derivedOllamaModelName('qwen3:4b-instruct-2507-q4_K_M', 32768)).toBe(
      'qwen3-4b-instruct-2507-q4_K_M-jarvis-ctx:32768',
    )
    expect(derivedOllamaModelName('library/llama3:8b', 8192)).toBe(
      'library-llama3-8b-jarvis-ctx:8192',
    )
    expect(derivedOllamaModelName('mymodel', 16384)).toBe(
      'mymodel-jarvis-ctx:16384',
    )
  })
})

describe('ensureOllamaNumCtx', () => {
  test('non-ollama providers pass through untouched, no daemon traffic', async () => {
    const { fetchImpl, calls } = makeFetchMock()
    const provider = makeProvider({ name: 'deepseek' })
    const out = await ensureOllamaNumCtx(provider, fetchImpl, {} as any)
    expect(out).toBe(provider)
    expect(calls.length).toBe(0)
  })

  test('creates the derived model and routes to it', async () => {
    const { fetchImpl, calls } = makeFetchMock()
    const out = await ensureOllamaNumCtx(makeProvider(), fetchImpl, {} as any)
    expect(out.model).toBe('qwen3-4b-instruct-2507-q4_K_M-jarvis-ctx:32768')
    const create = calls.find(c => c.url.endsWith('/api/create'))
    expect(create).toBeDefined()
    // Native API lives at the daemon root, not under /v1.
    expect(create!.url).toBe('http://localhost:11434/api/create')
    expect(create!.body).toMatchObject({
      model: 'qwen3-4b-instruct-2507-q4_K_M-jarvis-ctx:32768',
      from: 'qwen3:4b-instruct-2507-q4_K_M',
      parameters: { num_ctx: 32768 },
    })
  })

  test('honors JARVIS_OLLAMA_NUM_CTX from the provided env', async () => {
    const { fetchImpl } = makeFetchMock()
    const out = await ensureOllamaNumCtx(makeProvider(), fetchImpl, {
      JARVIS_OLLAMA_NUM_CTX: '12288',
    } as any)
    expect(out.model).toBe('qwen3-4b-instruct-2507-q4_K_M-jarvis-ctx:12288')
  })

  test('memoizes: a second request does not re-create', async () => {
    const { fetchImpl, calls } = makeFetchMock()
    await ensureOllamaNumCtx(makeProvider(), fetchImpl, {} as any)
    const createsAfterFirst = calls.filter(c =>
      c.url.endsWith('/api/create'),
    ).length
    await ensureOllamaNumCtx(makeProvider(), fetchImpl, {} as any)
    const createsAfterSecond = calls.filter(c =>
      c.url.endsWith('/api/create'),
    ).length
    expect(createsAfterFirst).toBe(1)
    expect(createsAfterSecond).toBe(1)
  })

  test('falls back to the base tag when create fails, and retries next time', async () => {
    const failing = makeFetchMock({ createStatus: 500 })
    const provider = makeProvider()
    const out1 = await ensureOllamaNumCtx(provider, failing.fetchImpl, {} as any)
    expect(out1.model).toBe(provider.model)
    // Failure must NOT be memoized — a healthy daemon later should recover.
    const healthy = makeFetchMock()
    const out2 = await ensureOllamaNumCtx(provider, healthy.fetchImpl, {} as any)
    expect(out2.model).toBe('qwen3-4b-instruct-2507-q4_K_M-jarvis-ctx:32768')
  })

  test('disabled knob returns the provider unchanged with no daemon traffic', async () => {
    const { fetchImpl, calls } = makeFetchMock()
    const provider = makeProvider()
    const out = await ensureOllamaNumCtx(provider, fetchImpl, {
      JARVIS_OLLAMA_NUM_CTX: '0',
    } as any)
    expect(out).toBe(provider)
    expect(calls.length).toBe(0)
  })

  test('already-derived tags are never re-derived (no variant-of-variant)', async () => {
    const { fetchImpl, calls } = makeFetchMock()
    const provider = makeProvider({
      model: 'qwen3-4b-instruct-2507-q4_K_M-jarvis-ctx:32768',
    })
    const out = await ensureOllamaNumCtx(provider, fetchImpl, {} as any)
    expect(out).toBe(provider)
    expect(calls.length).toBe(0)
  })

  test('cleans up stale variants of the same base after a create', async () => {
    const calls: FetchCall[] = []
    const fetchImpl = (async (url: any, init?: any) => {
      const u = String(url)
      const body = init?.body ? JSON.parse(init.body as string) : undefined
      calls.push({ url: u, body })
      if (u.endsWith('/api/create')) {
        return new Response('{"status":"success"}', { status: 200 })
      }
      if (u.endsWith('/api/tags')) {
        return new Response(
          JSON.stringify({
            models: [
              { model: 'qwen3:4b-instruct-2507-q4_K_M' },
              { model: 'qwen3-4b-instruct-2507-q4_K_M-jarvis-ctx:8192' },
              { model: 'qwen3-4b-instruct-2507-q4_K_M-jarvis-ctx:32768' },
              { model: 'other-model-jarvis-ctx:8192' },
            ],
          }),
          { status: 200 },
        )
      }
      if (u.endsWith('/api/delete')) {
        return new Response('{}', { status: 200 })
      }
      return new Response('{}', { status: 200 })
    }) as typeof fetch
    await ensureOllamaNumCtx(makeProvider(), fetchImpl, {} as any)
    // Cleanup is fire-and-forget; give the microtask queue a beat.
    await new Promise(r => setTimeout(r, 10))
    const deletes = calls.filter(c => c.url.endsWith('/api/delete'))
    expect(deletes.length).toBe(1)
    // Only the SAME base's stale variant goes — never the base model, the
    // current variant, or another base's variants.
    expect(deletes[0].body).toMatchObject({
      model: 'qwen3-4b-instruct-2507-q4_K_M-jarvis-ctx:8192',
    })
  })
})

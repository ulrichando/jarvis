import { beforeAll, describe, expect, test } from 'bun:test'

beforeAll(() => {
  process.env.DEEPSEEK_API_KEY = 'test-deepseek'
  process.env.ANTHROPIC_API_KEY = 'test-anthropic'
  process.env.OPENAI_API_KEY = 'test-openai'
  process.env.KIMI_API_KEY = 'test-kimi'
  process.env.GOOGLE_API_KEY = 'test-gemini'
  process.env.GEMINI_API_KEY = 'test-gemini'
  // Deterministic default provider for the no-model case.
  process.env.JARVIS_PROVIDER = 'deepseek'
})

import { classifyChatCompletionsRequest, buildHubConfig } from './hubGateway.js'

describe('classifyChatCompletionsRequest', () => {
  test('OpenAI-family model routes to its provider', () => {
    const r = classifyChatCompletionsRequest('deepseek-v4-flash')
    expect(r.kind).toBe('route')
    if (r.kind === 'route') expect(r.provider.name).toBe('deepseek')
  })

  test('Anthropic model is rejected — must use /v1/messages', () => {
    const r = classifyChatCompletionsRequest('claude-haiku-4-5')
    expect(r.kind).toBe('reject')
    if (r.kind === 'reject') expect(r.status).toBe(400)
  })

  test('absent model falls back to the default provider (non-anthropic here)', () => {
    const r = classifyChatCompletionsRequest(undefined)
    expect(r.kind).toBe('route')
    if (r.kind === 'route') expect(r.provider.name).toBe('deepseek')
  })
})

describe('buildHubConfig', () => {
  test('reports provider key-presence from env', () => {
    const cfg = buildHubConfig()
    expect(cfg.status).toBe('ok')
    expect(cfg.providers.deepseek).toBe(true)   // set in beforeAll
    expect(cfg.default_provider).toBe('deepseek')
  })

  test('a provider with no key reads false', () => {
    const saved = process.env.OPENAI_API_KEY
    delete process.env.OPENAI_API_KEY
    expect(buildHubConfig().providers.openai).toBe(false)
    process.env.OPENAI_API_KEY = saved
  })
})

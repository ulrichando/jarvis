import { beforeAll, describe, expect, test } from 'bun:test'

// The Provider builder calls resolveApiKey() which reads provider-keyed
// env vars. Tests don't hit upstream — these dummies just keep the
// builder from throwing. Ollama has no key requirement.
beforeAll(() => {
  process.env.DEEPSEEK_API_KEY = 'test-deepseek'
  process.env.GOOGLE_API_KEY = 'test-gemini'
  process.env.OPENAI_API_KEY = 'test-openai'
  process.env.KIMI_API_KEY = 'test-kimi'
  process.env.ANTHROPIC_API_KEY = 'test-anthropic'
})

import { getProviderForModel } from './providers.js'

describe('providers — supportsVision plumbing', () => {
  // Vision-capable models per jarvisModelRegistry.test.ts.
  // Keep this list in sync with the registry's allowlist — the
  // registry test owns the "what's vision-capable" intent; this
  // test owns "the Provider type carries it through".
  test.each([
    ['gpt-4o', true],
    ['gpt-4o-mini', true],
    ['gpt-5-mini', true],
    ['gpt-5', true],
    ['gpt-5.1', true],
    ['gemini-flash', true],
    ['gemini-2.0-flash', true],
    ['gemini-pro', true],
    ['gemini-2.5-pro', true],
    ['kimi-k2.6-instant', true],
    ['kimi-k2.6-thinking', true],
    ['kimi-k2.6-agent', true],
    ['kimi-k2.6-swarm', true],
  ] as const)('provider for %s has supportsVision: %p', (modelId, expected) => {
    const p = getProviderForModel(modelId)
    expect(p).not.toBeNull()
    expect(p?.supportsVision).toBe(expected)
  })

  test.each([
    ['gpt-5-nano', false],
    ['deepseek-chat', false],
    ['deepseek-reasoner', false],
    ['deepseek-v4-flash', false],
    ['deepseek-v4-pro', false],
  ] as const)('provider for %s has supportsVision: %p (default false)', (modelId, expected) => {
    const p = getProviderForModel(modelId)
    expect(p).not.toBeNull()
    expect(p?.supportsVision).toBe(expected)
  })

  test('Provider.supportsVision is always boolean (never undefined)', () => {
    // Defends against accidentally exposing the optional registry shape.
    // Consumers (convert.ts) read this as a hard boolean; undefined
    // would silently coerce to false but break === comparisons.
    const p = getProviderForModel('gpt-4o')
    expect(typeof p?.supportsVision).toBe('boolean')
    const q = getProviderForModel('deepseek-chat')
    expect(typeof q?.supportsVision).toBe('boolean')
  })
})

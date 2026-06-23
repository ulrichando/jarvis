import { beforeAll, describe, expect, test } from 'bun:test'

beforeAll(() => {
  process.env.DEEPSEEK_API_KEY = 'test-deepseek'
  process.env.GROQ_API_KEY = 'test-groq'
  process.env.GOOGLE_API_KEY = 'test-gemini'
  process.env.GEMINI_API_KEY = 'test-gemini'
  process.env.OPENAI_API_KEY = 'test-openai'
  process.env.KIMI_API_KEY = 'test-kimi'
  process.env.ANTHROPIC_API_KEY = 'test-anthropic'
})

import { getProviderForModel } from './providers.js'

// All values verified against vendor docs on 2026-05-27 by a researcher
// agent. Sources: docs.anthropic.com, api-docs.deepseek.com,
// developers.openai.com/api/docs/models, ai.google.dev/gemini-api,
// console.groq.com/docs/models, platform.moonshot.ai. See PR #33.
describe('maxOutputTokens — provider defaults (post-2026-05-27 bumps)', () => {
  test.each([
    // Anthropic: METADATA only — passthrough doesn't use this. Per
    // platform.claude.com/docs/en/about-claude/models/overview verified
    // 2026-05-27: Sonnet 4.6 + Haiku 4.5 both 64K, Opus 4.7 = 128K.
    ['claude-haiku-4-5', 65536],
    ['claude-sonnet-4-6', 65536],
    // DeepSeek: bumped 32K → 64K. v4-pro thinking needs the headroom.
    ['deepseek-chat', 65536],
    ['deepseek-reasoner', 65536],
    ['deepseek-v4-flash', 65536],
    ['deepseek-v4-pro', 65536],
    // OpenAI provider default 32K applies to the GPT-5 family.
    ['gpt-5-nano', 32768],
    ['gpt-5-mini', 32768],
    ['gpt-5', 32768],
    ['gpt-5.1', 32768],
    // Gemini: bumped 8K → 32K. Live-verified Google's OpenAI-compat
    // accepts max_tokens=16384 with finish_reason=stop.
    ['gemini-flash', 32768],
    ['gemini-2.0-flash', 32768],
    ['gemini-pro', 32768],
    ['gemini-2.5-pro', 32768],
    // Kimi: bumped 16K → 32K. Conservative middle below OpenRouter's
    // unverified 49K per-step ceiling.
    ['kimi-k2.6-instant', 32768],
    ['kimi-k2.6-thinking', 32768],
    ['kimi-k2.6-agent', 32768],
    ['kimi-k2.6-swarm', 32768],
    // Groq provider default 32K applies to qwen3, llama-3.x, gpt-oss.
    ['qwen/qwen3-32b', 32768],
    ['llama-3.3-70b-versatile', 32768],
    ['llama-3.1-8b-instant', 32768],
    ['openai/gpt-oss-120b', 32768],
  ] as const)('%s → maxOutputTokens = %p', (modelId, expected) => {
    const p = getProviderForModel(modelId)
    expect(p).not.toBeNull()
    expect(p?.maxOutputTokens).toBe(expected)
  })
})

describe('maxOutputTokens — per-model overrides (stricter than provider default)', () => {
  // These three models have lower API caps than their provider family.
  // The override in the registry pins them so requests don't carry a
  // too-high max_tokens that the upstream would 400 on.

  test('gpt-4o → 16K (OpenAI provider default 32K → overridden)', () => {
    expect(getProviderForModel('gpt-4o')?.maxOutputTokens).toBe(16384)
  })

  test('gpt-4o-mini → 16K (OpenAI provider default 32K → overridden)', () => {
    expect(getProviderForModel('gpt-4o-mini')?.maxOutputTokens).toBe(16384)
  })

  test('meta-llama/llama-4-scout → 8K (Groq provider default 32K → overridden)', () => {
    expect(
      getProviderForModel('meta-llama/llama-4-scout-17b-16e-instruct')?.maxOutputTokens,
    ).toBe(8192)
  })

  test('claude-opus-4-8 → 128K (Anthropic provider default 64K → overridden upward)', () => {
    // Per Anthropic docs, Opus 4.8 supports 128K output (vs Sonnet 4.6 /
    // Haiku 4.5 at 64K). METADATA-only since Anthropic uses passthrough,
    // but kept accurate for any future UI surfacing of model capabilities.
    expect(getProviderForModel('claude-opus-4-8')?.maxOutputTokens).toBe(131072)
  })
})

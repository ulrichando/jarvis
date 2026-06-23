import { describe, expect, test } from 'bun:test'
import {
  getJarvisModel,
  getJarvisModelCapabilityOverride,
  getJarvisModels,
} from './jarvisModelRegistry.js'

// Models we have explicitly confirmed support OpenAI-shape image_url
// content blocks via their upstream API (per provider docs as of
// 2026-05-26). Each addition here must be backed by a real curl test
// before flipping the flag — phantom vision support causes silent
// 400s mid-stream when the upstream rejects the image_url part.
const VISION_CAPABLE_MODELS = [
  // OpenAI multimodal models — official vision support
  'gpt-4o',
  'gpt-4o-mini',
  'gpt-5-mini',
  'gpt-5',
  'gpt-5.1',
  // Gemini natively multimodal across both Flash and Pro tiers
  'gemini-flash',
  'gemini-2.0-flash',
  'gemini-pro',
  'gemini-2.5-pro',
  // Kimi K2.6 — verified via curl 2026-05-27 (Moonshot's
  // /v1/chat/completions accepts image_url base64 and correctly
  // names the dominant color of a solid-color test PNG). All four
  // jarvis ids share upstream `kimi-k2.6` so all four flip together.
  'kimi-k2.6-instant',
  'kimi-k2.6-thinking',
  'kimi-k2.6-agent',
  'kimi-k2.6-swarm',
] as const

// Explicitly NOT vision (either upstream doesn't support it, or
// upstream claims to but real testing shows it doesn't actually
// process pixels).
// gpt-5-nano: OpenAI lists it as text-only.
// llama-4-scout: VERIFIED FAILING 2026-05-27. Groq's vision endpoint
//   accepts the image_url shape (no error), but the model returns
//   hallucinated content unrelated to the actual image (called a
//   solid pink PNG "a black-and-white Copy to clipboard icon"; said
//   "black" for both pink AND green test images). Either Groq's
//   deployment doesn't have functional vision wired up, or Scout's
//   vision is too weak to ship. Flipping would silently degrade UX.
// All deepseek-*: text-only models; deepseek-VL not in registry.
// All other groq models: text-only (qwen, llama-3.x, gpt-oss-120b).
// ollama: depends on the local model — punt for now.
// claude-*: anthropic-passthrough, vision handled natively (not via
//   the OpenAI-shape converter); the flag here is moot for them.
const NON_VISION_MODELS = [
  'gpt-5-nano',
  'deepseek-chat',
  'deepseek-reasoner',
  'deepseek-v4-flash',
  'deepseek-v4-pro',
  'qwen/qwen3-32b',
  'llama-3.3-70b-versatile',
  'llama-3.1-8b-instant',
  'openai/gpt-oss-120b',
  'meta-llama/llama-4-scout-17b-16e-instruct',
] as const

describe('jarvisModelRegistry — supportsVision', () => {
  test.each(VISION_CAPABLE_MODELS)(
    'model %s is marked supportsVision: true',
    (modelId) => {
      const m = getJarvisModel(modelId)
      expect(m).not.toBeNull()
      expect(m?.supportsVision).toBe(true)
    },
  )

  test.each(NON_VISION_MODELS)(
    'model %s is NOT marked supportsVision (proxy will flatten images to "[image]")',
    (modelId) => {
      const m = getJarvisModel(modelId)
      expect(m).not.toBeNull()
      // Default undefined is treated as false by the proxy — either is OK.
      expect(m?.supportsVision ?? false).toBe(false)
    },
  )

  test('no model declares supportsVision without being in the explicit list', () => {
    // Guard against drive-by `supportsVision: true` additions that
    // bypass the verification process. If you're adding a new
    // vision-capable model, ALSO add it to VISION_CAPABLE_MODELS
    // above so the snapshot of intent stays in sync.
    const flagged = getJarvisModels()
      .filter((m) => m.supportsVision === true)
      .map((m) => m.id)
      .sort()
    const expected = [...VISION_CAPABLE_MODELS].sort()
    expect(flagged).toEqual(expected)
  })
})

describe('jarvisModelRegistry — thinking capability override', () => {
  // Regression: Anthropic tiers are declared `adaptive_thinking` (not the
  // literal `thinking`). modelSupportsThinking() queries `thinking`; if the
  // override returns false here, the CLI omits the `thinking` param but still
  // attaches clear_thinking → every Claude request 400s. adaptive/interleaved
  // thinking MUST imply base thinking support.
  test.each(['claude-opus-4-8', 'claude-sonnet-4-6', 'claude-haiku-4-5'])(
    'adaptive-thinking model %s reports the `thinking` capability',
    (modelId) => {
      expect(getJarvisModel(modelId)).not.toBeNull()
      expect(getJarvisModelCapabilityOverride(modelId, 'thinking')).toBe(true)
    },
  )

  test('non-thinking capabilities are still answered literally', () => {
    // haiku-4-5 has adaptive_thinking but NOT effort — must stay false.
    expect(getJarvisModelCapabilityOverride('claude-haiku-4-5', 'effort')).toBe(
      false,
    )
    expect(getJarvisModelCapabilityOverride('claude-opus-4-8', 'effort')).toBe(
      true,
    )
  })

  test('unknown model returns undefined (no override)', () => {
    expect(
      getJarvisModelCapabilityOverride('totally-made-up-model', 'thinking'),
    ).toBeUndefined()
  })

  test('Opus is on 4.8, not the retired 4.7 id', () => {
    expect(getJarvisModel('claude-opus-4-8')).toBeDefined()
    expect(getJarvisModel('claude-opus-4-7')).toBeUndefined()
  })
})

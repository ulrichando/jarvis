import { describe, expect, test } from 'bun:test'
import {
  getJarvisModel,
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
] as const

// Explicitly NOT vision (either upstream doesn't support it, or we
// haven't verified yet — pending curl smoke test before flipping).
// gpt-5-nano: OpenAI lists it as text-only.
// llama-4-scout: pending verification (Groq's OpenAI-compat layer).
// kimi-k2.6-instant: pending verification (Moonshot's docs claim
//   multimodal but the request shape isn't documented for our path).
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

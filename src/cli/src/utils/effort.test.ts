import { beforeAll, describe, expect, test } from 'bun:test'
import {
  clampEffortToModel,
  convertEffortValueToLevel,
  getEffortValueDescription,
  isUltracodeActive,
  modelSupportsXhighEffort,
  resolveAppliedEffort,
  toPersistableEffort,
  ULTRACODE,
} from './effort.js'

// The per-model effort ladder rides on the jarvis registry capabilities
// (xhigh_effort / max_effort); mirror the live launcher env.
beforeAll(() => {
  process.env.JARVIS_MODEL_REGISTRY_ENABLED = '1'
  delete process.env.CLAUDE_CODE_EFFORT_LEVEL
  delete process.env.CLAUDE_CODE_ALWAYS_ENABLE_EFFORT
})

describe('clampEffortToModel', () => {
  test('max stays max on max-capable models', () => {
    expect(clampEffortToModel('max', 'claude-sonnet-4-6')).toBe('max')
    expect(clampEffortToModel('max', 'claude-opus-4-8')).toBe('max')
    expect(clampEffortToModel('max', 'claude-fable-5[1m]')).toBe('max')
  })

  test('max clamps to xhigh on DeepSeek (xhigh_effort, no max_effort)', () => {
    expect(clampEffortToModel('max', 'deepseek-v4-pro')).toBe('xhigh')
    expect(clampEffortToModel('xhigh', 'deepseek-v4-pro')).toBe('xhigh')
  })

  test('max and xhigh clamp to high on OpenAI GPT-5 (effort only)', () => {
    expect(clampEffortToModel('max', 'gpt-5')).toBe('high')
    expect(clampEffortToModel('xhigh', 'gpt-5')).toBe('high')
  })

  test('levels at or below a model ceiling pass through', () => {
    expect(clampEffortToModel('high', 'gpt-5')).toBe('high')
    expect(clampEffortToModel('low', 'deepseek-v4-pro')).toBe('low')
    expect(clampEffortToModel('medium', 'claude-sonnet-4-6')).toBe('medium')
  })
})

describe('modelSupportsXhighEffort', () => {
  test('max-capable implies xhigh-capable', () => {
    expect(modelSupportsXhighEffort('claude-sonnet-4-6')).toBe(true)
  })
  test('registry xhigh_effort capability is honored', () => {
    expect(modelSupportsXhighEffort('deepseek-v4-pro')).toBe(true)
    expect(modelSupportsXhighEffort('deepseek-reasoner')).toBe(true)
  })
  test('effort-only registry models are not xhigh-capable', () => {
    expect(modelSupportsXhighEffort('gpt-5')).toBe(false)
    expect(modelSupportsXhighEffort('gpt-5-mini')).toBe(false)
  })
})

describe('ultracode session pseudo-level', () => {
  test('resolves to xhigh on xhigh-capable models', () => {
    expect(resolveAppliedEffort('claude-sonnet-4-6', ULTRACODE)).toBe('xhigh')
    expect(resolveAppliedEffort('deepseek-v4-pro', ULTRACODE)).toBe('xhigh')
  })
  test('degrades to high where xhigh is unsupported', () => {
    expect(resolveAppliedEffort('gpt-5', ULTRACODE)).toBe('high')
  })
  test('never persists to settings', () => {
    expect(toPersistableEffort(ULTRACODE)).toBeUndefined()
  })
  test('displays as xhigh with the workflow description', () => {
    expect(convertEffortValueToLevel(ULTRACODE)).toBe('xhigh')
    expect(getEffortValueDescription(ULTRACODE)).toBe(
      'xhigh + dynamic workflow orchestration',
    )
  })
  test('isUltracodeActive matches only the pseudo-level', () => {
    expect(isUltracodeActive(ULTRACODE)).toBe(true)
    expect(isUltracodeActive('xhigh')).toBe(false)
    expect(isUltracodeActive(undefined)).toBe(false)
  })
})

describe('resolveAppliedEffort max clamping', () => {
  test('max requested on DeepSeek lands on xhigh (was high)', () => {
    expect(resolveAppliedEffort('deepseek-v4-pro', 'max')).toBe('xhigh')
  })
  test('max requested on GPT-5 lands on high', () => {
    expect(resolveAppliedEffort('gpt-5', 'max')).toBe('high')
  })
  test('max requested on Anthropic 4.6+/Fable stays max', () => {
    expect(resolveAppliedEffort('claude-opus-4-8', 'max')).toBe('max')
    expect(resolveAppliedEffort('claude-fable-5[1m]', 'max')).toBe('max')
  })
})

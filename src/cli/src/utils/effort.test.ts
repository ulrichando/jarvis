import { beforeAll, describe, expect, test } from 'bun:test'
import {
  clampEffortToModel,
  convertEffortValueToLevel,
  findUltrathinkBoostForTurn,
  getEffortValueDescription,
  isUltracodeActive,
  modelSupportsXhighEffort,
  raiseEffortValue,
  resolveAppliedEffort,
  toPersistableEffort,
  ULTRACODE,
} from './effort.js'
import type { Message } from '../types/message.js'

// Minimal Message-shaped literals for findUltrathinkBoostForTurn — only the
// discriminant fields the scan reads matter.
const userMsg = { type: 'user' } as unknown as Message
const assistantMsg = { type: 'assistant' } as unknown as Message
const ultrathinkMsg = (level: 'max' | 'xhigh' | 'high') =>
  ({
    type: 'attachment',
    attachment: { type: 'ultrathink_effort', level },
  }) as unknown as Message

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

describe('raiseEffortValue (ultrathink turn boost)', () => {
  test('undefined session takes the boost', () => {
    expect(raiseEffortValue(undefined, 'max')).toBe('max')
  })
  test('raises a lower session level to the boost', () => {
    expect(raiseEffortValue('medium', 'max')).toBe('max')
    expect(raiseEffortValue('high', 'xhigh')).toBe('xhigh')
  })
  test('never lowers — an equal-or-higher session value is kept', () => {
    expect(raiseEffortValue('max', 'xhigh')).toBe('max')
    expect(raiseEffortValue('high', 'high')).toBe('high')
  })
  test('ULTRACODE (rides xhigh): kept when boost ties, raised when boost exceeds', () => {
    // xhigh-ceiling model → boost is xhigh → ULTRACODE identity preserved so
    // workflow mode + the xhigh resolution survive.
    expect(raiseEffortValue(ULTRACODE, 'xhigh')).toBe(ULTRACODE)
    // max-capable model → boost is max → wire effort raised (appState stays
    // ULTRACODE elsewhere; this value only feeds the API effort param).
    expect(raiseEffortValue(ULTRACODE, 'max')).toBe('max')
  })
  test('ant numeric values keep their identity when their tier suffices', () => {
    // Numeric effort is ant-only (convertEffortValueToLevel gates on USER_TYPE);
    // outside ant, numbers resolve to 'high' and get raised like any other.
    const prev = process.env.USER_TYPE
    process.env.USER_TYPE = 'ant'
    try {
      expect(raiseEffortValue(130, 'max')).toBe(130) // 130 > 128 → max tier, kept
      expect(raiseEffortValue(40, 'max')).toBe('max') // 40 → low tier, raised
    } finally {
      if (prev === undefined) delete process.env.USER_TYPE
      else process.env.USER_TYPE = prev
    }
  })
})

describe('findUltrathinkBoostForTurn', () => {
  test('finds the attachment in the trailing user segment', () => {
    expect(
      findUltrathinkBoostForTurn([userMsg, ultrathinkMsg('max')]),
    ).toBe('max')
  })
  test('stops at the first assistant message (prior-turn isolation)', () => {
    // An older ultrathink attachment behind a completed assistant turn does
    // not leak into the current turn.
    expect(
      findUltrathinkBoostForTurn([
        ultrathinkMsg('max'),
        assistantMsg,
        userMsg,
      ]),
    ).toBeUndefined()
  })
  test('returns undefined for empty / no-attachment tails', () => {
    expect(findUltrathinkBoostForTurn([])).toBeUndefined()
    expect(findUltrathinkBoostForTurn([userMsg])).toBeUndefined()
  })
})

describe('ultrathink end-to-end (raise → resolve → per-model clamp)', () => {
  const boostFor = (model: string) =>
    raiseEffortValue(undefined, clampEffortToModel('max', model))
  test('lands on the model ceiling', () => {
    expect(resolveAppliedEffort('claude-fable-5[1m]', boostFor('claude-fable-5[1m]'))).toBe('max')
    expect(resolveAppliedEffort('claude-opus-4-8', boostFor('claude-opus-4-8'))).toBe('max')
    expect(resolveAppliedEffort('deepseek-v4-pro', boostFor('deepseek-v4-pro'))).toBe('xhigh')
    expect(resolveAppliedEffort('gpt-5', boostFor('gpt-5'))).toBe('high')
  })
  test('CLAUDE_CODE_EFFORT_LEVEL env override still wins absolutely', () => {
    process.env.CLAUDE_CODE_EFFORT_LEVEL = 'low'
    expect(resolveAppliedEffort('claude-fable-5[1m]', 'max')).toBe('low')
    delete process.env.CLAUDE_CODE_EFFORT_LEVEL
  })
})

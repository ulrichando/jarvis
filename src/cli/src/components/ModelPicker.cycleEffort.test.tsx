import { describe, expect, test } from 'bun:test'

import { cycleEffortLevel } from './ModelPicker.js'

// The picker's ← → effort cycle must offer only the rungs the focused model's
// API accepts (mirrors clampEffortToModel's max → xhigh → high ladder) —
// otherwise the picker shows ◉ xhigh while the wire carries high.
describe('cycleEffortLevel per-model ladder', () => {
  test('max-capable model (Opus): full ladder, wraps max → low', () => {
    expect(cycleEffortLevel('high', 'right', true, true)).toBe('xhigh')
    expect(cycleEffortLevel('xhigh', 'right', true, true)).toBe('max')
    expect(cycleEffortLevel('max', 'right', true, true)).toBe('low')
  })

  test('xhigh-but-not-max model (DeepSeek v4): tops out at xhigh', () => {
    expect(cycleEffortLevel('high', 'right', false, true)).toBe('xhigh')
    expect(cycleEffortLevel('xhigh', 'right', false, true)).toBe('low')
    expect(cycleEffortLevel('low', 'left', false, true)).toBe('xhigh')
  })

  test('effort-only model (GPT-5 / gpt-oss): xhigh is NOT offered', () => {
    expect(cycleEffortLevel('high', 'right', false, false)).toBe('low')
    expect(cycleEffortLevel('low', 'left', false, false)).toBe('high')
  })

  test('stale out-of-ladder level (model switch) re-enters at high', () => {
    // e.g. effort was 'xhigh' on DeepSeek, focus moves to GPT-5: treat as high.
    expect(cycleEffortLevel('xhigh', 'right', false, false)).toBe('low')
    expect(cycleEffortLevel('max', 'left', false, false)).toBe('medium')
  })

  test('includeMax forces xhigh into the ladder even if the flag disagrees (max implies xhigh)', () => {
    expect(cycleEffortLevel('high', 'right', true, false)).toBe('xhigh')
  })
})

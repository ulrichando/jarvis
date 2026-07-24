import { describe, expect, test } from 'bun:test'
import { parseGoalVerdict } from './goalEvaluator.js'

describe('parseGoalVerdict — first non-empty line must start with YES/NO', () => {
  test('plain YES / NO with reasoning on following lines', () => {
    const yes = parseGoalVerdict('YES\nThe file exists and contains DONE.')
    expect(yes).toEqual({ met: true, reason: 'The file exists and contains DONE.' })
    const no = parseGoalVerdict('NO\nThe test still fails.')
    expect(no).toEqual({ met: false, reason: 'The test still fails.' })
  })

  test('reasoning on the same line as the verdict token is captured', () => {
    expect(parseGoalVerdict('NO — the file is empty')).toEqual({
      met: false,
      reason: 'the file is empty',
    })
    expect(parseGoalVerdict('YES: all three turns confirmed')).toEqual({
      met: true,
      reason: 'all three turns confirmed',
    })
  })

  test('markdown decoration and leading bullets are tolerated', () => {
    expect(parseGoalVerdict('**NO** — not yet')?.met).toBe(false)
    expect(parseGoalVerdict('- YES: done')?.met).toBe(true)
    expect(parseGoalVerdict('> no, still missing the file')?.met).toBe(false)
  })

  test('case-insensitive verdict token', () => {
    expect(parseGoalVerdict('yes')?.met).toBe(true)
    expect(parseGoalVerdict('No\nreason')?.met).toBe(false)
  })

  test('bare verdict with no reasoning gets a default reason', () => {
    expect(parseGoalVerdict('YES')).toEqual({ met: true, reason: 'Condition met.' })
    expect(parseGoalVerdict('NO')).toEqual({
      met: false,
      reason: 'Condition not yet met.',
    })
  })

  test('leading blank lines before the verdict are skipped', () => {
    expect(parseGoalVerdict('\n\n  YES\ndone')?.met).toBe(true)
  })

  test('combines same-line + following-line reasoning', () => {
    expect(
      parseGoalVerdict('YES the file exists\nand it contains exactly DONE'),
    ).toEqual({
      met: true,
      reason: 'the file exists and it contains exactly DONE',
    })
  })

  test('ambiguous / empty replies return null (caller fails open)', () => {
    expect(parseGoalVerdict('maybe, hard to tell')).toBeNull()
    expect(parseGoalVerdict('')).toBeNull()
    expect(parseGoalVerdict('   \n   ')).toBeNull()
    // "NOPE" is not the word "NO" (no word boundary) → ambiguous, not a NO.
    expect(parseGoalVerdict('NOPE')).toBeNull()
  })

  test('long reasoning is truncated to keep continuation messages bounded', () => {
    const v = parseGoalVerdict(`NO\n${'x'.repeat(600)}`)
    expect(v?.met).toBe(false)
    expect(v!.reason.length).toBeLessThanOrEqual(401) // 400 cap + ellipsis
    expect(v!.reason.endsWith('…')).toBe(true)
  })
})

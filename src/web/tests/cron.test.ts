import { describe, expect, test } from 'vitest'
import { cronMatches, cronIsDue, parseNaturalSchedule } from '@/lib/cron'

describe('cronMatches', () => {
  test('hourly (0 * * * *)', () => {
    expect(cronMatches('0 * * * *', new Date(2026, 5, 12, 9, 0))).toBe(true)
    expect(cronMatches('0 * * * *', new Date(2026, 5, 12, 9, 1))).toBe(false)
  })
  test('daily at 9 (0 9 * * *)', () => {
    expect(cronMatches('0 9 * * *', new Date(2026, 5, 12, 9, 0))).toBe(true)
    expect(cronMatches('0 9 * * *', new Date(2026, 5, 12, 10, 0))).toBe(false)
  })
  test('step (*/15 * * * *)', () => {
    expect(cronMatches('*/15 * * * *', new Date(2026, 5, 12, 9, 15))).toBe(true)
    expect(cronMatches('*/15 * * * *', new Date(2026, 5, 12, 9, 16))).toBe(false)
  })
  test('weekday range (0 9 * * 1-5) matches iff dow in 1..5', () => {
    for (let day = 8; day <= 14; day++) {
      const d = new Date(2026, 5, day, 9, 0)
      const expected = d.getDay() >= 1 && d.getDay() <= 5
      expect(cronMatches('0 9 * * 1-5', d)).toBe(expected)
    }
  })
  test('weekly matches its own dow only', () => {
    const d = new Date(2026, 5, 12, 9, 0)
    expect(cronMatches(`0 9 * * ${d.getDay()}`, d)).toBe(true)
    expect(cronMatches(`0 9 * * ${(d.getDay() + 1) % 7}`, d)).toBe(false)
  })
})

describe('cronIsDue', () => {
  test('due when a matching minute passed since lastRun', () => {
    const now = new Date(2026, 5, 12, 9, 2).getTime()
    expect(cronIsDue('0 9 * * *', new Date(2026, 5, 12, 8, 30).getTime(), now)).toBe(true)
  })
  test('not due if it already ran after the matching minute', () => {
    const now = new Date(2026, 5, 12, 9, 2).getTime()
    expect(cronIsDue('0 9 * * *', new Date(2026, 5, 12, 9, 1).getTime(), now)).toBe(false)
  })
  test('not due before the matching minute', () => {
    const now = new Date(2026, 5, 12, 8, 59).getTime()
    expect(cronIsDue('0 9 * * *', null, now)).toBe(false)
  })
})

describe('parseNaturalSchedule', () => {
  test('recurring phrases → cron, no `at`', () => {
    expect(parseNaturalSchedule('hourly')?.cron).toBe('0 * * * *')
    expect(parseNaturalSchedule('every day at 9am')?.cron).toBe('0 9 * * *')
    expect(parseNaturalSchedule('weekdays at 8')?.cron).toBe('0 8 * * 1-5')
    expect(parseNaturalSchedule('every monday at 10am')?.cron).toBe('0 10 * * 1')
    expect(parseNaturalSchedule('every day at 9am')?.at).toBeUndefined()
  })
  test('relative / one-time phrases set `at`', () => {
    const inTwo = parseNaturalSchedule('in 2 hours')
    expect(inTwo?.at).toBeGreaterThan(Date.now())
    const tom = parseNaturalSchedule('tomorrow at 9am')
    expect(tom?.at).toBeGreaterThan(Date.now())
  })
  test('pm conversion + unparseable → null', () => {
    expect(parseNaturalSchedule('every day at 3pm')?.cron).toBe('0 15 * * *')
    expect(parseNaturalSchedule('sometime whenever')).toBeNull()
  })
})

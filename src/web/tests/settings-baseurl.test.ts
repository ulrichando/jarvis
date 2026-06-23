import { describe, expect, test } from 'vitest'
import { baseURLSchema } from '@/lib/settings/base-url'

describe('baseURLSchema', () => {
  test('accepts a valid URL', () => {
    expect(baseURLSchema.safeParse('http://127.0.0.1:11434').success).toBe(true)
    expect(baseURLSchema.safeParse('https://api.example.com/v1').success).toBe(true)
  })

  test('accepts "" and null (clear) and undefined (omit)', () => {
    expect(baseURLSchema.safeParse('').success).toBe(true)
    expect(baseURLSchema.safeParse(null).success).toBe(true)
    expect(baseURLSchema.safeParse(undefined).success).toBe(true)
  })

  test('rejects a non-URL string', () => {
    expect(baseURLSchema.safeParse('not a url').success).toBe(false)
    expect(baseURLSchema.safeParse('http://').success).toBe(false)
  })
})

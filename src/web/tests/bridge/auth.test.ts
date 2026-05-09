import { describe, expect, test } from 'vitest'
import { extractBearer } from '@/lib/bridge/auth'

describe('extractBearer', () => {
  test('returns the token from a well-formed header', () => {
    expect(extractBearer('Bearer abc123')).toBe('abc123')
  })

  test('case-insensitive scheme', () => {
    expect(extractBearer('bearer xyz')).toBe('xyz')
    expect(extractBearer('BEARER tok')).toBe('tok')
  })

  test('returns null on missing or malformed', () => {
    expect(extractBearer(null)).toBeNull()
    expect(extractBearer('')).toBeNull()
    expect(extractBearer('Basic abc')).toBeNull()
    expect(extractBearer('Bearer')).toBeNull()
    expect(extractBearer('Bearer  ')).toBeNull()
  })
})

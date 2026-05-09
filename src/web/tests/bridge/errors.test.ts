import { describe, expect, test } from 'vitest'
import { bridgeError } from '@/lib/bridge/errors'

describe('bridgeError', () => {
  test('builds NextResponse-compatible body and status', async () => {
    const res = bridgeError(401, 'unauthorized', 'Bad token')
    expect(res.status).toBe(401)
    const body = await res.json()
    expect(body).toEqual({
      error: { type: 'unauthorized', detail: 'Bad token', message: 'Bad token' },
    })
  })

  test('omits detail when not provided', async () => {
    const res = bridgeError(404, 'not_found')
    expect(res.status).toBe(404)
    const body = await res.json()
    expect(body).toEqual({ error: { type: 'not_found' } })
  })
})

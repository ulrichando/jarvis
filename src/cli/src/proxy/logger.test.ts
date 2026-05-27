import { afterEach, beforeEach, describe, expect, test, spyOn } from 'bun:test'

import { logDeepseekCacheStats } from './logger.js'

describe('logDeepseekCacheStats — DeepSeek context-cache telemetry', () => {
  let calls: string[] = []
  let spy: ReturnType<typeof spyOn>

  beforeEach(() => {
    calls = []
    spy = spyOn(console, 'log').mockImplementation((...args: unknown[]) => {
      calls.push(args.map((a) => String(a)).join(' '))
    })
  })

  afterEach(() => {
    spy.mockRestore()
  })

  test('logs hit/miss/ratio with 8-char req-id prefix', () => {
    logDeepseekCacheStats('abc12345-9999-aaaa-bbbb-cccccccccccc', 800, 200)
    expect(calls).toEqual([
      '[jarvis-proxy] [abc12345] deepseek cache: hit=800 miss=200 ratio=80%',
    ])
  })

  test('zero-total: emits ratio=0 (no division-by-zero)', () => {
    logDeepseekCacheStats('reqid1234-rest', 0, 0)
    expect(calls).toEqual([
      '[jarvis-proxy] [reqid123] deepseek cache: hit=0 miss=0 ratio=0%',
    ])
  })

  test('all-miss: ratio=0', () => {
    logDeepseekCacheStats('req-xxxxxxxx', 0, 1500)
    expect(calls).toEqual([
      '[jarvis-proxy] [req-xxxx] deepseek cache: hit=0 miss=1500 ratio=0%',
    ])
  })

  test('all-hit: ratio=100', () => {
    logDeepseekCacheStats('full-hit-id', 1500, 0)
    expect(calls).toEqual([
      '[jarvis-proxy] [full-hit] deepseek cache: hit=1500 miss=0 ratio=100%',
    ])
  })

  test('rounds ratio to nearest integer percent', () => {
    // 333 / (333 + 667) = 0.333 → 33%
    logDeepseekCacheStats('round-it-test', 333, 667)
    expect(calls).toEqual([
      '[jarvis-proxy] [round-it] deepseek cache: hit=333 miss=667 ratio=33%',
    ])
  })

  test('short request id (under 8 chars) is logged verbatim without padding', () => {
    logDeepseekCacheStats('abc', 100, 100)
    expect(calls).toEqual([
      '[jarvis-proxy] [abc] deepseek cache: hit=100 miss=100 ratio=50%',
    ])
  })
})

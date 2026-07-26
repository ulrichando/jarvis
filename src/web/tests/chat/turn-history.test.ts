import { describe, expect, test } from 'vitest'
import type { UIMessage } from 'ai'
import { buildTurnHistory } from '@/lib/chat/turn-history'

const msg = (id: string, role: 'user' | 'assistant', text: string): UIMessage =>
  ({ id, role, parts: [{ type: 'text', text }] }) as UIMessage

describe('buildTurnHistory (finding #5 — stage progression uses finalized, not stale, history)', () => {
  const prior = [
    msg('u1', 'user', 'build stage 1'),
    msg('a1', 'assistant', 'stage 1 done'),
  ]
  const user = msg('u2', 'user', 'auto-progress to stage 2')

  test('default: current messages + the new user message', () => {
    expect(buildTurnHistory(prior, user).map((m) => m.id)).toEqual([
      'u1',
      'a1',
      'u2',
    ])
  })

  test('continuationHistory OVERRIDES the (stale) closure messages', () => {
    // The bug: the re-entrant submit()'s `messages` closure is stale, missing
    // the just-finished stage's reply. The caller threads the finalized history.
    const stale = [msg('u1', 'user', 'build stage 1')] // no assistant reply yet
    const finalized = [
      msg('u1', 'user', 'build stage 1'),
      msg('a1', 'assistant', 'stage 1 done + <jarvisPlan>'),
    ]
    const out = buildTurnHistory(stale, user, finalized)
    expect(out.map((m) => m.id)).toEqual(['u1', 'a1', 'u2'])
    // Stage 1's reply is present → stage 2 won't "go off the rails".
    expect(out.some((m) => m.role === 'assistant')).toBe(true)
  })

  test('undefined continuationHistory falls back to prior (normal user turns)', () => {
    expect(
      buildTurnHistory(prior, user, undefined).map((m) => m.id),
    ).toEqual(['u1', 'a1', 'u2'])
  })
})

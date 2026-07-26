import { describe, expect, test, beforeEach } from 'vitest'
import { _resetForTests, getStore } from '@/lib/bridge/db'
import {
  createEnvironment,
  getOrCreateSession,
  appendSessionEvent,
  firstUserPrompt,
  lastEvent,
  findEnvironments,
} from '@/lib/bridge/store'

// Finding #7: the sessions list read every session's FULL transcript on each 6s
// poll. These point queries + the batch env lookup replace that.
const USER = '00000000-0000-0000-0000-0000000000c7'

beforeEach(() => _resetForTests())

function seed(sessionId: string) {
  const store = getStore()
  const env = createEnvironment(store, {
    machine_name: `m-${sessionId}`,
    directory: '/w',
    git_repo_url: 'https://github.com/o/r',
    max_sessions: 4,
    worker_type: 'container',
    user_id: USER,
  })
  getOrCreateSession(store, sessionId, env.environment_id, 't')
  return env
}

describe('sidebar point queries (finding #7)', () => {
  test('firstUserPrompt = earliest user_prompt; lastEvent = newest event', () => {
    const store = getStore()
    seed('s1')
    appendSessionEvent(store, 's1', { type: 'status', payload: { status: 'boot' } })
    appendSessionEvent(store, 's1', { type: 'user_prompt', payload: { prompt: 'first' } })
    appendSessionEvent(store, 's1', { type: 'user_prompt', payload: { prompt: 'second' } })
    appendSessionEvent(store, 's1', { type: 'assistant', payload: { text: 'reply' } })

    const fp = firstUserPrompt(store, 's1')
    expect(fp?.type).toBe('user_prompt')
    expect((JSON.parse(fp!.payload_json) as { prompt: string }).prompt).toBe('first')

    const le = lastEvent(store, 's1')
    expect(le?.type).toBe('assistant')
  })

  test('firstUserPrompt / lastEvent → null for an empty or unknown session', () => {
    expect(firstUserPrompt(getStore(), 'ghost')).toBeNull()
    expect(lastEvent(getStore(), 'ghost')).toBeNull()
  })

  test('findEnvironments batches + dedups ids into an id→row map', () => {
    const store = getStore()
    const e1 = seed('s1')
    const e2 = seed('s2')
    const map = findEnvironments(store, [
      e1.environment_id,
      e2.environment_id,
      e1.environment_id,
      '',
    ])
    expect(map.size).toBe(2)
    expect(map.get(e1.environment_id)?.environment_id).toBe(e1.environment_id)
    expect(findEnvironments(store, []).size).toBe(0)
  })
})

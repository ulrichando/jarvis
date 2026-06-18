import { describe, expect, test, beforeEach, vi } from 'vitest'
import { _resetForTests, getStore } from '@/lib/bridge/db'
import { createRoutine, findRoutine, updateRoutine } from '@/lib/bridge/store'

const runs = vi.hoisted(() => ({ calls: [] as string[] }))
vi.mock('@/lib/bridge/routines-run', () => ({
  runRoutine: async (_store: unknown, routine: { routine_id: string }) => {
    runs.calls.push(routine.routine_id)
    return { sessionId: 'sess' }
  },
}))

beforeEach(() => {
  _resetForTests()
  runs.calls = []
})

describe('runRoutinesTick', () => {
  test('fires a due recurring routine, skips a paused one', async () => {
    const store = getStore()
    const due = createRoutine(store, {
      name: 'a',
      instructions: 'x',
      repo: 'owner/demo',
      trigger: { type: 'schedule', cron: '* * * * *' },
    })
    const paused = createRoutine(store, {
      name: 'b',
      instructions: 'y',
      repo: 'owner/demo',
      trigger: { type: 'schedule', cron: '* * * * *' },
    })
    updateRoutine(store, paused.routine_id, { paused: true })

    const { runRoutinesTick } = await import('@/lib/bridge/routines-tick')
    expect(await runRoutinesTick(store, 'http://127.0.0.1:3000')).toBe(1)
    expect(runs.calls).toEqual([due.routine_id])
  })

  test('one-time (at) fires once then pauses', async () => {
    const store = getStore()
    const r = createRoutine(store, {
      name: 'once',
      instructions: 'z',
      repo: 'owner/demo',
      trigger: { type: 'schedule', cron: '0 0 * * *', at: Date.now() - 60_000 },
    })
    const { runRoutinesTick } = await import('@/lib/bridge/routines-tick')
    expect(await runRoutinesTick(store, 'o')).toBe(1)
    expect(findRoutine(store, r.routine_id)!.paused).toBe(1)
    runs.calls = []
    expect(await runRoutinesTick(store, 'o')).toBe(0)
  })

  test('does not fire a future one-time', async () => {
    const store = getStore()
    createRoutine(store, {
      name: 'future',
      instructions: 'z',
      repo: 'owner/demo',
      trigger: { type: 'schedule', cron: '0 0 * * *', at: Date.now() + 3_600_000 },
    })
    const { runRoutinesTick } = await import('@/lib/bridge/routines-tick')
    expect(await runRoutinesTick(store, 'o')).toBe(0)
  })
})

describe('runGithubRoutines (event filters)', () => {
  test('fires only when filters match the payload', async () => {
    const store = getStore()
    createRoutine(store, {
      name: 'pr-by-alice',
      instructions: 'review',
      repo: 'owner/demo',
      trigger: { type: 'github', events: ['pull_request'], filters: { author: 'alice', baseBranch: 'main' } },
    })
    const { runGithubRoutines } = await import('@/lib/bridge/routines-tick')

    // Wrong author → no fire.
    runs.calls = []
    expect(
      await runGithubRoutines(store, 'o', 'pull_request', {
        pull_request: { user: { login: 'bob' }, base: { ref: 'main' } },
      }),
    ).toBe(0)

    // Matching author + base → fires.
    runs.calls = []
    expect(
      await runGithubRoutines(store, 'o', 'pull_request', {
        pull_request: { user: { login: 'alice' }, base: { ref: 'main' } },
      }),
    ).toBe(1)

    // Wrong event entirely → no fire.
    runs.calls = []
    expect(await runGithubRoutines(store, 'o', 'release', {})).toBe(0)
  })
})

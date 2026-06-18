import { describe, expect, test, beforeEach, vi } from 'vitest'
import { _resetForTests, getStore } from '@/lib/bridge/db'
import {
  getOrCreateSession,
  createEnvironment,
  setSessionAutofix,
  setSessionContainer,
  listSessionEvents,
  findSession,
} from '@/lib/bridge/store'

// Mutable status the github mock reports (vi.hoisted so the hoisted mock
// factory can reference it).
const state = vi.hoisted(() => ({ failing: true, sha: 'sha-1' }))

vi.mock('@/lib/bridge/containers', () => ({
  getContainerDiff: async () => ({
    branch: 'jarvis/x',
    base: 'origin/main',
    ahead: 1,
    stat: '',
    diff: '',
  }),
}))
vi.mock('@/lib/connectors/github', () => ({
  githubPrStatus: async () => ({
    ok: true,
    status: {
      pr: { number: 1, url: 'https://github.com/owner/demo/pull/1', state: 'open', draft: false },
      checks: {
        total: 2,
        passed: 1,
        failed: state.failing ? 1 : 0,
        pending: 0,
        failing: state.failing ? ['build'] : [],
      },
      sha: state.sha,
    },
  }),
}))

beforeEach(() => {
  _resetForTests()
  state.failing = true
  state.sha = 'sha-1'
})

function seedAutofixSession(): string {
  const store = getStore()
  const env = createEnvironment(store, {
    machine_name: 'Cloud container',
    directory: '/workspace',
    git_repo_url: 'https://github.com/owner/demo',
    max_sessions: 4,
    worker_type: 'container',
    user_id: null,
  })
  getOrCreateSession(store, 'a1b2c3d400112233', env.environment_id)
  setSessionContainer(store, 'a1b2c3d400112233', { container: 'jarvis-code-x', repo: 'owner/demo' })
  setSessionAutofix(store, 'a1b2c3d400112233', true)
  return 'a1b2c3d400112233'
}

describe('runAutofixTick', () => {
  test('messages the session once per failing commit, not repeatedly', async () => {
    const id = seedAutofixSession()
    const store = getStore()
    const { runAutofixTick } = await import('@/lib/bridge/autofix')

    expect(await runAutofixTick(store)).toBe(1)
    const prompts = () =>
      listSessionEvents(store, id, 0).filter((e) => e.type === 'user_prompt')
    expect(prompts().length).toBe(1)
    expect(findSession(store, id)!.autofix_sha).toBe('sha-1')

    // Same failing commit → no repeat.
    expect(await runAutofixTick(store)).toBe(0)
    expect(prompts().length).toBe(1)

    // A new failing commit (the agent pushed a fix that still fails) → fix again.
    state.sha = 'sha-2'
    expect(await runAutofixTick(store)).toBe(1)
    expect(prompts().length).toBe(2)
  })

  test('does nothing when CI is passing', async () => {
    seedAutofixSession()
    state.failing = false
    const { runAutofixTick } = await import('@/lib/bridge/autofix')
    expect(await runAutofixTick(getStore())).toBe(0)
  })

  test('does nothing for sessions without auto-fix enabled', async () => {
    const store = getStore()
    const env = createEnvironment(store, {
      machine_name: 'Cloud container',
      directory: '/workspace',
      git_repo_url: 'https://github.com/owner/demo',
      max_sessions: 4,
      worker_type: 'container',
      user_id: null,
    })
    getOrCreateSession(store, 'b1b2c3d400112233', env.environment_id)
    setSessionContainer(store, 'b1b2c3d400112233', { container: 'jarvis-code-y', repo: 'owner/demo' })
    // autofix NOT enabled
    const { runAutofixTick } = await import('@/lib/bridge/autofix')
    expect(await runAutofixTick(store)).toBe(0)
  })
})

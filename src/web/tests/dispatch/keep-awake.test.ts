import { describe, expect, test, beforeEach, vi } from 'vitest'
import { EventEmitter } from 'node:events'

// Direct unit test of the keep-awake module: spawn is MOCKED so no real
// systemd-inhibit ever runs on the test host. The module keeps a module-level
// Map keyed by sessionId, so each test uses its own sessionId to stay
// independent of ordering.

class FakeChild extends EventEmitter {
  kill = vi.fn((signal?: string) => {
    this.emit('exit', 0, signal ?? null)
    return true
  })
  unref = vi.fn()
}

const spawned = vi.hoisted(() => ({ children: [] as InstanceType<typeof EventEmitter>[] }))
const spawnMock = vi.hoisted(() => vi.fn())
vi.mock('node:child_process', async (importOriginal) => {
  const actual = await importOriginal<typeof import('node:child_process')>()
  // Node builtins need the default export preserved for interop.
  return { ...actual, spawn: spawnMock, default: { ...actual, spawn: spawnMock } }
})

import { acquireKeepAwake, releaseKeepAwake } from '@/lib/dispatch/keep-awake'

beforeEach(() => {
  spawned.children = []
  spawnMock.mockReset()
  spawnMock.mockImplementation(() => {
    const child = new FakeChild()
    spawned.children.push(child)
    return child
  })
})

describe('dispatch keep-awake — systemd-inhibit lifecycle', () => {
  test('acquire spawns exactly one systemd-inhibit with the expected args, unref’d', () => {
    acquireKeepAwake('ka-args')
    expect(spawnMock).toHaveBeenCalledTimes(1)
    expect(spawnMock).toHaveBeenCalledWith(
      'systemd-inhibit',
      [
        '--what=idle:sleep',
        '--mode=block',
        '--who=Jarvis Dispatch',
        '--why=dispatch task running',
        'sleep',
        '21600', // 6h safety cap — an orphaned inhibitor self-releases
      ],
      { stdio: 'ignore' },
    )
    const child = spawned.children[0] as FakeChild
    // Never keeps the server process alive for the lock.
    expect(child.unref).toHaveBeenCalledTimes(1)
    releaseKeepAwake('ka-args')
  })

  test('a second acquire for the same sessionId does not spawn again (dedup)', () => {
    acquireKeepAwake('ka-dedup')
    acquireKeepAwake('ka-dedup')
    expect(spawnMock).toHaveBeenCalledTimes(1)
    releaseKeepAwake('ka-dedup')
  })

  test('release kills the tracked child; a subsequent acquire spawns again', () => {
    acquireKeepAwake('ka-cycle')
    const first = spawned.children[0] as FakeChild
    releaseKeepAwake('ka-cycle')
    expect(first.kill).toHaveBeenCalledTimes(1)
    expect(first.kill).toHaveBeenCalledWith('SIGTERM')
    // Lock dropped → re-acquire holds a fresh inhibitor (the multi-turn path).
    acquireKeepAwake('ka-cycle')
    expect(spawnMock).toHaveBeenCalledTimes(2)
    releaseKeepAwake('ka-cycle')
  })

  test('release without a held lock is a safe no-op', () => {
    expect(() => releaseKeepAwake('ka-never-acquired')).not.toThrow()
    expect(spawnMock).not.toHaveBeenCalled()
  })

  test('when spawn throws, acquire does not throw (graceful no-op host)', () => {
    spawnMock.mockImplementation(() => {
      throw new Error('spawn systemd-inhibit ENOENT')
    })
    expect(() => acquireKeepAwake('ka-enoent')).not.toThrow()
    // Nothing tracked → a later acquire (working spawn) tries again.
    spawnMock.mockImplementation(() => {
      const child = new FakeChild()
      spawned.children.push(child)
      return child
    })
    acquireKeepAwake('ka-enoent')
    expect(spawnMock).toHaveBeenCalledTimes(2)
    releaseKeepAwake('ka-enoent')
  })
})

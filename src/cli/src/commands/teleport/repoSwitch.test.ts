import { describe, expect, test } from 'bun:test'
import { switchToRepoCheckout, type RepoSwitchDeps } from './repoSwitch.js'
import type { CodeSession } from '../../utils/teleport/api.js'

function session(repo: CodeSession['repo']): CodeSession {
  return { id: 'sess1', title: 't', repo } as unknown as CodeSession
}
const maxrun = { name: 'maxrun', owner: { login: 'ulrichando' } }

function deps(over: Partial<RepoSwitchDeps>): { d: RepoSwitchDeps; chdirTo: string[] } {
  const chdirTo: string[] = []
  const d: RepoSwitchDeps = {
    cwd: () => '/home/u/Projects/jarvis',
    validateRepoAtPath: async () => false,
    trackedPaths: async () => [],
    gitRootParent: () => '/home/u/Projects',
    chdir: p => void chdirTo.push(p),
    ...over,
  }
  return { d, chdirTo }
}

describe('switchToRepoCheckout', () => {
  test('no repo requirement → no_repo, no chdir', async () => {
    const { d, chdirTo } = deps({})
    expect(await switchToRepoCheckout(session(null), d)).toEqual({ kind: 'no_repo' })
    expect(chdirTo).toEqual([])
  })

  test('already in the session repo → already_here, no chdir', async () => {
    const { d, chdirTo } = deps({ validateRepoAtPath: async () => true })
    expect(await switchToRepoCheckout(session(maxrun), d)).toEqual({ kind: 'already_here' })
    expect(chdirTo).toEqual([])
  })

  test('sibling checkout found → chdir there + switched', async () => {
    const { d, chdirTo } = deps({
      // current dir is NOT maxrun; the sibling path IS.
      validateRepoAtPath: async p => p === '/home/u/Projects/maxrun',
    })
    expect(await switchToRepoCheckout(session(maxrun), d)).toEqual({
      kind: 'switched',
      path: '/home/u/Projects/maxrun',
    })
    expect(chdirTo).toEqual(['/home/u/Projects/maxrun'])
  })

  test('tracked path is preferred over the sibling guess', async () => {
    const { d, chdirTo } = deps({
      trackedPaths: async () => ['/mnt/work/maxrun'],
      validateRepoAtPath: async p => p !== '/home/u/Projects/jarvis', // any candidate valid
    })
    const r = await switchToRepoCheckout(session(maxrun), d)
    expect(r).toEqual({ kind: 'switched', path: '/mnt/work/maxrun' })
    expect(chdirTo).toEqual(['/mnt/work/maxrun'])
  })

  test('no checkout anywhere → not_found with the repo key', async () => {
    const { d, chdirTo } = deps({}) // everything invalid
    expect(await switchToRepoCheckout(session(maxrun), d)).toEqual({
      kind: 'not_found',
      repo: 'ulrichando/maxrun',
    })
    expect(chdirTo).toEqual([])
  })
})

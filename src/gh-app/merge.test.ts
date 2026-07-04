// src/gh-app/merge.test.ts
import { test, expect, describe } from 'bun:test'
import { parseMergeCommand, mergePr, type MergeDeps } from './merge.js'

function jsonRes(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), { status, headers: { 'content-type': 'application/json' } })
}
function fakeFetch(handler?: (url: string, init: RequestInit) => Response | Promise<Response>) {
  const calls: { url: string; init: RequestInit }[] = []
  const f = (async (url: unknown, init?: unknown) => {
    const i = (init ?? {}) as RequestInit
    calls.push({ url: String(url), init: i })
    return handler ? handler(String(url), i) : jsonRes(200, {})
  }) as typeof fetch
  const deps: MergeDeps = { fetch: f }
  return { deps, calls }
}

describe('parseMergeCommand', () => {
  test('"merge" → squash default', () => {
    expect(parseMergeCommand('merge')).toEqual({ method: 'squash' })
  })
  test('explicit method overrides the default', () => {
    expect(parseMergeCommand('merge rebase')).toEqual({ method: 'rebase' })
    expect(parseMergeCommand('merge merge')).toEqual({ method: 'merge' })
    expect(parseMergeCommand('merge squash')).toEqual({ method: 'squash' })
  })
  test('natural-language "merge this PR" → squash', () => {
    expect(parseMergeCommand('merge this PR please')).toEqual({ method: 'squash' })
  })
  test('case- + whitespace-tolerant', () => {
    expect(parseMergeCommand('  MERGE   Rebase ')).toEqual({ method: 'rebase' })
  })
  test('non-merge tasks → null (must be the WORD "merge" first)', () => {
    expect(parseMergeCommand('fix the flaky test')).toBeNull()
    expect(parseMergeCommand('remerge')).toBeNull()
    expect(parseMergeCommand('unmerge it')).toBeNull()
    expect(parseMergeCommand('')).toBeNull()
  })
})

describe('mergePr', () => {
  test('PUTs /merge with method + token; ok on 200', async () => {
    const { deps, calls } = fakeFetch(() => jsonRes(200, { merged: true }))
    const r = await mergePr('owner/repo', 13, 'squash', 'ghs_tok', deps)
    expect(r).toEqual({ ok: true })
    expect(calls[0].url).toBe('https://api.github.com/repos/owner/repo/pulls/13/merge')
    expect(calls[0].init.method).toBe('PUT')
    expect(JSON.parse(String(calls[0].init.body))).toEqual({ merge_method: 'squash' })
    expect((calls[0].init.headers as Record<string, string>).Authorization).toBe('Bearer ghs_tok')
  })
  test('rebase method is passed through', async () => {
    const { deps, calls } = fakeFetch(() => jsonRes(200, { merged: true }))
    await mergePr('o/r', 1, 'rebase', 't', deps)
    expect(JSON.parse(String(calls[0].init.body))).toEqual({ merge_method: 'rebase' })
  })
  test('405 (not mergeable) → surfaces GitHub message', async () => {
    const { deps } = fakeFetch(() => jsonRes(405, { message: 'Pull Request is not mergeable' }))
    const r = await mergePr('owner/repo', 13, 'squash', 't', deps)
    expect(r.ok).toBe(false)
    expect(r.ok === false && r.error).toContain('not mergeable')
  })
  test('non-JSON error body → status fallback', async () => {
    const { deps } = fakeFetch(() => new Response('nope', { status: 403 }))
    const r = await mergePr('owner/repo', 13, 'squash', 't', deps)
    expect(r).toEqual({ ok: false, error: 'GitHub 403' })
  })
  test('network throw → ok:false', async () => {
    const deps: MergeDeps = { fetch: (async () => { throw new Error('boom') }) as typeof fetch }
    const r = await mergePr('owner/repo', 13, 'squash', 't', deps)
    expect(r.ok).toBe(false)
  })
})

// src/cli/src/gh-agent/gh.test.ts
import { describe, expect, test } from 'bun:test'
import { listMentions, postComment, SELF_MARKER, type GhRunner } from './gh.js'

function stub(stdout: string): { run: GhRunner; calls: string[][] } {
  const calls: string[][] = []
  const run: GhRunner = async (args) => {
    calls.push(args)
    return { stdout, stderr: '', code: 0 }
  }
  return { run, calls }
}

function comment(over: Record<string, unknown> = {}): Record<string, unknown> {
  return {
    id: 2,
    body: '@jarvis add tests',
    user: { login: 'alice' },
    created_at: '2026-07-01T11:00:00Z',
    updated_at: '2026-07-01T11:30:00Z',
    issue_url: 'https://api.github.com/repos/o/r/issues/13',
    html_url: 'https://github.com/o/r/issues/13#c2',
    ...over,
  }
}

// gh api --paginate --slurp emits ONE top-level JSON array of PAGES: [[...],[...]].
const slurp = (...pages: unknown[][]) => JSON.stringify(pages)

describe('gh-agent gh wrappers', () => {
  test('listMentions parses a slurped page and keeps only trigger matches', async () => {
    const api = slurp([
      comment({ id: 1, body: 'hello', user: { login: 'bob' }, updated_at: '2026-07-01T10:00:00Z', issue_url: 'https://api.github.com/repos/o/r/issues/12', html_url: 'https://github.com/o/r/issues/12#c1' }),
      comment(),
    ])
    const { run } = stub(api)
    const res = await listMentions('o/r', '@jarvis', '2026-07-01T00:00:00Z', run)
    expect(res).not.toBeNull()
    expect(res!.mentions).toHaveLength(1)
    expect(res!.mentions[0]).toMatchObject({ id: 2, author: 'alice', issueNumber: 13 })
    // ?since= filters on updated_at, so the cursor math needs it verbatim.
    expect(res!.mentions[0].updatedAt).toBe('2026-07-01T11:30:00Z')
  })

  test('multi-page --slurp output is flattened across pages (pagination guard)', async () => {
    const api = slurp(
      [comment({ id: 10, issue_url: 'https://api.github.com/repos/o/r/issues/1' })],
      [comment({ id: 20, issue_url: 'https://api.github.com/repos/o/r/issues/2', updated_at: '2026-07-01T12:00:00Z' })],
    )
    const { run } = stub(api)
    const res = await listMentions('o/r', '@jarvis', '2026-07-01T00:00:00Z', run)
    expect(res!.mentions.map(m => m.id)).toEqual([10, 20])
    expect(res!.maxUpdatedAt).toBe('2026-07-01T12:00:00Z')
  })

  test('listMentions passes an encoded since cursor plus --paginate --slurp', async () => {
    const { run, calls } = stub('[]')
    const res = await listMentions('o/r', '@jarvis', '2026-07-01T00:00:00Z', run)
    const joined = calls[0].join(' ')
    expect(joined).toContain('repos/o/r/issues/comments')
    expect(joined).toContain('since=2026-07-01T00%3A00%3A00Z')
    expect(calls[0]).toContain('--paginate')
    expect(calls[0]).toContain('--slurp')
    // Empty fetch is a SUCCESS with no data, not a failure.
    expect(res).toEqual({ mentions: [], maxUpdatedAt: null })
  })

  test('listMentions returns null on nonzero gh exit (fetch failure ≠ empty)', async () => {
    const run: GhRunner = async () => ({ stdout: '', stderr: 'boom', code: 1 })
    expect(await listMentions('o/r', '@jarvis', '2026-07-01T00:00:00Z', run)).toBeNull()
  })

  test('listMentions returns null on malformed stdout (fetch failure ≠ empty)', async () => {
    const { run } = stub('][ not json')
    expect(await listMentions('o/r', '@jarvis', '2026-07-01T00:00:00Z', run)).toBeNull()
  })

  test('non-array JSON (error object) → empty sweep, not null', async () => {
    const { run } = stub('{"message":"Validation Failed"}')
    expect(await listMentions('o/r', '@jarvis', '2026-07-01T00:00:00Z', run)).toEqual({ mentions: [], maxUpdatedAt: null })
  })

  test('trigger matches on word boundaries: @jarvisfan99 no, @jarvis yes', async () => {
    const api = slurp([
      comment({ id: 7, body: '@jarvisfan99 hi' }),
      comment({ id: 8, body: 'please @jarvis do x' }),
      comment({ id: 9, body: 'mail me at bot@jarvis today' }), // preceded by \w → not a mention
    ])
    const { run } = stub(api)
    const res = await listMentions('o/r', '@jarvis', '2026-07-01T00:00:00Z', run)
    expect(res!.mentions.map(m => m.id)).toEqual([8])
  })

  test('comments carrying the self-marker are never treated as mentions', async () => {
    const api = slurp([comment({ id: 9, body: `@jarvis echo\n\n${SELF_MARKER}` })])
    const { run } = stub(api)
    const res = await listMentions('o/r', '@jarvis', '2026-07-01T00:00:00Z', run)
    expect(res!.mentions).toHaveLength(0)
  })

  test('maxUpdatedAt is the max across ALL fetched comments, not just matches', async () => {
    const api = slurp([
      comment({ id: 1, body: 'unrelated chatter', updated_at: '2026-07-01T12:45:00Z' }),
      comment({ id: 2, body: '@jarvis do x', updated_at: '2026-07-01T11:30:00Z' }),
    ])
    const { run } = stub(api)
    const res = await listMentions('o/r', '@jarvis', '2026-07-01T00:00:00Z', run)
    expect(res!.mentions.map(m => m.id)).toEqual([2])
    expect(res!.maxUpdatedAt).toBe('2026-07-01T12:45:00Z')
  })

  test('malformed rows are dropped by the shape guard (never throws)', async () => {
    const api = slurp([
      { id: 'not-a-number', body: '@jarvis hi', created_at: 'x', updated_at: 'x' },
      { id: 5, body: 42, created_at: 'x', updated_at: 'x' },
      { id: 6, body: '@jarvis no timestamps' },
      null,
      42,
      comment({ id: 7, body: '@jarvis but no issue_url', issue_url: undefined }),
      comment(),
    ])
    const { run } = stub(api)
    const res = await listMentions('o/r', '@jarvis', '2026-07-01T00:00:00Z', run)
    expect(res!.mentions.map(m => m.id)).toEqual([2])
  })

  test('postComment posts to the issue comments endpoint with the body', async () => {
    const { run, calls } = stub('{}')
    await postComment('o/r', 13, 'ack', run)
    const args = calls[0]
    expect(args.join(' ')).toContain('repos/o/r/issues/13/comments')
    expect(args).toContain('body=ack')
  })
})

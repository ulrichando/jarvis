// src/cli/src/gh-agent/gh.test.ts
import { describe, expect, test } from 'bun:test'
import { listMentions, postComment, type GhRunner } from './gh.js'

function stub(stdout: string): { run: GhRunner; calls: string[][] } {
  const calls: string[][] = []
  const run: GhRunner = async (args) => {
    calls.push(args)
    return { stdout, stderr: '', code: 0 }
  }
  return { run, calls }
}

describe('gh-agent gh wrappers', () => {
  test('listMentions parses comments and keeps only trigger matches', async () => {
    const api = JSON.stringify([
      { id: 1, body: 'hello', user: { login: 'bob' }, created_at: '2026-07-01T10:00:00Z', updated_at: '2026-07-01T10:00:00Z', issue_url: 'https://api.github.com/repos/o/r/issues/12', html_url: 'https://github.com/o/r/issues/12#c1' },
      { id: 2, body: '@jarvis add tests', user: { login: 'alice' }, created_at: '2026-07-01T11:00:00Z', updated_at: '2026-07-01T11:30:00Z', issue_url: 'https://api.github.com/repos/o/r/issues/13', html_url: 'https://github.com/o/r/issues/13#c2' },
    ])
    const { run } = stub(api)
    const mentions = await listMentions('o/r', '@jarvis', '2026-07-01T00:00:00Z', run)
    expect(mentions).toHaveLength(1)
    expect(mentions[0]).toMatchObject({ id: 2, author: 'alice', issueNumber: 13 })
    expect(mentions[0].body).toContain('@jarvis')
    // ?since= filters on updated_at, so the cursor math needs it verbatim.
    expect(mentions[0].updatedAt).toBe('2026-07-01T11:30:00Z')
  })

  test('listMentions passes the since cursor to gh api', async () => {
    const { run, calls } = stub('[]')
    await listMentions('o/r', '@jarvis', '2026-07-01T00:00:00Z', run)
    const joined = calls[0].join(' ')
    expect(joined).toContain('repos/o/r/issues/comments')
    expect(joined).toContain('since=2026-07-01T00:00:00Z')
  })

  test('listMentions returns [] on nonzero gh exit (never throws)', async () => {
    const run: GhRunner = async () => ({ stdout: '', stderr: 'boom', code: 1 })
    expect(await listMentions('o/r', '@jarvis', '2026-07-01T00:00:00Z', run)).toEqual([])
  })

  test('postComment posts to the issue comments endpoint with the body', async () => {
    const { run, calls } = stub('{}')
    await postComment('o/r', 13, 'ack', run)
    const args = calls[0]
    expect(args.join(' ')).toContain('repos/o/r/issues/13/comments')
    expect(args).toContain('body=ack')
  })
})

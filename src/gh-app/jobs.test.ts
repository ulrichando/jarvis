// src/gh-app/jobs.test.ts
import { test, expect, describe } from 'bun:test'
import { enqueue, claimNext, markDone, markFailed, setTrackingComment, setSession, countToday, ensureSchema, jobStore, type SqlClient } from './jobs.js'

// Recording fake sql client: captures (text, params) and returns canned rows
// per call — tests never touch Postgres.
function fakeSql(rowsFor: (text: string, params: unknown[]) => unknown[]) {
  const calls: { text: string; params: unknown[] }[] = []
  const sql: SqlClient = async (text, params = []) => {
    calls.push({ text, params })
    return { rows: rowsFor(text, params) }
  }
  return { sql, calls }
}

describe('gh-app jobs', () => {
  test('enqueue inserts the job fields and returns the new id', async () => {
    const { sql, calls } = fakeSql(() => [{ id: 42 }])
    const id = await enqueue(sql, { installationId: 555, repo: 'o/r', issueNumber: 7, task: 'add HELLO.md', isPR: false })
    expect(id).toBe(42)
    expect(calls.length).toBe(1)
    expect(calls[0]!.text).toMatch(/insert into gh_app_jobs/i)
    expect(calls[0]!.params).toEqual([555, 'o/r', 7, 'add HELLO.md', false, null])
  })

  test('enqueue stores the triggering comment id when present', async () => {
    const { sql, calls } = fakeSql(() => [{ id: 43 }])
    await enqueue(sql, { installationId: 555, repo: 'o/r', issueNumber: 7, task: 't', isPR: false, commentId: 4242 })
    expect(calls[0]!.text).toMatch(/comment_id/i)
    expect(calls[0]!.params).toEqual([555, 'o/r', 7, 't', false, 4242])
  })

  test('claimNext atomically claims the oldest queued job and maps the row', async () => {
    const { sql, calls } = fakeSql(() => [{
      id: 9, installation_id: 555, repo: 'o/r', issue_number: 7, task: 't', is_pr: true,
    }])
    const job = await claimNext(sql)
    expect(job).toEqual({ id: 9, installationId: 555, repo: 'o/r', issueNumber: 7, task: 't', isPR: true })
    // Atomic claim: single UPDATE … WHERE status='queued' … SKIP LOCKED … RETURNING
    expect(calls.length).toBe(1)
    expect(calls[0]!.text).toMatch(/update gh_app_jobs/i)
    expect(calls[0]!.text).toMatch(/skip locked/i)
    expect(calls[0]!.text).toMatch(/returning/i)
  })

  test('claimNext maps comment_id when the row has one (bigint comes back stringy)', async () => {
    const { sql } = fakeSql(() => [{
      id: 9, installation_id: 555, repo: 'o/r', issue_number: 7, task: 't', is_pr: false, comment_id: '4242',
    }])
    const job = await claimNext(sql)
    expect(job?.commentId).toBe(4242)
  })

  test('claimNext returns null when the queue is empty', async () => {
    const { sql } = fakeSql(() => [])
    expect(await claimNext(sql)).toBeNull()
  })

  test('setTrackingComment records the tracking comment id on the row', async () => {
    const { sql, calls } = fakeSql(() => [])
    await setTrackingComment(sql, 9, 987654)
    expect(calls.length).toBe(1)
    expect(calls[0]!.text).toMatch(/update gh_app_jobs set tracking_comment_id/i)
    expect(calls[0]!.params).toEqual([987654, 9])
  })

  test('markDone / markFailed update status with the id (error recorded)', async () => {
    const { sql, calls } = fakeSql(() => [])
    await markDone(sql, 9)
    await markFailed(sql, 10, 'boom')
    expect(calls[0]!.text).toMatch(/'done'/)
    expect(calls[0]!.params).toEqual([9])
    expect(calls[1]!.text).toMatch(/'failed'/)
    expect(calls[1]!.params).toEqual(['boom', 10])
  })

  test('countToday returns the count of runs started today', async () => {
    const { sql, calls } = fakeSql(() => [{ n: '3' }])
    expect(await countToday(sql)).toBe(3)
    expect(calls[0]!.text).toMatch(/count/i)
  })

  test('ensureSchema creates the table idempotently; jobStore binds the sql client', async () => {
    const { sql, calls } = fakeSql((text) =>
      /insert/i.test(text) ? [{ id: 1 }] : /count/i.test(text) ? [{ n: '0' }] : [])
    await ensureSchema(sql)
    expect(calls[0]!.text).toMatch(/create table if not exists gh_app_jobs/i)
    const store = jobStore(sql)
    await store.enqueue({ installationId: 1, repo: 'o/r', issueNumber: 2, task: 't', isPR: false })
    await store.setTrackingComment(1, 22)
    expect(calls.some((c) => /tracking_comment_id/i.test(c.text) && /update/i.test(c.text))).toBe(true)
    expect(await store.countToday()).toBe(0)
    expect(await store.claimNext()).toBeNull()
  })

  test('ensureSchema migrates pre-feedback tables: alter … add column if not exists', async () => {
    const { sql, calls } = fakeSql(() => [])
    await ensureSchema(sql)
    const alters = calls.filter((c) => /alter table gh_app_jobs add column if not exists/i.test(c.text))
    expect(alters.some((c) => /comment_id bigint/i.test(c.text))).toBe(true)
    expect(alters.some((c) => /tracking_comment_id bigint/i.test(c.text))).toBe(true)
  })

  test('setSession records the /code session id + url on the row (Phase C)', async () => {
    const { sql, calls } = fakeSql(() => [])
    await setSession(sql, 9, 'ab12', 'https://0wlan.com/code/session_ab12')
    expect(calls.length).toBe(1)
    expect(calls[0]!.text).toMatch(/update gh_app_jobs set session_id/i)
    expect(calls[0]!.params).toEqual(['ab12', 'https://0wlan.com/code/session_ab12', 9])
  })

  test('ensureSchema migrates pre-session tables (session_id/session_url); jobStore binds setSession', async () => {
    const { sql, calls } = fakeSql(() => [])
    await ensureSchema(sql)
    const alters = calls.filter((c) => /alter table gh_app_jobs add column if not exists/i.test(c.text))
    expect(alters.some((c) => /session_id text/i.test(c.text))).toBe(true)
    expect(alters.some((c) => /session_url text/i.test(c.text))).toBe(true)
    await jobStore(sql).setSession(3, 'cd34', 'https://0wlan.com/code/session_cd34')
    expect(calls.some((c) => /update gh_app_jobs set session_id/i.test(c.text))).toBe(true)
  })
})

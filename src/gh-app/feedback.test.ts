// src/gh-app/feedback.test.ts
import { test, expect, describe } from 'bun:test'
import { SELF_MARKER } from '../cli/src/gh-agent/gh.js'
import {
  acknowledge, report, workingMessage, resultMessage, prNumberFromUrl, workerFeedback,
  type FeedbackDeps,
} from './feedback.js'

const TOKEN = 'ghs_tok'

function jsonRes(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), { status, headers: { 'content-type': 'application/json' } })
}

// Recording fake fetch: captures (url, init) per call; a handler routes canned
// responses (or throws) per URL — tests never touch GitHub.
function fakeFetch(handler?: (url: string, init: RequestInit) => Response | Promise<Response>) {
  const calls: { url: string; init: RequestInit }[] = []
  const f = (async (url: unknown, init?: unknown) => {
    const i = (init ?? {}) as RequestInit
    calls.push({ url: String(url), init: i })
    return handler ? handler(String(url), i) : jsonRes(200, {})
  }) as typeof fetch
  const deps: FeedbackDeps = { fetch: f }
  return { deps, calls }
}

const bodyOf = (c: { init: RequestInit }) => JSON.parse(String(c.init.body)) as any
const headersOf = (c: { init: RequestInit }) => (c.init.headers ?? {}) as Record<string, string>

describe('gh-app feedback acknowledge', () => {
  test('reacts 👀 on the trigger comment, posts the working comment, returns its id', async () => {
    const { deps, calls } = fakeFetch((url) =>
      url.endsWith('/issues/7/comments') ? jsonRes(201, { id: 987 }) : jsonRes(201, {}))
    const id = await acknowledge('o/r', { issueNumber: 7, task: 'add HELLO.md', commentId: 4242 }, TOKEN, deps)
    expect(id).toBe(987)
    expect(calls.length).toBe(2)
    // 1) the eyes reaction on the EXACT triggering comment
    expect(calls[0]!.url).toBe('https://api.github.com/repos/o/r/issues/comments/4242/reactions')
    expect(calls[0]!.init.method).toBe('POST')
    expect(bodyOf(calls[0]!)).toEqual({ content: 'eyes' })
    // 2) the tracking comment on the issue
    expect(calls[1]!.url).toBe('https://api.github.com/repos/o/r/issues/7/comments')
    expect(calls[1]!.init.method).toBe('POST')
    const posted = bodyOf(calls[1]!).body as string
    expect(posted).toContain('working on')
    expect(posted).toContain('add HELLO.md')
    expect(posted).toContain(SELF_MARKER) // webhook self-filter must skip our own posts
    // GitHub REST headers on both calls
    for (const c of calls) {
      const h = headersOf(c)
      expect(h.Authorization).toBe(`Bearer ${TOKEN}`)
      expect(h.Accept).toBe('application/vnd.github+json')
      expect(h['X-GitHub-Api-Version']).toBe('2022-11-28')
      expect(h['User-Agent']).toBe('jarvis-gh-app')
    }
  })

  test('a reaction failure is best-effort — the working comment still posts', async () => {
    const { deps, calls } = fakeFetch((url) => {
      if (url.includes('/reactions')) throw new Error('reaction rejected')
      return jsonRes(201, { id: 988 })
    })
    const id = await acknowledge('o/r', { issueNumber: 7, task: 't', commentId: 4242 }, TOKEN, deps)
    expect(id).toBe(988)
    expect(calls.length).toBe(2)
  })

  test('no commentId (issues.opened trigger) → no reaction call, just the comment', async () => {
    const { deps, calls } = fakeFetch(() => jsonRes(201, { id: 989 }))
    const id = await acknowledge('o/r', { issueNumber: 3, task: 't' }, TOKEN, deps)
    expect(id).toBe(989)
    expect(calls.length).toBe(1)
    expect(calls[0]!.url).toBe('https://api.github.com/repos/o/r/issues/3/comments')
  })

  test('working-comment post failing (HTTP or throw) → null, never throws', async () => {
    const http = fakeFetch((url) => (url.includes('/reactions') ? jsonRes(201, {}) : jsonRes(403, {})))
    expect(await acknowledge('o/r', { issueNumber: 7, task: 't', commentId: 1 }, TOKEN, http.deps)).toBeNull()
    const boom = fakeFetch((url) => { if (!url.includes('/reactions')) throw new Error('net down'); return jsonRes(201, {}) })
    expect(await acknowledge('o/r', { issueNumber: 7, task: 't', commentId: 1 }, TOKEN, boom.deps)).toBeNull()
  })
})

describe('gh-app feedback report', () => {
  test('PATCHes the tracking comment with the ok+PR message (number derived from the url)', async () => {
    const { deps, calls } = fakeFetch()
    await report('o/r', 987, { ok: true, prUrl: 'https://github.com/o/r/pull/12' }, TOKEN, deps)
    expect(calls.length).toBe(1)
    expect(calls[0]!.url).toBe('https://api.github.com/repos/o/r/issues/comments/987')
    expect(calls[0]!.init.method).toBe('PATCH')
    const body = bodyOf(calls[0]!).body as string
    expect(body).toContain('opened **#12**')
    expect(body).toContain('[Review it](https://github.com/o/r/pull/12)')
    expect(body).toContain(SELF_MARKER)
  })

  test('noChanges → the no-changes message', async () => {
    const { deps, calls } = fakeFetch()
    await report('o/r', 987, { ok: true, noChanges: true }, TOKEN, deps)
    expect(bodyOf(calls[0]!).body).toContain('No changes were needed')
  })

  test('failed → the error message', async () => {
    const { deps, calls } = fakeFetch()
    await report('o/r', 987, { ok: false, error: 'sandbox exited 1' }, TOKEN, deps)
    const body = bodyOf(calls[0]!).body as string
    expect(body).toContain("Couldn't complete")
    expect(body).toContain('sandbox exited 1')
  })

  test('no tracking comment id → falls back to POSTing a fresh issue comment', async () => {
    const { deps, calls } = fakeFetch()
    await report('o/r', null, { ok: true, noChanges: true }, TOKEN, deps, 7)
    expect(calls.length).toBe(1)
    expect(calls[0]!.url).toBe('https://api.github.com/repos/o/r/issues/7/comments')
    expect(calls[0]!.init.method).toBe('POST')
  })

  test('best-effort: a thrown fetch never escapes', async () => {
    const { deps } = fakeFetch(() => { throw new Error('net down') })
    await report('o/r', 987, { ok: true }, TOKEN, deps) // must not throw
  })
})

describe('gh-app feedback messages', () => {
  test('workingMessage quotes the task and carries the self-marker', () => {
    const m = workingMessage('add HELLO.md')
    expect(m).toContain('**talos** is on it — working on:')
    expect(m).toContain('> add HELLO.md')
    expect(m).toContain(SELF_MARKER)
  })

  test('multiline tasks stay inside the quote block', () => {
    expect(workingMessage('line one\nline two')).toContain('> line one\n> line two')
  })

  test('resultMessage: ok without a derivable PR number just links the url', () => {
    const m = resultMessage({ ok: true, prUrl: 'https://github.com/o/r/compare/x' })
    expect(m).toContain('[Review it](https://github.com/o/r/compare/x)')
    expect(m).not.toContain('**#')
  })

  test('resultMessage: plain ok (no prUrl, no noChanges) → generic done', () => {
    expect(resultMessage({ ok: true })).toContain('✅ Done.')
  })

  test('resultMessage: a merge outcome → "✅ Merged #N (method)"', () => {
    expect(resultMessage({ ok: true, merged: { number: 13, method: 'squash' } })).toContain('✅ Merged **#13** (squash)')
    expect(resultMessage({ ok: true, merged: { number: 7, method: 'rebase' } })).toContain('✅ Merged **#7** (rebase)')
  })

  test('resultMessage: failed without error text still reads sanely', () => {
    expect(resultMessage({ ok: false })).toContain('unknown error')
  })

  test('every result message carries the self-marker', () => {
    for (const r of [{ ok: true, prUrl: 'https://github.com/o/r/pull/1' }, { ok: true, noChanges: true }, { ok: true }, { ok: false, error: 'x' }]) {
      expect(resultMessage(r)).toContain(SELF_MARKER)
    }
  })

  test('prNumberFromUrl', () => {
    expect(prNumberFromUrl('https://github.com/o/r/pull/12')).toBe(12)
    expect(prNumberFromUrl('https://github.com/o/r/pull/12#issuecomment-1')).toBe(12)
    expect(prNumberFromUrl('https://github.com/o/r/issues/12')).toBeNull()
  })
})

describe('gh-app feedback session links (Phase C)', () => {
  const S = 'https://0wlan.com/code/session_ab12'

  test('workingMessage with a session url adds the watch-live line', () => {
    const m = workingMessage('add HELLO.md', S)
    expect(m).toContain(`▶︎ Watch it live: ${S}`)
    expect(m).toContain('> add HELLO.md')
    expect(m).toContain(SELF_MARKER)
  })

  test('workingMessage WITHOUT a session url is byte-identical to the sandbox shape', () => {
    expect(workingMessage('add HELLO.md')).toBe(
      `🤖 **talos** is on it — working on:\n\n> add HELLO.md\n\n${SELF_MARKER}`,
    )
  })

  test('resultMessage: PR + session url → "watch the run" link alongside the PR', () => {
    const m = resultMessage({ ok: true, prUrl: 'https://github.com/o/r/pull/12', sessionUrl: S })
    expect(m).toBe(
      `✅ Done — opened **#12** for this. [Review it](https://github.com/o/r/pull/12) · [watch the run](${S}).\n\n${SELF_MARKER}`,
    )
  })

  test('resultMessage: session url rides every outcome shape (noChanges / plain ok / failed)', () => {
    expect(resultMessage({ ok: true, noChanges: true, sessionUrl: S })).toContain(`[watch the run](${S})`)
    expect(resultMessage({ ok: true, sessionUrl: S })).toContain(`[watch the run](${S})`)
    expect(resultMessage({ ok: false, error: 'x', sessionUrl: S })).toContain(`[watch the run](${S})`)
  })

  test('resultMessage WITHOUT a session url stays byte-identical to the sandbox shapes', () => {
    expect(resultMessage({ ok: true, prUrl: 'https://github.com/o/r/pull/12' })).toBe(
      `✅ Done — opened **#12** for this. [Review it](https://github.com/o/r/pull/12).\n\n${SELF_MARKER}`,
    )
    expect(resultMessage({ ok: true, prUrl: 'https://github.com/o/r/compare/x' })).toBe(
      `✅ Done — [Review it](https://github.com/o/r/compare/x).\n\n${SELF_MARKER}`,
    )
    expect(resultMessage({ ok: true, noChanges: true })).toBe(`ℹ️ No changes were needed for this.\n\n${SELF_MARKER}`)
    expect(resultMessage({ ok: true })).toBe(`✅ Done.\n\n${SELF_MARKER}`)
    expect(resultMessage({ ok: false, error: 'x' })).toBe(`⚠️ Couldn't complete this — \`x\`.\n\n${SELF_MARKER}`)
  })

  test('acknowledge threads the session url into the posted tracking comment', async () => {
    const { deps, calls } = fakeFetch(() => jsonRes(201, { id: 990 }))
    const id = await acknowledge('o/r', { issueNumber: 7, task: 't', sessionUrl: S }, TOKEN, deps)
    expect(id).toBe(990)
    expect(bodyOf(calls[0]!).body).toContain(`▶︎ Watch it live: ${S}`)
  })

  test('workerFeedback binding forwards the session url to acknowledge', async () => {
    const { deps, calls } = fakeFetch((url) =>
      url.endsWith('/issues/7/comments') ? jsonRes(201, { id: 56 }) : jsonRes(201, {}))
    const job = { id: 1, installationId: 5, repo: 'o/r', issueNumber: 7, task: 'do it', isPR: false }
    expect(await workerFeedback(deps).acknowledge(job, TOKEN, S)).toBe(56)
    expect(bodyOf(calls[0]!).body).toContain(`▶︎ Watch it live: ${S}`)
  })
})

describe('gh-app workerFeedback binding', () => {
  const job = { id: 1, installationId: 5, repo: 'o/r', issueNumber: 7, task: 'do it', isPR: false, commentId: 4242 }

  test('acknowledge maps the job onto the REST calls', async () => {
    const { deps, calls } = fakeFetch((url) =>
      url.endsWith('/issues/7/comments') ? jsonRes(201, { id: 55 }) : jsonRes(201, {}))
    const fb = workerFeedback(deps)
    expect(await fb.acknowledge(job, TOKEN)).toBe(55)
    expect(calls[0]!.url).toContain('/repos/o/r/issues/comments/4242/reactions')
    expect(calls[1]!.url).toContain('/repos/o/r/issues/7/comments')
  })

  test('report edits the tracking comment; falls back to the job issue when there is none', async () => {
    const a = fakeFetch()
    await workerFeedback(a.deps).report(job, 55, { ok: true }, TOKEN)
    expect(a.calls[0]!.init.method).toBe('PATCH')
    expect(a.calls[0]!.url).toContain('/issues/comments/55')
    const b = fakeFetch()
    await workerFeedback(b.deps).report(job, null, { ok: true }, TOKEN)
    expect(b.calls[0]!.init.method).toBe('POST')
    expect(b.calls[0]!.url).toContain('/issues/7/comments')
  })
})

// src/gh-app/webhook.test.ts
import { test, expect, describe } from 'bun:test'
import { createHmac } from 'node:crypto'
import { handleWebhook, type WebhookDeps } from './webhook.js'
import type { NewJob } from './jobs.js'

const SECRET = 'topsecret'

function sigFor(body: string) { return 'sha256=' + createHmac('sha256', SECRET).update(body).digest('hex') }

function issueCommentPayload(over: {
  body?: string; author?: string; association?: string; action?: string
  installationId?: number | null; issueNumber?: number; isPR?: boolean; commentId?: number
} = {}) {
  const p: any = {
    action: over.action ?? 'created',
    issue: {
      number: over.issueNumber ?? 7,
      ...(over.isPR ? { pull_request: { url: 'x' } } : {}),
    },
    comment: {
      ...(over.commentId !== undefined ? { id: over.commentId } : {}),
      body: over.body ?? '@jarvis add HELLO.md',
      user: { login: over.author ?? 'ulrichando' },
      author_association: over.association ?? 'OWNER',
    },
    repository: { full_name: 'o/r' },
  }
  if (over.installationId !== null) p.installation = { id: over.installationId ?? 555 }
  return JSON.stringify(p)
}

function harness() {
  const enqueued: NewJob[] = []
  const deps: WebhookDeps = {
    secret: SECRET,
    allowlist: ['ulrichando'],
    enqueue: async (j) => { enqueued.push(j) },
  }
  return { deps, enqueued }
}

function headersFor(body: string, event = 'issue_comment', sig?: string) {
  return {
    'x-hub-signature-256': sig ?? sigFor(body),
    'x-github-event': event,
    'x-github-delivery': 'd-1',
  }
}

describe('gh-app handleWebhook', () => {
  test('bad signature → 401, nothing enqueued', async () => {
    const { deps, enqueued } = harness()
    const body = issueCommentPayload()
    const r = await handleWebhook(headersFor(body, 'issue_comment', 'sha256=' + '0'.repeat(64)), body, deps)
    expect(r.status).toBe(401)
    expect(enqueued.length).toBe(0)
  })

  test('@jarvis from OWNER on an issue → 202 + one job enqueued', async () => {
    const { deps, enqueued } = harness()
    const body = issueCommentPayload()
    const r = await handleWebhook(headersFor(body), body, deps)
    expect(r.status).toBe(202)
    expect(enqueued.length).toBe(1)
    expect(enqueued[0]).toEqual({ installationId: 555, repo: 'o/r', issueNumber: 7, task: 'add HELLO.md', isPR: false })
  })

  test('triggering comment id rides the job (for the 👀 ack)', async () => {
    const { deps, enqueued } = harness()
    const body = issueCommentPayload({ commentId: 4242 })
    const r = await handleWebhook(headersFor(body), body, deps)
    expect(r.status).toBe(202)
    expect(enqueued[0]!.commentId).toBe(4242)
  })

  test('PR comment maps isPR:true', async () => {
    const { deps, enqueued } = harness()
    const body = issueCommentPayload({ isPR: true })
    const r = await handleWebhook(headersFor(body), body, deps)
    expect(r.status).toBe(202)
    expect(enqueued[0]!.isPR).toBe(true)
  })

  test('authorized author but no @jarvis trigger → 202 ack, nothing enqueued', async () => {
    const { deps, enqueued } = harness()
    const body = issueCommentPayload({ body: 'just chatting, no trigger' })
    const r = await handleWebhook(headersFor(body), body, deps)
    expect(r.status).toBe(202)
    expect(enqueued.length).toBe(0)
  })

  test('unauthorized author (association NONE) → 202 ack, nothing enqueued', async () => {
    const { deps, enqueued } = harness()
    const body = issueCommentPayload({ author: 'rando', association: 'NONE' })
    const r = await handleWebhook(headersFor(body), body, deps)
    expect(r.status).toBe(202)
    expect(enqueued.length).toBe(0)
  })

  test('trusted association but not on allowlist → nothing enqueued', async () => {
    const { deps, enqueued } = harness()
    const body = issueCommentPayload({ author: 'colleague', association: 'COLLABORATOR' })
    const r = await handleWebhook(headersFor(body), body, deps)
    expect(r.status).toBe(202)
    expect(enqueued.length).toBe(0)
  })

  test('edited comment (action != created) → 202, nothing enqueued', async () => {
    const { deps, enqueued } = harness()
    const body = issueCommentPayload({ action: 'edited' })
    const r = await handleWebhook(headersFor(body), body, deps)
    expect(r.status).toBe(202)
    expect(enqueued.length).toBe(0)
  })

  test('missing installation id → 202, nothing enqueued (cannot mint a token)', async () => {
    const { deps, enqueued } = harness()
    const body = issueCommentPayload({ installationId: null })
    const r = await handleWebhook(headersFor(body), body, deps)
    expect(r.status).toBe(202)
    expect(enqueued.length).toBe(0)
  })

  test('ping / unrelated events ack 202 without enqueueing', async () => {
    const { deps, enqueued } = harness()
    const body = JSON.stringify({ zen: 'Keep it logically awesome.' })
    const r = await handleWebhook(headersFor(body, 'ping'), body, deps)
    expect(r.status).toBe(202)
    expect(enqueued.length).toBe(0)
  })

  test('unparseable JSON body → 400, nothing enqueued', async () => {
    const { deps, enqueued } = harness()
    const body = 'not json'
    const r = await handleWebhook(headersFor(body), body, deps)
    expect(r.status).toBe(400)
    expect(enqueued.length).toBe(0)
  })
})

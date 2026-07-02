// src/cli/src/gh-action/event.test.ts
import { test, expect } from 'bun:test'
import { isAuthorized, parseActionEvent } from './event.js'

const base = { repo: 'o/n', trigger: '@jarvis' }
function ctx(name: string, payload: unknown) {
  return { eventName: name, repo: base.repo, trigger: base.trigger, payload }
}

test('issue_comment with trigger → task extracted', () => {
  const e = parseActionEvent(ctx('issue_comment', {
    action: 'created',
    issue: { number: 7 },
    comment: { body: '@jarvis add a README', user: { login: 'ulrichando' }, author_association: 'OWNER' },
  }))
  expect(e).toEqual({ repo: 'o/n', issueNumber: 7, isPR: false, task: 'add a README', author: 'ulrichando', association: 'OWNER' })
})

test('issue_comment on a PR → isPR true', () => {
  const e = parseActionEvent(ctx('issue_comment', {
    action: 'created',
    issue: { number: 9, pull_request: { url: 'x' } },
    comment: { body: '@jarvis fix it', user: { login: 'ulrichando' }, author_association: 'MEMBER' },
  }))
  expect(e?.isPR).toBe(true)
})

test('no trigger → null', () => {
  const e = parseActionEvent(ctx('issue_comment', {
    action: 'created', issue: { number: 7 },
    comment: { body: 'just a normal comment', user: { login: 'x' }, author_association: 'OWNER' },
  }))
  expect(e).toBeNull()
})

test('unsupported event → null', () => {
  expect(parseActionEvent(ctx('push', {}))).toBeNull()
})

test('self comment (bot marker) → null (no trigger loop)', () => {
  const e = parseActionEvent(ctx('issue_comment', {
    action: 'created', issue: { number: 7 },
    comment: { body: '@jarvis done <!-- jarvis-gh-agent -->', user: { login: 'x' }, author_association: 'OWNER' },
  }))
  expect(e).toBeNull()
})

test('OWNER/MEMBER/COLLABORATOR pass; NONE fails', () => {
  expect(isAuthorized('OWNER', [])).toBe(true)
  expect(isAuthorized('MEMBER', [])).toBe(true)
  expect(isAuthorized('COLLABORATOR', [])).toBe(true)
  expect(isAuthorized('NONE', [])).toBe(false)
  expect(isAuthorized('CONTRIBUTOR', [])).toBe(false)
})
test('explicit allowlist is an AND-narrowing on the login', () => {
  // when an allowlist is set, association must pass AND login must be listed
  expect(isAuthorized('OWNER', ['someoneelse'], 'ulrichando')).toBe(false)
  expect(isAuthorized('OWNER', ['ulrichando'], 'ulrichando')).toBe(true)
})

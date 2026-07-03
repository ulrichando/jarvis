// src/gh-app/manifest.test.ts
import { test, expect } from 'bun:test'
import { buildManifest } from './manifest.js'

test('manifest is private with the right perms + events + webhook url', () => {
  const m = buildManifest('https://gh.0wlan.com')
  expect(m.public).toBe(false)
  expect(m.hook_attributes.url).toBe('https://gh.0wlan.com/webhook')
  expect(m.redirect_url).toBe('https://gh.0wlan.com/setup/callback')
  expect(m.default_permissions).toMatchObject({ contents: 'write', pull_requests: 'write', issues: 'write', metadata: 'read' })
  expect(m.default_events).toEqual(expect.arrayContaining(['issue_comment', 'issues', 'pull_request_review_comment']))
})

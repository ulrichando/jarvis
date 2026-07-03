// src/gh-app/manifest.test.ts
import { test, expect } from 'bun:test'
import { buildManifest, convertManifestCode } from './manifest.js'

test('manifest is private with the right perms + events + webhook url', () => {
  const m = buildManifest('https://gh.0wlan.com')
  expect(m.public).toBe(false)
  expect(m.hook_attributes.url).toBe('https://gh.0wlan.com/webhook')
  expect(m.redirect_url).toBe('https://gh.0wlan.com/setup/callback')
  expect(m.default_permissions).toMatchObject({ contents: 'write', pull_requests: 'write', issues: 'write', metadata: 'read' })
  expect(m.default_events).toEqual(expect.arrayContaining(['issue_comment', 'issues', 'pull_request_review_comment']))
})

test('convertManifestCode POSTs the conversion and extracts appId/pem/webhookSecret', async () => {
  const calls: { url: string; init: RequestInit | undefined }[] = []
  const fakeFetch = (async (url: string | URL | Request, init?: RequestInit) => {
    calls.push({ url: String(url), init })
    return new Response(JSON.stringify({
      id: 4242,
      pem: '-----BEGIN RSA PRIVATE KEY-----\nabc\n-----END RSA PRIVATE KEY-----',
      webhook_secret: 'whsec-xyz',
      client_id: 'Iv1.deadbeef',
      client_secret: 'cs-ignored',
    }), { status: 201, headers: { 'content-type': 'application/json' } })
  }) as typeof fetch
  const saved: unknown[] = []
  const creds = await convertManifestCode('the-code', {
    fetch: fakeFetch,
    saveCreds: async (c) => { saved.push(c) },
  })
  expect(calls.length).toBe(1)
  expect(calls[0]!.url).toBe('https://api.github.com/app-manifests/the-code/conversions')
  expect(calls[0]!.init?.method).toBe('POST')
  expect((calls[0]!.init?.headers as Record<string, string>)['Accept']).toBe('application/vnd.github+json')
  expect(creds.appId).toBe(4242)
  expect(creds.pem).toContain('BEGIN RSA PRIVATE KEY')
  expect(creds.webhookSecret).toBe('whsec-xyz')
  expect(saved.length).toBe(1)
  expect(saved[0]).toEqual(creds)
})

test('convertManifestCode rejects on a non-2xx conversion response', async () => {
  const fakeFetch = (async () => new Response('{"message":"Not Found"}', { status: 404 })) as typeof fetch
  await expect(convertManifestCode('bad-code', { fetch: fakeFetch })).rejects.toThrow(/404/)
})

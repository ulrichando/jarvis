// src/gh-app/token.test.ts
import { test, expect, describe } from 'bun:test'
import { generateKeyPairSync, createVerify } from 'node:crypto'
import { appJwt, installationToken, installationTokenForRepo } from './token.js'

const { privateKey, publicKey } = generateKeyPairSync('rsa', {
  modulusLength: 2048,
  publicKeyEncoding: { type: 'spki', format: 'pem' },
  privateKeyEncoding: { type: 'pkcs8', format: 'pem' },
})

describe('gh-app appJwt', () => {
  test('produces a 3-part RS256 JWT with iss/iat/exp per GitHub App rules', () => {
    const nowS = 1_760_000_000
    const jwt = appJwt(4242, privateKey, nowS)
    const parts = jwt.split('.')
    expect(parts.length).toBe(3)
    const header = JSON.parse(Buffer.from(parts[0]!, 'base64url').toString('utf8'))
    expect(header).toEqual({ alg: 'RS256', typ: 'JWT' })
    const payload = JSON.parse(Buffer.from(parts[1]!, 'base64url').toString('utf8'))
    expect(payload.iss).toBe('4242')
    expect(payload.iat).toBe(nowS - 60)   // 60s clock-skew backdate
    expect(payload.exp).toBe(nowS + 540)  // 9min < GitHub's 10min max
    expect(payload.exp - payload.iat).toBeLessThanOrEqual(600)
    // Signature actually verifies as RSA-SHA256 over header.payload
    const v = createVerify('RSA-SHA256')
    v.update(`${parts[0]}.${parts[1]}`)
    expect(v.verify(publicKey, Buffer.from(parts[2]!, 'base64url'))).toBe(true)
    // Tampered payload must NOT verify
    const v2 = createVerify('RSA-SHA256')
    v2.update(`${parts[0]}.${Buffer.from('{"iss":"9"}').toString('base64url')}`)
    expect(v2.verify(publicKey, Buffer.from(parts[2]!, 'base64url'))).toBe(false)
  })
})

describe('gh-app installationToken', () => {
  test('POSTs /app/installations/{id}/access_tokens with Bearer jwt and returns token/expiresAt', async () => {
    const calls: { url: string; init: RequestInit | undefined }[] = []
    const fakeFetch = (async (url: string | URL | Request, init?: RequestInit) => {
      calls.push({ url: String(url), init })
      return new Response(JSON.stringify({ token: 'ghs_abc123', expires_at: '2026-07-03T13:00:00Z' }), { status: 201 })
    }) as typeof fetch
    const r = await installationToken(987, 'the.app.jwt', { fetch: fakeFetch })
    expect(calls.length).toBe(1)
    expect(calls[0]!.url).toBe('https://api.github.com/app/installations/987/access_tokens')
    expect(calls[0]!.init?.method).toBe('POST')
    const h = calls[0]!.init?.headers as Record<string, string>
    expect(h['Authorization']).toBe('Bearer the.app.jwt')
    expect(h['Accept']).toBe('application/vnd.github+json')
    expect(r).toEqual({ token: 'ghs_abc123', expiresAt: '2026-07-03T13:00:00Z' })
  })

  // Without a body GitHub mints a token for ALL the installation's repos with
  // ALL the app's permissions — least-privilege scoping is one POST body.
  test('scopes the token to the target repo with minimal write permissions', async () => {
    const calls: { url: string; init: RequestInit | undefined }[] = []
    const fakeFetch = (async (url: string | URL | Request, init?: RequestInit) => {
      calls.push({ url: String(url), init })
      return new Response(JSON.stringify({ token: 'ghs_scoped', expires_at: '2026-07-03T13:00:00Z' }), { status: 201 })
    }) as typeof fetch
    const r = await installationToken(987, 'the.app.jwt', { fetch: fakeFetch }, 'ulrichando/jarvis')
    expect(r.token).toBe('ghs_scoped')
    const body = JSON.parse(String(calls[0]!.init?.body))
    expect(body.repositories).toEqual(['jarvis']) // repo NAME, not owner/name
    expect(body.permissions).toEqual({ contents: 'write', pull_requests: 'write', issues: 'write' })
    const h = calls[0]!.init?.headers as Record<string, string>
    expect(h['content-type']).toBe('application/json')
  })

  test('non-2xx minting response rejects without leaking the jwt', async () => {
    const fakeFetch = (async () => new Response('{"message":"bad creds"}', { status: 401 })) as typeof fetch
    let err: Error | null = null
    try { await installationToken(987, 'secret.jwt.value', { fetch: fakeFetch }) } catch (e) { err = e as Error }
    expect(err).not.toBeNull()
    expect(err!.message).toContain('401')
    expect(err!.message).not.toContain('secret.jwt.value')
  })
})

describe('gh-app installationTokenForRepo', () => {
  test('resolves the repo installation then mints a repo-scoped token', async () => {
    const calls: { url: string; init: RequestInit | undefined }[] = []
    const fakeFetch = (async (url: string | URL | Request, init?: RequestInit) => {
      calls.push({ url: String(url), init })
      if (String(url).includes('/repos/')) {
        return new Response(JSON.stringify({ id: 555 }), { status: 200 })
      }
      return new Response(JSON.stringify({ token: 'ghs_fresh', expires_at: '2026-07-04T20:00:00Z' }), { status: 201 })
    }) as typeof fetch
    const r = await installationTokenForRepo(4242, privateKey, 'ulrichando/maxrun', { fetch: fakeFetch })
    expect(r).toEqual({ token: 'ghs_fresh', expiresAt: '2026-07-04T20:00:00Z' })
    expect(calls.length).toBe(2)
    expect(calls[0]!.url).toBe('https://api.github.com/repos/ulrichando/maxrun/installation')
    expect(calls[1]!.url).toBe('https://api.github.com/app/installations/555/access_tokens')
    // Repo-scoped mint: repositories carries the bare repo NAME.
    expect(JSON.parse(String(calls[1]!.init?.body)).repositories).toEqual(['maxrun'])
  })

  test('installation lookup failure rejects with status only', async () => {
    const fakeFetch = (async () => new Response('{"message":"Not Found"}', { status: 404 })) as typeof fetch
    let err: Error | null = null
    try { await installationTokenForRepo(4242, privateKey, 'o/gone', { fetch: fakeFetch }) } catch (e) { err = e as Error }
    expect(err).not.toBeNull()
    expect(err!.message).toBe('installation lookup failed: HTTP 404')
  })
})

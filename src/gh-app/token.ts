// src/gh-app/token.ts — GitHub App auth: app JWT → installation access token.
//
// The app JWT (RS256, signed with the app's private key) only proves "I am
// app N" — it can't touch repos. The worker exchanges it per-job for a
// short-lived installation token scoped to the repos of ONE installation;
// that token is what gh/git use in the sandbox. Zero-dep signing via
// node:crypto (same hand-rolled-JWT approach as proxyJwt.ts, RS256 here
// because GitHub requires the app's RSA key).
import { createSign } from 'node:crypto'

function b64urlJson(obj: unknown): string {
  return Buffer.from(JSON.stringify(obj)).toString('base64url')
}

/**
 * RS256 app JWT per GitHub's rules: iat backdated 60s (clock skew), exp ≤10min
 * out (we use 9). `iss` is the App ID as a string.
 */
export function appJwt(appId: number | string, pem: string, nowS: number = Math.floor(Date.now() / 1000)): string {
  const header = { alg: 'RS256', typ: 'JWT' }
  const payload = { iss: String(appId), iat: nowS - 60, exp: nowS + 540 }
  const signingInput = `${b64urlJson(header)}.${b64urlJson(payload)}`
  const signer = createSign('RSA-SHA256')
  signer.update(signingInput)
  const sig = signer.sign(pem).toString('base64url')
  return `${signingInput}.${sig}`
}

export type TokenDeps = { fetch: typeof fetch }
export type InstallationToken = { token: string; expiresAt: string }

export async function installationToken(
  installationId: number,
  jwt: string,
  deps: TokenDeps,
): Promise<InstallationToken> {
  const res = await deps.fetch(`https://api.github.com/app/installations/${installationId}/access_tokens`, {
    method: 'POST',
    headers: {
      Authorization: `Bearer ${jwt}`,
      Accept: 'application/vnd.github+json',
    },
  })
  if (!res.ok) {
    // Status only — never echo the request (jwt) or the body into errors that
    // end up in job rows / logs.
    throw new Error(`installation token mint failed: HTTP ${res.status}`)
  }
  const raw = (await res.json()) as { token?: unknown; expires_at?: unknown }
  if (typeof raw.token !== 'string' || typeof raw.expires_at !== 'string') {
    throw new Error('installation token mint: response missing token/expires_at')
  }
  return { token: raw.token, expiresAt: raw.expires_at }
}

/* eslint-disable custom-rules/no-process-exit -- CLI subcommand handler intentionally exits */

/**
 * JARVIS-account login for the self-hosted stack — `jarvis auth login`.
 *
 * Signs in to the JARVIS web app (better-auth email+password), fetches this
 * user's long-lived Remote Control token (GET /api/bridge/token), and persists
 * JARVIS_BRIDGE_BASE_URL + JARVIS_BRIDGE_TOKEN to ~/.jarvis/keys.env, which
 * every launcher sources (`set -a`) into the environment. After that,
 * /remote-control and `jarvis remote-control` run against the self-hosted CCR
 * server — no claude.ai account or subscription involved (that path still
 * exists behind `jarvis auth login --claudeai`; see handlers/auth.ts).
 *
 * The web session is used once (to mint/fetch the bridge token) and then
 * signed out; the only artifact stored locally is the bridge token.
 */

import { createInterface } from 'node:readline/promises'

import {
  keysEnvPath,
  readKeysEnvValue,
  removeKeysEnvKeys,
  upsertKeysEnv,
} from '../../utils/jarvisKeysEnv.js'

const DEFAULT_SERVER_URL = 'http://localhost:3000'
const FETCH_TIMEOUT_MS = 10_000

const BASE_URL_KEY = 'JARVIS_BRIDGE_BASE_URL'
const TOKEN_KEY = 'JARVIS_BRIDGE_TOKEN'

function fail(message: string): never {
  process.stderr.write(message.endsWith('\n') ? message : message + '\n')
  process.exit(1)
}

/** Accepts "host:3000", "http://host:3000/", … → "http://host:3000". */
function normalizeServerUrl(raw: string): string {
  let candidate = raw.trim()
  if (!/^https?:\/\//i.test(candidate)) candidate = `http://${candidate}`
  let parsed: URL
  try {
    parsed = new URL(candidate)
  } catch {
    fail(`Invalid server URL: ${raw}`)
  }
  const path = parsed.pathname.replace(/\/+$/, '')
  return parsed.origin + path
}

/**
 * The web-app root, used for /api/auth/* and /api/bridge/token. Accepts a
 * plain server URL or a full bridge base (…/api/bridge — what the bridge
 * runtime appends /v1/* to, and what the Settings card shows) and strips the
 * bridge suffix.
 */
function resolveServerRoot(flagUrl: string | undefined): string {
  const raw =
    flagUrl ??
    process.env[BASE_URL_KEY] ??
    readKeysEnvValue(BASE_URL_KEY) ??
    DEFAULT_SERVER_URL
  return normalizeServerUrl(raw).replace(/\/api\/bridge$/, '')
}

/** What the bridge runtime expects in JARVIS_BRIDGE_BASE_URL: it appends
 * /v1/environments/… so the value must include the /api/bridge prefix. */
function bridgeBaseFromRoot(root: string): string {
  return `${root}/api/bridge`
}

function isLoopback(url: string): boolean {
  const host = new URL(url).hostname
  return host === 'localhost' || host === '127.0.0.1' || host === '::1'
}

async function promptLine(label: string): Promise<string> {
  const rl = createInterface({ input: process.stdin, output: process.stdout })
  try {
    return (await rl.question(label)).trim()
  } finally {
    rl.close()
  }
}

/** Password prompt with echo off (raw mode, manual line editing). */
function promptHidden(label: string): Promise<string> {
  process.stdout.write(label)
  return new Promise((resolve, reject) => {
    const stdin = process.stdin
    let value = ''
    const wasRaw = stdin.isRaw
    stdin.setRawMode?.(true)
    stdin.resume()
    stdin.setEncoding('utf8')
    const cleanup = () => {
      stdin.removeListener('data', onData)
      stdin.setRawMode?.(wasRaw ?? false)
      stdin.pause()
      process.stdout.write('\n')
    }
    const onData = (chunk: string) => {
      for (const ch of chunk) {
        if (ch === '\r' || ch === '\n') {
          cleanup()
          resolve(value)
          return
        }
        if (ch === '\u0003') {
          // Ctrl-C
          cleanup()
          reject(new Error('Cancelled'))
          return
        }
        if (ch === '\u007f' || ch === '\b') {
          value = value.slice(0, -1)
          continue
        }
        value += ch
      }
    }
    stdin.on('data', onData)
  })
}

async function fetchJson(
  url: string,
  init?: RequestInit,
): Promise<Response> {
  return fetch(url, { ...init, signal: AbortSignal.timeout(FETCH_TIMEOUT_MS) })
}

/** Cookie header value from a response's Set-Cookie headers. */
function sessionCookie(res: Response): string | undefined {
  const headers = res.headers as Headers & { getSetCookie?: () => string[] }
  const setCookies =
    headers.getSetCookie?.() ??
    (res.headers.get('set-cookie') ? [res.headers.get('set-cookie')!] : [])
  const pairs = setCookies
    .map(c => c.split(';')[0]!.trim())
    .filter(c => c.includes('='))
  return pairs.length > 0 ? pairs.join('; ') : undefined
}

async function bodyDetail(res: Response): Promise<string> {
  try {
    const text = await res.text()
    const parsed = JSON.parse(text) as { message?: string; error?: string }
    return parsed.message ?? parsed.error ?? text.slice(0, 200)
  } catch {
    return ''
  }
}

function persistCredentials(baseUrl: string, token: string): void {
  upsertKeysEnv({ [BASE_URL_KEY]: baseUrl, [TOKEN_KEY]: token })
  // Keep this process consistent (e.g. if a verify step runs after).
  process.env[BASE_URL_KEY] = baseUrl
  process.env[TOKEN_KEY] = token
}

function printSuccess(baseUrl: string, who: string, machines?: number): void {
  process.stdout.write(
    `Logged in to ${baseUrl}${who ? ` as ${who}` : ''}.\n` +
      `Remote Control credentials saved to ${keysEnvPath()}` +
      (machines !== undefined
        ? ` (${machines} machine${machines === 1 ? '' : 's'} registered on your account).\n`
        : '.\n') +
      'Start a new `jarvis` session and run /remote-control — or run ' +
      '`jarvis remote-control` to serve this machine headless.\n',
  )
}

export async function jarvisAuthLogin(opts: {
  url?: string
  email?: string
  token?: string
}): Promise<void> {
  const serverRoot = resolveServerRoot(opts.url)
  const bridgeBase = bridgeBaseFromRoot(serverRoot)

  if (!isLoopback(serverRoot) && serverRoot.startsWith('http://')) {
    process.stderr.write(
      `Warning: ${serverRoot} is plain HTTP on a non-loopback host — credentials ` +
        'travel in cleartext, and the Remote Control worker only accepts HTTPS ' +
        'or localhost HTTP base URLs.\n',
    )
  }

  // Escape hatch: a token pasted from Settings → Connectors skips the
  // email/password sign-in entirely.
  if (opts.token) {
    persistCredentials(bridgeBase, opts.token.trim())
    printSuccess(serverRoot, '')
    process.exit(0)
  }

  // Probe the server before asking for credentials.
  const probe = await fetchJson(`${serverRoot}/api/auth/ok`).catch(
    () => undefined,
  )
  if (!probe?.ok) {
    fail(
      `Can't reach the JARVIS server at ${serverRoot}` +
        (probe ? ` (HTTP ${probe.status} from /api/auth/ok)` : '') +
        '.\nIs the web app running? Pass --url <http://host:3000> to use a different server.',
    )
  }

  const interactive = !!process.stdin.isTTY
  const envPassword = process.env.JARVIS_LOGIN_PASSWORD
  if (!interactive && (!opts.email || !envPassword)) {
    fail(
      'Not a terminal: pass --email <address> and set JARVIS_LOGIN_PASSWORD ' +
        'to log in non-interactively (or pass --token from Settings → Connectors).',
    )
  }
  const email = opts.email ?? (await promptLine(`Email for ${serverRoot}: `))
  if (!email) fail('Email is required.')
  const password = envPassword ?? (await promptHidden('Password: '))
  if (!password) fail('Password is required.')

  const signIn = await fetchJson(`${serverRoot}/api/auth/sign-in/email`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ email, password }),
  }).catch((err: unknown) =>
    fail(`Could not reach ${serverRoot}: ${err instanceof Error ? err.message : String(err)}`),
  )
  if (signIn.status === 401 || signIn.status === 403) {
    fail(
      'Invalid email or password.\n' +
        `Manage your JARVIS account in the web app at ${serverRoot}.`,
    )
  }
  if (!signIn.ok) {
    fail(`Login failed: HTTP ${signIn.status} ${await bodyDetail(signIn)}`)
  }
  const cookie = sessionCookie(signIn)
  if (!cookie) {
    fail(
      'Login succeeded but the server returned no session cookie — cannot ' +
        'fetch the Remote Control token.',
    )
  }

  const tokenRes = await fetchJson(`${serverRoot}/api/bridge/token`, {
    headers: { cookie },
  })
  if (!tokenRes.ok) {
    fail(
      `Logged in, but fetching the Remote Control token failed: HTTP ${tokenRes.status} ` +
        `${await bodyDetail(tokenRes)}`,
    )
  }
  const { token } = (await tokenRes.json()) as { token?: string }
  if (!token || typeof token !== 'string') {
    fail('Logged in, but the server returned no Remote Control token.')
  }

  // Best-effort extras: machine count for the success line, then drop the
  // web session — the bridge token is the only long-lived artifact.
  let machines: number | undefined
  try {
    const envRes = await fetchJson(`${serverRoot}/api/bridge/v1/environments`, {
      headers: { cookie },
    })
    if (envRes.ok) {
      const parsed = (await envRes.json()) as { environments?: unknown[] }
      if (Array.isArray(parsed.environments)) machines = parsed.environments.length
    }
  } catch {
    /* cosmetic only */
  }
  await fetchJson(`${serverRoot}/api/auth/sign-out`, {
    method: 'POST',
    headers: { cookie, 'Content-Type': 'application/json' },
    body: '{}',
  }).catch(() => {})

  persistCredentials(bridgeBase, token)
  printSuccess(serverRoot, email, machines)
  process.exit(0)
}

/**
 * Remove the persisted JARVIS Remote Control credentials. Does NOT exit —
 * the `auth logout` action composes this with the Anthropic logout when that
 * path is enabled. The server-side token stays valid (it's the stable
 * per-account token shown in Settings → Connectors); this only disconnects
 * this machine.
 */
export async function jarvisAuthLogout(): Promise<void> {
  const removed = removeKeysEnvKeys([BASE_URL_KEY, TOKEN_KEY])
  delete process.env[BASE_URL_KEY]
  delete process.env[TOKEN_KEY]
  process.stdout.write(
    removed
      ? `Removed JARVIS Remote Control credentials from ${keysEnvPath()}.\n`
      : 'No JARVIS Remote Control credentials were stored on this machine.\n',
  )
}

/** Status line data for `jarvis auth status`. Env wins (launchers source
 * keys.env); falls back to the file for direct binary invocations. */
export function getJarvisBridgeStatus(): {
  baseUrl: string | undefined
  tokenConfigured: boolean
} {
  const baseUrl = process.env[BASE_URL_KEY] || readKeysEnvValue(BASE_URL_KEY)
  const token = process.env[TOKEN_KEY] || readKeysEnvValue(TOKEN_KEY)
  return { baseUrl: baseUrl || undefined, tokenConfigured: !!token }
}

// src/gh-app/server.ts — the gh.0wlan.com Bun service: setup + webhook + worker.
//
// Routes:
//   GET  /setup           → manifest form (one click → GitHub creates the app)
//   GET  /setup/callback  → exchanges the manifest code, persists credentials
//   POST /webhook         → signature-verified event intake → job queue
//   GET  /health          → liveness
//
// `makeApp(deps)` is the pure, fully-injected fetch handler (smoke-tested);
// the import.meta.main block wires real env/Postgres/Docker and starts the
// worker loop alongside Bun.serve.
import { mkdirSync, readFileSync, writeFileSync } from 'node:fs'
import { dirname } from 'node:path'
import { setupPageHtml, convertManifestCode, type AppCreds } from './manifest.js'
import { handleWebhook, type WebhookDeps } from './webhook.js'
import { ensureSchema, jobStore, type SqlClient } from './jobs.js'
import { appJwt, installationToken } from './token.js'
import { capsFromEnv, startWorker } from './worker.js'
import { runInSandbox, DEFAULT_SANDBOX_IMAGE, type SpawnSpec, type SpawnResult } from './runInSandbox.js'

/** GitHub's documented webhook payload cap. */
export const MAX_WEBHOOK_BYTES = 25 * 1024 * 1024

export type ServerDeps = {
  base: string
  webhook: WebhookDeps
  fetch?: typeof fetch
  saveCreds?: (c: AppCreds) => Promise<void> | void
  /** One-time setup gate: when this reports true, /setup/callback refuses to
   * convert/overwrite — GitHub reaches the host unauthenticated, so without
   * it anyone could POST their own manifest and take over the app creds. */
  credsExist?: () => Promise<boolean> | boolean
  log?: (m: string) => void
}

export function makeApp(deps: ServerDeps): (req: Request) => Promise<Response> {
  const f = deps.fetch ?? fetch
  return async (req: Request): Promise<Response> => {
    const url = new URL(req.url)
    const path = url.pathname

    if (req.method === 'GET' && path === '/health') return new Response('ok', { status: 200 })

    if (req.method === 'GET' && path === '/setup') {
      return new Response(setupPageHtml(deps.base), { status: 200, headers: { 'content-type': 'text/html; charset=utf-8' } })
    }

    if (req.method === 'GET' && path === '/setup/callback') {
      const code = url.searchParams.get('code')
      if (!code) return new Response('missing code', { status: 400 })
      // Idempotent capture: refuse when creds are already present. This route
      // is reachable unauthenticated (GitHub redirects land here), so it must
      // be one-shot — never an overwrite path.
      if (await deps.credsExist?.()) {
        deps.log?.('setup/callback: refused — app already configured (creds present)')
        return new Response('already configured — credentials exist; refusing to overwrite', { status: 409 })
      }
      try {
        const creds = await convertManifestCode(code, { fetch: f, saveCreds: deps.saveCreds })
        // Confirmation only — NEVER echo pem/webhookSecret back over HTTP.
        return new Response(
          `<!doctype html><p>JARVIS App created (appId ${creds.appId}). Credentials captured — restart the service to load them, then install the app on a repo.</p>`,
          { status: 200, headers: { 'content-type': 'text/html; charset=utf-8' } },
        )
      } catch (e) {
        deps.log?.(`setup/callback: ${e instanceof Error ? e.message : String(e)}`)
        return new Response('conversion failed', { status: 502 })
      }
    }

    if (req.method === 'POST' && path === '/webhook') {
      // GitHub caps webhook payloads at 25 MB — a bigger body is not GitHub.
      // Reject on the declared length BEFORE buffering (this handler used to
      // read the whole body into memory ahead of the signature check), and
      // re-check after the read for chunked/no-Content-Length senders.
      const declaredLen = Number(req.headers.get('content-length') ?? 0)
      if (declaredLen > MAX_WEBHOOK_BYTES) return new Response('payload too large', { status: 413 })
      const raw = new Uint8Array(await req.arrayBuffer()) // RAW bytes — signature is over these
      if (raw.byteLength > MAX_WEBHOOK_BYTES) return new Response('payload too large', { status: 413 })
      const headers: Record<string, string> = {}
      req.headers.forEach((v, k) => { headers[k] = v })
      const r = await handleWebhook(headers, raw, deps.webhook)
      return new Response(r.body, { status: r.status })
    }

    return new Response('not found', { status: 404 })
  }
}

// --- credentials: env first, then the captured creds file ---
export function credsFromEnvOrFile(
  env: Record<string, string | undefined>,
  readFile: (p: string) => string = (p) => readFileSync(p, 'utf8'),
): AppCreds | null {
  if (env.GH_APP_ID && env.GH_APP_PRIVATE_KEY && env.GH_APP_WEBHOOK_SECRET) {
    return { appId: Number(env.GH_APP_ID), pem: env.GH_APP_PRIVATE_KEY, webhookSecret: env.GH_APP_WEBHOOK_SECRET }
  }
  try {
    const raw = JSON.parse(readFile(credsPath(env)))
    if (typeof raw.appId === 'number' && typeof raw.pem === 'string' && typeof raw.webhookSecret === 'string') {
      return { appId: raw.appId, pem: raw.pem, webhookSecret: raw.webhookSecret }
    }
  } catch { /* not set up yet */ }
  return null
}

function credsPath(env: Record<string, string | undefined>): string {
  return env.GH_APP_CREDS_PATH ?? '/data/creds.json'
}

// Provider keys the in-sandbox `jarvis -p` needs to reach an LLM. STRICT
// allowlist — the sandbox runs untrusted-task-driven code, so app creds,
// webhook secret, and DB DSN must never cross into it.
const SANDBOX_ENV_KEYS = [
  'ANTHROPIC_API_KEY', 'DEEPSEEK_API_KEY', 'GROQ_API_KEY', 'OPENAI_API_KEY',
  'JARVIS_PROVIDER', 'JARVIS_MODEL',
] as const

export function sandboxEnvPassthrough(env: Record<string, string | undefined>): Record<string, string> {
  const out: Record<string, string> = {}
  for (const k of SANDBOX_ENV_KEYS) if (env[k]) out[k] = env[k]!
  return out
}

// --- Docker Engine API spawn (via DOCKER_HOST → tecnativa docker-proxy) ---
//
// No docker binary in the image: the restricted proxy speaks the plain Engine
// REST API, which is all a create → start → wait → logs → force-remove
// lifecycle needs. Timeout = race on /wait, then force-remove (kills too).
//
// The sandbox network is REQUIRED and fail-closed: without an explicit
// NetworkMode the container lands on the default bridge with full internet
// egress while raw provider keys sit in its env — prompt-injected repo
// content could exfiltrate them. No network → refuse to spawn (the worker
// marks the job failed); never run a job on the open bridge.
export function dockerSpawnContainer(dockerHost: string, network: string | undefined) {
  if (!network?.trim()) {
    throw new Error(
      'GH_APP_SANDBOX_NETWORK is required — refusing to spawn a sandbox on the default (open-egress) docker bridge',
    )
  }
  const base = dockerHost.replace(/^tcp:\/\//, 'http://').replace(/\/$/, '')
  return async (spec: SpawnSpec): Promise<SpawnResult> => {
    const create = await fetch(`${base}/containers/create`, {
      method: 'POST',
      headers: { 'content-type': 'application/json' },
      body: JSON.stringify({
        Image: spec.image,
        Cmd: spec.cmd,
        Env: Object.entries(spec.env).filter(([, v]) => v !== undefined).map(([k, v]) => `${k}=${v}`),
        WorkingDir: '/app',
        HostConfig: {
          // Writable rootfs (the entry clones under os.tmpdir()); still fenced:
          // no host mounts, no privileges, bounded memory/pids.
          ReadonlyRootfs: !spec.writableWorkdir,
          CapDrop: ['ALL'],
          SecurityOpt: ['no-new-privileges'],
          Memory: 2 * 1024 * 1024 * 1024,
          PidsLimit: 512,
          NetworkMode: network,
        },
      }),
    })
    if (create.status !== 201) throw new Error(`container create failed: HTTP ${create.status}`)
    const { Id } = (await create.json()) as { Id: string }
    const rm = () => fetch(`${base}/containers/${Id}?force=true`, { method: 'DELETE' }).catch(() => {})
    try {
      const start = await fetch(`${base}/containers/${Id}/start`, { method: 'POST' })
      if (start.status !== 204) throw new Error(`container start failed: HTTP ${start.status}`)
      const waited = await Promise.race([
        fetch(`${base}/containers/${Id}/wait`, { method: 'POST' }).then((r) => r.json() as Promise<{ StatusCode: number }>),
        new Promise<never>((_, rej) => setTimeout(() => rej(new Error(`container timed out after ${spec.timeoutSec}s`)), spec.timeoutSec * 1000)),
      ])
      let stderr = ''
      try {
        const logs = await fetch(`${base}/containers/${Id}/logs?stdout=1&stderr=1&tail=50`)
        stderr = demuxDockerLogs(new Uint8Array(await logs.arrayBuffer())).slice(-2000)
      } catch { /* logs are best-effort */ }
      return { code: waited.StatusCode, stdout: '', stderr }
    } finally {
      await rm()
    }
  }
}

/** Strip the 8-byte frame headers from Docker's multiplexed log stream. */
export function demuxDockerLogs(buf: Uint8Array): string {
  let out = ''
  let i = 0
  while (i + 8 <= buf.length) {
    const size = (buf[i + 4]! << 24) | (buf[i + 5]! << 16) | (buf[i + 6]! << 8) | buf[i + 7]!
    const start = i + 8
    const end = Math.min(start + size, buf.length)
    out += new TextDecoder().decode(buf.subarray(start, end))
    if (size <= 0) break
    i = end
  }
  return out || new TextDecoder().decode(buf) // non-multiplexed (tty) fallback
}

if (import.meta.main) {
  const env = process.env
  const log = (m: string) => console.log(`[gh-app] ${new Date().toISOString()} ${m}`)
  const creds = credsFromEnvOrFile(env)
  if (!creds) log('no app credentials yet — /webhook will 401 until /setup runs and the service restarts')

  const dbUrl = env.DATABASE_URL ?? env.GH_APP_DATABASE_URL
  if (!dbUrl) { console.error('[gh-app] DATABASE_URL is required'); process.exit(1) }
  // Bun's built-in Postgres client; adapted to the driver-agnostic SqlClient.
  const { SQL } = await import('bun')
  const db = new SQL(dbUrl)
  const sql: SqlClient = async (text, params = []) => ({ rows: (await db.unsafe(text, params as never[])) as unknown as any[] })
  await ensureSchema(sql)
  const store = jobStore(sql)

  const caps = capsFromEnv(env)
  if (!env.GH_APP_SANDBOX_NETWORK?.trim()) {
    log('GH_APP_SANDBOX_NETWORK is not set — sandbox spawns will be REFUSED (jobs marked failed) until an egress-restricted docker network is configured')
  }
  const allowlist = (env.GH_APP_ALLOWLIST ?? 'ulrichando').split(',').map((s) => s.trim()).filter(Boolean)
  const trigger = env.GH_APP_TRIGGER ?? '@jarvis'

  const saveCreds = (c: AppCreds) => {
    const p = credsPath(env)
    mkdirSync(dirname(p), { recursive: true })
    writeFileSync(p, JSON.stringify(c, null, 2), { mode: 0o600 })
    log(`credentials captured to ${p} (appId ${c.appId}) — restart to activate`)
  }

  const app = makeApp({
    base: env.GH_APP_BASE_URL ?? 'https://gh.0wlan.com',
    webhook: {
      secret: creds?.webhookSecret ?? '',
      allowlist,
      trigger,
      enqueue: (j) => store.enqueue(j),
      log,
    },
    saveCreds,
    // Re-read on every callback (not the boot-time `creds` snapshot) so the
    // takeover window closes the moment the FIRST callback writes the file.
    credsExist: () => credsFromEnvOrFile(env) !== null,
    log,
  })

  const worker = creds
    ? startWorker({
        store,
        mintToken: async (installationId, repo) =>
          (await installationToken(installationId, appJwt(creds.appId, creds.pem), { fetch }, repo)).token,
        runInSandbox: (job, token) =>
          runInSandbox(job, token, {
            spawnContainer: dockerSpawnContainer(env.DOCKER_HOST ?? 'tcp://docker-proxy:2375', env.GH_APP_SANDBOX_NETWORK),
            image: env.GH_APP_SANDBOX_IMAGE ?? DEFAULT_SANDBOX_IMAGE,
            timeoutSec: caps.timeoutSec,
            // IS_SANDBOX=1 satisfies the CLI's root guard for bypass mode (the
            // no-internet check is USER_TYPE=ant-only, skipped for self-hosted);
            // JARVIS_REQUIRE_LOGIN=0 skips the interactive login gate — the
            // sandbox authenticates to models via the passed provider keys.
            entryEnv: { ...sandboxEnvPassthrough(env), GH_APP_ALLOWLIST: allowlist.join(','), GH_APP_TRIGGER: trigger, IS_SANDBOX: '1', JARVIS_REQUIRE_LOGIN: '0' },
          }),
        dailyCap: caps.dailyCap,
        concurrency: caps.concurrency,
        log,
      })
    : null

  const port = Number(env.GH_APP_PORT ?? 8790)
  Bun.serve({ port, hostname: '0.0.0.0', fetch: app })
  log(`listening on :${port} (worker ${worker ? 'on' : 'off — no creds'}; cap ${caps.dailyCap}/day, ${caps.concurrency} concurrent)`)
}

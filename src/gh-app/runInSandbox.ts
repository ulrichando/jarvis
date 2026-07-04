// src/gh-app/runInSandbox.ts — host side of the sandboxed run.
//
// The worker never runs the task in-process: it spawns a throwaway container
// (via the injected spawnContainer — wired to the docker-proxy on the VPS,
// same restricted Docker path the /code sandboxes use) whose command is the
// container entry (`bun src/gh-app/containerEntry.ts`). The installation
// token and the job travel ONLY via env — never argv (argv is visible in
// `docker inspect` / process lists) — and any error text that comes back is
// token-redacted before it can land in job rows or logs.
import type { Job } from './jobs.js'

export type SpawnSpec = {
  image: string
  cmd: string[]
  env: Record<string, string | undefined>
  timeoutSec: number
  /** The entry clones into its workdir — must be writable even when the
   * rootfs is read-only. */
  writableWorkdir: boolean
}

export type SpawnResult = { code: number; stdout: string; stderr: string }

export type SandboxDeps = {
  spawnContainer: (spec: SpawnSpec) => Promise<SpawnResult>
  image?: string
  timeoutSec?: number
  /** Extra env for the entry (GH_APP_ALLOWLIST, GH_APP_TRIGGER, …). */
  entryEnv?: Record<string, string>
}

export type SandboxOutcome = { ok: boolean; error?: string }

export const DEFAULT_SANDBOX_IMAGE = 'jarvis-gh-app'

export async function runInSandbox(job: Job, token: string, deps: SandboxDeps): Promise<SandboxOutcome> {
  const timeoutSec = deps.timeoutSec ?? 900
  const spec: SpawnSpec = {
    image: deps.image ?? DEFAULT_SANDBOX_IMAGE,
    cmd: ['bun', 'src/gh-app/containerEntry.ts'],
    env: {
      ...deps.entryEnv,
      GH_TOKEN: token,
      GH_APP_JOB: JSON.stringify(job),
      GH_APP_TIMEOUT_SEC: String(timeoutSec),
    },
    timeoutSec,
    writableWorkdir: true,
  }
  const redact = (s: string) => (token ? s.split(token).join('***') : s)
  try {
    const r = await deps.spawnContainer(spec)
    if (r.code === 0) return { ok: true }
    return { ok: false, error: redact(`sandbox exited ${r.code}: ${r.stderr.slice(0, 500)}`) }
  } catch (e) {
    const msg = e instanceof Error ? e.message : String(e)
    return { ok: false, error: redact(`sandbox spawn failed: ${msg}`) }
  }
}

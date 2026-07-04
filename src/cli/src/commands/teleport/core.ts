import { execFile } from 'node:child_process'
import { promises as fs } from 'node:fs'
import path from 'node:path'
import { promisify } from 'node:util'

import { readKeysEnvValue } from '../../utils/jarvisKeysEnv.js'

// Non-UI teleport logic shared by the /teleport slash picker (TeleportPicker.tsx)
// and the pull path. Returns structured results (never process.exit / readline)
// so it's safe to call from inside the live REPL.

const TIMEOUT_MS = 20_000
const execFileP = promisify(execFile)

export type CloudSession = {
  session_id: string
  title: string
  repo: string
  updated_at: number | null
}

function auth(): { base: string; token: string } | { error: string } {
  const raw =
    process.env.JARVIS_BRIDGE_BASE_URL ||
    readKeysEnvValue('JARVIS_BRIDGE_BASE_URL') ||
    process.env.JARVIS_SERVER_URL ||
    readKeysEnvValue('JARVIS_SERVER_URL')
  const token =
    process.env.JARVIS_BRIDGE_TOKEN || readKeysEnvValue('JARVIS_BRIDGE_TOKEN')
  if (!raw || !token) {
    return { error: 'Not linked to a JARVIS server. Run `jarvis auth login` first.' }
  }
  // Bare origin — JARVIS_BRIDGE_BASE_URL carries a /api/bridge suffix the call
  // paths below already include, so strip it to avoid a doubled path (404).
  const base = raw.replace(/\/+$/, '').replace(/\/api\/bridge$/, '').replace(/\/api$/, '')
  return { base, token }
}

async function getJson<T>(
  url: string,
  token: string,
): Promise<{ ok: true; data: T } | { ok: false; error: string }> {
  try {
    const r = await fetch(url, {
      headers: { authorization: `Bearer ${token}` },
      signal: AbortSignal.timeout(TIMEOUT_MS),
    })
    if (r.status === 401) {
      return { ok: false, error: 'Server rejected the token — run `jarvis auth login` again.' }
    }
    if (!r.ok) return { ok: false, error: `Server error: HTTP ${r.status}` }
    return { ok: true, data: (await r.json()) as T }
  } catch (e) {
    return { ok: false, error: `Could not reach the server: ${e instanceof Error ? e.message : String(e)}` }
  }
}

async function git(args: string[], cwd?: string): Promise<{ stdout: string; ok: boolean }> {
  try {
    const { stdout } = await execFileP('git', args, { cwd, timeout: TIMEOUT_MS })
    return { stdout: String(stdout).trim(), ok: true }
  } catch {
    return { stdout: '', ok: false }
  }
}

export async function fetchCloudSessions(): Promise<
  { ok: true; sessions: CloudSession[] } | { ok: false; error: string }
> {
  const a = auth()
  if ('error' in a) return { ok: false, error: a.error }
  const res = await getJson<{ sessions?: CloudSession[] }>(`${a.base}/api/bridge/v1/cli/sessions`, a.token)
  if (!res.ok) return { ok: false, error: res.error }
  return { ok: true, sessions: res.data.sessions ?? [] }
}

export type PullResult = {
  repo: string
  branch: string
  resumed: boolean
}

export async function pullSession(
  id: string,
): Promise<{ ok: true; result: PullResult } | { ok: false; error: string }> {
  const a = auth()
  if ('error' in a) return { ok: false, error: a.error }
  const info = await getJson<{ repo?: string; branch?: string; native_jsonl?: string }>(
    `${a.base}/api/bridge/v1/sessions/${encodeURIComponent(id)}/teleport`,
    a.token,
  )
  if (!info.ok) return { ok: false, error: info.error }
  const { repo, branch, native_jsonl } = info.data
  if (!repo || !branch) {
    return { ok: false, error: 'That session has no pushed branch yet — open a PR (or push) in the session first.' }
  }
  // Defense-in-depth: branch reaches `git` argv — reject a name that could
  // smuggle a flag (`-…`) or traverse (`..`), even from our own server.
  if (!/^[A-Za-z0-9_./-]+$/.test(branch) || branch.startsWith('-') || branch.includes('..')) {
    return { ok: false, error: 'The session returned an unusable branch name.' }
  }

  // Only teleport inside a matching checkout — never mutate an unrelated repo.
  const inRepo = await git(['rev-parse', '--is-inside-work-tree'])
  if (!inRepo.ok) {
    return { ok: false, error: `Run this from a checkout of ${repo} (or use the shell: jarvis teleport ${id}).` }
  }
  const remote = (await git(['remote', 'get-url', 'origin'])).stdout
  if (!remote.includes(repo)) {
    return { ok: false, error: `You're in a different repository. cd into a ${repo} checkout and retry.` }
  }
  const dirty = (await git(['status', '--porcelain'])).stdout
  if (dirty) {
    return { ok: false, error: 'Working directory is not clean. Commit or stash your changes before teleporting.' }
  }

  const cwd = process.cwd()
  await git(['fetch', 'origin', '--', branch], cwd)
  let co = await git(['checkout', '--', branch], cwd)
  if (!co.ok) co = await git(['checkout', '-b', branch, `origin/${branch}`], cwd)
  if (!co.ok) return { ok: false, error: `Could not check out ${branch}. Fetch it manually: git fetch origin ${branch}.` }

  let resumed = false
  if (native_jsonl && native_jsonl.trim()) {
    try {
      const { getProjectDir } = await import('../../utils/sessionStoragePortable.js')
      const canonical = await fs.realpath(cwd).catch(() => cwd)
      const dir = getProjectDir(canonical)
      await fs.mkdir(dir, { recursive: true })
      await fs.writeFile(path.join(dir, `${id}.jsonl`), native_jsonl, 'utf8')
      resumed = true
    } catch {
      /* branch checked out regardless */
    }
  }
  return { ok: true, result: { repo, branch, resumed } }
}

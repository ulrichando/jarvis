import { execFile } from 'node:child_process'
import { promises as fs } from 'node:fs'
import { randomUUID } from 'node:crypto'
import path from 'node:path'
import { promisify } from 'node:util'

import { readKeysEnvValue } from '../../utils/jarvisKeysEnv.js'
import { getProjectDir } from '../../utils/sessionStoragePortable.js'
import { detectCurrentRepository } from '../../utils/detectRepository.js'

// Non-UI teleport logic shared by the /teleport slash picker (TeleportPicker.tsx).
// Models claude.ai's teleport: SAME-REPO ONLY — you must be in a checkout of the
// session's repo; we verify, check out the branch, and prepare the conversation
// for an in-place resume. We do NOT clone cross-repo (claude.ai doesn't either;
// cross-repo teleport is unimplemented upstream). Returns structured results
// (never process.exit) so it's safe from inside the live REPL.

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

/** Rewrite a container transcript so it resumes as a LOCAL session: point every
 *  line's sessionId at a fresh local UUID (the cloud id is 16-hex, but the CLI
 *  resume path is UUID-keyed) and rewrite the in-container cwd (/workspace/<repo>)
 *  to the local checkout. Non-JSON lines pass through untouched. */
export function rewriteTranscript(jsonl: string, sessionId: string, cwd: string): string {
  const out: string[] = []
  for (const line of jsonl.split('\n')) {
    if (!line.trim()) continue
    try {
      const o = JSON.parse(line) as Record<string, unknown>
      if ('sessionId' in o) o.sessionId = sessionId
      if ('cwd' in o) o.cwd = cwd
      out.push(JSON.stringify(o))
    } catch {
      out.push(line)
    }
  }
  return out.join('\n') + '\n'
}

export type TeleportPrep =
  // Conversation is ready — the picker calls context.resume(resumeId, …).
  | { kind: 'resume'; repo: string; branch: string; resumeId: string }
  // Branch checked out, but no conversation to resume (never happened / lost).
  | { kind: 'checkout'; repo: string; branch: string }

/**
 * Prepare a teleport, claude.ai-style. Verifies you're in a checkout of the
 * session's repo (else a clear mismatch error — no cloning), cleans/checks out
 * the branch, and stages the conversation for an in-place resume. `mismatchRepo`
 * is set when the only problem is being in the wrong repo, so the UI can guide.
 */
export async function prepareTeleport(
  id: string,
): Promise<
  | { ok: true; prep: TeleportPrep }
  | { ok: false; error: string; mismatchRepo?: string }
> {
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
  // Argv-injection guards: branch + repo both reach `git` argv.
  if (!/^[A-Za-z0-9_./-]+$/.test(branch) || branch.startsWith('-') || branch.includes('..')) {
    return { ok: false, error: 'The session returned an unusable branch name.' }
  }
  if (!/^[A-Za-z0-9](?:[A-Za-z0-9._-]){0,38}\/[A-Za-z0-9][A-Za-z0-9._-]{0,99}$/.test(repo) || repo.includes('..')) {
    return { ok: false, error: 'The session returned an unusable repository name.' }
  }

  // Claude-style: you must be in a checkout of the session's repo.
  const current = await detectCurrentRepository()
  if (!current || current.toLowerCase() !== repo.toLowerCase()) {
    return {
      ok: false,
      mismatchRepo: repo,
      error: `Teleport needs a checkout of ${repo}. Open that repo in a terminal (cd into your ${repo} clone), then run /teleport again.`,
    }
  }

  const dirty = (await git(['status', '--porcelain'])).stdout
  if (dirty) {
    return { ok: false, error: 'Working directory is not clean. Commit or stash your changes before teleporting.' }
  }

  // No `--` before the branch: `git checkout -- <x>` restores a PATHSPEC, not a
  // branch switch. The regex guard above is the flag-injection defense.
  await git(['fetch', 'origin', branch])
  let co = await git(['checkout', branch])
  if (!co.ok) co = await git(['checkout', '-b', branch, `origin/${branch}`])
  if (!co.ok) {
    return { ok: false, error: `Could not check out ${branch}. Fetch it manually: git fetch origin ${branch}.` }
  }

  // Stage the conversation for an in-place resume (fresh local UUID).
  if (native_jsonl && native_jsonl.trim()) {
    try {
      const root = (await git(['rev-parse', '--show-toplevel'])).stdout || process.cwd()
      const canonical = await fs.realpath(root).catch(() => root)
      const resumeId = randomUUID()
      const dir = getProjectDir(canonical)
      await fs.mkdir(dir, { recursive: true })
      await fs.writeFile(path.join(dir, `${resumeId}.jsonl`), rewriteTranscript(native_jsonl, resumeId, canonical), 'utf8')
      return { ok: true, prep: { kind: 'resume', repo, branch, resumeId } }
    } catch {
      /* fall through to checkout-only */
    }
  }
  return { ok: true, prep: { kind: 'checkout', repo, branch } }
}

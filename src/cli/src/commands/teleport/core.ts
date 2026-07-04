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

/** Clone a (possibly private) GitHub repo. Prefer `gh repo clone` — it uses the
 *  user's gh auth, so private repos work non-interactively; a bare
 *  `git clone https://…` would prompt for a password and fail with no TTY.
 *  Falls back to git (public repos / a configured credential helper). */
async function cloneRepo(repo: string, dest: string): Promise<boolean> {
  const CLONE_TIMEOUT_MS = 180_000 // clones can be large
  try {
    await execFileP('gh', ['repo', 'clone', repo, dest], { timeout: CLONE_TIMEOUT_MS })
    return true
  } catch {
    try {
      await execFileP(
        'git',
        ['-c', 'credential.interactive=false', 'clone', `https://github.com/${repo}.git`, dest],
        { timeout: CLONE_TIMEOUT_MS },
      )
      return true
    } catch {
      return false
    }
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
  /** Absolute path the branch was checked out in. Set (and `cloned`) when the
   *  session's repo differs from the current checkout, so we cloned it. */
  clonedPath?: string
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
  // Same for repo: it reaches `gh repo clone <repo> <dest>` and the derived
  // dest basename. Require the GitHub owner/name grammar — each segment starts
  // with an alphanumeric (so the arg can't be read as a flag), no `..` (so the
  // dest can't escape the parent dir).
  if (
    !/^[A-Za-z0-9](?:[A-Za-z0-9._-]){0,38}\/[A-Za-z0-9][A-Za-z0-9._-]{0,99}$/.test(repo) ||
    repo.includes('..')
  ) {
    return { ok: false, error: 'The session returned an unusable repository name.' }
  }

  // Where does the branch get checked out? If the current dir IS a checkout of
  // the session's repo, teleport in place (a true resume). Otherwise the
  // session is for a DIFFERENT repo (e.g. running /teleport from repo A,
  // selecting a session for repo B) — clone it as a sibling so we never mutate
  // the current session's tree, and land the branch + conversation there.
  const inRepo = await git(['rev-parse', '--is-inside-work-tree'])
  const remote = inRepo.ok ? (await git(['remote', 'get-url', 'origin'])).stdout : ''
  const sameRepo = inRepo.ok && remote.includes(repo)

  let workdir = process.cwd()
  let clonedPath: string | undefined

  if (sameRepo) {
    const dirty = (await git(['status', '--porcelain'])).stdout
    if (dirty) {
      return { ok: false, error: 'Working directory is not clean. Commit or stash your changes before teleporting.' }
    }
  } else {
    // Clone target: a sibling of the current git root (or of cwd), named for
    // the repo. Reuse an existing matching clone rather than re-cloning.
    const gitRoot = inRepo.ok ? (await git(['rev-parse', '--show-toplevel'])).stdout : ''
    const parent = path.dirname(gitRoot || process.cwd())
    const dest = path.join(parent, repo.split('/').pop() || 'repo')
    const destIsRepo = await git(['rev-parse', '--is-inside-work-tree'], dest)
    const destRemote = destIsRepo.ok ? (await git(['remote', 'get-url', 'origin'], dest)).stdout : ''
    if (!(destIsRepo.ok && destRemote.includes(repo))) {
      if (destIsRepo.ok || (await fs.stat(dest).then(() => true).catch(() => false))) {
        return { ok: false, error: `${dest} already exists and isn't ${repo}. Move it aside and retry.` }
      }
      const cloned = await cloneRepo(repo, dest)
      if (!cloned) {
        return { ok: false, error: `Could not clone ${repo}. Ensure \`gh auth login\` (or git creds) has access, then retry.` }
      }
    }
    workdir = dest
    clonedPath = dest
  }

  // No `--` before the branch: `git checkout -- <x>` is a PATHSPEC restore, not
  // a branch switch. The regex guard above (no leading `-`, no `..`) is the
  // flag-injection defense, and execFile never shell-splits the single arg.
  await git(['fetch', 'origin', branch], workdir)
  let co = await git(['checkout', branch], workdir)
  if (!co.ok) co = await git(['checkout', '-b', branch, `origin/${branch}`], workdir)
  if (!co.ok) return { ok: false, error: `Could not check out ${branch}. Fetch it manually: git fetch origin ${branch}.` }

  let resumed = false
  if (native_jsonl && native_jsonl.trim()) {
    try {
      const { getProjectDir } = await import('../../utils/sessionStoragePortable.js')
      const canonical = await fs.realpath(workdir).catch(() => workdir)
      const dir = getProjectDir(canonical)
      await fs.mkdir(dir, { recursive: true })
      await fs.writeFile(path.join(dir, `${id}.jsonl`), native_jsonl, 'utf8')
      resumed = true
    } catch {
      /* branch checked out regardless */
    }
  }
  return { ok: true, result: { repo, branch, resumed, clonedPath } }
}

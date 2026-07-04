import { execFile } from 'node:child_process'
import { promises as fs } from 'node:fs'
import path from 'node:path'
import { promisify } from 'node:util'

import type { Command } from '../../commands.js'
import type { LocalCommandCall } from '../../types/command.js'
import { readKeysEnvValue } from '../../utils/jarvisKeysEnv.js'

// `/teleport [id]` (alias `/tp`) — pull a cloud /code session to this machine
// from inside a session (the in-REPL counterpart of the `jarvis teleport`
// shell subcommand; self-hosted equivalent of claude.ai's /teleport). Slash
// commands run in the live REPL, so this NEVER process.exit()s or prompts on
// stdin — it returns text. Deliberately self-contained (its own fetch + git,
// mirroring the subcommand) rather than importing the subcommand's helpers,
// which exit the process on error.

const execFileP = promisify(execFile)
const TIMEOUT_MS = 20_000

function auth(): { base: string; token: string } | { error: string } {
  const base =
    process.env.JARVIS_BRIDGE_BASE_URL ||
    readKeysEnvValue('JARVIS_BRIDGE_BASE_URL') ||
    process.env.JARVIS_SERVER_URL ||
    readKeysEnvValue('JARVIS_SERVER_URL')
  const token =
    process.env.JARVIS_BRIDGE_TOKEN || readKeysEnvValue('JARVIS_BRIDGE_TOKEN')
  if (!base || !token) {
    return { error: 'Not linked to a JARVIS server. Run `jarvis auth login` first.' }
  }
  return { base: base.replace(/\/+$/, ''), token }
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

type SessionRow = { session_id: string; title: string; repo: string; updated_at: number | null }

function age(ts: number | null): string {
  if (!ts) return ''
  const m = Math.max(0, Math.round((Date.now() - ts) / 60_000))
  if (m < 60) return `${m}m`
  const h = Math.round(m / 60)
  return h < 48 ? `${h}h` : `${Math.round(h / 24)}d`
}

async function listSessions(base: string, token: string): Promise<string> {
  const res = await getJson<{ sessions?: SessionRow[] }>(`${base}/api/bridge/v1/cli/sessions`, token)
  if (!res.ok) return res.error
  const rows = res.data.sessions ?? []
  if (rows.length === 0) {
    return 'No cloud sessions yet. Start one from a shell: jarvis cloud "fix the failing test"'
  }
  const lines = rows.map(
    (s) => `  ${s.session_id}  ${age(s.updated_at).padEnd(4)} ${s.repo.padEnd(24)} ${s.title.slice(0, 48)}`,
  )
  return [
    'Cloud sessions — teleport one here with `/teleport <id>`:',
    ...lines,
  ].join('\n')
}

async function pull(base: string, token: string, id: string): Promise<string> {
  const info = await getJson<{ repo?: string; branch?: string; native_jsonl?: string }>(
    `${base}/api/bridge/v1/sessions/${encodeURIComponent(id)}/teleport`,
    token,
  )
  if (!info.ok) return info.error
  const { repo, branch, native_jsonl } = info.data
  if (!repo || !branch) {
    return 'That session has no pushed branch yet — open a PR (or push) in the session first.'
  }
  // Defense-in-depth: branch reaches `git` argv. A name starting with `-`
  // (or containing `..`) could smuggle a flag / traverse — reject it even
  // though it comes from our own server.
  if (!/^[A-Za-z0-9_./-]+$/.test(branch) || branch.startsWith('-') || branch.includes('..')) {
    return 'The session returned an unusable branch name.'
  }

  // Only teleport when we're already in a matching checkout — a slash command
  // shouldn't clone into or mutate an unrelated repo under the running session.
  const inRepo = await git(['rev-parse', '--is-inside-work-tree'])
  if (!inRepo.ok) {
    return `Run this from a checkout of ${repo}. From a shell you can also clone-and-teleport: jarvis teleport ${id}`
  }
  const remote = (await git(['remote', 'get-url', 'origin'])).stdout
  if (!remote.includes(repo)) {
    return `You're in a different repository. cd into a ${repo} checkout and retry (or: jarvis teleport ${id}).`
  }
  const dirty = (await git(['status', '--porcelain'])).stdout
  if (dirty) {
    return 'Working directory is not clean. Commit or stash your changes before teleporting.'
  }

  const cwd = process.cwd()
  await git(['fetch', 'origin', '--', branch], cwd)
  let co = await git(['checkout', '--', branch], cwd)
  // No `--` before a new-branch name in `checkout -b`; the regex above is the guard.
  if (!co.ok) co = await git(['checkout', '-b', branch, `origin/${branch}`], cwd)
  if (!co.ok) return `Could not check out ${branch}. Fetch it manually: git fetch origin ${branch}.`

  // Drop the native session transcript so `jarvis --resume <id>` continues the
  // exact conversation. We can't hot-swap the running session, so we point the
  // user at the restart. Best-effort — the branch is checked out regardless.
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
      /* branch is still checked out; just no conversation restore */
    }
  }

  return [
    `✓ Teleported session ${id}`,
    `  repo:   ${repo}`,
    `  branch: ${branch} (checked out)`,
    resumed
      ? `  conversation restored — continue it with:  jarvis --resume ${id}`
      : `  (no conversation transcript available — the container may be gone)`,
  ].join('\n')
}

const call: LocalCommandCall = async (args) => {
  const a = auth()
  if ('error' in a) return { type: 'text', value: a.error }
  const id = args.trim().split(/\s+/)[0] ?? ''
  const value = id
    ? await pull(a.base, a.token, id)
    : await listSessions(a.base, a.token)
  return { type: 'text', value }
}

const teleport = {
  type: 'local',
  name: 'teleport',
  aliases: ['tp'],
  description: 'Pull a cloud /code session to this machine (list, or /teleport <id>)',
  isEnabled: () => true,
  isHidden: false,
  supportsNonInteractive: true,
  load: () => Promise.resolve({ call }),
} satisfies Command

export default teleport

/* eslint-disable custom-rules/no-process-exit -- CLI subcommand handler intentionally exits */

/**
 * `jarvis teleport <sessionId>` — pull a cloud /code session down to this
 * machine (the inverse of --remote). The upstream Claude Code teleport command
 * is a disabled stub (commands/teleport/index.js) wired for claude.ai cloud
 * sessions; this is the self-hosted equivalent for JARVIS /code sessions.
 *
 * Fetches the session's repo, the branch its work lives on, and a transcript
 * from the JARVIS web app (GET /api/bridge/v1/sessions/{id}/teleport, authed
 * with JARVIS_BRIDGE_TOKEN), checks the branch out locally, and saves the
 * transcript so you can continue with `jarvis`. Additive + self-contained: it
 * shells out to git and never touches the main session loop.
 */

import { execFile } from 'node:child_process'
import { promises as fs } from 'node:fs'
import path from 'node:path'
import { promisify } from 'node:util'

import { readKeysEnvValue } from '../../utils/jarvisKeysEnv.js'

const execFileP = promisify(execFile)
const TIMEOUT_MS = 20_000

function fail(message: string): never {
  process.stderr.write(message.endsWith('\n') ? message : message + '\n')
  process.exit(1)
}

async function git(args: string[], cwd?: string): Promise<{ stdout: string; ok: boolean }> {
  try {
    const { stdout } = await execFileP('git', args, { cwd, timeout: TIMEOUT_MS })
    return { stdout: String(stdout).trim(), ok: true }
  } catch {
    return { stdout: '', ok: false }
  }
}

export async function jarvisTeleport(sessionId: string): Promise<void> {
  const base =
    readKeysEnvValue('JARVIS_BRIDGE_BASE_URL') || process.env.JARVIS_BRIDGE_BASE_URL
  const token =
    readKeysEnvValue('JARVIS_BRIDGE_TOKEN') || process.env.JARVIS_BRIDGE_TOKEN
  if (!base || !token) {
    fail('Not linked to a JARVIS server. Run `jarvis auth login` first.')
  }

  let info: { repo?: string; branch?: string; transcript?: string }
  try {
    const r = await fetch(
      `${base.replace(/\/+$/, '')}/api/bridge/v1/sessions/${encodeURIComponent(sessionId)}/teleport`,
      { headers: { Authorization: `Bearer ${token}` }, signal: AbortSignal.timeout(TIMEOUT_MS) },
    )
    if (!r.ok) fail(`Teleport failed: server returned ${r.status}.`)
    info = (await r.json()) as typeof info
  } catch (e) {
    fail(`Teleport failed: ${String(e)}`)
  }

  const { repo, branch, transcript } = info
  if (!repo || !branch) {
    fail('That session has no pushed branch yet — open a PR (or push) in the session first.')
  }

  // Locate the repo: reuse the current checkout if it matches, else clone.
  let cwd = process.cwd()
  const inRepo = await git(['rev-parse', '--is-inside-work-tree'])
  if (inRepo.ok) {
    const remote = (await git(['remote', 'get-url', 'origin'])).stdout
    if (!remote.includes(repo)) {
      fail(`You're inside a different repository. cd into a ${repo} checkout (or an empty directory to clone into) and retry.`)
    }
  } else {
    const dir = path.join(process.cwd(), repo.split('/').pop() || 'repo')
    process.stdout.write(`Cloning ${repo} into ${dir} …\n`)
    const cloned = await git(['clone', `https://github.com/${repo}.git`, dir])
    if (!cloned.ok) fail(`Clone failed. Clone ${repo} manually, then run teleport from inside it.`)
    cwd = dir
  }

  process.stdout.write(`Fetching branch ${branch} …\n`)
  await git(['fetch', 'origin', branch], cwd)
  let co = await git(['checkout', branch], cwd)
  if (!co.ok) co = await git(['checkout', '-b', branch, `origin/${branch}`], cwd)
  if (!co.ok) fail(`Could not check out ${branch}. Fetch it manually: git fetch origin ${branch}.`)

  let transcriptPath = ''
  if (transcript && transcript.trim()) {
    const jdir = path.join(cwd, '.jarvis')
    await fs.mkdir(jdir, { recursive: true }).catch(() => {})
    transcriptPath = path.join(jdir, `teleport-${sessionId}.md`)
    await fs.writeFile(transcriptPath, transcript, 'utf8').catch(() => {})
  }

  const rel = path.relative(process.cwd(), cwd)
  process.stdout.write(
    `\n✓ Teleported session ${sessionId}\n` +
      `  repo:   ${repo}\n` +
      `  branch: ${branch} (checked out${rel ? ` in ${rel}` : ''})\n` +
      (transcriptPath ? `  transcript: ${transcriptPath}\n` : '') +
      `\nContinue here: ${rel ? `cd ${rel} && ` : ''}jarvis\n`,
  )
  process.exit(0)
}

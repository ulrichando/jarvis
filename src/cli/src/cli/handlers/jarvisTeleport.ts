/* eslint-disable custom-rules/no-process-exit -- CLI subcommand handler intentionally exits */

/**
 * `jarvis teleport [sessionId]` — pull a cloud /code session down to this
 * machine (the inverse of `jarvis cloud`). The upstream Claude Code teleport
 * command is a disabled stub (commands/teleport/index.js) wired for claude.ai
 * cloud sessions; this is the self-hosted equivalent for JARVIS /code sessions.
 *
 * With no sessionId it lists your recent cloud sessions and prompts for a
 * pick (claude.ai's `--teleport` picker). Then it fetches the session's repo,
 * the branch its work lives on, and a transcript from the JARVIS web app
 * (GET /api/bridge/v1/sessions/{id}/teleport, authed with the bridge token
 * from `jarvis auth login`), refuses to touch a dirty working tree (commit or
 * stash first — same rule as upstream), checks the branch out, and saves the
 * transcript so you can continue with `jarvis`. Additive + self-contained: it
 * shells out to git and never touches the main session loop.
 */

import { execFile } from 'node:child_process'
import { promises as fs } from 'node:fs'
import path from 'node:path'
import readline from 'node:readline'
import { promisify } from 'node:util'

import {
  bridgeFail,
  bridgeFetch,
  listCloudSessions,
  printSessionTable,
} from './bridgeClient.js'
// Import-light on purpose (fs/path/env only) — safe for a fast-path
// subcommand; pulling app-graph modules here risks the boot deadlock class.
import { getProjectDir } from '../../utils/sessionStoragePortable.js'

const execFileP = promisify(execFile)
const TIMEOUT_MS = 20_000

async function git(args: string[], cwd?: string): Promise<{ stdout: string; ok: boolean }> {
  try {
    const { stdout } = await execFileP('git', args, { cwd, timeout: TIMEOUT_MS })
    return { stdout: String(stdout).trim(), ok: true }
  } catch {
    return { stdout: '', ok: false }
  }
}

async function pickSession(): Promise<string> {
  const rows = await listCloudSessions()
  if (rows.length === 0) {
    bridgeFail('No cloud sessions to teleport. Start one: jarvis cloud "fix the failing test"')
  }
  printSessionTable(rows)
  const rl = readline.createInterface({ input: process.stdin, output: process.stdout })
  const answer = await new Promise<string>((resolve) => {
    rl.question(`\nTeleport which session? [1-${rows.length}] `, resolve)
  })
  rl.close()
  const idx = Number(answer.trim())
  if (!Number.isInteger(idx) || idx < 1 || idx > rows.length) {
    bridgeFail('No session picked.')
  }
  return rows[idx - 1]!.session_id
}

export async function jarvisTeleport(sessionId?: string): Promise<void> {
  const id = sessionId?.trim() || (await pickSession())

  const info = await bridgeFetch<{
    repo?: string
    branch?: string
    transcript?: string
    native_jsonl?: string
  }>(`/api/bridge/v1/sessions/${encodeURIComponent(id)}/teleport`)
  const { repo, branch, transcript } = info
  if (!repo || !branch) {
    bridgeFail('That session has no pushed branch yet — open a PR (or push) in the session first.')
  }
  // Defense-in-depth: branch reaches `git` argv — reject a name that could
  // smuggle a flag (`-…`) or traverse (`..`), even from our own server.
  if (!/^[A-Za-z0-9_./-]+$/.test(branch!) || branch!.startsWith('-') || branch!.includes('..')) {
    bridgeFail('The session returned an unusable branch name.')
  }

  // Locate the repo: reuse the current checkout if it matches, else clone.
  let cwd = process.cwd()
  const inRepo = await git(['rev-parse', '--is-inside-work-tree'])
  if (inRepo.ok) {
    const remote = (await git(['remote', 'get-url', 'origin'])).stdout
    if (!remote.includes(repo)) {
      bridgeFail(
        `You're inside a different repository. cd into a ${repo} checkout (or an empty directory to clone into) and retry.`,
      )
    }
    // Upstream teleport rule: never checkout over uncommitted work.
    const dirty = (await git(['status', '--porcelain'])).stdout
    if (dirty) {
      bridgeFail(
        'Git working directory is not clean. Commit or stash your changes before teleporting.',
      )
    }
  } else {
    const dir = path.join(process.cwd(), repo.split('/').pop() || 'repo')
    process.stdout.write(`Cloning ${repo} into ${dir} …\n`)
    const cloned = await git(['clone', `https://github.com/${repo}.git`, dir])
    if (!cloned.ok) bridgeFail(`Clone failed. Clone ${repo} manually, then run teleport from inside it.`)
    cwd = dir
  }

  process.stdout.write(`Fetching branch ${branch} …\n`)
  await git(['fetch', 'origin', '--', branch], cwd)
  let co = await git(['checkout', '--', branch], cwd)
  if (!co.ok) co = await git(['checkout', '-b', branch, `origin/${branch}`], cwd)
  if (!co.ok) bridgeFail(`Could not check out ${branch}. Fetch it manually: git fetch origin ${branch}.`)

  // Full-fidelity resume (claude.ai --teleport behavior): the server exports
  // the CLI's own session jsonl from the container. Drop it into THIS
  // checkout's project dir so `jarvis --resume <id>` continues the exact
  // conversation. Falls back to the markdown transcript when the container
  // (and its file) are already gone.
  let resumed = false
  if (info.native_jsonl && info.native_jsonl.trim()) {
    try {
      const canonicalCwd = await fs.realpath(cwd).catch(() => cwd)
      const projDir = getProjectDir(canonicalCwd)
      await fs.mkdir(projDir, { recursive: true })
      await fs.writeFile(path.join(projDir, `${id}.jsonl`), info.native_jsonl, 'utf8')
      resumed = true
    } catch {
      /* fall back to the markdown transcript below */
    }
  }
  let transcriptPath = ''
  if (!resumed && transcript && transcript.trim()) {
    const jdir = path.join(cwd, '.jarvis')
    await fs.mkdir(jdir, { recursive: true }).catch(() => {})
    transcriptPath = path.join(jdir, `teleport-${id}.md`)
    await fs.writeFile(transcriptPath, transcript, 'utf8').catch(() => {})
  }

  const rel = path.relative(process.cwd(), cwd)
  process.stdout.write(
    `\n✓ Teleported session ${id}\n` +
      `  repo:   ${repo}\n` +
      `  branch: ${branch} (checked out${rel ? ` in ${rel}` : ''})\n` +
      (resumed
        ? `  conversation: restored (native session file)\n`
        : transcriptPath
          ? `  transcript: ${transcriptPath} (container gone — summary only)\n`
          : '') +
      `\nContinue here: ${rel ? `cd ${rel} && ` : ''}${resumed ? `jarvis --resume ${id}` : 'jarvis'}\n`,
  )
  process.exit(0)
}

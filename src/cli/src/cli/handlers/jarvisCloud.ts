/* eslint-disable custom-rules/no-process-exit -- CLI subcommand handler intentionally exits */

/**
 * `jarvis cloud "task"` — start a /code cloud session on the JARVIS server
 * from the terminal (self-hosted equivalent of claude.ai's `claude --cloud`).
 *
 * Detects the GitHub repo from the current checkout's `origin` remote, POSTs
 * the task to the server (which get-or-creates the per-repo container target
 * and launches the session), and prints the session URL. The task runs in the
 * cloud while you keep working locally; pull the result down later with
 * `jarvis teleport`.
 *
 * `jarvis cloud` with no prompt lists your recent cloud sessions instead
 * (same data the teleport picker shows).
 */

import { execFile } from 'node:child_process'
import { promisify } from 'node:util'

import {
  bridgeFail,
  bridgeFetch,
  listCloudSessions,
  printSessionTable,
} from './bridgeClient.js'

const execFileP = promisify(execFile)
const GIT_TIMEOUT_MS = 10_000

async function git(args: string[]): Promise<string> {
  try {
    const { stdout } = await execFileP('git', args, { timeout: GIT_TIMEOUT_MS })
    return String(stdout).trim()
  } catch {
    return ''
  }
}

/** owner/name from any GitHub remote shape (https, ssh, host alias). */
export function parseGithubRepo(remote: string): string | null {
  const m = /github\.com[:/]+([^/\s]+)\/([^/\s]+?)(?:\.git)?$/.exec(remote.trim())
  return m ? `${m[1]}/${m[2]}` : null
}

export async function jarvisCloud(
  promptWords: string[],
  opts: { model?: string; mode?: string } = {},
): Promise<void> {
  const prompt = promptWords.join(' ').trim()

  // No prompt → list recent cloud sessions (what you'd teleport into).
  if (!prompt) {
    const rows = await listCloudSessions()
    if (rows.length === 0) {
      process.stdout.write(
        'No cloud sessions yet. Start one: jarvis cloud "fix the failing test"\n',
      )
      return
    }
    printSessionTable(rows)
    process.stdout.write(
      '\nOpen one in the terminal: jarvis teleport <session-id>\n',
    )
    return
  }

  const remote = await git(['remote', 'get-url', 'origin'])
  const repo = remote ? parseGithubRepo(remote) : null
  if (!repo) {
    bridgeFail(
      'Not inside a GitHub-remote repository. Run from a checkout whose `origin` points at github.com.',
    )
  }

  // The cloud session clones from GitHub, not from this machine — unpushed
  // local commits won't be visible to it. Warn, don't block.
  const unpushed = await git(['rev-list', '--count', '@{u}..HEAD'])
  if (unpushed && Number(unpushed) > 0) {
    process.stderr.write(
      `⚠ ${unpushed} unpushed commit(s) on this branch — the cloud session clones from GitHub and won't see them. Push first if the task needs them.\n`,
    )
  }

  const j = await bridgeFetch<{ session_id?: string; url?: string }>(
    '/api/bridge/v1/cli/sessions',
    {
      method: 'POST',
      body: {
        repo,
        prompt,
        ...(opts.model ? { model: opts.model } : {}),
        ...(opts.mode ? { permission_mode: opts.mode } : {}),
      },
    },
  )
  if (!j.session_id) bridgeFail('Server did not return a session id.')
  process.stdout.write(
    `✓ Cloud session started on ${repo}\n` +
      `  watch:    ${j.url ?? j.session_id}\n` +
      `  teleport: jarvis teleport ${j.session_id}\n`,
  )
  process.exit(0)
}

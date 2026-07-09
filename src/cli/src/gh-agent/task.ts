// src/cli/src/gh-agent/task.ts
import { execFileNoThrow, execFileNoThrowWithCwd } from '../utils/execFileNoThrow.js'
import { mkdtempSync, rmSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { type GhAgentConfig, isAllowedAuthor } from './config.js'
import { type Mention, postComment, SELF_MARKER, triggerRegex } from './gh.js'

export type Runner = (args: string[]) => Promise<{ stdout: string; stderr: string; code: number }>
export type TaskDeps = {
  gh: Runner
  git: Runner            // caller passes full argv incl. `-C <dir>`
  exec: (file: string, args: string[], cwd: string, timeoutSec: number) => Promise<{ stdout: string; stderr: string; code: number }>
  makeTempDir: () => string
  cleanup: (dir: string) => void
}
export type TaskResult = { ok: boolean; prUrl?: string; noChanges?: boolean; error?: string }

// Path to the jarvis launcher (this same CLI), used to run `jarvis -p` headless
// inside the clone. Repo-root bin/jarvis; resolved relative to this file's
// location (src/cli/src/gh-agent → repo root is four up).
import { fileURLToPath } from 'node:url'
import { dirname, resolve } from 'node:path'
function jarvisBin(): string {
  const here = dirname(fileURLToPath(import.meta.url))
  return resolve(here, '../../../../bin/jarvis')
}

export function realDeps(): TaskDeps {
  const run = (file: string): Runner => async (args) => {
    const r = await execFileNoThrow(file, args)
    return { stdout: r.stdout, stderr: r.stderr, code: r.code }
  }
  return {
    gh: run('gh'),
    git: run('git'),
    exec: async (file, args, cwd, timeoutSec) => {
      // argv invocation (NO shell) — the jarvis prompt carries untrusted GitHub
      // comment text. A shell string here let `$(...)`/backticks in a comment
      // run as commands (JSON.stringify escapes quotes, not command
      // substitution, which stays live inside bash double-quotes). execa runs
      // the file directly with cwd, so the prompt can never reach a shell.
      const r = await execFileNoThrowWithCwd(file, args, { cwd, timeout: timeoutSec * 1000 })
      // Surface the wrapper's failure reason (execa shortMessage — e.g.
      // "Command timed out after Nms") AHEAD of raw stderr: the caller slices
      // stderr to 200 chars, and a bare exit code tells the operator nothing.
      const stderr = r.error ? [r.error, r.stderr].filter(Boolean).join('\n') : r.stderr
      return { stdout: r.stdout, stderr, code: r.code }
    },
    makeTempDir: () => mkdtempSync(join(tmpdir(), 'jarvis-gh-')),
    cleanup: (dir) => { try { rmSync(dir, { recursive: true, force: true }) } catch { /* best effort */ } },
  }
}

export function taskText(body: string, trigger: string): string {
  // Slice AFTER the trigger using the SAME word-boundary/case-insensitive match
  // that listMentions used to accept this comment. indexOf was case-sensitive
  // and matched embedded substrings, so '@Jarvis fix' or 'email@jarvis.com fix'
  // either left the '@jarvis' token in the prompt or sliced at the wrong offset.
  const m = body.match(triggerRegex(trigger))
  const raw = (m ? body.slice(m.index! + m[0].length) : body).trim()
  // Strip NUL + non-printable control chars (keep \n \t). Defense in depth now
  // that the prompt goes via argv (a NUL would truncate the argv at the execve
  // boundary), and keeps control chars out of the commit-message / PR-body uses.
  return Array.from(raw)
    .filter((ch) => { const c = ch.charCodeAt(0); return c > 0x1f ? c !== 0x7f : c === 0x0a || c === 0x09 })
    .join('')
}

export async function executeTask(
  repo: string,
  m: Mention,
  cfg: GhAgentConfig,
  deps: TaskDeps,
): Promise<TaskResult> {
  // Bare "@jarvis" with no task text: nothing to do — bail before any clone
  // or subprocess. A full bypass-mode agent run on an empty prompt is all
  // cost for zero signal.
  const task = taskText(m.body, cfg.trigger)
  if (task === '') return { ok: true, noChanges: true }
  const dir = deps.makeTempDir()
  const gitc = (...a: string[]) => deps.git(['-C', dir, ...a])
  try {
    // 1. issue vs PR — a failed probe (rate limit, network) must be a hard
    // stop: silently treating a PR as an issue would open a DUPLICATE PR.
    const info = await deps.gh(['api', `repos/${repo}/issues/${m.issueNumber}`, '--jq', '.pull_request'])
    if (info.code !== 0) return { ok: false, error: `issue/PR probe failed: ${info.stderr}` }
    const isPR = info.stdout.trim() !== '' && info.stdout.trim() !== 'null'

    // 2. clone (shallow) into the temp dir
    const clone = await deps.gh(['repo', 'clone', repo, dir, '--', '--depth', '1'])
    if (clone.code !== 0) return { ok: false, error: `clone failed: ${clone.stderr}` }

    // 3. position on the right branch
    let branch: string
    let defaultBranch = 'master'
    if (isPR) {
      // SECURITY: the allowlist gates the COMMENTER; this gates whose CODE
      // runs. `jarvis -p` executes in bypass mode inside the checked-out head,
      // so a fork PR (or a same-repo PR from a non-allowlisted author) would
      // hand an untrusted tree a full agent — refuse those outright.
      const meta = await deps.gh(['pr', 'view', String(m.issueNumber), '--repo', repo, '--json', 'headRefName,isCrossRepository,author'])
      if (meta.code !== 0) return { ok: false, error: `pr view failed: ${meta.stderr}` }
      let pv: { headRefName?: string; isCrossRepository?: boolean; author?: { login?: string } }
      try { pv = JSON.parse(meta.stdout) } catch { return { ok: false, error: 'pr view: unparseable json' } }
      const prAuthor = pv.author?.login ?? ''
      if (pv.isCrossRepository || !isAllowedAuthor(cfg, prAuthor)) {
        await postComment(repo, m.issueNumber, `Declining automated changes: this PR's head is untrusted (fork or non-allowlisted author @${prAuthor}). Push from an allowlisted same-repo branch to use @jarvis.\n\n${SELF_MARKER}`, deps.gh)
        return { ok: true, noChanges: true }
      }
      branch = pv.headRefName ?? ''
      if (!branch) return { ok: false, error: 'pr view: empty headRefName' }
      // clone is shallow on the default branch; fetch + checkout the PR head
      await gitc('fetch', '--depth', '1', 'origin', branch)
      const co = await gitc('checkout', branch)
      if (co.code !== 0) return { ok: false, error: `checkout ${branch} failed: ${co.stderr}` }
    } else {
      const db = await deps.gh(['repo', 'view', repo, '--json', 'defaultBranchRef', '--jq', '.defaultBranchRef.name'])
      if (db.code !== 0) return { ok: false, error: `default-branch lookup failed: ${db.stderr}` }
      defaultBranch = db.stdout.trim() || 'master'
      branch = `jarvis/gh-${m.issueNumber}-${Date.now().toString(36)}`
      const co = await gitc('checkout', '-b', branch)
      if (co.code !== 0) return { ok: false, error: `branch create failed: ${co.stderr}` }
    }

    // 4. run jarvis -p headless (edits only; git is done here deterministically)
    const prompt = `You are handling a GitHub request. Make ONLY the code changes for this task; do NOT run git commit, git push, or gh. Task: ${task}`
    const ex = await deps.exec(jarvisBin(), ['-p', prompt], dir, cfg.executionTimeoutSec)
    if (ex.code !== 0) return { ok: false, error: `jarvis -p exited ${ex.code}: ${ex.stderr.slice(0, 200)}` }

    // 5. stage + detect changes
    const add = await gitc('add', '-A')
    if (add.code !== 0) return { ok: false, error: `git add failed: ${add.stderr}` }
    // `diff --cached --quiet`: 0 = no changes, 1 = changes, >1 = git itself
    // failed — that last one must NOT read as "changes present".
    const diff = await gitc('diff', '--cached', '--quiet')
    if (diff.code === 0) {
      await postComment(repo, m.issueNumber,
        `No changes were needed for @${m.author}'s request: "${task}"\n\n${SELF_MARKER}`, deps.gh)
      return { ok: true, noChanges: true }
    }
    if (diff.code !== 1) return { ok: false, error: `git diff failed: ${diff.stderr}` }

    // 6. commit + push. Subject is first-line-only: taskText preserves \n, and
    // a newline inside `-m`/`--title` breaks the subject (and 422s pr create).
    const subject = task.split('\n')[0].slice(0, 60)
    const c = await gitc('-c', 'user.name=jarvis-gh-agent', '-c', 'user.email=jarvis@0wlan.com',
      'commit', '-m', `jarvis: ${subject}`)
    if (c.code !== 0) return { ok: false, error: `commit failed: ${c.stderr}` }
    const push = await gitc('push', '-u', 'origin', isPR ? `HEAD:${branch}` : branch)
    if (push.code !== 0) return { ok: false, error: `push failed: ${push.stderr}` }

    // 7. issue → open PR; PR → comment
    if (isPR) {
      await deps.gh(['pr', 'comment', String(m.issueNumber), '--repo', repo, '--body',
        `Pushed changes for @${m.author}'s request to \`${branch}\`.\n\n${SELF_MARKER}`])
      return { ok: true }
    }
    const pr = await deps.gh(['pr', 'create', '--repo', repo, '--base', defaultBranch, '--head', branch,
      '--title', `jarvis: ${subject}`,
      '--body', `Closes #${m.issueNumber}. Requested by @${m.author}: "${task}"\n\n_Automated by jarvis gh-agent. Review before merge._\n\n${SELF_MARKER}`])
    if (pr.code !== 0) return { ok: false, error: `pr create failed: ${pr.stderr}` }
    return { ok: true, prUrl: pr.stdout.trim() }
  } finally {
    deps.cleanup(dir)
  }
}

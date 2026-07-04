// src/gh-app/containerEntry.ts — the IN-CONTAINER main for a sandboxed job.
//
// Runs inside the throwaway jarvis-gh-app container. Reads GH_TOKEN +
// GH_APP_JOB from env, makes git/gh token-authed, then hands the job to the
// shared engine — `gh-agent::executeTask` — which owns the whole
// clone → jarvis -p → commit → push → PR flow INCLUDING the
// untrusted-PR-head refusal (fork / non-allowlisted PR author). This file
// adds the two App-specific pieces around it:
//   - git auth: a global `url.….insteadOf` rewrite to
//     https://x-access-token:<token>@github.com/ so the engine's plain
//     `git push` is authed (gh itself reads GH_TOKEN). Written to the
//     container's own gitconfig — dies with the container.
//   - .claude neutralize: same guard as gh-action — the target repo's
//     .claude/ is stashed OUTSIDE the tree while `jarvis -p` runs so its
//     hooks/settings can't hijack the agent, and restored before `git add`
//     so the move never lands in the PR.
import { existsSync, mkdtempSync, renameSync, rmSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { executeTask, realDeps, type TaskDeps } from '../cli/src/gh-agent/task.js'
import type { GhAgentConfig } from '../cli/src/gh-agent/config.js'
import type { Mention } from '../cli/src/gh-agent/gh.js'
import type { Job } from './jobs.js'

/**
 * Rebuild a Mention the engine's taskText() reduces back to EXACTLY job.task:
 * the trigger goes at position 0, so indexOf finds it there even when the
 * task text itself mentions the trigger later.
 */
export function mentionFromJob(job: Job, trigger: string): Mention {
  const now = new Date().toISOString()
  return {
    id: job.id,
    body: `${trigger} ${job.task}`,
    author: 'jarvis-gh-app', // author gate already ran at the webhook; engine only echoes this
    createdAt: now,
    updatedAt: now,
    issueNumber: job.issueNumber,
    url: `https://github.com/${job.repo}/issues/${job.issueNumber}`,
  }
}

export function cfgFromEnv(env: Record<string, string | undefined>): GhAgentConfig {
  const allowlist = (env.GH_APP_ALLOWLIST ?? 'ulrichando').split(',').map((s) => s.trim()).filter(Boolean)
  const t = Number(env.GH_APP_TIMEOUT_SEC)
  return {
    repos: [],
    allowlist,
    trigger: env.GH_APP_TRIGGER ?? '@jarvis',
    pollSeconds: 0, // unused — no poll loop in the App path
    executionTimeoutSec: Number.isFinite(t) && t > 0 ? t : 900,
  }
}

/** Pure argv (no shell — the token must never pass through a shell parse). */
export function gitAuthConfigArgs(token: string): string[] {
  return ['config', '--global', `url.https://x-access-token:${token}@github.com/.insteadOf`, 'https://github.com/']
}

/** Same stash-outside-the-tree pattern as gh-action's realActionDeps. */
export function neutralizeClaude(dir: string): () => void {
  const src = join(dir, '.claude')
  if (!existsSync(src)) return () => {}
  let stash: string
  try { stash = join(mkdtempSync(join(tmpdir(), 'jarvis-claude-')), '.claude'); renameSync(src, stash) } catch { return () => {} }
  return () => { try { if (existsSync(src)) rmSync(src, { recursive: true, force: true }); renameSync(stash, src) } catch { /* best effort */ } }
}

/** Wrap the engine deps so `jarvis -p` runs with the clone's .claude stashed. */
export function buildEntryDeps(base: TaskDeps): TaskDeps {
  return {
    ...base,
    exec: async (file, args, cwd, timeoutSec) => {
      const restore = neutralizeClaude(cwd)
      try {
        return await base.exec(file, args, cwd, timeoutSec)
      } finally {
        restore() // BEFORE the engine's git add — the stash never lands in the PR
      }
    },
  }
}

async function main(): Promise<number> {
  const token = process.env.GH_TOKEN ?? ''
  const rawJob = process.env.GH_APP_JOB ?? ''
  if (!token || !rawJob) {
    console.error('containerEntry: GH_TOKEN and GH_APP_JOB are required')
    return 2
  }
  let job: Job
  try { job = JSON.parse(rawJob) } catch { console.error('containerEntry: GH_APP_JOB is not valid JSON'); return 2 }

  const cfg = cfgFromEnv(process.env)
  const deps = buildEntryDeps(realDeps())

  // Auth plain `git` for github.com pushes (gh reads GH_TOKEN on its own).
  const auth = await deps.git(gitAuthConfigArgs(token))
  if (auth.code !== 0) { console.error('containerEntry: git auth config failed'); return 1 }

  const result = await executeTask(job.repo, mentionFromJob(job, cfg.trigger), cfg, deps)
  // Engine errors may embed subprocess stderr → redact the token before stdout.
  console.log(JSON.stringify(result).split(token).join('***'))
  return result.ok ? 0 : 1
}

if (import.meta.main) {
  main().then((code) => process.exit(code), (e) => { console.error(String(e?.message ?? e)); process.exit(1) })
}

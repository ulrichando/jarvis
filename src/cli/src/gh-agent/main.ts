// src/cli/src/gh-agent/main.ts
import { type GhAgentConfig, isAllowedAuthor, loadGhAgentConfig } from './config.js'
import { addHandledIds, advanceCursor, readCursor, readHandledIds } from './cursor.js'
import { type GhRunner, listMentions, type Mention } from './gh.js'
import { taskText } from './task.js'

export type RunOnceArgs = { repo?: string; dryRun: boolean }
export type RunOnceDeps = {
  run?: GhRunner
  cfg?: GhAgentConfig
  cursorDir?: string
  execute?: (repo: string, m: Mention, cfg: GhAgentConfig) => Promise<{ ok: boolean; prUrl?: string; noChanges?: boolean; error?: string }>
  // Injectable clock (ms) for the per-sweep wall-clock budget; tests drive it.
  now?: () => number
}

// owner/name only — anything else never reaches a gh invocation.
const REPO_RE = /^[\w.-]+\/[\w.-]+$/

function log(msg: string): void {
  process.stdout.write(`[gh-agent] ${msg}\n`)
}

function warn(msg: string): void {
  process.stderr.write(`[gh-agent] ${msg}\n`)
}

export async function runGhAgentOnce(args: RunOnceArgs, deps: RunOnceDeps = {}): Promise<void> {
  const cfg = deps.cfg ?? loadGhAgentConfig()
  // Default executor resolves lazily inside the arrow: tests inject a stub,
  // and dry-run never constructs the real exec deps at all.
  const execute = deps.execute ?? (async (repo, m, cfg) => { const { executeTask, realDeps } = await import('./task.js'); return executeTask(repo, m, cfg, realDeps()) })
  const requested = args.repo ? [args.repo] : cfg.repos
  const repos = requested.filter(r => {
    if (REPO_RE.test(r)) return true
    warn(`skipping malformed repo "${r}" (expected owner/name)`)
    return false
  })
  if (repos.length === 0) {
    log('no repos configured (set repos[] in ~/.jarvis/gh-agent.json or pass --repo owner/name)')
    return
  }
  for (const repo of repos) {
    const since = readCursor(repo, deps.cursorDir)
    const res = await listMentions(repo, cfg.trigger, since, deps.run)
    if (res === null) {
      // Fetch FAILURE (gh exit / bad JSON) — not an empty window. Say so
      // loudly and leave all state untouched so the next sweep retries.
      warn(`${repo}: poll failed (gh error) — skipping`)
      process.exitCode = 1
      continue
    }
    const { mentions, maxUpdatedAt } = res
    log(`${repo}: ${mentions.length} new mention(s) since ${since}`)
    // Oldest-first for deterministic handling order.
    const ordered = [...mentions].sort((a, b) => a.createdAt.localeCompare(b.createdAt))
    // GitHub's ?since= is INCLUSIVE (updated_at >= since): the last handled
    // mention re-enters every sweep. Comment-id dedupe is the real no-replay
    // guarantee; the cursor only narrows the fetch window.
    const handled = readHandledIds(repo, deps.cursorDir)
    const fresh = ordered.filter(m => !handled.has(m.id))
    // Oldest updated_at among mentions NOT finished this sweep (execution failed,
    // OR deferred because the wall-clock budget ran out) — the window must not
    // advance past it or the retry could never re-fetch it (?since= is inclusive).
    let oldestUnfinished: string | null = null
    const foldUnfinished = (updatedAt: string) => {
      if (oldestUnfinished === null || updatedAt < oldestUnfinished) oldestUnfinished = updatedAt
    }
    const now = deps.now ?? Date.now
    const startedAt = now()
    for (let i = 0; i < fresh.length; i++) {
      const m = fresh[i]!
      if (!isAllowedAuthor(cfg, m.author)) {
        log(`  #${m.issueNumber} ignored — @${m.author} not in allowlist`)
        // Decision is final: mark handled now so it isn't re-evaluated every
        // sweep. (Dry-run persists NOTHING — a preview must not consume.)
        if (!args.dryRun) addHandledIds(repo, [m.id], deps.cursorDir)
        continue
      }
      if (args.dryRun) {
        const task = taskText(m.body, cfg.trigger)
        log('  #'+m.issueNumber+' DRY-RUN would handle @'+m.author+': "'+task+'"')
      } else {
        // Wall-clock budget: never START an execution that couldn't finish
        // before systemd's TimeoutStartSec SIGKILLs the sweep mid-task. Defer
        // this and every remaining fresh mention to the next sweep, treating
        // them as unfinished so the cursor can't skip past them.
        const elapsedSec = (now() - startedAt) / 1000
        if (elapsedSec + cfg.executionTimeoutSec > cfg.sweepBudgetSec) {
          const deferred = fresh.slice(i)
          log(`  budget (${cfg.sweepBudgetSec}s) reached — deferring ${deferred.length} mention(s) to next sweep`)
          for (const d of deferred) foldUnfinished(d.updatedAt)
          break
        }
        const res = await execute(repo, m, cfg)
        if (res.ok) {
          log(`  #${m.issueNumber} ${res.noChanges ? 'no-changes' : (res.prUrl ? 'PR '+res.prUrl : 'pushed')} @${m.author}`)
          // Per-mention, immediately: a mid-sweep crash must not replay tasks.
          addHandledIds(repo, [m.id], deps.cursorDir)
        } else {
          log(`  #${m.issueNumber} FAILED @${m.author}: ${res.error ?? 'unknown'}`)
          // do NOT mark handled → retried next sweep; surface failure to exit.
          process.exitCode = 1
          foldUnfinished(m.updatedAt)
        }
      }
    }
    // Advance the since-window to the newest FETCHED comment (matching or
    // not) so unrelated chatter still shrinks the window — but never past an
    // unfinished task: ?since= is inclusive (updated_at >= since), so advancing
    // exactly TO the oldest unfinished mention keeps it re-fetchable next sweep.
    // advanceCursor's monotonic floor absorbs any regression. Dry-run
    // persists nothing.
    const windowTo = oldestUnfinished ?? maxUpdatedAt
    if (!args.dryRun && windowTo !== null) {
      advanceCursor(repo, windowTo, deps.cursorDir)
    }
  }
}

// src/cli/src/gh-agent/main.ts
import { type GhAgentConfig, isAllowedAuthor, loadGhAgentConfig } from './config.js'
import { addHandledIds, advanceCursor, readCursor, readHandledIds } from './cursor.js'
import { type GhRunner, listMentions, postComment, SELF_MARKER } from './gh.js'

export type RunOnceArgs = { repo?: string; dryRun: boolean }
export type RunOnceDeps = { run?: GhRunner; cfg?: GhAgentConfig; cursorDir?: string }

// owner/name only — anything else never reaches a gh invocation.
const REPO_RE = /^[\w.-]+\/[\w.-]+$/

function log(msg: string): void {
  process.stdout.write(`[gh-agent] ${msg}\n`)
}

function warn(msg: string): void {
  process.stderr.write(`[gh-agent] ${msg}\n`)
}

function taskText(body: string, trigger: string): string {
  const i = body.indexOf(trigger)
  return (i === -1 ? body : body.slice(i + trigger.length)).trim()
}

export async function runGhAgentOnce(args: RunOnceArgs, deps: RunOnceDeps = {}): Promise<void> {
  const cfg = deps.cfg ?? loadGhAgentConfig()
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
    // Oldest updated_at among mentions whose ack FAILED this sweep — the
    // window must not advance past it or the retry could never re-fetch it.
    let oldestFailed: string | null = null
    for (const m of fresh) {
      if (!isAllowedAuthor(cfg, m.author)) {
        log(`  #${m.issueNumber} ignored — @${m.author} not in allowlist`)
        // Decision is final: mark handled now so it isn't re-evaluated every
        // sweep. (Dry-run persists NOTHING — a preview must not consume.)
        if (!args.dryRun) addHandledIds(repo, [m.id], deps.cursorDir)
        continue
      }
      const task = taskText(m.body, cfg.trigger)
      if (args.dryRun) {
        log(`  #${m.issueNumber} DRY-RUN would ack @${m.author}: "${task}"`)
      } else {
        const ok = await postComment(
          repo,
          m.issueNumber,
          // SELF_MARKER lets the next sweep filter this ack out (no self-loop).
          `👀 Jarvis received this from @${m.author}: "${task}"\n\n_(P1: acknowledgement only — automated execution lands in P2.)_\n\n${SELF_MARKER}`,
          deps.run,
        )
        log(`  #${m.issueNumber} ${ok ? 'acked' : 'ACK FAILED'} @${m.author}`)
        if (ok) {
          // Per-mention, immediately: a mid-sweep crash must not replay acks.
          addHandledIds(repo, [m.id], deps.cursorDir)
        } else {
          // NOT handled → retried next sweep; surface the failure to the exit.
          process.exitCode = 1
          if (oldestFailed === null || m.updatedAt < oldestFailed) oldestFailed = m.updatedAt
        }
      }
    }
    // Advance the since-window to the newest FETCHED comment (matching or
    // not) so unrelated chatter still shrinks the window — but never past a
    // failed ack: ?since= is inclusive (updated_at >= since), so advancing
    // exactly TO the failed mention keeps it re-fetchable next sweep.
    // advanceCursor's monotonic floor absorbs any regression. Dry-run
    // persists nothing.
    const windowTo = oldestFailed ?? maxUpdatedAt
    if (!args.dryRun && windowTo !== null) {
      advanceCursor(repo, windowTo, deps.cursorDir)
    }
  }
}

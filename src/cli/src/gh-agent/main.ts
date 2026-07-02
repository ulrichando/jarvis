// src/cli/src/gh-agent/main.ts
import { type GhAgentConfig, isAllowedAuthor, loadGhAgentConfig } from './config.js'
import { addHandledIds, advanceCursor, readCursor, readHandledIds } from './cursor.js'
import { type GhRunner, listMentions, postComment, type Mention } from './gh.js'

export type RunOnceArgs = { repo?: string; dryRun: boolean }
export type RunOnceDeps = { run?: GhRunner; cfg?: GhAgentConfig; cursorDir?: string }

function log(msg: string): void {
  process.stdout.write(`[gh-agent] ${msg}\n`)
}

function taskText(body: string, trigger: string): string {
  const i = body.indexOf(trigger)
  return (i === -1 ? body : body.slice(i + trigger.length)).trim()
}

export async function runGhAgentOnce(args: RunOnceArgs, deps: RunOnceDeps = {}): Promise<void> {
  const cfg = deps.cfg ?? loadGhAgentConfig()
  const repos = args.repo ? [args.repo] : cfg.repos
  if (repos.length === 0) {
    log('no repos configured (set repos[] in ~/.jarvis/gh-agent.json or pass --repo owner/name)')
    return
  }
  for (const repo of repos) {
    const since = readCursor(repo, deps.cursorDir)
    const mentions: Mention[] = await listMentions(repo, cfg.trigger, since, deps.run)
    log(`${repo}: ${mentions.length} new mention(s) since ${since}`)
    // Oldest-first for deterministic handling order.
    const ordered = [...mentions].sort((a, b) => a.createdAt.localeCompare(b.createdAt))
    // GitHub's ?since= is INCLUSIVE (updated_at >= since): the last handled
    // mention re-enters every sweep. Comment-id dedupe is the real no-replay
    // guarantee; the cursor only narrows the fetch window.
    const handled = readHandledIds(repo, deps.cursorDir)
    const fresh = ordered.filter(m => !handled.has(m.id))
    for (const m of fresh) {
      if (!isAllowedAuthor(cfg, m.author)) {
        log(`  #${m.issueNumber} ignored — @${m.author} not in allowlist`)
        continue
      }
      const task = taskText(m.body, cfg.trigger)
      if (args.dryRun) {
        log(`  #${m.issueNumber} DRY-RUN would ack @${m.author}: "${task}"`)
      } else {
        const ok = await postComment(
          repo,
          m.issueNumber,
          `👀 Jarvis received this from @${m.author}: "${task}"\n\n_(P1: acknowledgement only — automated execution lands in P2.)_`,
          deps.run,
        )
        log(`  #${m.issueNumber} ${ok ? 'acked' : 'ACK FAILED'} @${m.author}`)
      }
    }
    if (fresh.length > 0) {
      addHandledIds(repo, fresh.map(m => m.id), deps.cursorDir)
      // Advance ONCE to the max updated_at across the FETCHED mentions, with
      // the sweep's own `since` as the floor — never regresses (advanceCursor
      // is monotonic besides).
      advanceCursor(
        repo,
        mentions.reduce((mx, m) => (m.updatedAt > mx ? m.updatedAt : mx), since),
        deps.cursorDir,
      )
    }
  }
}

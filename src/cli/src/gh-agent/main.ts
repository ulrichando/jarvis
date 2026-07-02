// src/cli/src/gh-agent/main.ts
import { type GhAgentConfig, isAllowedAuthor, loadGhAgentConfig } from './config.js'
import { advanceCursor, readCursor } from './cursor.js'
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
    // Oldest-first so the cursor advances monotonically.
    const ordered = [...mentions].sort((a, b) => a.createdAt.localeCompare(b.createdAt))
    for (const m of ordered) {
      if (!isAllowedAuthor(cfg, m.author)) {
        log(`  #${m.issueNumber} ignored — @${m.author} not in allowlist`)
        advanceCursor(repo, m.createdAt, deps.cursorDir)
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
      advanceCursor(repo, m.createdAt, deps.cursorDir)
    }
  }
}

// src/cli/src/gh-action/main.ts
import { readFileSync, existsSync, renameSync, mkdtempSync, rmSync, cpSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join, dirname } from 'node:path'
import { execFileNoThrow, execFileNoThrowWithCwd } from '../utils/execFileNoThrow.js'
import { actionCtxFromEnv, parseActionEvent, isAuthorized, type ActionEvent } from './event.js'
import { SELF_MARKER } from '../gh-agent/gh.js'

export type ActionDeps = {
  readEvent: () => ActionEvent | null
  allowlist: string[]
  workspace: string
  jarvisBin?: string
  timeoutSec?: number
  neutralizeClaude: (ws: string) => () => void   // returns a restore fn (call before `git add`)
  exec: (prompt: string) => Promise<{ code: number; stdout: string; stderr: string }>
  git: (args: string[]) => Promise<{ code: number; stdout: string; stderr: string }>
  gh: (args: string[]) => Promise<{ code: number; stdout: string; stderr: string }>
  prIsUntrusted?: (repo: string, n: number, allowlist: string[]) => Promise<boolean>
  log: (m: string) => void
  dryRun: boolean
}

export type ActionResult = { ok?: boolean; skipped?: boolean; prUrl?: string; error?: string; noChanges?: boolean }

export async function runGhActionOnce(d: ActionDeps): Promise<ActionResult> {
  const ev = d.readEvent()
  if (!ev) { d.log('gh-action: no matching @jarvis event'); return { skipped: true } }
  if (!isAuthorized(ev.association, d.allowlist, ev.author)) {
    d.log(`gh-action: @${ev.author} (${ev.association}) not authorized — skipping`); return { skipped: true }
  }
  if (ev.isPR && d.prIsUntrusted && await d.prIsUntrusted(ev.repo, ev.issueNumber, d.allowlist)) {
    d.log('gh-action: untrusted PR head — refusing'); return { skipped: true }
  }
  if (d.dryRun) { d.log(`gh-action DRY-RUN would handle #${ev.issueNumber}: "${ev.task}"`); return { skipped: true } }

  // SECURITY: move the target repo's .claude out of the tree so its hooks/settings
  // can't hijack the agent, then restore it BEFORE `git add` so the move never
  // lands in the PR. `finally` guarantees restore even if jarvis throws/times out.
  const restoreClaude = d.neutralizeClaude(d.workspace)
  const prompt = `You are handling a GitHub request. Make ONLY the code changes for this task; do NOT run git commit, git push, or gh. Task: ${ev.task}`
  let ex: { code: number; stdout: string; stderr: string }
  try { ex = await d.exec(prompt) } finally { restoreClaude() }
  if (ex.code !== 0) return { ok: false, error: `jarvis -p exited ${ex.code}: ${ex.stderr.slice(0, 200)}` }

  const gitc = d.git
  const add = await gitc(['add', '-A']); if (add.code !== 0) return { ok: false, error: `git add failed: ${add.stderr}` }
  const diff = await gitc(['diff', '--cached', '--quiet'])
  if (diff.code === 0) { await d.gh(['pr', 'comment', String(ev.issueNumber), '--repo', ev.repo, '--body', `No changes were needed for @${ev.author}'s request.\n\n${SELF_MARKER}`]); return { ok: true, noChanges: true } }
  if (diff.code !== 1) return { ok: false, error: `git diff failed: ${diff.stderr}` }

  const subject = ev.task.split('\n')[0]!.slice(0, 60)
  const branch = ev.isPR ? '' : `jarvis/gh-${ev.issueNumber}-${Date.now().toString(36)}`
  if (!ev.isPR) { const co = await gitc(['checkout', '-b', branch]); if (co.code !== 0) return { ok: false, error: `branch: ${co.stderr}` } }
  const c = await gitc(['-c', 'user.name=jarvis-gh-action', '-c', 'user.email=jarvis@0wlan.com', 'commit', '-m', `jarvis: ${subject}`])
  if (c.code !== 0) return { ok: false, error: `commit failed: ${c.stderr}` }
  const push = await gitc(['push', '-u', 'origin', ev.isPR ? 'HEAD' : branch]); if (push.code !== 0) return { ok: false, error: `push failed: ${push.stderr}` }

  if (ev.isPR) { await d.gh(['pr', 'comment', String(ev.issueNumber), '--repo', ev.repo, '--body', `Pushed changes for @${ev.author}.\n\n${SELF_MARKER}`]); return { ok: true } }
  const base = (await d.gh(['repo', 'view', ev.repo, '--json', 'defaultBranchRef', '--jq', '.defaultBranchRef.name'])).stdout.trim() || 'main'
  const pr = await d.gh(['pr', 'create', '--repo', ev.repo, '--base', base, '--head', branch, '--title', `jarvis: ${subject}`,
    '--body', `Closes #${ev.issueNumber}. Requested by @${ev.author}: "${ev.task}"\n\n_Automated by @jarvis. Review before merge._\n\n${SELF_MARKER}`])
  if (pr.code !== 0) return { ok: false, error: `pr create failed: ${pr.stderr}` }
  return { ok: true, prUrl: pr.stdout.trim() }
}

// Real deps for the runner. `neutralizeClaude` renames the target repo's .claude
// so its hooks/settings can't hijack the agent (ephemeral runner, safe).
export function realActionDeps(): ActionDeps {
  const ws = process.env.GITHUB_WORKSPACE ?? process.cwd()
  const jarvisBin = process.env.JARVIS_BIN ?? 'jarvis'
  const timeoutSec = Number(process.env.JARVIS_GH_TIMEOUT ?? '600')
  const run = (file: string) => async (args: string[]) => { const r = await execFileNoThrow(file, args); return { code: r.code, stdout: r.stdout, stderr: r.stderr } }
  return {
    readEvent: () => { const ctx = actionCtxFromEnv(p => readFileSync(p, 'utf8')); return ctx ? parseActionEvent(ctx) : null },
    allowlist: (process.env.JARVIS_GH_ALLOWLIST ?? '').split(',').map(s => s.trim()).filter(Boolean),
    workspace: ws, jarvisBin, timeoutSec,
    neutralizeClaude: (dir) => {
      const src = join(dir, '.claude')
      if (!existsSync(src)) return () => {}
      // Stash the target repo's .claude OUTSIDE the work tree (so neither the
      // removal nor the stash shows up to `git add -A`) while the agent runs —
      // its hooks/settings must not hijack the agent. SECURITY-load-bearing.
      //
      // Prefer a stash in the repo's PARENT dir: it's outside the git tree AND
      // (almost always) on the same filesystem as .claude, so renameSync is
      // atomic. os tmpdir is often a DIFFERENT mount (esp. in CI) → renameSync
      // throws EXDEV, which the old code swallowed into a no-op — silently
      // leaving .claude in the tree, un-neutralized. A cpSync+rm fallback
      // covers any residual cross-device case so neutralization ALWAYS happens.
      let stashDir: string
      try { stashDir = mkdtempSync(join(dirname(dir), 'jarvis-claude-')) }
      catch { stashDir = mkdtempSync(join(tmpdir(), 'jarvis-claude-')) }
      const stash = join(stashDir, '.claude')
      const move = (from: string, to: string) => {
        try { renameSync(from, to) }
        catch {
          // Cross-device (EXDEV) or other rename failure → copy then remove.
          cpSync(from, to, { recursive: true })
          rmSync(from, { recursive: true, force: true })
        }
      }
      try { move(src, stash) }
      catch (e) {
        // Could not move .claude out AT ALL — do not silently run with the
        // target's hooks live. Remove it (git still tracks it; restore re-checks
        // it out) and surface the failure loudly.
        try { rmSync(src, { recursive: true, force: true }) } catch {}
        try { rmSync(stashDir, { recursive: true, force: true }) } catch {}
        console.error('[jarvis-gh-action] could not stash .claude, removed it to stay safe:', (e as Error).message)
        return () => {}
      }
      return () => {
        try {
          if (existsSync(src)) rmSync(src, { recursive: true, force: true })
          move(stash, src)
        } catch { /* best effort */ }
        finally { try { rmSync(stashDir, { recursive: true, force: true }) } catch {} }
      }
    },
    exec: async (prompt) => { const r = await execFileNoThrowWithCwd(jarvisBin, ['-p', prompt], { cwd: ws, timeout: timeoutSec * 1000, env: { ...process.env, JARVIS_SKIP_VERIFY: '1' } }); return { code: r.code, stdout: r.stdout, stderr: r.stderr } },
    git: (args) => run('git')(['-C', ws, ...args]),
    gh: run('gh'),
    prIsUntrusted: async (repo, n, allowlist) => {
      const r = await execFileNoThrow('gh', ['pr', 'view', String(n), '--repo', repo, '--json', 'isCrossRepository,author'])
      if (r.code !== 0) return true
      try { const v = JSON.parse(r.stdout); return !!v.isCrossRepository || (allowlist.length > 0 && !allowlist.some((a: string) => a.toLowerCase() === (v.author?.login ?? '').toLowerCase())) } catch { return true }
    },
    log: (m) => console.log(`[gh-action] ${m}`),
    dryRun: process.argv.includes('--dry-run') || process.env.JARVIS_GH_DRY_RUN === '1',
  }
}

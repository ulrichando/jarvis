// src/gh-app/runInSandbox.test.ts
import { test, expect, describe } from 'bun:test'
import { mkdtempSync, mkdirSync, writeFileSync, existsSync, rmSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { runInSandbox, type SandboxDeps, type SpawnSpec } from './runInSandbox.js'
import { mentionFromJob, cfgFromEnv, gitAuthConfigArgs, neutralizeClaude, buildEntryDeps } from './containerEntry.js'
import type { Job } from './jobs.js'
import type { TaskDeps } from '../cli/src/gh-agent/task.js'

const TOKEN = 'ghs_supersecret123'
const job: Job = { id: 9, installationId: 555, repo: 'o/r', issueNumber: 7, task: 'add HELLO.md', isPR: false }

function harness(result: { code: number; stdout: string; stderr: string } | Error = { code: 0, stdout: '{"ok":true}', stderr: '' }) {
  const specs: SpawnSpec[] = []
  const deps: SandboxDeps = {
    spawnContainer: async (spec) => {
      specs.push(spec)
      if (result instanceof Error) throw result
      return result
    },
    timeoutSec: 900,
  }
  return { deps, specs }
}

describe('gh-app runInSandbox', () => {
  test('spawns with token in env (never argv), job as env json, writable workdir, timeout applied', async () => {
    const { deps, specs } = harness()
    const r = await runInSandbox(job, TOKEN, deps)
    expect(r.ok).toBe(true)
    expect(specs.length).toBe(1)
    const spec = specs[0]!
    // token reaches the container ONLY via env
    expect(spec.env.GH_TOKEN).toBe(TOKEN)
    expect(spec.cmd.join(' ')).not.toContain(TOKEN)
    expect(JSON.stringify(spec.image)).not.toContain(TOKEN)
    // the job rides env too, parseable back to the same shape
    expect(JSON.parse(spec.env.GH_APP_JOB!)).toEqual(job)
    // sandbox constraints
    expect(spec.writableWorkdir).toBe(true)
    expect(spec.timeoutSec).toBe(900)
    // the command runs the container entry, not an inline shell string
    expect(spec.cmd[0]).toBe('bun')
    expect(spec.cmd.some((c) => c.includes('containerEntry'))).toBe(true)
  })

  test('non-zero exit → ok:false with the token redacted from the error', async () => {
    const { deps } = harness({ code: 1, stdout: '', stderr: `fatal: auth failed for https://x-access-token:${TOKEN}@github.com/o/r` })
    const r = await runInSandbox(job, TOKEN, deps)
    expect(r.ok).toBe(false)
    expect(r.error).toContain('exited 1')
    expect(r.error).not.toContain(TOKEN)
  })

  test('spawnContainer throwing (timeout) → ok:false, message redacted', async () => {
    const { deps } = harness(new Error(`container timed out; env GH_TOKEN=${TOKEN}`))
    const r = await runInSandbox(job, TOKEN, deps)
    expect(r.ok).toBe(false)
    expect(r.error).toContain('timed out')
    expect(r.error).not.toContain(TOKEN)
  })
})

describe('gh-app containerEntry helpers', () => {
  test('mentionFromJob reconstructs a body executeTask strips back to the exact task', () => {
    const m = mentionFromJob(job, '@jarvis')
    expect(m.issueNumber).toBe(7)
    expect(m.body).toBe('@jarvis add HELLO.md')
    // a task that itself mentions the trigger later must not be truncated:
    // indexOf finds the LEADING trigger, so the slice returns the full task.
    const tricky = mentionFromJob({ ...job, task: 'note that @jarvis is the bot name' }, '@jarvis')
    expect(tricky.body).toBe('@jarvis note that @jarvis is the bot name')
  })

  test('cfgFromEnv: allowlist csv + defaults', () => {
    const cfg = cfgFromEnv({ GH_APP_ALLOWLIST: 'ulrichando, other', GH_APP_TIMEOUT_SEC: '300' })
    expect(cfg.allowlist).toEqual(['ulrichando', 'other'])
    expect(cfg.trigger).toBe('@jarvis')
    expect(cfg.executionTimeoutSec).toBe(300)
    const dflt = cfgFromEnv({})
    expect(dflt.allowlist).toEqual(['ulrichando'])
    expect(dflt.executionTimeoutSec).toBe(900)
  })

  test('gitAuthConfigArgs is pure argv (no shell) with the x-access-token rewrite', () => {
    const args = gitAuthConfigArgs('tok123')
    expect(args[0]).toBe('config')
    expect(args).toContain('url.https://x-access-token:tok123@github.com/.insteadOf')
    expect(args[args.length - 1]).toBe('https://github.com/')
  })

  test('neutralizeClaude stashes .claude during exec and restores it after', () => {
    const dir = mkdtempSync(join(tmpdir(), 'ghapp-nc-'))
    try {
      mkdirSync(join(dir, '.claude'))
      writeFileSync(join(dir, '.claude', 'settings.json'), '{"hooks":{}}')
      const restore = neutralizeClaude(dir)
      expect(existsSync(join(dir, '.claude'))).toBe(false)
      restore()
      expect(existsSync(join(dir, '.claude', 'settings.json'))).toBe(true)
      // absent .claude → no-op restore, no throw
      const restore2 = neutralizeClaude(join(dir, 'nope'))
      restore2()
    } finally {
      rmSync(dir, { recursive: true, force: true })
    }
  })

  test('buildEntryDeps wraps exec so .claude is neutralized exactly around jarvis -p', async () => {
    const dir = mkdtempSync(join(tmpdir(), 'ghapp-wrap-'))
    try {
      mkdirSync(join(dir, '.claude'))
      let claudePresentDuringExec: boolean | null = null
      const base: TaskDeps = {
        gh: async () => ({ stdout: '', stderr: '', code: 0 }),
        git: async () => ({ stdout: '', stderr: '', code: 0 }),
        exec: async (_f, _a, cwd) => {
          claudePresentDuringExec = existsSync(join(cwd, '.claude'))
          return { stdout: '', stderr: '', code: 0 }
        },
        makeTempDir: () => dir,
        cleanup: () => {},
      }
      const wrapped = buildEntryDeps(base)
      const r = await wrapped.exec('jarvis', ['-p', 'x'], dir, 10)
      expect(r.code).toBe(0)
      expect(claudePresentDuringExec).toBe(false)          // hidden while the agent ran
      expect(existsSync(join(dir, '.claude'))).toBe(true)  // restored before git add
    } finally {
      rmSync(dir, { recursive: true, force: true })
    }
  })
})

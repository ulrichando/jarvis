// src/cli/src/gh-agent/task.test.ts
import { describe, expect, test } from 'bun:test'
import { tmpdir } from 'node:os'
import { executeTask, realDeps, taskText, type TaskDeps } from './task.js'
import type { Mention } from './gh.js'
import { DEFAULTS } from './config.js'

const mention = (over: Partial<Mention> = {}): Mention => ({
  id: 1, body: '@jarvis add a hello file', author: 'ulrichando',
  createdAt: '2026-07-01T10:00:00Z', updatedAt: '2026-07-01T10:00:00Z',
  issueNumber: 12, url: 'u12', ...over,
})

// Records the ordered subprocess calls and lets each test script outcomes.
function harness(opts: {
  isPR?: boolean
  defaultBranch?: string
  headRef?: string
  crossRepo?: boolean
  prAuthor?: string
  producedChanges?: boolean
  execCode?: number
  probeCode?: number
  dbCode?: number
  addCode?: number
  diffCode?: number
  commitCode?: number
} = {}) {
  const calls: string[] = []
  let execCall: { file: string; args: string[]; cwd: string; timeoutSec: number } | undefined
  const deps: TaskDeps = {
    gh: async (args) => {
      calls.push('gh ' + args.join(' '))
      if (args[0] === 'api' && /issues\/\d+$/.test(args[1] ?? '')) {
        if (opts.probeCode) return { stdout: '', stderr: 'rate limited', code: opts.probeCode }
        // `--jq '.pull_request'` prints the object for a PR, `null` for an issue.
        return { stdout: JSON.stringify(opts.isPR ? { pull_request: { url: 'x' } } : null), stderr: '', code: 0 }
      }
      if (args[0] === 'repo' && args[1] === 'view') {
        if (opts.dbCode) return { stdout: '', stderr: 'api down', code: opts.dbCode }
        return { stdout: opts.defaultBranch ?? 'master', stderr: '', code: 0 }
      }
      if (args[0] === 'pr' && args[1] === 'view') {
        // `--json headRefName,isCrossRepository,author` → one JSON object
        return { stdout: JSON.stringify({ headRefName: opts.headRef ?? 'feature-x', isCrossRepository: opts.crossRepo ?? false, author: { login: opts.prAuthor ?? 'ulrichando' } }), stderr: '', code: 0 }
      }
      if (args[0] === 'pr' && args[1] === 'create') return { stdout: 'https://github.com/o/r/pull/99', stderr: '', code: 0 }
      return { stdout: '', stderr: '', code: 0 } // pr comment
    },
    git: async (args) => {
      calls.push('git ' + args.join(' '))
      if (args.includes('add')) return { stdout: '', stderr: opts.addCode ? 'index locked' : '', code: opts.addCode ?? 0 }
      // `diff --cached --quiet`: exit 1 = changes present, 0 = none, >1 = git error
      if (args.includes('diff') && args.includes('--quiet')) return { stdout: '', stderr: opts.diffCode && opts.diffCode > 1 ? 'bad object' : '', code: opts.diffCode ?? (opts.producedChanges === false ? 0 : 1) }
      if (args.includes('commit')) return { stdout: '', stderr: opts.commitCode ? 'hook rejected' : '', code: opts.commitCode ?? 0 }
      return { stdout: '', stderr: '', code: 0 }
    },
    exec: async (file, args, cwd, timeoutSec) => { execCall = { file, args, cwd, timeoutSec }; calls.push('exec jarvis -p'); return { stdout: 'done', stderr: '', code: opts.execCode ?? 0 } },
    makeTempDir: () => { calls.push('mktemp'); return '/tmp/gha-test-clone' },
    cleanup: () => { calls.push('cleanup') },
  }
  return { deps, calls, execCall: () => execCall }
}

describe('gh-agent executeTask', () => {
  test('ISSUE: clone → jarvis -p → commit → push new branch → open PR → cleanup', async () => {
    const { deps, calls } = harness({ isPR: false, defaultBranch: 'master', producedChanges: true })
    const r = await executeTask('o/r', mention(), DEFAULTS, deps)
    expect(r.ok).toBe(true)
    expect(r.prUrl).toBe('https://github.com/o/r/pull/99')
    const seq = calls.join('\n')
    expect(seq).toContain('gh repo clone o/r')
    expect(seq).toContain('exec jarvis -p')
    expect(seq).toContain('git -C /tmp/gha-test-clone push')
    expect(seq).toContain('gh pr create')
    expect(calls[calls.length - 1]).toBe('cleanup') // always cleans up
  })

  test('PR: commit to the PR head branch + comment, no pr create', async () => {
    const { deps, calls } = harness({ isPR: true, headRef: 'feat-y', producedChanges: true })
    const r = await executeTask('o/r', mention({ issueNumber: 20 }), DEFAULTS, deps)
    expect(r.ok).toBe(true)
    const seq = calls.join('\n')
    expect(seq).toContain('gh pr view 20')
    expect(seq).toContain('feat-y')          // checked out / pushed the PR branch
    expect(seq).toContain('gh pr comment 20')
    expect(seq).not.toContain('gh pr create')
    expect(calls[calls.length - 1]).toBe('cleanup')
  })

  test('no changes produced → comments "no changes", no branch/PR', async () => {
    const { deps, calls } = harness({ isPR: false, producedChanges: false })
    const r = await executeTask('o/r', mention(), DEFAULTS, deps)
    expect(r.ok).toBe(true)
    expect(r.noChanges).toBe(true)
    const seq = calls.join('\n')
    expect(seq).not.toContain('git -C /tmp/gha-test-clone push')
    expect(seq).not.toContain('gh pr create')
    expect(seq).toContain('gh api -X POST') // a comment was posted
    expect(calls[calls.length - 1]).toBe('cleanup')
  })

  test('jarvis -p failure → ok:false, no push, still cleans up', async () => {
    const { deps, calls } = harness({ isPR: false, execCode: 124 }) // timeout exit
    const r = await executeTask('o/r', mention(), DEFAULTS, deps)
    expect(r.ok).toBe(false)
    expect(calls.join('\n')).not.toContain('push')
    expect(calls[calls.length - 1]).toBe('cleanup')
  })

  test('issue/PR probe failure (e.g. rate limit) → ok:false, no clone (no duplicate-PR risk)', async () => {
    const { deps, calls } = harness({ probeCode: 1 })
    const r = await executeTask('o/r', mention(), DEFAULTS, deps)
    expect(r.ok).toBe(false)
    expect(r.error).toContain('probe failed')
    const seq = calls.join('\n')
    expect(seq).not.toContain('gh repo clone')
    expect(seq).not.toContain('exec jarvis -p')
    expect(calls[calls.length - 1]).toBe('cleanup')
  })

  test('default-branch lookup failure → ok:false, no silent master fallback', async () => {
    const { deps, calls } = harness({ isPR: false, dbCode: 1 })
    const r = await executeTask('o/r', mention(), DEFAULTS, deps)
    expect(r.ok).toBe(false)
    const seq = calls.join('\n')
    expect(seq).not.toContain('exec jarvis -p')
    expect(seq).not.toContain('push')
    expect(calls[calls.length - 1]).toBe('cleanup')
  })

  test('git add failure → ok:false, nothing committed or pushed', async () => {
    const { deps, calls } = harness({ isPR: false, addCode: 1 })
    const r = await executeTask('o/r', mention(), DEFAULTS, deps)
    expect(r.ok).toBe(false)
    expect(r.error).toContain('add failed')
    const seq = calls.join('\n')
    expect(seq).not.toContain(' commit ')
    expect(seq).not.toContain('push')
  })

  test('git diff error (code >1) → ok:false, NOT treated as "changes present"', async () => {
    const { deps, calls } = harness({ isPR: false, diffCode: 2 })
    const r = await executeTask('o/r', mention(), DEFAULTS, deps)
    expect(r.ok).toBe(false)
    expect(r.error).toContain('diff failed')
    const seq = calls.join('\n')
    expect(seq).not.toContain(' commit ')
    expect(seq).not.toContain('push')
  })

  test('git commit failure → ok:false, no push, no "pushed"/PR comment', async () => {
    const { deps, calls } = harness({ isPR: false, producedChanges: true, commitCode: 1 })
    const r = await executeTask('o/r', mention(), DEFAULTS, deps)
    expect(r.ok).toBe(false)
    expect(r.error).toContain('commit failed')
    const seq = calls.join('\n')
    expect(seq).not.toContain('push')
    expect(seq).not.toContain('gh pr create')
    expect(seq).not.toContain('gh pr comment')
    expect(seq).not.toContain('gh api -X POST')
  })

  test('fork PR (isCrossRepository) → decline comment, noChanges, jarvis NEVER runs on the untrusted head', async () => {
    const { deps, calls } = harness({ isPR: true, crossRepo: true })
    const r = await executeTask('o/r', mention({ issueNumber: 21 }), DEFAULTS, deps)
    expect(r.ok).toBe(true)
    expect(r.noChanges).toBe(true)
    const seq = calls.join('\n')
    expect(seq).toContain('Declining automated changes')
    expect(seq).not.toContain('exec jarvis -p')
    expect(seq).not.toContain('push')
    expect(calls[calls.length - 1]).toBe('cleanup')
  })

  test('PR authored outside the allowlist → decline comment, noChanges, jarvis NEVER runs', async () => {
    const { deps, calls } = harness({ isPR: true, prAuthor: 'mallory' })
    const r = await executeTask('o/r', mention({ issueNumber: 22 }), DEFAULTS, deps)
    expect(r.ok).toBe(true)
    expect(r.noChanges).toBe(true)
    const seq = calls.join('\n')
    expect(seq).toContain('Declining automated changes')
    expect(seq).toContain('@mallory')
    expect(seq).not.toContain('exec jarvis -p')
    expect(seq).not.toContain('push')
  })

  test('bare trigger (no task text) → noChanges without clone, comment, or subprocess', async () => {
    const { deps, calls } = harness()
    const r = await executeTask('o/r', mention({ body: '  @jarvis  ' }), DEFAULTS, deps)
    expect(r.ok).toBe(true)
    expect(r.noChanges).toBe(true)
    expect(calls).toEqual([]) // no makeTempDir, no gh, no git, no exec
  })

  test('realDeps.exec surfaces the timeout reason, not just a bare exit code', async () => {
    const r = await realDeps().exec('sleep', ['5'], tmpdir(), 0.1)
    expect(r.code).not.toBe(0)
    expect(r.stderr).toMatch(/timed out/i)
  }, 10000)

  test('multiline task → commit subject and PR title use the first line only', async () => {
    const { deps, calls } = harness({ isPR: false, producedChanges: true })
    const r = await executeTask('o/r', mention({ body: '@jarvis fix the widget\nAlso update the docs to match.' }), DEFAULTS, deps)
    expect(r.ok).toBe(true)
    const commit = calls.find(c => c.includes(' commit -m '))!
    expect(commit).toContain('commit -m jarvis: fix the widget')
    expect(commit).not.toContain('\n')
    const create = calls.find(c => c.includes('pr create'))!
    // args are space-joined: a newline in the --title arg would break this pairing
    expect(create).toContain('--title jarvis: fix the widget --body')
  })

  test('case-mismatched trigger (@Jarvis) still strips the token — listMentions accepts /i, so taskText must too', async () => {
    // Regression: taskText used case-sensitive indexOf. listMentions matches
    // '@Jarvis' with the /i regex and enqueues the comment, but indexOf('@jarvis')
    // then missed → the '@Jarvis' token leaked into the prompt AND the commit
    // subject. Now both use the same case-insensitive regex.
    const { deps, calls } = harness({ isPR: false, producedChanges: true })
    const r = await executeTask('o/r', mention({ body: '@Jarvis fix the widget' }), DEFAULTS, deps)
    expect(r.ok).toBe(true)
    const commit = calls.find(c => c.includes(' commit -m '))!
    expect(commit).toContain('commit -m jarvis: fix the widget')
    expect(commit).not.toContain('@Jarvis')
  })

  test('embedded trigger substring does not mis-slice — word-boundary match', async () => {
    // 'notify@jarvis.io then @jarvis do the work': indexOf would slice at the
    // FIRST substring (inside the email), leaving '.io then @jarvis do the work'.
    // The word-boundary regex skips the email and matches the real mention.
    const task = taskText('notify@jarvis.io then @jarvis fix the widget', '@jarvis')
    expect(task).toBe('fix the widget')
  })

  test('taskText: bare/absent trigger and control-char stripping', () => {
    expect(taskText('  @jarvis  ', '@jarvis')).toBe('')
    // No trigger at all → whole body (defensive; listMentions should never pass this)
    expect(taskText('just some text', '@jarvis')).toBe('just some text')
    // NUL and other control chars stripped, newline/tab kept
    expect(taskText('@jarvis a\x00b\nc\td', '@jarvis')).toBe('ab\nc\td')
  })

  test('SECURITY: untrusted task text reaches jarvis as one argv element, never a shell string', async () => {
    // Regression guard for the command-injection class: a GitHub comment is
    // attacker-controlled. If task.ts ever rebuilt a shell command string
    // (e.g. `exec(`${bin} -p ${JSON.stringify(prompt)}`)`), these metacharacters
    // would execute. The exec seam must receive (bareBinaryPath, ['-p', prompt]).
    const payload = '`touch /tmp/pwned`; $(rm -rf ~) && echo owned'
    const { deps, execCall } = harness({ isPR: false, producedChanges: true })
    const r = await executeTask('o/r', mention({ body: `@jarvis ${payload}` }), DEFAULTS, deps)
    expect(r.ok).toBe(true)
    const call = execCall()
    expect(call).toBeDefined()
    // file is the bare jarvis binary path — NOT a shell string with the payload baked in
    expect(call!.file).toMatch(/\/bin\/jarvis$/)
    expect(call!.file).not.toContain(' -p')
    expect(call!.file).not.toContain(payload)
    // the untrusted payload is confined to a single argv slot, verbatim (unescaped = it's data, not code)
    expect(call!.args[0]).toBe('-p')
    expect(call!.args.length).toBe(2)
    expect(call!.args[1]).toContain(payload)
  })
})

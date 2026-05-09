# CLI Voice Functionality Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add `/voice-tests` + `/voice-status` slash commands and a `voice-log-analyzer` project subagent to the jarvis CLI, mirroring the existing `.claude/commands/voice-*.md` Claude-Code commands and `.claude/agents/log-analyzer.md` agent.

**Architecture:** TypeScript/Bun. Each new slash command pairs a thin `LocalCommandCall` wrapper (spawns external processes via `execFile`) with a pure parser/formatter helper that's unit-tested in isolation via `bun:test`. The agent is a single project-level markdown file picked up by the existing CLI agent loader from `<repo>/.jarvis/agents/`. Mirrors the existing `voice-restart.ts` / `voice-logs.ts` pattern at `src/cli/src/commands/voice/`.

**Tech Stack:** TypeScript, Bun runtime, `bun:test` for unit tests, `node:child_process.execFile`, `node:fs/promises`, `os`, `path`.

**Spec:** [docs/superpowers/specs/2026-05-09-cli-voice-functionality-design.md](../specs/2026-05-09-cli-voice-functionality-design.md)

---

## File structure

| Path | Action | Responsibility |
|:--|:--|:--|
| `src/cli/src/commands/voice/parsePytestSummary.ts` | Create | Pure: extract pytest summary line + first-failure block from stdout |
| `src/cli/src/commands/voice/parsePytestSummary.test.ts` | Create | `bun:test` cases for all-pass, mixed, errors, ANSI, garbage |
| `src/cli/src/commands/voice/tests.ts` | Create | `LocalCommandCall`: spawn pytest, call parser, return text |
| `src/cli/src/commands/voice/formatStatus.ts` | Create | Pure: format `{voice, bridge, lastTurnAt}` → multi-line text |
| `src/cli/src/commands/voice/formatStatus.test.ts` | Create | `bun:test` cases for active/inactive/unknown × age combinations |
| `src/cli/src/commands/voice/status.ts` | Create | `LocalCommandCall`: spawn systemctl + sqlite3, call formatter |
| `src/cli/src/commands/voice/index.ts` | Modify | Export `voiceTests`, `voiceStatus` `Command` definitions |
| `src/cli/src/commands.ts` | Modify | Declare `voiceTestsCommand` + `voiceStatusCommand`; spread into list |
| `.jarvis/agents/voice-log-analyzer.md` | Create | Project-level agent definition (frontmatter + body) |

---

## Task 1: Pure `parsePytestSummary` + tests

**Files:**
- Create: `src/cli/src/commands/voice/parsePytestSummary.ts`
- Create: `src/cli/src/commands/voice/parsePytestSummary.test.ts`

The parser is a pure function — no DB, no spawn, no globals. We TDD it to lock down the regex shape before wiring it into the command handler. Mirrors the seam we used for the consolidator (parser unit-tested separately from the I/O wrapper).

- [ ] **Step 1: Write the failing tests**

Create `src/cli/src/commands/voice/parsePytestSummary.test.ts`:

```typescript
import { describe, expect, test } from 'bun:test'
import { parsePytestSummary } from './parsePytestSummary.js'

describe('parsePytestSummary', () => {
  test('extracts summary on all-pass', () => {
    const stdout = '.....\n24 passed in 1.37s\n'
    const r = parsePytestSummary(stdout)
    expect(r.summary).toBe('24 passed in 1.37s')
    expect(r.firstFailure).toBeNull()
  })

  test('extracts summary with skipped + warnings', () => {
    const stdout = '...s..\n1057 passed, 2 skipped, 3 warnings in 17.47s\n'
    const r = parsePytestSummary(stdout)
    expect(r.summary).toBe('1057 passed, 2 skipped, 3 warnings in 17.47s')
    expect(r.firstFailure).toBeNull()
  })

  test('extracts summary + first failure block on mixed', () => {
    const stdout = [
      '.F....',
      '=================================== FAILURES ===================================',
      '______________________________ test_something _________________________________',
      'tests/test_foo.py:42: in test_something',
      '    assert x == 1',
      'E   AssertionError: assert 0 == 1',
      '______________________________ test_other _____________________________________',
      'tests/test_foo.py:99: in test_other',
      '    assert y == 2',
      'E   AssertionError: assert 0 == 2',
      '=========================== short test summary info ============================',
      'FAILED tests/test_foo.py::test_something',
      'FAILED tests/test_foo.py::test_other',
      '2 failed, 21 passed in 1.72s',
      '',
    ].join('\n')
    const r = parsePytestSummary(stdout)
    expect(r.summary).toBe('2 failed, 21 passed in 1.72s')
    expect(r.firstFailure).toContain('test_something')
    expect(r.firstFailure).toContain('AssertionError: assert 0 == 1')
    // First failure ONLY — should not include test_other.
    expect(r.firstFailure).not.toContain('test_other')
  })

  test('extracts summary on collection error', () => {
    const stdout = [
      '=================================== ERRORS ====================================',
      '________________________ ERROR collecting test_foo.py _________________________',
      "ModuleNotFoundError: No module named 'foo'",
      '=========================== short test summary info ============================',
      'ERROR tests/test_foo.py',
      '!!!!!!!!! Interrupted: 1 error during collection !!!!!!!!!',
      '1 error in 3.72s',
      '',
    ].join('\n')
    const r = parsePytestSummary(stdout)
    expect(r.summary).toBe('1 error in 3.72s')
    expect(r.firstFailure).toContain('ERROR collecting test_foo.py')
    expect(r.firstFailure).toContain("No module named 'foo'")
  })

  test('strips ANSI before parsing', () => {
    const stdout = '[32m.....[0m\n[1m24 passed in 1.37s[0m\n'
    const r = parsePytestSummary(stdout)
    expect(r.summary).toBe('24 passed in 1.37s')
  })

  test('returns nulls on empty input', () => {
    expect(parsePytestSummary('')).toEqual({ summary: null, firstFailure: null })
  })

  test('returns nulls on garbage with no summary line', () => {
    const r = parsePytestSummary('not a pytest output at all\nrandom text\n')
    expect(r.summary).toBeNull()
    expect(r.firstFailure).toBeNull()
  })

  test('picks up summary even with trailing blank lines', () => {
    const stdout = '24 passed in 1.37s\n\n\n'
    const r = parsePytestSummary(stdout)
    expect(r.summary).toBe('24 passed in 1.37s')
  })
})
```

- [ ] **Step 2: Run tests to verify they fail**

Run:

```bash
cd src/cli && bun test src/commands/voice/parsePytestSummary.test.ts 2>&1 | tail -10
```

Expected: 8 fails — `Cannot find module './parsePytestSummary'` (or similar).

- [ ] **Step 3: Implement the parser**

Create `src/cli/src/commands/voice/parsePytestSummary.ts`:

```typescript
/**
 * Pure parser for pytest -q --tb=short stdout.
 *
 * Returns the pytest summary line (the "X passed, Y failed in Zs" line at
 * the end) and, when failures are present, the first failure's traceback
 * block. Returns `null` for either field when the input doesn't contain
 * the expected structure — caller treats null as "couldn't parse, fall back
 * to raw output".
 *
 * Pure function. No I/O, no globals.
 */

export interface PytestSummary {
  summary: string | null
  firstFailure: string | null
}

// pytest's summary line shape: "X passed[, Y failed][, Z skipped]... in N.Ms"
const SUMMARY_RE =
  /(\d+ (?:passed|failed|skipped|error|errors|deselected|warning|warnings)(?:, \d+ (?:passed|failed|skipped|error|errors|deselected|warning|warnings))*) in [\d.]+s/

// Strip ANSI escape sequences (color codes, bold, etc.)
function stripAnsi(text: string): string {
  // eslint-disable-next-line no-control-regex
  return text.replace(/\[[0-9;]*m/g, '')
}

function extractSummary(lines: string[]): string | null {
  // Search from the end backwards — the summary is always the last
  // matching line in pytest's output.
  for (let i = lines.length - 1; i >= 0; i--) {
    const m = SUMMARY_RE.exec(lines[i])
    if (m) {
      // Reconstruct full summary: "X passed[, ...] in Ns" — m[0] already
      // contains the matched substring with the duration.
      return m[0]
    }
  }
  return null
}

function extractFirstFailure(lines: string[]): string | null {
  // The FAILURES (or ERRORS) section is delimited by `===` header lines.
  // Inside it, each failure is wrapped in `_____ test_name _____` separators.
  // We capture from the first `_____` separator until the NEXT `_____`
  // separator OR the next `===` block, whichever comes first.
  let inFailureSection = false
  let captureStart = -1
  let captureEnd = -1
  for (let i = 0; i < lines.length; i++) {
    const line = lines[i]
    if (/^=+ (FAILURES|ERRORS) =+$/.test(line)) {
      inFailureSection = true
      continue
    }
    if (!inFailureSection) continue
    // A `_____ ... _____` separator marks a failure boundary.
    const sepMatch = /^_+ .+ _+$/.test(line)
    if (sepMatch) {
      if (captureStart === -1) {
        captureStart = i
        continue
      }
      // Hit the next separator — first-failure ends here.
      captureEnd = i
      break
    }
    // A `===` boundary (e.g. "short test summary info") closes the failure
    // section before any second separator appeared.
    if (captureStart !== -1 && /^=+ .+ =+$/.test(line)) {
      captureEnd = i
      break
    }
  }
  if (captureStart === -1) return null
  if (captureEnd === -1) captureEnd = lines.length
  return lines.slice(captureStart, captureEnd).join('\n').trim() || null
}

export function parsePytestSummary(stdout: string): PytestSummary {
  if (!stdout) return { summary: null, firstFailure: null }
  const cleaned = stripAnsi(stdout)
  const lines = cleaned.split('\n')
  return {
    summary: extractSummary(lines),
    firstFailure: extractFirstFailure(lines),
  }
}
```

- [ ] **Step 4: Run tests to verify they pass**

Run:

```bash
cd src/cli && bun test src/commands/voice/parsePytestSummary.test.ts 2>&1 | tail -5
```

Expected: `8 pass, 0 fail`.

- [ ] **Step 5: Commit**

```bash
cd /home/ulrich/Documents/Projects/jarvis
git add src/cli/src/commands/voice/parsePytestSummary.ts \
        src/cli/src/commands/voice/parsePytestSummary.test.ts
git commit -m "feat(cli): add pure parsePytestSummary helper + 8 tests"
```

---

## Task 2: `/voice-tests` command + registry wiring

**Files:**
- Create: `src/cli/src/commands/voice/tests.ts`
- Modify: `src/cli/src/commands/voice/index.ts`
- Modify: `src/cli/src/commands.ts`

Wire the parser behind a `LocalCommandCall` that spawns the voice-agent's pytest and returns a smart-summary text result.

- [ ] **Step 1: Create the command implementation**

Create `src/cli/src/commands/voice/tests.ts`:

```typescript
import { execFile } from 'node:child_process'
import { access } from 'node:fs/promises'
import * as os from 'node:os'
import * as path from 'node:path'
import { promisify } from 'node:util'

import { logEvent } from '../../services/analytics/index.js'
import type { LocalCommandCall } from '../../types/command.js'
import { parsePytestSummary } from './parsePytestSummary.js'

const execFileAsync = promisify(execFile)

const PYTEST_TIMEOUT_MS = 120_000
const PYTEST_MAX_BUFFER = 10 * 1024 * 1024 // 10 MB

function resolveVoiceAgentPath(): string {
  const env = process.env.JARVIS_VOICE_AGENT_PATH
  if (env && env.length > 0) return env
  return path.join(
    os.homedir(),
    'Documents',
    'Projects',
    'jarvis',
    'src',
    'voice-agent',
  )
}

async function pathExists(p: string): Promise<boolean> {
  try {
    await access(p)
    return true
  } catch {
    return false
  }
}

// Split a pytest-arg string into argv entries, respecting "double-quoted"
// substrings. Never run through a shell.
function splitArgs(input: string | undefined): string[] {
  if (!input) return []
  const trimmed = input.trim()
  if (!trimmed) return []
  const out: string[] = []
  const re = /"([^"]*)"|(\S+)/g
  let m: RegExpExecArray | null
  while ((m = re.exec(trimmed)) !== null) {
    out.push(m[1] !== undefined ? m[1] : m[2])
  }
  return out
}

export const call: LocalCommandCall = async args => {
  const startedAt = Date.now()
  const vaPath = resolveVoiceAgentPath()
  if (!(await pathExists(vaPath))) {
    return {
      type: 'text' as const,
      value: `Voice agent path not found at ${vaPath}; set JARVIS_VOICE_AGENT_PATH.`,
    }
  }
  const venvPython = path.join(vaPath, '.venv', 'bin', 'python')
  if (!(await pathExists(venvPython))) {
    return {
      type: 'text' as const,
      value:
        `voice-agent venv missing at ${venvPython} — ` +
        `run \`cd ${vaPath} && python -m venv .venv && pip install -r requirements.txt\`.`,
    }
  }

  const extraArgs = splitArgs(args)
  const argv = ['-m', 'pytest', 'tests/', ...extraArgs, '--tb=short', '-q']

  let stdout = ''
  let stderr = ''
  let exitCode = 0
  let timedOut = false

  try {
    const result = await execFileAsync(venvPython, argv, {
      cwd: vaPath,
      timeout: PYTEST_TIMEOUT_MS,
      maxBuffer: PYTEST_MAX_BUFFER,
    })
    stdout = result.stdout
    stderr = result.stderr
  } catch (err) {
    const e = err as NodeJS.ErrnoException & {
      stdout?: string
      stderr?: string
      code?: number | string
      signal?: string
    }
    stdout = e.stdout ?? ''
    stderr = e.stderr ?? ''
    if (e.signal === 'SIGTERM' || e.code === 'ETIMEDOUT') {
      timedOut = true
    }
    if (typeof e.code === 'number') exitCode = e.code
  }

  const durationMs = Date.now() - startedAt

  if (timedOut) {
    logEvent('tengu_voice_tests_run', {
      withFilter: extraArgs.length > 0,
      passed: false,
      durationMs,
    })
    return {
      type: 'text' as const,
      value:
        `Pytest exceeded ${PYTEST_TIMEOUT_MS / 1000}s timeout. ` +
        `Run manually: cd ${vaPath} && .venv/bin/python -m pytest tests/`,
    }
  }

  const combined = stdout + (stderr ? `\n${stderr}` : '')
  const { summary, firstFailure } = parsePytestSummary(combined)
  const passed = exitCode === 0 && summary !== null && !/failed|error/i.test(summary)

  logEvent('tengu_voice_tests_run', {
    withFilter: extraArgs.length > 0,
    passed,
    durationMs,
  })

  if (!summary) {
    // Couldn't parse — fall back to raw tail.
    const tail = combined.split('\n').slice(-30).join('\n')
    return {
      type: 'text' as const,
      value: `Pytest output (couldn't extract summary):\n${tail}`,
    }
  }

  if (passed) {
    return { type: 'text' as const, value: summary }
  }

  const lines = [summary]
  if (firstFailure) {
    lines.push('', 'First failure:', firstFailure)
  }
  return { type: 'text' as const, value: lines.join('\n') }
}
```

- [ ] **Step 2: Add the `voiceTests` Command export**

Edit `src/cli/src/commands/voice/index.ts`. Append after the existing `voiceLogs` block:

```typescript
export const voiceTests: Command = {
  type: 'local',
  name: 'voice-tests',
  description: 'Run the voice-agent pytest suite (optionally with -k filter)',
  isEnabled: () => isVoiceGrowthBookEnabled(),
  get isHidden() {
    return !isVoiceModeEnabled()
  },
  supportsNonInteractive: true,
  load: () => import('./tests.js'),
}
```

- [ ] **Step 3: Register `voiceTestsCommand` in the registry**

Edit `src/cli/src/commands.ts`. Find the existing block (around lines 80–88):

```typescript
const voiceLogsCommand = feature('VOICE_MODE')
  ? require('./commands/voice/index.js').voiceLogs
  : null
```

Append immediately after it:

```typescript
const voiceTestsCommand = feature('VOICE_MODE')
  ? require('./commands/voice/index.js').voiceTests
  : null
```

Then find the existing spread at line ~336:

```typescript
  ...(voiceLogsCommand ? [voiceLogsCommand] : []),
```

Append immediately after it:

```typescript
  ...(voiceTestsCommand ? [voiceTestsCommand] : []),
```

- [ ] **Step 4: Boot the CLI to verify import + command surface**

Run:

```bash
cd src/cli && bun ./scripts/run-cli.mjs --help 2>&1 | head -20
```

Expected: CLI boots without import errors. (Help output may not list voice-tests since it's gated behind `VOICE_MODE` + GrowthBook + voice-mode-enabled, but the import has to succeed.)

If you can run an interactive session: invoke `/voice-tests -k consolidator` → should return `24 passed in <Xs>` (or similar) per the consolidator commits already on this branch.

- [ ] **Step 5: Commit**

```bash
git add src/cli/src/commands/voice/tests.ts \
        src/cli/src/commands/voice/index.ts \
        src/cli/src/commands.ts
git commit -m "feat(cli): add /voice-tests slash command (pytest smart-summary)"
```

---

## Task 3: Pure `formatStatus` + tests

**Files:**
- Create: `src/cli/src/commands/voice/formatStatus.ts`
- Create: `src/cli/src/commands/voice/formatStatus.test.ts`

A pure formatter that takes structured status inputs and returns the multi-line text the user sees. Tests cover every cell of the (voice × bridge × age) matrix that matters.

- [ ] **Step 1: Write the failing tests**

Create `src/cli/src/commands/voice/formatStatus.test.ts`:

```typescript
import { describe, expect, test } from 'bun:test'
import { formatVoiceStatus } from './formatStatus.js'

describe('formatVoiceStatus', () => {
  test('happy path — both active, last turn old', () => {
    const out = formatVoiceStatus({
      voice: 'active',
      bridge: 'active',
      lastTurnAt: '2026-05-09T12:00:00Z',
      nowEpochMs: Date.parse('2026-05-09T12:10:00Z'),
    })
    expect(out).toContain('voice-agent: active')
    expect(out).toContain('bridge:      active')
    expect(out).toContain('last turn:   2026-05-09T12:00:00Z (10m 0s ago)')
    expect(out).not.toContain('WARNING')
  })

  test('warns when last turn within 60s', () => {
    const out = formatVoiceStatus({
      voice: 'active',
      bridge: 'active',
      lastTurnAt: '2026-05-09T12:09:30Z',
      nowEpochMs: Date.parse('2026-05-09T12:10:00Z'),
    })
    expect(out).toContain('(30s ago)')
    expect(out).toContain(
      "WARNING: <60s since last turn — voice session may be active. Don't restart without asking.",
    )
  })

  test('voice inactive, bridge active', () => {
    const out = formatVoiceStatus({
      voice: 'inactive',
      bridge: 'active',
      lastTurnAt: null,
      nowEpochMs: Date.parse('2026-05-09T12:10:00Z'),
    })
    expect(out).toContain('voice-agent: inactive')
    expect(out).toContain('bridge:      active')
    expect(out).toContain('last turn:   no telemetry yet')
    expect(out).not.toContain('WARNING')
  })

  test('voice unknown (systemctl missing)', () => {
    const out = formatVoiceStatus({
      voice: 'unknown',
      bridge: 'unknown',
      lastTurnAt: '2026-05-09T11:00:00Z',
      nowEpochMs: Date.parse('2026-05-09T12:10:00Z'),
    })
    expect(out).toContain('voice-agent: unknown')
    expect(out).toContain('bridge:      unknown')
  })

  test('lastTurnAt invalid → unknown', () => {
    const out = formatVoiceStatus({
      voice: 'active',
      bridge: 'active',
      lastTurnAt: 'not a date',
      nowEpochMs: Date.parse('2026-05-09T12:10:00Z'),
    })
    expect(out).toContain('last turn:   unknown (could not parse timestamp)')
  })

  test('age formatting — hours and minutes', () => {
    const out = formatVoiceStatus({
      voice: 'active',
      bridge: 'active',
      lastTurnAt: '2026-05-09T10:30:15Z',
      nowEpochMs: Date.parse('2026-05-09T12:10:00Z'),
    })
    expect(out).toContain('(1h 39m 45s ago)')
  })

  test('age formatting — exactly 60s does NOT warn', () => {
    // 60s exactly is NOT a warning (we treat <60s as the threshold).
    const out = formatVoiceStatus({
      voice: 'active',
      bridge: 'active',
      lastTurnAt: '2026-05-09T12:09:00Z',
      nowEpochMs: Date.parse('2026-05-09T12:10:00Z'),
    })
    expect(out).toContain('(60s ago)')
    expect(out).not.toContain('WARNING')
  })

  test('age formatting — 59s warns', () => {
    const out = formatVoiceStatus({
      voice: 'active',
      bridge: 'active',
      lastTurnAt: '2026-05-09T12:09:01Z',
      nowEpochMs: Date.parse('2026-05-09T12:10:00Z'),
    })
    expect(out).toContain('(59s ago)')
    expect(out).toContain('WARNING')
  })
})
```

- [ ] **Step 2: Run tests to verify they fail**

Run:

```bash
cd src/cli && bun test src/commands/voice/formatStatus.test.ts 2>&1 | tail -5
```

Expected: 8 fails — module not found.

- [ ] **Step 3: Implement the formatter**

Create `src/cli/src/commands/voice/formatStatus.ts`:

```typescript
/**
 * Pure formatter for /voice-status output.
 *
 * Mirrors .claude/hooks/SessionStart.sh's reporting shape: voice + bridge
 * service status, last-turn timestamp + age, plus a <60s warning to
 * discourage restarting mid-session.
 */

export type ServiceState = 'active' | 'inactive' | 'failed' | 'unknown'

export interface VoiceStatusInputs {
  voice: ServiceState
  bridge: ServiceState
  /** ISO-8601 timestamp string from turn_telemetry.db, or null if no telemetry. */
  lastTurnAt: string | null
  /** Wall-clock epoch ms at format time. Injected for tests. */
  nowEpochMs: number
}

const ACTIVE_SESSION_THRESHOLD_S = 60

function formatAge(seconds: number): string {
  if (seconds < 0) return '0s'
  const h = Math.floor(seconds / 3600)
  const m = Math.floor((seconds % 3600) / 60)
  const s = seconds % 60
  if (h > 0) return `${h}h ${m}m ${s}s`
  if (m > 0) return `${m}m ${s}s`
  return `${s}s`
}

function formatLastTurn(
  lastTurnAt: string | null,
  nowEpochMs: number,
): { line: string; ageSeconds: number | null } {
  if (lastTurnAt === null) {
    return { line: 'no telemetry yet', ageSeconds: null }
  }
  const parsed = Date.parse(lastTurnAt)
  if (Number.isNaN(parsed)) {
    return { line: 'unknown (could not parse timestamp)', ageSeconds: null }
  }
  const ageSec = Math.max(0, Math.floor((nowEpochMs - parsed) / 1000))
  return {
    line: `${lastTurnAt} (${formatAge(ageSec)} ago)`,
    ageSeconds: ageSec,
  }
}

export function formatVoiceStatus(inputs: VoiceStatusInputs): string {
  const { voice, bridge, lastTurnAt, nowEpochMs } = inputs
  const turn = formatLastTurn(lastTurnAt, nowEpochMs)
  const lines = [
    `voice-agent: ${voice}`,
    `bridge:      ${bridge}`,
    `last turn:   ${turn.line}`,
  ]
  if (turn.ageSeconds !== null && turn.ageSeconds < ACTIVE_SESSION_THRESHOLD_S) {
    lines.push(
      `WARNING: <60s since last turn — voice session may be active. Don't restart without asking.`,
    )
  }
  return lines.join('\n')
}
```

- [ ] **Step 4: Run tests to verify they pass**

Run:

```bash
cd src/cli && bun test src/commands/voice/formatStatus.test.ts 2>&1 | tail -5
```

Expected: `8 pass, 0 fail`.

- [ ] **Step 5: Commit**

```bash
git add src/cli/src/commands/voice/formatStatus.ts \
        src/cli/src/commands/voice/formatStatus.test.ts
git commit -m "feat(cli): add pure formatVoiceStatus helper + 8 tests"
```

---

## Task 4: `/voice-status` command + registry wiring

**Files:**
- Create: `src/cli/src/commands/voice/status.ts`
- Modify: `src/cli/src/commands/voice/index.ts`
- Modify: `src/cli/src/commands.ts`

Spawn `systemctl is-active` for both services + `sqlite3` for last-turn timestamp, hand the result to the pure formatter.

- [ ] **Step 1: Create the command implementation**

Create `src/cli/src/commands/voice/status.ts`:

```typescript
import { execFile } from 'node:child_process'
import { access } from 'node:fs/promises'
import * as os from 'node:os'
import * as path from 'node:path'
import { promisify } from 'node:util'

import { logEvent } from '../../services/analytics/index.js'
import type { LocalCommandCall } from '../../types/command.js'
import {
  formatVoiceStatus,
  type ServiceState,
} from './formatStatus.js'

const execFileAsync = promisify(execFile)

const TELEMETRY_DB = path.join(
  os.homedir(),
  '.local',
  'share',
  'jarvis',
  'turn_telemetry.db',
)

const SYSTEMCTL_TIMEOUT_MS = 3000
const SQLITE_TIMEOUT_MS = 3000

async function isActive(unit: string): Promise<ServiceState> {
  try {
    const { stdout } = await execFileAsync(
      'systemctl',
      ['--user', 'is-active', unit],
      { timeout: SYSTEMCTL_TIMEOUT_MS },
    )
    const s = stdout.trim()
    if (s === 'active' || s === 'inactive' || s === 'failed') return s
    return 'unknown'
  } catch (err) {
    const e = err as NodeJS.ErrnoException & { stdout?: string }
    // systemctl is-active exits non-zero for inactive/failed but still
    // prints the status to stdout.
    const s = (e.stdout ?? '').trim()
    if (s === 'inactive' || s === 'failed' || s === 'active') return s
    return 'unknown'
  }
}

async function readLastTurn(): Promise<string | null> {
  try {
    await access(TELEMETRY_DB)
  } catch {
    return null
  }
  try {
    const { stdout } = await execFileAsync(
      'sqlite3',
      [
        TELEMETRY_DB,
        'SELECT ts_utc FROM turns ORDER BY ts_utc DESC LIMIT 1',
      ],
      { timeout: SQLITE_TIMEOUT_MS },
    )
    const ts = stdout.trim()
    return ts.length > 0 ? ts : null
  } catch {
    return null
  }
}

export const call: LocalCommandCall = async () => {
  const [voice, bridge, lastTurnAt] = await Promise.all([
    isActive('jarvis-voice-agent.service'),
    isActive('jarvis-bridge.service'),
    readLastTurn(),
  ])

  const text = formatVoiceStatus({
    voice,
    bridge,
    lastTurnAt,
    nowEpochMs: Date.now(),
  })

  logEvent('tengu_voice_status_checked', {
    voiceActive: voice === 'active',
    bridgeActive: bridge === 'active',
    sessionActive: text.includes('WARNING'),
  })

  return { type: 'text' as const, value: text }
}
```

- [ ] **Step 2: Add the `voiceStatus` Command export**

Edit `src/cli/src/commands/voice/index.ts`. Append after the `voiceTests` block from Task 2:

```typescript
export const voiceStatus: Command = {
  type: 'local',
  name: 'voice-status',
  description: 'Show voice-agent + bridge service status and last-turn age',
  isEnabled: () => isVoiceGrowthBookEnabled(),
  get isHidden() {
    return !isVoiceModeEnabled()
  },
  supportsNonInteractive: true,
  load: () => import('./status.js'),
}
```

- [ ] **Step 3: Register `voiceStatusCommand` in the registry**

Edit `src/cli/src/commands.ts`. Append after the `voiceTestsCommand` declaration block from Task 2:

```typescript
const voiceStatusCommand = feature('VOICE_MODE')
  ? require('./commands/voice/index.js').voiceStatus
  : null
```

Append after the `voiceTestsCommand` spread entry:

```typescript
  ...(voiceStatusCommand ? [voiceStatusCommand] : []),
```

- [ ] **Step 4: Boot the CLI to verify import succeeds**

Run:

```bash
cd src/cli && bun ./scripts/run-cli.mjs --help 2>&1 | head -20
```

Expected: CLI boots without import errors.

- [ ] **Step 5: Commit**

```bash
git add src/cli/src/commands/voice/status.ts \
        src/cli/src/commands/voice/index.ts \
        src/cli/src/commands.ts
git commit -m "feat(cli): add /voice-status slash command (systemd + telemetry probe)"
```

---

## Task 5: `voice-log-analyzer` project agent

**Files:**
- Create: `.jarvis/agents/voice-log-analyzer.md`

The CLI's existing agent loader auto-discovers project-level agents from `<repo-root>/.jarvis/agents/<name>.md`. Adding the file is sufficient — no other wiring.

- [ ] **Step 1: Create the agent file**

Create `.jarvis/agents/voice-log-analyzer.md` (the directory may not exist yet — create it):

```markdown
---
name: voice-log-analyzer
description: "Use when JARVIS is misbehaving and the symptom is in logs — 'JARVIS is silent' / 'saying gibberish' / 'wrong specialist' / 'TTS leaking protocol shapes' / 'breaker open.' Parses ~/.local/share/jarvis/logs/voice-agent.log + telemetry DB, finds the failing pattern, names a likely root cause. Phase-1 only — does NOT propose fixes."
tools: Bash, Read, Grep
color: yellow
---

You are a Phase-1 log analyzer for the JARVIS voice agent. Your job is to look at logs and telemetry, identify the failing pattern, and name the most likely root cause. You do **NOT** propose fixes — that's a separate phase. End your output with a one-line root-cause hypothesis.

## Where to look

- **JSON application log:** `~/.local/share/jarvis/logs/voice-agent.log` — one JSON object per line with `timestamp`, `level`, `message`, `name`, `pid`, `job_id`, `room_id`. Use `grep '"level": "ERROR"'` etc. and parse with `python3 -c "import sys,json; ..."`.
- **Rotated archives:** `~/.local/share/jarvis/logs/voice-agent.log.<stamp>.gz` — search with `zgrep`.
- **Telemetry DB:** `~/.local/share/jarvis/turn_telemetry.db` — table `turns` with columns `ts_utc, user_text, jarvis_text, route, llm_used, voice_used, ttfw_ms, total_audio_ms, route_fallback, notes, specialist, interrupted, input_tokens, output_tokens, cost_usd, context_pressure`.
- **Systemd journal:** `journalctl --user -u jarvis-voice-agent.service` — service start/stop, kill events. Application errors go to the JSON log, NOT the journal.
- **Structured status:** if Bash systemctl access is restricted, call the CLI's `VoiceAgentStatusTool` for a typed status snapshot.

## Patterns to recognize

- **"Silent" failure:** No assistant text in the most recent N turns, but user transcripts are landing — usually a specialist tool-gate refusal loop or a STAY-IN-SUPERVISOR violation.
- **"Gibberish":** TTS-leaking protocol shapes (`task_done(...)`, `<function>...</function>`, JSON arrays in text content) — sanitizers/pycall.py is the pinch point.
- **"Wrong specialist":** Supervisor transferring to specialist for conversational input — STAY-IN-SUPERVISOR rule violation.
- **"Breaker open":** Circuit breaker tripped on an LLM provider — see `circuit_breaker` log lines, FallbackAdapter cascade.
- **Confab drops:** Real assistant turns rejected by `confab_detector` — check `_has_recent_extraction_evidence` window and `_SAVE_CLAIM_RE` gate.

## What to report

1. **Symptom you observed** (one sentence, with evidence: 3-5 most relevant log lines or telemetry rows).
2. **Pattern matched** (which of the recognized patterns above; or "novel" if none).
3. **Likely root cause** (one sentence, with file path + function name where the issue most plausibly lives).

Do NOT propose code changes. Do NOT modify state.
```

- [ ] **Step 2: Verify the agent file parses**

Run:

```bash
cd /home/ulrich/Documents/Projects/jarvis
ls .jarvis/agents/voice-log-analyzer.md
head -10 .jarvis/agents/voice-log-analyzer.md
```

Expected: file exists; first 10 lines show the YAML frontmatter (`---` ... `---`) and the start of the body.

- [ ] **Step 3: Commit**

```bash
git add .jarvis/agents/voice-log-analyzer.md
git commit -m "feat(cli-agents): add voice-log-analyzer project agent"
```

---

## Task 6: Final verification

**Files:** none modified.

- [ ] **Step 1: Run all new unit tests together**

```bash
cd src/cli && bun test src/commands/voice/ 2>&1 | tail -10
```

Expected: `20 pass, 0 fail` (9 from `parsePytestSummary` + 11 from `formatStatus`). Original plan called for 16; review-driven additions added 4 (xfailed token coverage, `failed` ServiceState coverage, `sqlite3Missing` formatter input, sessionActive structured flag).

- [ ] **Step 2: Boot the CLI**

```bash
cd src/cli && bun ./scripts/run-cli.mjs --help 2>&1 | head -20
```

Expected: CLI starts without import errors.

- [ ] **Step 3: Manual smoke (interactive)**

In an interactive jarvis-CLI session (`bin/jarvis`):

1. `/voice-status` — should print three lines (voice-agent / bridge / last turn) and possibly a WARNING.
2. `/voice-tests -k consolidator` — should return `24 passed in <X>s` (the consolidator suite is on this branch).
3. Trigger the agent: send a message like "JARVIS has been silent for the last 5 minutes — can you check?" and confirm the `voice-log-analyzer` agent is dispatched (the CLI's agent picker should match on the description).

If steps 1–2 work and the agent is at least visible in `/agents` listing, ship it.

- [ ] **Step 4: Push the branch**

```bash
cd /home/ulrich/Documents/Projects/jarvis && git push 2>&1 | tail -5
```

---

## What we built

| Surface | Output |
|:--|:--|
| New TS files | `parsePytestSummary.ts`, `formatStatus.ts`, `tests.ts`, `status.ts` (~250 lines total) |
| New unit tests | 16 (`bun:test`) |
| Modified | `commands/voice/index.ts`, `commands.ts` |
| New agent | `.jarvis/agents/voice-log-analyzer.md` |
| Behavior | `/voice-tests [args]` → smart pytest summary; `/voice-status` → systemd + telemetry probe with 60s session warning; `voice-log-analyzer` agent dispatched on diagnosis-shaped prompts. |

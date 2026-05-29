import { execFile } from 'node:child_process'
import { access } from 'node:fs/promises'
import * as os from 'node:os'
import * as path from 'node:path'
import { promisify } from 'node:util'
import { fileURLToPath } from 'node:url'

import { logEvent } from '../../services/analytics/index.js'
import type { LocalCommandCall } from '../../types/command.js'
import { parsePytestSummary } from './parsePytestSummary.js'

const execFileAsync = promisify(execFile)

const PYTEST_TIMEOUT_MS = 120_000
const PYTEST_MAX_BUFFER = 10 * 1024 * 1024 // 10 MB

function resolveVoiceAgentPath(): string {
  // 1. Explicit override
  const env = process.env.JARVIS_VOICE_AGENT_PATH
  if (env && env.length > 0) return env

  // 2. Derive from this file's location:
  //    src/cli/src/commands/voice/tests.ts → ../../../../ = src/
  //    then voice-agent sibling of cli/
  try {
    const thisFile = fileURLToPath(import.meta.url)
    // thisFile = <repo>/src/cli/src/commands/voice/tests.ts (or .js after build)
    // Four levels up reaches <repo>/src/
    const repoSrc = path.resolve(path.dirname(thisFile), '..', '..', '..', '..')
    const derived = path.join(repoSrc, 'voice-agent')
    return derived
  } catch {
    // import.meta.url unavailable (e.g. CommonJS fallback) — skip to homedir
  }

  // 3. Last resort: well-known homedir layout
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
function splitArgs(input: string): string[] {
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
  let bufferOverflow = false

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
    if (e.code === 'ERR_CHILD_PROCESS_STDIO_MAXBUFFER') {
      stdout = (e.stdout ?? '') + '\n[output truncated at 10 MB]'
      stderr = e.stderr ?? ''
      bufferOverflow = true
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
        `Pytest exceeded ${PYTEST_TIMEOUT_MS / 1000}s timeout (still running). ` +
        `Run manually: cd ${vaPath} && .venv/bin/python -m pytest tests/`,
    }
  }

  const combined = stdout + (stderr ? `\n${stderr}` : '')
  const { summary, firstFailure } = parsePytestSummary(combined)
  const passed = exitCode === 0 && summary !== null && !/\bfailed\b|\berror\b/i.test(summary)

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

  const truncNote = bufferOverflow ? '[output truncated at 10 MB]\n' : ''

  if (passed) {
    return { type: 'text' as const, value: truncNote + summary }
  }

  const lines = [summary]
  if (firstFailure) {
    lines.push('', 'First failure:', firstFailure)
  }
  return { type: 'text' as const, value: truncNote + lines.join('\n') }
}

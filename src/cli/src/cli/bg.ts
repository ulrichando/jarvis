/* eslint-disable custom-rules/no-process-exit */
/**
 * Background session management for `jarvis ps|logs|attach|kill|--bg`.
 * Reads the PID registry in ~/.jarvis/sessions/ and dispatches to tmux.
 * Only loaded when feature('BG_SESSIONS') is true (gated in cli.tsx).
 */
import { access, mkdir, readdir, readFile } from 'fs/promises'
import { homedir } from 'os'
import { join, resolve } from 'path'
import { execFileSync, spawn } from 'child_process'

import { cliError } from './exit.js'
import { getClaudeConfigHomeDir } from '../utils/envUtils.js'
import { isProcessRunning } from '../utils/genericProcessUtils.js'
import { jsonParse } from '../utils/slowOperations.js'

// ── Types ─────────────────────────────────────────────────────────────────

type SessionKind = 'interactive' | 'bg' | 'daemon' | 'daemon-worker'
type SessionStatus = 'busy' | 'idle' | 'waiting'

interface SessionRecord {
  pid: number
  sessionId: string
  cwd: string
  startedAt: number
  kind: SessionKind
  entrypoint?: string
  name?: string
  logPath?: string
  agent?: string
  status?: SessionStatus
  waitingFor?: string
  updatedAt?: number
}

// ── Helpers ───────────────────────────────────────────────────────────────

function getSessionsDir(): string {
  return join(getClaudeConfigHomeDir(), 'sessions')
}

async function readAllSessions(): Promise<Array<SessionRecord & { alive: boolean }>> {
  const dir = getSessionsDir()
  let files: string[]
  try {
    files = await readdir(dir)
  } catch {
    return []
  }

  const sessions: Array<SessionRecord & { alive: boolean }> = []
  for (const file of files) {
    if (!/^\d+\.json$/.test(file)) continue
    try {
      const raw = await readFile(join(dir, file), 'utf8')
      const rec = jsonParse(raw) as SessionRecord
      sessions.push({ ...rec, alive: isProcessRunning(rec.pid) })
    } catch {
      // corrupted or race-deleted — skip
    }
  }
  return sessions
}

function findSession(
  sessions: Array<SessionRecord & { alive: boolean }>,
  identifier: string | undefined,
): SessionRecord & { alive: boolean } {
  if (!identifier) {
    const found = sessions.find(s => s.alive && s.kind === 'bg')
    if (!found) return cliError('No live background sessions. Use: jarvis ps')
    return found
  }
  const pid = parseInt(identifier, 10)
  if (!isNaN(pid)) {
    const found = sessions.find(s => s.pid === pid)
    if (!found) return cliError(`No session with PID ${pid}`)
    return found
  }
  const found = sessions.find(
    s => s.name === identifier || s.sessionId?.startsWith(identifier),
  )
  if (!found) return cliError(`No session matching '${identifier}'`)
  return found
}

function fmtAge(startedAt: number): string {
  const s = Math.floor((Date.now() - startedAt) / 1000)
  if (s < 60) return `${s}s`
  if (s < 3600) return `${Math.floor(s / 60)}m`
  return `${Math.floor(s / 3600)}h`
}

// Derive the jarvis launcher path from the running process. Works for both
// source-run (bun + cli.tsx) and compiled binary paths.
function getJarvisBin(): string {
  if (process.env.JARVIS_BIN) return process.env.JARVIS_BIN
  const entry = process.argv[1] ?? ''
  const marker = '/src/cli/src/entrypoints/'
  if (entry.includes(marker)) {
    const repoRoot = entry.split(marker)[0]!
    return join(repoRoot, 'bin', 'jarvis')
  }
  // Compiled binary: re-invoke the binary itself
  return resolve(entry || process.execPath)
}

// ── Public handlers ───────────────────────────────────────────────────────

export async function psHandler(_args: string[]): Promise<void> {
  const sessions = await readAllSessions()
  const live = sessions.filter(s => s.alive)

  if (live.length === 0) {
    process.stdout.write('No active Jarvis sessions.\n')
    return
  }

  const headers = ['PID', 'KIND', 'STATUS', 'NAME', 'CWD', 'AGE']
  const rows = live.map(s => [
    String(s.pid),
    s.kind,
    s.status ?? 'idle',
    s.name ?? '-',
    s.cwd.replace(homedir(), '~'),
    fmtAge(s.startedAt),
  ])
  const widths = headers.map((h, i) =>
    Math.max(h.length, ...rows.map(r => (r[i] ?? '').length)),
  )
  const fmt = (cols: string[]) =>
    cols.map((c, i) => c.padEnd(widths[i]!)).join('  ')

  process.stdout.write(fmt(headers) + '\n')
  process.stdout.write(widths.map(w => '-'.repeat(w)).join('  ') + '\n')
  for (const row of rows) {
    process.stdout.write(fmt(row) + '\n')
  }
}

export async function logsHandler(identifier: string | undefined): Promise<void> {
  const sessions = await readAllSessions()
  const session = findSession(sessions, identifier)

  if (!session.logPath) {
    return cliError(`Session ${session.pid} has no log path recorded`)
  }
  try {
    await access(session.logPath)
  } catch {
    return cliError(`Log file not found: ${session.logPath}`)
  }

  const tail = spawn('tail', ['-f', session.logPath], { stdio: 'inherit' })
  await new Promise<void>(res => {
    tail.on('close', res)
    process.on('SIGINT', () => {
      tail.kill()
      res()
    })
  })
}

export async function attachHandler(identifier: string | undefined): Promise<void> {
  const sessions = await readAllSessions()
  const session = findSession(sessions, identifier)

  if (session.kind !== 'bg') {
    return cliError(
      `Session ${session.pid} is kind '${session.kind}', not 'bg'. ` +
        `Only background sessions have a tmux pane to attach to.`,
    )
  }

  const tmuxName = session.name ?? `jarvis-bg-${session.pid}`
  try {
    execFileSync('tmux', ['has-session', '-t', tmuxName], { stdio: 'ignore' })
  } catch {
    return cliError(
      `tmux session '${tmuxName}' not found — the session may have already exited`,
    )
  }

  const tmux = spawn('tmux', ['attach-session', '-t', tmuxName], {
    stdio: 'inherit',
  })
  await new Promise<void>(res => {
    tmux.on('close', res)
  })
}

export async function killHandler(identifier: string | undefined): Promise<void> {
  const sessions = await readAllSessions()
  const session = findSession(sessions, identifier)

  if (!session.alive) {
    return cliError(`Session ${session.pid} is not running`)
  }
  try {
    process.kill(session.pid, 'SIGTERM')
    process.stdout.write(
      `Sent SIGTERM to session ${session.pid} (${session.name ?? 'unnamed'})\n`,
    )
  } catch (e) {
    return cliError(
      `Failed to kill session ${session.pid}: ${e instanceof Error ? e.message : String(e)}`,
    )
  }
}

export async function handleBgFlag(args: string[]): Promise<void> {
  // Strip --bg / --background, keep the rest as the jarvis invocation args
  const rest = args.filter(a => a !== '--bg' && a !== '--background')

  // Optional --name/-n flag
  let name = ''
  const ni = rest.findIndex(a => a === '--name' || a === '-n')
  if (ni !== -1 && rest[ni + 1]) {
    name = rest[ni + 1]!
    rest.splice(ni, 2)
  }

  const ts = Date.now()
  if (!name) name = `bg-${ts}`
  const tmuxName = `jarvis-${name}`

  const logDir = join(getClaudeConfigHomeDir(), 'logs', 'bg-sessions')
  const logPath = join(logDir, `${name}-${ts}.log`)
  await mkdir(logDir, { recursive: true, mode: 0o700 })

  const bin = getJarvisBin()
  // Shell-quote each arg (single-quote escaping)
  const shellQuote = (s: string) => `'${s.replace(/'/g, "'\\''")}'`
  const jarvisCmd = [bin, ...rest].map(shellQuote).join(' ')
  // Tee to log so `jarvis logs` works after detach
  const shellCmd = `${jarvisCmd} 2>&1 | tee ${shellQuote(logPath)}`

  const env: Record<string, string> = {
    ...(process.env as Record<string, string>),
    CLAUDE_CODE_SESSION_KIND: 'bg',
    CLAUDE_CODE_SESSION_NAME: tmuxName,
    CLAUDE_CODE_SESSION_LOG: logPath,
  }
  // Unset TMUX so the child process can create its own tmux session
  delete env['TMUX']

  try {
    execFileSync(
      'tmux',
      ['new-session', '-d', '-s', tmuxName, '--', 'bash', '-c', shellCmd],
      { env, stdio: 'ignore' },
    )
  } catch (e) {
    return cliError(
      `Failed to start tmux session '${tmuxName}': ${e instanceof Error ? e.message : String(e)}\n` +
        `Is tmux installed? Try: which tmux`,
    )
  }

  process.stdout.write(
    [
      `Started background session '${tmuxName}'`,
      `  Logs:   ${logPath}`,
      `  Attach: jarvis attach ${tmuxName}`,
      `  Kill:   jarvis kill ${tmuxName}`,
      `  List:   jarvis ps`,
      '',
    ].join('\n'),
  )
}

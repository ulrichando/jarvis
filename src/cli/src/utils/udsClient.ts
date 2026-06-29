import { readdir, readFile } from 'fs/promises'
import { createConnection } from 'net'
import { join } from 'path'
import { getClaudeConfigHomeDir } from './envUtils.js'
import { isProcessRunning } from './genericProcessUtils.js'
import { jsonParse, jsonStringify } from './slowOperations.js'

export type LiveSession = {
  pid: number
  sessionId?: string
  cwd?: string
  startedAt?: number
  kind?: string
  name?: string
  messagingSocketPath?: string
  bridgeSessionId?: string
}

function asString(value: unknown): string | undefined {
  return typeof value === 'string' && value.length > 0 ? value : undefined
}

function asNumber(value: unknown): number | undefined {
  return typeof value === 'number' && Number.isFinite(value) ? value : undefined
}

function parseLiveSession(value: unknown): LiveSession | null {
  if (!value || typeof value !== 'object') return null
  const data = value as Record<string, unknown>
  const pid = asNumber(data.pid)
  if (!pid || !isProcessRunning(pid)) return null
  return {
    pid,
    sessionId: asString(data.sessionId),
    cwd: asString(data.cwd),
    startedAt: asNumber(data.startedAt),
    kind: asString(data.kind),
    name: asString(data.name),
    messagingSocketPath: asString(data.messagingSocketPath),
    bridgeSessionId: asString(data.bridgeSessionId),
  }
}

export async function sendToUdsSocket(
  socketPath: string,
  message: string,
): Promise<void> {
  await new Promise<void>((resolve, reject) => {
    const socket = createConnection(socketPath)
    let settled = false
    const settle = (err?: Error) => {
      if (settled) return
      settled = true
      err ? reject(err) : resolve()
    }
    socket.once('error', settle)
    socket.once('connect', () => {
      socket.end(jsonStringify({ message }))
    })
    socket.once('close', hadError => {
      if (!hadError) settle()
    })
  })
}

export async function listAllLiveSessions(): Promise<LiveSession[]> {
  const dir = join(getClaudeConfigHomeDir(), 'sessions')
  let files: string[]
  try {
    files = await readdir(dir)
  } catch {
    return []
  }

  const sessions: LiveSession[] = []
  for (const file of files) {
    if (!/^\d+\.json$/.test(file)) continue
    try {
      const parsed = parseLiveSession(jsonParse(await readFile(join(dir, file), 'utf8')))
      if (parsed) sessions.push(parsed)
    } catch {
      // Ignore malformed or concurrently-written registry files.
    }
  }

  sessions.sort((a, b) => (b.startedAt ?? 0) - (a.startedAt ?? 0))
  return sessions
}

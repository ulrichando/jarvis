import { mkdir, unlink } from 'fs/promises'
import { createServer, type Server, type Socket } from 'net'
import { tmpdir } from 'os'
import { dirname, join } from 'path'
import { getSessionId } from '../bootstrap/state.js'
import { registerCleanup } from './cleanupRegistry.js'
import { logForDebugging } from './debug.js'
import { enqueue } from './messageQueueManager.js'
import { jsonParse } from './slowOperations.js'

type OnEnqueue = (message: string) => void | Promise<void>

const DEFAULT_BIND_TIMEOUT_MS = 500

let server: Server | null = null
let socketPath: string | undefined
let unregisterCleanup: (() => void) | undefined
let onEnqueue: OnEnqueue | null = null

export function setOnEnqueue(fn: OnEnqueue | null): void {
  onEnqueue = fn
}

export function getDefaultUdsSocketPath(): string {
  return join(tmpdir(), `jarvis-${process.pid}.sock`)
}

export function getUdsMessagingSocketPath(): string | undefined {
  return socketPath
}

function parseInboundMessage(raw: string): string {
  const trimmed = raw.trim()
  if (!trimmed) return ''
  try {
    const parsed = jsonParse(trimmed) as unknown
    if (
      parsed &&
      typeof parsed === 'object' &&
      'message' in parsed &&
      typeof (parsed as { message?: unknown }).message === 'string'
    ) {
      return (parsed as { message: string }).message
    }
  } catch {
    // Raw text is accepted for compatibility with simple clients.
  }
  return raw
}

function handleSocket(socket: Socket): void {
  const chunks: Buffer[] = []
  socket.on('data', chunk => {
    chunks.push(Buffer.isBuffer(chunk) ? chunk : Buffer.from(chunk))
  })
  socket.on('end', () => {
    const message = parseInboundMessage(Buffer.concat(chunks).toString('utf8'))
    if (!message.trim()) {
      socket.write('empty\n')
      return
    }
    enqueue({
      value: message,
      mode: 'prompt',
      priority: 'next',
      skipSlashCommands: true,
    })
    void onEnqueue?.(message)
    socket.write(`ok ${getSessionId()}\n`)
  })
}

async function closeServer(path: string): Promise<void> {
  const closing = server
  server = null
  socketPath = undefined
  if (closing) {
    await new Promise<void>(resolve => closing.close(() => resolve()))
  }
  await unlink(path).catch(() => {})
}

export async function startUdsMessaging(
  requestedPath = getDefaultUdsSocketPath(),
  opts: { isExplicit?: boolean } = {},
): Promise<void> {
  if (process.platform === 'win32') return
  if (server && socketPath === requestedPath) return
  if (server && socketPath) {
    await closeServer(socketPath)
  }

  await mkdir(dirname(requestedPath), { recursive: true, mode: 0o700 })
  await unlink(requestedPath).catch(() => {})

  const nextServer = createServer(handleSocket)
  try {
    await new Promise<void>((resolve, reject) => {
      let settled = false
      const finish = (fn: () => void) => {
        if (settled) return
        settled = true
        clearTimeout(timeout)
        nextServer.off('error', onError)
        fn()
      }
      const onError = (error: Error) => finish(() => reject(error))
      const timeout = setTimeout(
        () =>
          finish(() =>
            reject(
              new Error(
                `timed out binding UDS messaging socket at ${requestedPath}`,
              ),
            ),
          ),
        DEFAULT_BIND_TIMEOUT_MS,
      )
      nextServer.once('error', onError)
      nextServer.listen(requestedPath, () => {
        finish(resolve)
      })
    })
  } catch (error) {
    nextServer.close()
    if (opts.isExplicit) throw error
    logForDebugging(`[uds] disabled: ${String(error)}`)
    return
  }

  server = nextServer
  socketPath = requestedPath
  process.env.CLAUDE_CODE_MESSAGING_SOCKET = requestedPath
  unregisterCleanup = registerCleanup(async () => {
    if (socketPath === requestedPath) {
      await closeServer(requestedPath)
    }
    unregisterCleanup = undefined
  })
}

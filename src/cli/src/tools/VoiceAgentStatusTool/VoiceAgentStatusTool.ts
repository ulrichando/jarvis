import { execFile } from 'node:child_process'
import { readFile } from 'node:fs/promises'
import * as os from 'node:os'
import * as path from 'node:path'
import { promisify } from 'node:util'

import { z } from 'zod/v4'

import { buildTool, type ToolDef } from '../../Tool.js'
import { logForDebugging } from '../../utils/debug.js'
import { lazySchema } from '../../utils/lazySchema.js'

import { VOICE_AGENT_STATUS_TOOL_NAME } from './constants.js'
import { DESCRIPTION, PROMPT } from './prompt.js'

const execFileAsync = promisify(execFile)

const LOG_PATH = path.join(
  os.homedir(),
  '.local',
  'share',
  'jarvis',
  'logs',
  'voice-agent.log',
)

const MAX_LOG_LINES = 50

interface StatusInfo {
  status: 'running' | 'stopped' | 'failed' | 'unknown'
  uptime: string | null
  pid: number | null
  memory: string | null
  activeSince: string | null
}

async function getServiceStatus(): Promise<StatusInfo> {
  try {
    const { stdout } = await execFileAsync(
      'systemctl',
      ['--user', 'status', 'jarvis-voice-agent.service'],
      { timeout: 5000 },
    )

    const active = /Active:\s+(\w+)\s+\((.+)\)\s+since\s+(.+?)(?:\n|$)/.exec(stdout)
    const statusStr = active?.[1] ?? 'unknown'
    const activeSince = active?.[3] ?? null

    let status: StatusInfo['status'] = 'unknown'
    if (statusStr === 'active') status = 'running'
    else if (statusStr === 'inactive') status = 'stopped'
    else if (statusStr === 'failed') status = 'failed'

    const pidMatch = /Main PID:\s+(\d+)/.exec(stdout)
    const pid = pidMatch ? parseInt(pidMatch[1], 10) : null

    const memMatch = /Memory:\s+(.+?)(?:\n|$)/.exec(stdout)
    const memory = memMatch?.[1]?.trim() ?? null

    let uptime: string | null = null
    if (activeSince) {
      const since = new Date(activeSince)
      uptime = formatUptime(Date.now() - since.getTime())
    }

    return { status, uptime, pid, memory, activeSince }
  } catch (err) {
    logForDebugging(`[VoiceAgentStatus] systemctl error: ${String(err)}`)
    return { status: 'unknown', uptime: null, pid: null, memory: null, activeSince: null }
  }
}

async function getLogTail(lines: number): Promise<string[]> {
  try {
    const content = await readFile(LOG_PATH, 'utf-8')
    return content.split('\n').filter(Boolean).slice(-lines)
  } catch {
    return []
  }
}

function formatUptime(ms: number): string {
  const seconds = Math.floor(ms / 1000)
  if (seconds < 60) return `${seconds}s`
  const minutes = Math.floor(seconds / 60)
  if (minutes < 60) return `${minutes}m ${seconds % 60}s`
  const hours = Math.floor(minutes / 60)
  return `${hours}h ${minutes % 60}m`
}

const inputSchema = lazySchema(() =>
  z.strictObject({
    includeLogs: z
      .boolean()
      .optional()
      .describe(
        'When true, include the last log lines from the voice-agent log file. Default false.',
      ),
  }),
)
type InputSchema = ReturnType<typeof inputSchema>

const outputSchema = lazySchema(() =>
  z.object({
    status: z.enum(['running', 'stopped', 'failed', 'unknown']),
    uptime: z.string().nullable(),
    pid: z.number().nullable(),
    memory: z.string().nullable(),
    activeSince: z.string().nullable(),
    logs: z.array(z.string()).optional(),
  }),
)
type OutputSchema = ReturnType<typeof outputSchema>
export type Output = z.infer<OutputSchema>

export const VoiceAgentStatusTool = buildTool({
  name: VOICE_AGENT_STATUS_TOOL_NAME,
  searchHint: 'voice agent health check status service',
  maxResultSizeChars: 10_000,
  async description() {
    return DESCRIPTION
  },
  async prompt() {
    return PROMPT
  },
  get inputSchema(): InputSchema {
    return inputSchema()
  },
  get outputSchema(): OutputSchema {
    return outputSchema()
  },
  userFacingName() {
    return 'VoiceAgentStatus'
  },
  shouldDefer: true,
  isEnabled() {
    return true
  },
  isConcurrencySafe() {
    return true
  },
  isReadOnly() {
    return true
  },
  toAutoClassifierInput() {
    return ''
  },
  renderToolUseMessage() {
    return null
  },
  async call({ includeLogs }) {
    const status = await getServiceStatus()
    let logs: string[] | undefined
    if (includeLogs) {
      logs = await getLogTail(MAX_LOG_LINES)
    }
    return {
      data: { ...status, logs },
    }
  },
  mapToolResultToToolResultBlockParam(content, toolUseID) {
    const out = content as Output
    let text = `Voice agent: ${out.status.toUpperCase()}`
    if (out.pid) text += ` | PID: ${out.pid}`
    if (out.uptime) text += ` | Uptime: ${out.uptime}`
    if (out.memory) text += ` | Memory: ${out.memory}`
    if (out.activeSince) text += ` | Since: ${out.activeSince}`
    if (out.logs?.length) {
      text += `\n\nLast ${out.logs.length} log lines:\n${out.logs.join('\n')}`
    }
    return {
      tool_use_id: toolUseID,
      type: 'tool_result',
      content: text,
    }
  },
} satisfies ToolDef<InputSchema, Output>)

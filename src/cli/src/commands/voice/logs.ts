import { readFile } from 'node:fs/promises'
import * as os from 'node:os'
import * as path from 'node:path'

import { logEvent } from '../../services/analytics/index.js'
import type { LocalCommandCall } from '../../types/command.js'

const LOG_PATH = path.join(
  os.homedir(),
  '.local',
  'share',
  'jarvis',
  'logs',
  'voice-agent.log',
)

const DEFAULT_LINES = 20
const MAX_LINES = 100

function parseLineCount(args: string | undefined): number {
  if (!args) return DEFAULT_LINES
  const match = /(\d+)/.exec(args)
  if (!match) return DEFAULT_LINES
  const n = parseInt(match[1], 10)
  return Math.max(1, Math.min(n, MAX_LINES))
}

interface LogEntry {
  ts: string
  level: string
  msg: string
}

function parseLogLine(line: string): LogEntry | null {
  try {
    const entry = JSON.parse(line)
    return {
      ts: entry.timestamp ?? entry.ts ?? '',
      level: entry.level ?? entry.severity ?? 'INFO',
      msg: entry.message ?? entry.msg ?? line,
    }
  } catch {
    return { ts: '', level: '', msg: line }
  }
}

export const call: LocalCommandCall = async (args) => {
  const lineCount = parseLineCount(args)

  logEvent('tengu_voice_logs_viewed', { lines: lineCount })

  try {
    const content = await readFile(LOG_PATH, 'utf-8')
    const allLines = content.split('\n').filter(Boolean)
    const tail = allLines.slice(-lineCount)

    // Filter to ERROR and WARNING only
    const relevant = tail
      .map(parseLogLine)
      .filter(
        (entry): entry is LogEntry =>
          entry !== null &&
          (entry.level.toUpperCase().includes('ERROR') ||
            entry.level.toUpperCase().includes('WARN')),
      )

    if (relevant.length === 0) {
      return {
        type: 'text' as const,
        value: `No errors or warnings in the last ${tail.length} log lines (${allLines.length} total).`,
      }
    }

    const formatted = relevant
      .map(e => `[${e.ts}] ${e.level}: ${e.msg.slice(0, 200)}`)
      .join('\n')

    return {
      type: 'text' as const,
      value: `Last ${relevant.length} errors/warnings (of ${tail.length} recent lines, ${allLines.length} total):\n\n${formatted}`,
    }
  } catch {
    return {
      type: 'text' as const,
      value: `No voice-agent log found at ${LOG_PATH}. Is the voice agent running?`,
    }
  }
}

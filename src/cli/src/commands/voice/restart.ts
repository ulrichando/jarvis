import { execFile } from 'node:child_process'
import { access } from 'node:fs/promises'
import * as os from 'node:os'
import * as path from 'node:path'
import { promisify } from 'node:util'

import { logEvent } from '../../services/analytics/index.js'
import type { LocalCommandCall } from '../../types/command.js'

const execFileAsync = promisify(execFile)

const TELEMETRY_DB = path.join(
  os.homedir(),
  '.local',
  'share',
  'jarvis',
  'turn_telemetry.db',
)

async function checkActiveSession(): Promise<boolean> {
  try {
    try {
      await access(TELEMETRY_DB)
    } catch {
      return false
    }
    const { stdout } = await execFileAsync(
      'sqlite3',
      [TELEMETRY_DB, "SELECT ts_utc FROM turns ORDER BY ts_utc DESC LIMIT 1"],
      { timeout: 3000 },
    )
    const ts = stdout.trim()
    if (!ts) return false
    const lastTurn = new Date(ts)
    const ageMs = Date.now() - lastTurn.getTime()
    return ageMs < 60_000
  } catch {
    return false
  }
}

async function restartService(): Promise<{ success: boolean; message: string }> {
  try {
    await execFileAsync(
      'systemctl',
      ['--user', 'restart', 'jarvis-voice-agent.service'],
      { timeout: 10_000 },
    )

    // Wait briefly for the service to stabilize
    await new Promise(r => setTimeout(r, 2000))

    const { stdout } = await execFileAsync(
      'systemctl',
      ['--user', 'status', 'jarvis-voice-agent.service'],
      { timeout: 5000 },
    )

    const active = /Active:\s+(\w+)/.exec(stdout)
    const pid = /Main PID:\s+(\d+)/.exec(stdout)
    const mem = /Memory:\s+(.+?)(?:\n|$)/.exec(stdout)

    if (active?.[1] === 'active') {
      return {
        success: true,
        message: `Voice agent restarted — PID ${pid?.[1] ?? '?'}, memory ${mem?.[1]?.trim() ?? '?'}.`,
      }
    }
    return {
      success: false,
      message: `Service restarted but is now ${active?.[1] ?? 'unknown'} state.`,
    }
  } catch (err) {
    const msg = err instanceof Error ? err.message : String(err)
    return { success: false, message: `Restart failed: ${msg}` }
  }
}

export const call: LocalCommandCall = async (args) => {
  const isForced = args?.includes('--force')

  if (!isForced) {
    const active = await checkActiveSession()
    if (active) {
      return {
        type: 'text' as const,
        value:
          'Active voice session detected (turn within 60s). Use `/voice-restart --force` to restart anyway.',
      }
    }
  }

  logEvent('tengu_voice_restarted', { forced: isForced })
  const result = await restartService()

  return {
    type: 'text' as const,
    value: result.message,
  }
}

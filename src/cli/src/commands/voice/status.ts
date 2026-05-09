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

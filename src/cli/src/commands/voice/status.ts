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

interface IsActiveResult {
  state: ServiceState
  systemctlMissing: boolean
}

async function isActive(unit: string): Promise<IsActiveResult> {
  try {
    const { stdout } = await execFileAsync(
      'systemctl',
      ['--user', 'is-active', unit],
      { timeout: SYSTEMCTL_TIMEOUT_MS },
    )
    const s = stdout.trim()
    if (s === 'active' || s === 'inactive' || s === 'failed') {
      return { state: s, systemctlMissing: false }
    }
    return { state: 'unknown', systemctlMissing: false }
  } catch (err) {
    const e = err as NodeJS.ErrnoException & {
      stdout?: string
      code?: string | number
    }
    if (e.code === 'ENOENT') {
      return { state: 'unknown', systemctlMissing: true }
    }
    // systemctl is-active exits non-zero for inactive/failed but still
    // prints the status to stdout.
    const s = (e.stdout ?? '').trim()
    if (s === 'inactive' || s === 'failed') {
      return { state: s, systemctlMissing: false }
    }
    return { state: 'unknown', systemctlMissing: false }
  }
}

interface ReadLastTurnResult {
  value: string | null
  sqlite3Missing: boolean
}

async function readLastTurn(): Promise<ReadLastTurnResult> {
  try {
    await access(TELEMETRY_DB)
  } catch {
    return { value: null, sqlite3Missing: false }
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
    return { value: ts.length > 0 ? ts : null, sqlite3Missing: false }
  } catch (err) {
    const e = err as NodeJS.ErrnoException & { code?: string | number }
    if (e.code === 'ENOENT') {
      return { value: null, sqlite3Missing: true }
    }
    return { value: null, sqlite3Missing: false }
  }
}

export const call: LocalCommandCall = async () => {
  const [voiceResult, bridgeResult, turnResult] = await Promise.all([
    isActive('jarvis-voice-agent.service'),
    isActive('jarvis-bridge.service'),
    readLastTurn(),
  ])

  // If both services failed for the same reason (systemctl missing),
  // we're on a non-systemd host — say so explicitly.
  if (voiceResult.systemctlMissing && bridgeResult.systemctlMissing) {
    return {
      type: 'text' as const,
      value: 'systemctl not available — non-systemd host?',
    }
  }

  const result = formatVoiceStatus({
    voice: voiceResult.state,
    bridge: bridgeResult.state,
    lastTurnAt: turnResult.value,
    nowEpochMs: Date.now(),
    sqlite3Missing: turnResult.sqlite3Missing,
  })

  logEvent('tengu_voice_status_checked', {
    voiceActive: voiceResult.state === 'active',
    bridgeActive: bridgeResult.state === 'active',
    sessionActive: result.sessionActive,
  })

  return { type: 'text' as const, value: result.text }
}

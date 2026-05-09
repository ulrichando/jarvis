/**
 * Pure formatter for /voice-status output.
 *
 * Reports voice + bridge service status, last-turn timestamp + age, plus
 * a <60s warning to discourage restarting mid-session. Mirrors the
 * semantic content of .claude/hooks/SessionStart.sh's voice block (not
 * its exact bullet-list formatting — this output is plain text).
 */

export type ServiceState = 'active' | 'inactive' | 'failed' | 'unknown'

export interface VoiceStatusInputs {
  voice: ServiceState
  bridge: ServiceState
  /** ISO-8601 timestamp string from turn_telemetry.db, or null if no telemetry. */
  lastTurnAt: string | null
  /** Wall-clock epoch ms at format time. Injected for tests. */
  nowEpochMs: number
  /** When true, last-turn line is overridden with the sqlite3-missing message. */
  sqlite3Missing?: boolean
}

export interface VoiceStatusOutput {
  text: string
  sessionActive: boolean
}

const ACTIVE_SESSION_THRESHOLD_S = 60

function formatAge(seconds: number): string {
  if (seconds < 0) return '0s'
  const h = Math.floor(seconds / 3600)
  const m = Math.floor((seconds % 3600) / 60)
  const s = seconds % 60
  if (h > 0) return `${h}h ${m}m ${s}s`
  if (seconds >= 61) return `${m}m ${s}s`
  return `${seconds}s`
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

export function formatVoiceStatus(inputs: VoiceStatusInputs): VoiceStatusOutput {
  const { voice, bridge, lastTurnAt, nowEpochMs, sqlite3Missing } = inputs
  let lastTurnLine: string
  let ageSeconds: number | null
  if (sqlite3Missing) {
    lastTurnLine = 'unknown (sqlite3 not in PATH)'
    ageSeconds = null
  } else {
    const turn = formatLastTurn(lastTurnAt, nowEpochMs)
    lastTurnLine = turn.line
    ageSeconds = turn.ageSeconds
  }
  const sessionActive =
    ageSeconds !== null && ageSeconds < ACTIVE_SESSION_THRESHOLD_S
  const lines = [
    `voice-agent: ${voice}`,
    `bridge:      ${bridge}`,
    `last turn:   ${lastTurnLine}`,
  ]
  if (sessionActive) {
    lines.push(
      `WARNING: <60s since last turn — voice session may be active. Don't restart without asking.`,
    )
  }
  return { text: lines.join('\n'), sessionActive }
}

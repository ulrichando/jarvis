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

export function formatVoiceStatus(inputs: VoiceStatusInputs): string {
  const { voice, bridge, lastTurnAt, nowEpochMs } = inputs
  const turn = formatLastTurn(lastTurnAt, nowEpochMs)
  const lines = [
    `voice-agent: ${voice}`,
    `bridge:      ${bridge}`,
    `last turn:   ${turn.line}`,
  ]
  if (turn.ageSeconds !== null && turn.ageSeconds < ACTIVE_SESSION_THRESHOLD_S) {
    lines.push(
      `WARNING: <60s since last turn — voice session may be active. Don't restart without asking.`,
    )
  }
  return lines.join('\n')
}

import { describe, expect, test } from 'bun:test'
import { formatVoiceStatus } from './formatStatus.js'

describe('formatVoiceStatus', () => {
  test('happy path — both active, last turn old', () => {
    const out = formatVoiceStatus({
      voice: 'active',
      bridge: 'active',
      lastTurnAt: '2026-05-09T12:00:00Z',
      nowEpochMs: Date.parse('2026-05-09T12:10:00Z'),
    })
    expect(out).toContain('voice-agent: active')
    expect(out).toContain('bridge:      active')
    expect(out).toContain('last turn:   2026-05-09T12:00:00Z (10m 0s ago)')
    expect(out).not.toContain('WARNING')
  })

  test('warns when last turn within 60s', () => {
    const out = formatVoiceStatus({
      voice: 'active',
      bridge: 'active',
      lastTurnAt: '2026-05-09T12:09:30Z',
      nowEpochMs: Date.parse('2026-05-09T12:10:00Z'),
    })
    expect(out).toContain('(30s ago)')
    expect(out).toContain(
      "WARNING: <60s since last turn — voice session may be active. Don't restart without asking.",
    )
  })

  test('voice inactive, bridge active', () => {
    const out = formatVoiceStatus({
      voice: 'inactive',
      bridge: 'active',
      lastTurnAt: null,
      nowEpochMs: Date.parse('2026-05-09T12:10:00Z'),
    })
    expect(out).toContain('voice-agent: inactive')
    expect(out).toContain('bridge:      active')
    expect(out).toContain('last turn:   no telemetry yet')
    expect(out).not.toContain('WARNING')
  })

  test('voice unknown (systemctl missing)', () => {
    const out = formatVoiceStatus({
      voice: 'unknown',
      bridge: 'unknown',
      lastTurnAt: '2026-05-09T11:00:00Z',
      nowEpochMs: Date.parse('2026-05-09T12:10:00Z'),
    })
    expect(out).toContain('voice-agent: unknown')
    expect(out).toContain('bridge:      unknown')
  })

  test('failed state renders verbatim', () => {
    const out = formatVoiceStatus({
      voice: 'failed',
      bridge: 'inactive',
      lastTurnAt: '2026-05-09T11:00:00Z',
      nowEpochMs: Date.parse('2026-05-09T12:10:00Z'),
    })
    expect(out).toContain('voice-agent: failed')
    expect(out).toContain('bridge:      inactive')
  })

  test('lastTurnAt invalid → unknown', () => {
    const out = formatVoiceStatus({
      voice: 'active',
      bridge: 'active',
      lastTurnAt: 'not a date',
      nowEpochMs: Date.parse('2026-05-09T12:10:00Z'),
    })
    expect(out).toContain('last turn:   unknown (could not parse timestamp)')
  })

  test('age formatting — hours and minutes', () => {
    const out = formatVoiceStatus({
      voice: 'active',
      bridge: 'active',
      lastTurnAt: '2026-05-09T10:30:15Z',
      nowEpochMs: Date.parse('2026-05-09T12:10:00Z'),
    })
    expect(out).toContain('(1h 39m 45s ago)')
  })

  test('age formatting — exactly 60s does NOT warn', () => {
    // 60s exactly is NOT a warning (we treat <60s as the threshold).
    const out = formatVoiceStatus({
      voice: 'active',
      bridge: 'active',
      lastTurnAt: '2026-05-09T12:09:00Z',
      nowEpochMs: Date.parse('2026-05-09T12:10:00Z'),
    })
    expect(out).toContain('(60s ago)')
    expect(out).not.toContain('WARNING')
  })

  test('age formatting — 59s warns', () => {
    const out = formatVoiceStatus({
      voice: 'active',
      bridge: 'active',
      lastTurnAt: '2026-05-09T12:09:01Z',
      nowEpochMs: Date.parse('2026-05-09T12:10:00Z'),
    })
    expect(out).toContain('(59s ago)')
    expect(out).toContain('WARNING')
  })
})

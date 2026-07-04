import { describe, expect, test } from 'vitest'
import { reconstructNativeJsonl } from '@/app/api/bridge/v1/sessions/[sessionId]/teleport/route'

// Rebuilding a resumable native jsonl from persisted session events (for
// reclaimed-container sessions). Must produce valid, chained transcript lines
// the CLI can load: type/uuid/parentUuid/sessionId/cwd/timestamp/message.
function ev(type: string, payload: unknown, t = Date.now()) {
  return { type, payload_json: JSON.stringify(payload), created_at: t }
}

describe('reconstructNativeJsonl', () => {
  test('user_prompt + assistant + user → chained transcript lines', () => {
    const events = [
      ev('status', { type: 'status', status: 'init' }),
      ev('user_prompt', { prompt: 'fix the bug' }, 1000),
      ev('assistant', { message: { role: 'assistant', content: [{ type: 'text', text: 'On it.' }] } }, 2000),
      ev('result', { type: 'result' }),
      ev('user', { message: { role: 'user', content: [{ type: 'text', text: 'thanks' }] } }, 3000),
    ]
    const jsonl = reconstructNativeJsonl(events, 'cloud16hexid')
    const lines = jsonl.trim().split('\n').map((l) => JSON.parse(l))
    // Only the 3 conversation events (status/result dropped).
    expect(lines.length).toBe(3)
    expect(lines.map((l) => l.type)).toEqual(['user', 'assistant', 'user'])
    // Each line has the fields the CLI loader needs.
    for (const l of lines) {
      expect(typeof l.uuid).toBe('string')
      expect(l.sessionId).toBe('cloud16hexid')
      expect(l.cwd).toBe('/workspace')
      expect(l.message).toBeTruthy()
      expect(typeof l.timestamp).toBe('string')
    }
    // parentUuid forms a chain: first null, then previous uuid.
    expect(lines[0].parentUuid).toBeNull()
    expect(lines[1].parentUuid).toBe(lines[0].uuid)
    expect(lines[2].parentUuid).toBe(lines[1].uuid)
    // The seeded prompt became a user text message.
    expect(lines[0].message.content[0].text).toBe('fix the bug')
  })

  test('no conversation events → empty string (caller omits native_jsonl)', () => {
    expect(reconstructNativeJsonl([ev('status', { status: 'x' })], 'id')).toBe('')
    expect(reconstructNativeJsonl([], 'id')).toBe('')
  })

  test('skips unparseable payloads', () => {
    const events = [
      { type: 'assistant', payload_json: '{bad json', created_at: 1 },
      ev('assistant', { message: { role: 'assistant', content: [] } }, 2),
    ]
    expect(reconstructNativeJsonl(events, 'id').trim().split('\n').length).toBe(1)
  })
})

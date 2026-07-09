// @vitest-environment node
import { describe, expect, it } from 'vitest'
import { parseCcrInitialEvents, repoFromContext } from '@/app/api/v1/sessions/route'

// The EXACT shapes the teleport/ultraplan client sends (teleport.tsx:1159-1197):
// each payload wrapped as { type:'event', data:{...} }.
describe('parseCcrInitialEvents', () => {
  it('extracts plan mode + task from the ultraplan wrapped events', () => {
    const events = [
      { type: 'event', data: { type: 'control_request', request_id: 'set-mode-1', request: { subtype: 'set_permission_mode', mode: 'plan', ultraplan: true } } },
      { type: 'event', data: { uuid: 'u1', session_id: '', type: 'user', parent_tool_use_id: null, message: { role: 'user', content: 'plan a self evolve capability' } } },
    ]
    expect(parseCcrInitialEvents(events as never)).toEqual({ prompt: 'plan a self evolve capability', mode: 'plan' })
  })

  it('handles content as an array of text blocks', () => {
    const events = [
      { type: 'event', data: { type: 'user', message: { role: 'user', content: [{ type: 'text', text: 'do ' }, { type: 'text', text: 'research' }] } } },
    ]
    expect(parseCcrInitialEvents(events as never)).toEqual({ prompt: 'do research', mode: null })
  })

  it('also accepts unwrapped payloads (defensive)', () => {
    const events = [
      { type: 'control_request', request: { subtype: 'set_permission_mode', mode: 'plan' } },
      { type: 'user', message: { role: 'user', content: 'x' } },
    ]
    expect(parseCcrInitialEvents(events as never)).toEqual({ prompt: 'x', mode: 'plan' })
  })

  it('empty events → empty prompt, null mode', () => {
    expect(parseCcrInitialEvents(undefined)).toEqual({ prompt: '', mode: null })
  })
})

describe('repoFromContext', () => {
  it('parses owner/name from a github source url', () => {
    expect(repoFromContext({ sources: [{ url: 'https://github.com/ulrichando/jarvis' }] })).toBe('ulrichando/jarvis')
  })
  it('strips a trailing .git', () => {
    expect(repoFromContext({ sources: [{ url: 'https://github.com/ulrichando/adventure.git' }] })).toBe('ulrichando/adventure')
  })
  it('no sources / non-github → empty (scratch container)', () => {
    expect(repoFromContext({ sources: [] })).toBe('')
    expect(repoFromContext({})).toBe('')
    expect(repoFromContext({ sources: [{ url: 'https://gitlab.com/a/b' }] })).toBe('')
  })
})

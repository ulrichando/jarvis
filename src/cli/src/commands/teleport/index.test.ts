// src/cli/src/commands/teleport/index.test.ts
import { test, expect } from 'bun:test'
import teleport from './index.ts'
import { INTERNAL_ONLY_COMMANDS } from '../../commands.ts'

test('is an enabled, visible local-jsx command with the /tp alias', () => {
  expect(teleport.type).toBe('local-jsx')
  expect(teleport.name).toBe('teleport')
  expect(teleport.aliases).toEqual(['tp'])
  expect(teleport.isEnabled?.()).toBe(true)
  expect(teleport.isHidden).toBe(false)
  expect(typeof teleport.load).toBe('function')
})

// Regression guard for the real bug: teleport was registered in
// INTERNAL_ONLY_COMMANDS (ant-only), which is stripped from external/JARVIS
// builds — so /teleport never surfaced in the menu even though the command
// itself was valid. It must live in the MAIN command list, never here.
test('teleport is NOT in INTERNAL_ONLY_COMMANDS (would be stripped for external builds)', () => {
  expect(INTERNAL_ONLY_COMMANDS).not.toContain(teleport)
})

// src/cli/src/cli/handlers/bridgeClient.test.ts
import { test, expect } from 'bun:test'
import { normalizeBase } from './bridgeClient.ts'

// The call paths append the full `/api/bridge/v1/...`, so the base must be the
// bare origin. JARVIS_BRIDGE_BASE_URL carries a `/api/bridge` suffix that would
// otherwise double the path to a 404.
test('strips /api/bridge (and trailing slash) so the path never doubles', () => {
  expect(normalizeBase('https://0wlan.com/api/bridge')).toBe('https://0wlan.com')
  expect(normalizeBase('https://0wlan.com/api/bridge/')).toBe('https://0wlan.com')
  expect(normalizeBase('https://0wlan.com/api')).toBe('https://0wlan.com')
})

test('leaves a bare origin (JARVIS_SERVER_URL) unchanged', () => {
  expect(normalizeBase('https://0wlan.com')).toBe('https://0wlan.com')
  expect(normalizeBase('http://127.0.0.1:3000')).toBe('http://127.0.0.1:3000')
})

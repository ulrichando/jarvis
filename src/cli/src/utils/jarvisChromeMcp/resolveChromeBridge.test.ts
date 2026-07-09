// resolveChromeBridge.test.ts
import { test, expect } from 'bun:test'
import { parseLocalApiToken, resolveBaseUrl } from './resolveChromeBridge.js'

test('parseLocalApiToken extracts the token from the env-file body', () => {
  expect(parseLocalApiToken('JARVIS_LOCAL_API_TOKEN=abc123\n')).toBe('abc123')
  expect(parseLocalApiToken('# comment\nJARVIS_LOCAL_API_TOKEN="q w"\n')).toBe('q w')
  expect(parseLocalApiToken('nothing here')).toBeNull()
})

test('resolveBaseUrl honors the override then defaults to the local bridge', () => {
  expect(resolveBaseUrl({ JARVIS_CHROME_BRIDGE_URL: 'http://x:9' })).toBe('http://x:9')
  expect(resolveBaseUrl({})).toBe('http://127.0.0.1:8765')
})

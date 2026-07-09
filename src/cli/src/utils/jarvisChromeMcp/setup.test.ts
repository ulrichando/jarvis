import { test, expect } from 'bun:test'
import { setupJarvisInChrome } from './setup.js'
import { JARVIS_IN_CHROME_SERVER_NAME } from './server.js'

test('setupJarvisInChrome returns a dynamic in-process config + mcp__ allowedTools + prompt', () => {
  const s = setupJarvisInChrome()
  const cfg = s.mcpConfig[JARVIS_IN_CHROME_SERVER_NAME]
  expect(cfg.type).toBe('stdio')
  expect(cfg.scope).toBe('dynamic')
  expect(s.allowedTools).toContain(
    `mcp__${JARVIS_IN_CHROME_SERVER_NAME}__navigate`,
  )
  expect(s.allowedTools.length).toBe(22)
  expect(s.systemPrompt.length).toBeGreaterThan(0)
})

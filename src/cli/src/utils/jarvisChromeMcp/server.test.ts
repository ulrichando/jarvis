// server.test.ts
import { test, expect } from 'bun:test'
import { JARVIS_IN_CHROME_SERVER_NAME, isJarvisInChromeMCPServer, buildToolList } from './server.js'

test('name matcher recognizes the jarvis-in-chrome server only', () => {
  expect(isJarvisInChromeMCPServer(JARVIS_IN_CHROME_SERVER_NAME)).toBe(true)
  expect(isJarvisInChromeMCPServer('claude-in-chrome')).toBe(false)
})

test('buildToolList exposes 22 tools, each augmented with optional confirmed', () => {
  const tools = buildToolList()
  expect(tools.length).toBe(22)
  const nav = tools.find(t => t.name === 'navigate')!
  expect(nav.inputSchema.properties.confirmed).toEqual({ type: 'boolean', description: expect.any(String) })
})

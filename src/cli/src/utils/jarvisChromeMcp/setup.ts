import type { ScopedMcpServerConfig } from '../../services/mcp/types.js'
import { TOOLS, SYSTEM } from '../../bridge/browserTools.js'
import { JARVIS_IN_CHROME_SERVER_NAME } from './server.js'

export function setupJarvisInChrome(): {
  mcpConfig: Record<string, ScopedMcpServerConfig>
  allowedTools: string[]
  systemPrompt: string
} {
  const allowedTools = TOOLS.map(
    t => `mcp__${JARVIS_IN_CHROME_SERVER_NAME}__${t.name}`,
  )
  return {
    mcpConfig: {
      [JARVIS_IN_CHROME_SERVER_NAME]: {
        type: 'stdio' as const,
        command: process.execPath,
        args: ['--jarvis-in-chrome-mcp'], // unreachable: client.ts runs it in-process
        scope: 'dynamic' as const,
      },
    },
    allowedTools,
    systemPrompt: SYSTEM,
  }
}

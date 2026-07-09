// src/cli/src/utils/jarvisChromeMcp/server.ts
import { Server } from '@modelcontextprotocol/sdk/server/index.js'
import { CallToolRequestSchema, ListToolsRequestSchema } from '@modelcontextprotocol/sdk/types.js'
import { TOOLS } from '../../bridge/browserTools.js'
import { resolveChromeBridge } from './resolveChromeBridge.js'
import { callExtBrowse } from './extBrowseClient.js'

export const JARVIS_IN_CHROME_SERVER_NAME = 'jarvis-in-chrome'

/** Exact normalized-name match, mirroring isComputerUseMCPServer. */
export function isJarvisInChromeMCPServer(name: string): boolean {
  return name === JARVIS_IN_CHROME_SERVER_NAME
}

const CONFIRMED_PROP = { type: 'boolean' as const, description: 'Set true to proceed past the extension safety prompt for a mutating action.' }

/** ListTools payload: the shared TOOLS, each augmented with an optional `confirmed`. */
export function buildToolList() {
  return TOOLS.map(t => ({
    name: t.name,
    description: t.description,
    inputSchema: {
      ...t.input_schema,
      properties: { ...t.input_schema.properties, confirmed: CONFIRMED_PROP },
    },
  }))
}

/** data:image/png;base64,XXXX -> XXXX (for MCP image blocks). */
function stripDataUrl(s: string): string {
  const i = s.indexOf('base64,')
  return i >= 0 ? s.slice(i + 'base64,'.length) : s
}

export async function createJarvisInChromeMcpServer(): Promise<Server> {
  const server = new Server(
    { name: JARVIS_IN_CHROME_SERVER_NAME, version: '1.0.0' },
    { capabilities: { tools: {} } },
  )

  server.setRequestHandler(ListToolsRequestSchema, async () => ({ tools: buildToolList() }))

  server.setRequestHandler(CallToolRequestSchema, async req => {
    const action = req.params.name
    const raw = (req.params.arguments ?? {}) as Record<string, unknown>
    const confirmed = raw.confirmed === true
    const { confirmed: _drop, ...args } = raw

    let bridge
    try {
      bridge = resolveChromeBridge()
    } catch {
      return { isError: true, content: [{ type: 'text', text: 'Jarvis-in-Chrome bridge is not running — start the Jarvis desktop app.' }] }
    }

    const r = await callExtBrowse(action, args, confirmed, bridge)
    if (!r.ok) return { isError: true, content: [{ type: 'text', text: r.message }] }

    // screenshot returns a data-URL in image_b64 -> emit an image block.
    const result: any = r.result
    if (action === 'screenshot' && typeof result?.image_b64 === 'string') {
      return { content: [{ type: 'image', data: stripDataUrl(result.image_b64), mimeType: 'image/png' }] }
    }
    return { content: [{ type: 'text', text: JSON.stringify(result) }] }
  })

  return server
}

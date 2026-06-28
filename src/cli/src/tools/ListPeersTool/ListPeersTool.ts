import { z } from 'zod/v4'
import { buildTool, type ToolDef } from '../../Tool.js'
import { listAllLiveSessions } from '../../utils/udsClient.js'
import { lazySchema } from '../../utils/lazySchema.js'

const inputSchema = lazySchema(() => z.strictObject({}))
type InputSchema = ReturnType<typeof inputSchema>

const outputSchema = lazySchema(() =>
  z.object({
    peers: z.array(
      z.object({
        pid: z.number(),
        sessionId: z.string().optional(),
        cwd: z.string().optional(),
        startedAt: z.number().optional(),
        kind: z.string().optional(),
        name: z.string().optional(),
        messagingSocketPath: z.string().optional(),
        bridgeSessionId: z.string().optional(),
      }),
    ),
  }),
)
type OutputSchema = ReturnType<typeof outputSchema>
type Output = z.infer<OutputSchema>

export const ListPeersTool = buildTool({
  name: 'ListPeers',
  searchHint: 'list connected peers',
  maxResultSizeChars: 50_000,
  get inputSchema(): InputSchema {
    return inputSchema()
  },
  get outputSchema(): OutputSchema {
    return outputSchema()
  },
  isEnabled() { return true },
  isReadOnly() { return true },
  isConcurrencySafe() { return true },
  async description() { return 'List peer sessions connected via UDS.' },
  async prompt() { return 'Use ListPeers to see connected peer sessions.' },
  renderToolUseMessage() {
    return null
  },
  async call() {
    return { data: { peers: await listAllLiveSessions() } }
  },
  mapToolResultToToolResultBlockParam(output, toolUseID) {
    if (output.peers.length === 0) {
      return {
        tool_use_id: toolUseID,
        type: 'tool_result',
        content: 'No live peer sessions found.',
      }
    }
    const content = output.peers
      .map(peer => {
        const address = peer.messagingSocketPath
          ? `uds:${peer.messagingSocketPath}`
          : peer.bridgeSessionId
            ? `bridge:${peer.bridgeSessionId}`
            : 'unreachable'
        const name = peer.name ? ` ${peer.name}` : ''
        return `${address}${name} (${peer.kind ?? 'interactive'}, pid ${peer.pid}, ${peer.cwd ?? 'unknown cwd'})`
      })
      .join('\n')
    return {
      tool_use_id: toolUseID,
      type: 'tool_result',
      content,
    }
  },
} satisfies ToolDef<InputSchema, Output>)

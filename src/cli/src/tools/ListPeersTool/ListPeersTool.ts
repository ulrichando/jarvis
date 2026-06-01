// ListPeersTool — stub
import { z } from 'zod/v4'
import { buildTool, type ToolDef } from '../../Tool.js'
import { lazySchema } from '../../utils/lazySchema.js'

const inputSchema = lazySchema(() => z.strictObject({}))
type InputSchema = ReturnType<typeof inputSchema>

export const ListPeersTool = buildTool({
  name: 'ListPeers',
  searchHint: 'list connected peers',
  get inputSchema(): InputSchema { return inputSchema() },
  isEnabled() { return true },
  async description() { return 'List peer sessions connected via UDS.' },
  async prompt() { return 'Use ListPeers to see connected peer sessions.' },
} satisfies ToolDef<InputSchema, void>)

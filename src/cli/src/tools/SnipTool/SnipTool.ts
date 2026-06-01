// SnipTool — stub
import { z } from 'zod/v4'
import { buildTool, type ToolDef } from '../../Tool.js'
import { lazySchema } from '../../utils/lazySchema.js'

const inputSchema = lazySchema(() =>
  z.strictObject({
    start_line: z.number().optional().describe('Start line to snip'),
    end_line: z.number().optional().describe('End line to snip'),
  }),
)
type InputSchema = ReturnType<typeof inputSchema>

export const SnipTool = buildTool({
  name: 'Snip',
  searchHint: 'snip conversation history',
  get inputSchema(): InputSchema { return inputSchema() },
  isEnabled() { return true },
  async description() { return 'Remove a section of conversation history.' },
  async prompt() { return 'Use Snip to trim conversation context.' },
} satisfies ToolDef<InputSchema, void>)

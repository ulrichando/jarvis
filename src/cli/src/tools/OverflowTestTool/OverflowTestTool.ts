// OverflowTestTool — stub (internal testing only)
import { z } from 'zod/v4'
import { buildTool, type ToolDef } from '../../Tool.js'
import { lazySchema } from '../../utils/lazySchema.js'

const inputSchema = lazySchema(() =>
  z.strictObject({
    size: z.number().optional().describe('Size of overflow test data'),
  }),
)
type InputSchema = ReturnType<typeof inputSchema>

export const OverflowTestTool = buildTool({
  name: 'OverflowTest',
  searchHint: 'test overflow behavior',
  get inputSchema(): InputSchema { return inputSchema() },
  isEnabled() { return true },
  async description() { return 'Internal testing tool for overflow behavior.' },
  async prompt() { return 'Internal use only.' },
} satisfies ToolDef<InputSchema, void>)

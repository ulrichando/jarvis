// CtxInspectTool — stub
import { z } from 'zod/v4'
import { buildTool, type ToolDef } from '../../Tool.js'
import { lazySchema } from '../../utils/lazySchema.js'

const inputSchema = lazySchema(() =>
  z.strictObject({
    inspect: z.string().optional().describe('What to inspect in the context'),
  }),
)
type InputSchema = ReturnType<typeof inputSchema>

export const CtxInspectTool = buildTool({
  name: 'CtxInspect',
  searchHint: 'inspect context window',
  get inputSchema(): InputSchema { return inputSchema() },
  isEnabled() { return true },
  async description() { return 'Inspect the current context window contents.' },
  async prompt() { return 'Use CtxInspect to examine context window usage and token distribution.' },
} satisfies ToolDef<InputSchema, void>)

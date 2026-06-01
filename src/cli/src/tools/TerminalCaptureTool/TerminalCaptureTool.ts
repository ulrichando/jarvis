// TerminalCaptureTool — stub
import { z } from 'zod/v4'
import { buildTool, type ToolDef } from '../../Tool.js'
import { lazySchema } from '../../utils/lazySchema.js'

const inputSchema = lazySchema(() =>
  z.strictObject({
    command: z.string().optional().describe('Command to capture terminal output from'),
  }),
)
type InputSchema = ReturnType<typeof inputSchema>

export const TerminalCaptureTool = buildTool({
  name: 'TerminalCapture',
  searchHint: 'capture terminal output',
  get inputSchema(): InputSchema { return inputSchema() },
  isEnabled() { return true },
  async description() { return 'Capture terminal output from a running process.' },
  async prompt() { return 'Use TerminalCapture to capture and display terminal output.' },
} satisfies ToolDef<InputSchema, void>)

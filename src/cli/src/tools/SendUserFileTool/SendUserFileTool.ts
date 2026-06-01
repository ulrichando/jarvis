// SendUserFileTool — stub
import { z } from 'zod/v4'
import { buildTool, type ToolDef } from '../../Tool.js'
import { lazySchema } from '../../utils/lazySchema.js'

const inputSchema = lazySchema(() =>
  z.strictObject({
    file_path: z.string().describe('Path to the file to send'),
    message: z.string().optional().describe('Optional message with the file'),
  }),
)
type InputSchema = ReturnType<typeof inputSchema>

export const SendUserFileTool = buildTool({
  name: 'SendUserFile',
  searchHint: 'send a file to the user',
  get inputSchema(): InputSchema { return inputSchema() },
  isEnabled() { return true },
  async description() { return 'Send a file to the user.' },
  async prompt() { return 'Use SendUserFile to share files with the user.' },
} satisfies ToolDef<InputSchema, void>)

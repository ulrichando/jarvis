import { z } from 'zod/v4'
import { buildTool, type ToolDef } from '../../Tool.js'
import { isSnipRuntimeEnabled } from '../../services/compact/snipCompact.js'
import { lazySchema } from '../../utils/lazySchema.js'

const inputSchema = lazySchema(() =>
  z.strictObject({
    start_line: z.number().optional().describe('Start line to snip'),
    end_line: z.number().optional().describe('End line to snip'),
  }),
)
type InputSchema = ReturnType<typeof inputSchema>

const outputSchema = lazySchema(() =>
  z.object({
    success: z.boolean(),
    message: z.string(),
  }),
)
type OutputSchema = ReturnType<typeof outputSchema>
type Output = z.infer<OutputSchema>

export const SnipTool = buildTool({
  name: 'Snip',
  searchHint: 'snip conversation history',
  maxResultSizeChars: 10_000,
  get inputSchema(): InputSchema {
    return inputSchema()
  },
  get outputSchema(): OutputSchema {
    return outputSchema()
  },
  isEnabled() { return true },
  isReadOnly() { return true },
  isConcurrencySafe() { return true },
  async description() { return 'Remove a section of conversation history.' },
  async prompt() { return 'Use Snip to trim conversation context.' },
  renderToolUseMessage() {
    return null
  },
  async call() {
    if (!isSnipRuntimeEnabled()) {
      return {
        data: {
          success: false,
          message:
            'History snip runtime is not active in this build. No conversation messages were changed.',
        },
      }
    }
    return {
      data: {
        success: false,
        message:
          'History snip runtime is active, but manual range deletion is not implemented. No conversation messages were changed.',
      },
    }
  },
  mapToolResultToToolResultBlockParam(output, toolUseID) {
    return {
      tool_use_id: toolUseID,
      type: 'tool_result',
      content: output.message,
      is_error: !output.success,
    }
  },
} satisfies ToolDef<InputSchema, Output>)

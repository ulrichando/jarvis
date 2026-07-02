import { z } from 'zod/v4'
import { buildTool, type ToolDef } from '../../Tool.js'
import { isSnipRuntimeEnabled, _queueSnip } from '../../services/compact/snipCompact.js'
import { lazySchema } from '../../utils/lazySchema.js'

const inputSchema = lazySchema(() =>
  z.strictObject({
    start_id: z.string().describe('The [id:] anchor of the first message to snip'),
    end_id: z.string().describe('The [id:] anchor of the last message to snip'),
  }),
)
type InputSchema = ReturnType<typeof inputSchema>

const outputSchema = lazySchema(() =>
  z.object({ success: z.boolean(), message: z.string() }),
)
type OutputSchema = ReturnType<typeof outputSchema>
type Output = z.infer<OutputSchema>

export const SnipTool = buildTool({
  name: 'Snip',
  searchHint: 'remove a concluded range of conversation history from context',
  maxResultSizeChars: 10_000,
  get inputSchema(): InputSchema {
    return inputSchema()
  },
  get outputSchema(): OutputSchema {
    return outputSchema()
  },
  isEnabled() {
    return true
  },
  isReadOnly() {
    return true
  },
  isConcurrencySafe() {
    return true
  },
  async description() {
    return 'Remove a concluded/superseded range of conversation history from context, addressed by [id:] anchors. The removal is applied at the next turn boundary.'
  },
  async prompt() {
    return 'Use Snip to drop ranges of history that are concluded or superseded, freeing context. Address the range with the [id:] anchors shown on user messages. Never snip content still needed for the current task; you cannot snip the current turn.'
  },
  renderToolUseMessage() {
    return null
  },
  async call({ start_id, end_id }) {
    if (!isSnipRuntimeEnabled()) {
      return {
        data: {
          success: false,
          message: 'History snip runtime is disabled (JARVIS_HISTORY_SNIP=0).',
        },
      }
    }
    _queueSnip(start_id, end_id)
    return {
      data: {
        success: true,
        message: `Queued snip of [id:${start_id}]…[id:${end_id}]; it will be applied on the next turn (invalid ranges are ignored).`,
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

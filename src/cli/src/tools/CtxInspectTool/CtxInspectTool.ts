import { z } from 'zod/v4'
import { buildTool, type ToolDef } from '../../Tool.js'
import { lazySchema } from '../../utils/lazySchema.js'

const inputSchema = lazySchema(() =>
  z.strictObject({
    inspect: z.string().optional().describe('What to inspect in the context'),
  }),
)
type InputSchema = ReturnType<typeof inputSchema>

const outputSchema = lazySchema(() =>
  z.object({
    messageCount: z.number(),
    typeCounts: z.record(z.string(), z.number()),
    recentTypes: z.array(z.string()),
  }),
)
type OutputSchema = ReturnType<typeof outputSchema>
type Output = z.infer<OutputSchema>

export const CtxInspectTool = buildTool({
  name: 'CtxInspect',
  searchHint: 'inspect context window',
  maxResultSizeChars: 20_000,
  get inputSchema(): InputSchema {
    return inputSchema()
  },
  get outputSchema(): OutputSchema {
    return outputSchema()
  },
  isEnabled() { return true },
  isReadOnly() { return true },
  isConcurrencySafe() { return true },
  async description() { return 'Inspect the current context window contents.' },
  async prompt() { return 'Use CtxInspect to examine context window usage and token distribution.' },
  renderToolUseMessage() {
    return null
  },
  async call(_input, context) {
    const typeCounts: Record<string, number> = {}
    for (const msg of context.messages) {
      typeCounts[msg.type] = (typeCounts[msg.type] ?? 0) + 1
    }
    return {
      data: {
        messageCount: context.messages.length,
        typeCounts,
        recentTypes: context.messages.slice(-12).map(msg => msg.type),
      },
    }
  },
  mapToolResultToToolResultBlockParam(output, toolUseID) {
    return {
      tool_use_id: toolUseID,
      type: 'tool_result',
      content: [
        `Messages: ${output.messageCount}`,
        `Types: ${Object.entries(output.typeCounts)
          .map(([type, count]) => `${type}=${count}`)
          .join(', ')}`,
        `Recent: ${output.recentTypes.join(' -> ')}`,
      ].join('\n'),
    }
  },
} satisfies ToolDef<InputSchema, Output>)

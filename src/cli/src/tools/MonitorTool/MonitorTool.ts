import { z } from 'zod/v4'
import { buildTool, type ToolDef } from '../../Tool.js'
import { lazySchema } from '../../utils/lazySchema.js'
import { BashTool, type BashToolInput } from '../BashTool/BashTool.js'

const inputSchema = lazySchema(() =>
  z.strictObject({
    description: z.string().describe('Short human-readable description of what you are monitoring'),
    timeout_ms: z.number().optional().describe('Kill the monitor after this deadline (ms)'),
    persistent: z.boolean().optional().describe('Run for the lifetime of the session'),
    command: z.string().describe('Shell command or script to monitor'),
  }),
)
type InputSchema = ReturnType<typeof inputSchema>

const outputSchema = lazySchema(() =>
  z.object({
    success: z.boolean(),
    message: z.string(),
    taskId: z.string().optional(),
    output: z.string().optional(),
  }),
)
type OutputSchema = ReturnType<typeof outputSchema>
type Output = z.infer<OutputSchema>

function toBashInput(input: z.infer<InputSchema>): BashToolInput & {
  kind: 'monitor'
} {
  return {
    command: input.command,
    description: input.description,
    timeout: input.timeout_ms,
    run_in_background: true,
    kind: 'monitor',
  }
}

export const MonitorTool = buildTool({
  name: 'Monitor',
  searchHint: 'watch a long-running command and stream events',
  maxResultSizeChars: 20_000,
  get inputSchema(): InputSchema {
    return inputSchema()
  },
  get outputSchema(): OutputSchema {
    return outputSchema()
  },
  isEnabled() {
    return true
  },
  isReadOnly(input) {
    return BashTool.isReadOnly(toBashInput(input))
  },
  isConcurrencySafe(input) {
    return BashTool.isConcurrencySafe(toBashInput(input))
  },
  toAutoClassifierInput(input) {
    return input.command
  },
  async description() {
    return 'Start a background monitor that streams events from a long-running script.'
  },
  async prompt() {
    return 'Use Monitor to watch long-running shell commands in the background. It uses Bash permissions and returns a task ID whose output can be inspected from background tasks.'
  },
  async validateInput(input, context) {
    return BashTool.validateInput
      ? BashTool.validateInput(toBashInput(input), context)
      : { result: true }
  },
  async checkPermissions(input, context) {
    return BashTool.checkPermissions(toBashInput(input), context)
  },
  renderToolUseMessage() {
    return null
  },
  async call(input, context, canUseTool, parentMessage, onProgress) {
    const result = await BashTool.call(
      toBashInput(input),
      context,
      canUseTool,
      parentMessage,
      onProgress,
    )
    const bashResult = result.data as {
      backgroundTaskId?: string
      stdout?: string
      stderr?: string
    }
    const taskId = bashResult.backgroundTaskId
    return {
      data: {
        success: true,
        message: taskId
          ? `Monitor started in background with task ID ${taskId}.`
          : 'Monitor command completed before it needed to run in the background.',
        taskId,
        output: [bashResult.stdout, bashResult.stderr].filter(Boolean).join('\n'),
      },
    }
  },
  mapToolResultToToolResultBlockParam(output, toolUseID) {
    const lines = [output.message]
    if (output.output) lines.push('', output.output)
    return {
      tool_use_id: toolUseID,
      type: 'tool_result',
      content: lines.join('\n'),
      is_error: !output.success,
    }
  },
} satisfies ToolDef<InputSchema, Output>)

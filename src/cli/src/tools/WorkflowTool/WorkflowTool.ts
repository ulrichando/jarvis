import { z } from 'zod/v4'
import { buildTool, type ToolDef } from '../../Tool.js'
import { lazySchema } from '../../utils/lazySchema.js'

export const WORKFLOW_TOOL_NAME = 'Workflow'

const inputSchema = lazySchema(() =>
  z.strictObject({
    script: z.string().describe('Self-contained workflow script'),
    name: z.string().optional().describe('Name of a predefined workflow'),
    args: z.any().optional().describe('Optional input for the workflow'),
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

export const WorkflowTool = buildTool({
  name: WORKFLOW_TOOL_NAME,
  searchHint: 'orchestrate multi-agent workflows',
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
  isReadOnly() {
    return true
  },
  isConcurrencySafe() {
    return true
  },
  async description() {
    return 'Execute a workflow script that orchestrates multiple subagents deterministically.'
  },
  async prompt() {
    return 'Use Workflow only for installed workflow scripts. This JARVIS build does not currently bundle a workflow engine; use Agent and TodoWrite for orchestration instead.'
  },
  renderToolUseMessage() {
    return null
  },
  async call({ name }) {
    return {
      data: {
        success: false,
        message: name
          ? `Workflow "${name}" is not installed in this build.`
          : 'Workflow scripts are enabled but no local workflow engine or bundled workflows are installed.',
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

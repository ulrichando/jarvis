// WorkflowTool — stub (full implementation pending)
// Enable with --feature=WORKFLOW_SCRIPTS at build time.
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

export const WorkflowTool = buildTool({
  name: WORKFLOW_TOOL_NAME,
  searchHint: 'orchestrate multi-agent workflows',
  get inputSchema(): InputSchema { return inputSchema() },
  isEnabled() {
    return true
  },
  async description() {
    return 'Execute a workflow script that orchestrates multiple subagents deterministically.'
  },
  async prompt() {
    return 'Use Workflow for multi-step orchestration with parallel agents, pipelines, and structured output.'
  },
} satisfies ToolDef<InputSchema, void>)

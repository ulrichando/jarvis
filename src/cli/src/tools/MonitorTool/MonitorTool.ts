// MonitorTool — stub (full implementation pending)
// Currently delegates to BashTool for background process monitoring.
// Enable with --feature=MONITOR_TOOL at build time.
import { z } from 'zod/v4'
import { buildTool, type ToolDef } from '../../Tool.js'
import { lazySchema } from '../../utils/lazySchema.js'

const inputSchema = lazySchema(() =>
  z.strictObject({
    description: z.string().describe('Short human-readable description of what you are monitoring'),
    timeout_ms: z.number().optional().describe('Kill the monitor after this deadline (ms)'),
    persistent: z.boolean().optional().describe('Run for the lifetime of the session'),
    command: z.string().describe('Shell command or script to monitor'),
  }),
)
type InputSchema = ReturnType<typeof inputSchema>

export const MonitorTool = buildTool({
  name: 'Monitor',
  searchHint: 'watch a long-running command and stream events',
  get inputSchema(): InputSchema { return inputSchema() },
  isEnabled() {
    // Enable when MONITOR_TOOL feature flag is on and bash tool is available
    return true
  },
  async description() {
    return 'Start a background monitor that streams events from a long-running script.'
  },
  async prompt() {
    return 'Use Monitor to watch long-running commands and receive streaming output lines as events.'
  },
} satisfies ToolDef<InputSchema, void>)

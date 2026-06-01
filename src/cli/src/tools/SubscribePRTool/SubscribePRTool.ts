// SubscribePRTool — stub
import { z } from 'zod/v4'
import { buildTool, type ToolDef } from '../../Tool.js'
import { lazySchema } from '../../utils/lazySchema.js'

const inputSchema = lazySchema(() =>
  z.strictObject({
    pr_url: z.string().describe('URL of the PR to subscribe to'),
  }),
)
type InputSchema = ReturnType<typeof inputSchema>

export const SubscribePRTool = buildTool({
  name: 'SubscribePR',
  searchHint: 'subscribe to PR notifications',
  get inputSchema(): InputSchema { return inputSchema() },
  isEnabled() { return true },
  async description() { return 'Subscribe to notifications for a GitHub PR.' },
  async prompt() { return 'Use SubscribePR to get notified about PR updates.' },
} satisfies ToolDef<InputSchema, void>)

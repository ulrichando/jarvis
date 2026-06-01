// SleepTool — stub
// Enable with --feature=PROACTIVE or --feature=KAIROS at build time.
import { z } from 'zod/v4'
import { buildTool, type ToolDef } from '../../Tool.js'
import { lazySchema } from '../../utils/lazySchema.js'

const inputSchema = lazySchema(() =>
  z.strictObject({
    delaySeconds: z.number().describe('Seconds to sleep before next wake-up'),
    reason: z.string().describe('Why the agent is sleeping'),
  }),
)
type InputSchema = ReturnType<typeof inputSchema>

export const SleepTool = buildTool({
  name: 'Sleep',
  searchHint: 'pause agent execution for a duration',
  get inputSchema(): InputSchema { return inputSchema() },
  isEnabled() { return true },
  async description() { return 'Pause agent execution for a specified duration, then resume.' },
  async prompt() { return 'Use Sleep to pause and schedule wake-up for recurring or delayed tasks.' },
} satisfies ToolDef<InputSchema, void>)

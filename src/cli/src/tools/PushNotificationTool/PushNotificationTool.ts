// PushNotificationTool — stub
import { z } from 'zod/v4'
import { buildTool, type ToolDef } from '../../Tool.js'
import { lazySchema } from '../../utils/lazySchema.js'

const inputSchema = lazySchema(() =>
  z.strictObject({
    message: z.string().describe('The notification body'),
  }),
)
type InputSchema = ReturnType<typeof inputSchema>

export const PushNotificationTool = buildTool({
  name: 'PushNotification',
  searchHint: 'send desktop notification',
  get inputSchema(): InputSchema { return inputSchema() },
  isEnabled() { return true },
  async description() { return 'Send a desktop notification.' },
  async prompt() { return 'Use PushNotification to send alerts to the desktop.' },
} satisfies ToolDef<InputSchema, void>)

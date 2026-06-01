// WebBrowserTool — stub (delegates to WebFetch + WebSearch for now)
import { z } from 'zod/v4'
import { buildTool, type ToolDef } from '../../Tool.js'
import { lazySchema } from '../../utils/lazySchema.js'

const inputSchema = lazySchema(() =>
  z.strictObject({
    url: z.string().describe('The URL to open or interact with'),
    prompt: z.string().optional().describe('What to do on the page'),
  }),
)
type InputSchema = ReturnType<typeof inputSchema>

export const WebBrowserTool = buildTool({
  name: 'WebBrowser',
  searchHint: 'browse web pages interactively',
  get inputSchema(): InputSchema { return inputSchema() },
  isEnabled() { return true },
  async description() { return 'Open and interact with web pages.' },
  async prompt() { return 'Use WebBrowser to open URLs and interact with web content.' },
} satisfies ToolDef<InputSchema, void>)

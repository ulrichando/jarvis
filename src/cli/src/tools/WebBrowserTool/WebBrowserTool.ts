import { z } from 'zod/v4'
import { buildTool, type ToolDef } from '../../Tool.js'
import { lazySchema } from '../../utils/lazySchema.js'
import { WebFetchTool } from '../WebFetchTool/WebFetchTool.js'

const inputSchema = lazySchema(() =>
  z.strictObject({
    url: z.string().url().describe('The URL to open or interact with'),
    prompt: z.string().optional().describe('What to do on the page'),
  }),
)
type InputSchema = ReturnType<typeof inputSchema>

const outputSchema = lazySchema(() =>
  z.object({
    url: z.string(),
    result: z.string(),
    code: z.number().optional(),
    codeText: z.string().optional(),
  }),
)
type OutputSchema = ReturnType<typeof outputSchema>
type Output = z.infer<OutputSchema>

function toFetchInput(input: z.infer<InputSchema>) {
  return {
    url: input.url,
    prompt:
      input.prompt ??
      'Extract the main readable content from this page and summarize the useful facts.',
  }
}

export const WebBrowserTool = buildTool({
  name: 'WebBrowser',
  searchHint: 'browse web pages interactively',
  maxResultSizeChars: 100_000,
  get inputSchema(): InputSchema {
    return inputSchema()
  },
  get outputSchema(): OutputSchema {
    return outputSchema()
  },
  isEnabled() { return true },
  isReadOnly() { return true },
  isConcurrencySafe() { return true },
  toAutoClassifierInput(input) {
    return input.prompt ? `${input.url}: ${input.prompt}` : input.url
  },
  async description() { return 'Open and interact with web pages.' },
  async prompt() {
    return 'Use WebBrowser for URL-based browsing. In this build it fetches and processes page content through WebFetch; it does not yet drive an authenticated graphical browser session.'
  },
  async validateInput(input, context) {
    return WebFetchTool.validateInput
      ? WebFetchTool.validateInput(toFetchInput(input), context)
      : { result: true }
  },
  async checkPermissions(input, context) {
    return WebFetchTool.checkPermissions(toFetchInput(input), context)
  },
  renderToolUseMessage() {
    return null
  },
  async call(input, context, canUseTool, parentMessage, onProgress) {
    const result = await WebFetchTool.call(
      toFetchInput(input),
      context,
      canUseTool,
      parentMessage,
      onProgress,
    )
    const fetched = result.data as {
      url: string
      result: string
      code: number
      codeText: string
    }
    return {
      data: {
        url: fetched.url,
        result: fetched.result,
        code: fetched.code,
        codeText: fetched.codeText,
      },
    }
  },
  mapToolResultToToolResultBlockParam(output, toolUseID) {
    return {
      tool_use_id: toolUseID,
      type: 'tool_result',
      content: output.result,
    }
  },
} satisfies ToolDef<InputSchema, Output>)

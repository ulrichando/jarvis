import { z } from 'zod/v4'
import { buildTool, type ToolDef } from '../../Tool.js'
import type { TaskState } from '../../tasks/types.js'
import { tailFile } from '../../utils/fsOperations.js'
import { lazySchema } from '../../utils/lazySchema.js'

const inputSchema = lazySchema(() =>
  z.strictObject({
    command: z.string().optional().describe('Command to capture terminal output from'),
  }),
)
type InputSchema = ReturnType<typeof inputSchema>

const outputSchema = lazySchema(() =>
  z.object({
    tasks: z.array(
      z.object({
        id: z.string(),
        type: z.string(),
        status: z.string(),
        description: z.string(),
        command: z.string().optional(),
        outputFile: z.string(),
        tail: z.string(),
        totalBytes: z.number(),
      }),
    ),
  }),
)
type OutputSchema = ReturnType<typeof outputSchema>
type Output = z.infer<OutputSchema>

function taskCommand(task: TaskState): string | undefined {
  return 'command' in task && typeof task.command === 'string'
    ? task.command
    : undefined
}

export const TerminalCaptureTool = buildTool({
  name: 'TerminalCapture',
  searchHint: 'capture terminal output',
  maxResultSizeChars: 50_000,
  get inputSchema(): InputSchema {
    return inputSchema()
  },
  get outputSchema(): OutputSchema {
    return outputSchema()
  },
  isEnabled() { return true },
  isReadOnly() { return true },
  isConcurrencySafe() { return true },
  async description() { return 'Capture terminal output from a running process.' },
  async prompt() {
    return 'Use TerminalCapture to inspect output from currently registered background terminal tasks. It does not execute commands.'
  },
  renderToolUseMessage() {
    return null
  },
  async call({ command }, context) {
    const query = command?.toLowerCase()
    const tasks = Object.values(context.getAppState().tasks ?? {}).filter(task => {
      if (task.type !== 'local_bash') return false
      if (!query) return true
      return (
        task.description.toLowerCase().includes(query) ||
        (taskCommand(task)?.toLowerCase().includes(query) ?? false)
      )
    })

    const captured = await Promise.all(
      tasks.map(async task => {
        let tail = ''
        let totalBytes = 0
        try {
          const out = await tailFile(task.outputFile, 8_000)
          tail = out.content
          totalBytes = out.bytesTotal
        } catch {
          tail = ''
        }
        return {
          id: task.id,
          type: task.type,
          status: task.status,
          description: task.description,
          command: taskCommand(task),
          outputFile: task.outputFile,
          tail,
          totalBytes,
        }
      }),
    )

    return { data: { tasks: captured } }
  },
  mapToolResultToToolResultBlockParam(output, toolUseID) {
    if (output.tasks.length === 0) {
      return {
        tool_use_id: toolUseID,
        type: 'tool_result',
        content: 'No matching background terminal tasks found.',
      }
    }
    const content = output.tasks
      .map(task => {
        const header = `${task.id} [${task.status}] ${task.command ?? task.description}`
        const size = `Output file: ${task.outputFile} (${task.totalBytes} bytes)`
        return [header, size, task.tail].filter(Boolean).join('\n')
      })
      .join('\n\n---\n\n')
    return {
      tool_use_id: toolUseID,
      type: 'tool_result',
      content,
    }
  },
} satisfies ToolDef<InputSchema, Output>)

import type { ToolUseContext } from '../../Tool.js'
import type { Message } from '../../types/message.js'
import type { AgentDefinition } from '../AgentTool/loadAgentsDir.js'
import { runAgent } from '../AgentTool/runAgent.js'
import { finalizeAgentTool } from '../AgentTool/agentToolUtils.js'
import { createSyntheticOutputTool } from '../SyntheticOutputTool/SyntheticOutputTool.js'
import { createUserMessage, extractTextContent } from '../../utils/messages.js'
import { assembleToolPool } from '../../tools.js'
import { createAgentId } from '../../utils/uuid.js'
import type { AgentId } from '../../types/ids.js'
import type { ModelAlias } from '../../utils/model/aliases.js'
import type { DispatchResult, AgentOpts } from './agentCall.js'
import { getWorkflowAgentDefinition } from './workflowAgentDef.js'

export type DispatchDeps = {
  toolUseContext: ToolUseContext
  defaultModel: string
  runId: string
  agentControllers?: Map<string, AbortController>
  resolveAgentType: (name: string) => AgentDefinition | undefined
}

export function makeDispatch(deps: DispatchDeps) {
  return async function dispatch(
    prompt: string,
    opts: AgentOpts,
    signal: AbortSignal,
  ): Promise<DispatchResult> {
    const agentId = createAgentId()
    const controller = new AbortController()
    if (signal.aborted) controller.abort()
    else signal.addEventListener('abort', () => controller.abort())
    deps.agentControllers?.set(agentId, controller)

    const baseDef = opts.agentType
      ? deps.resolveAgentType(opts.agentType) ?? getWorkflowAgentDefinition()
      : getWorkflowAgentDefinition()

    const schemaTool = opts.schema ? createSyntheticOutputTool(opts.schema) : undefined
    if (schemaTool && 'error' in schemaTool) {
      deps.agentControllers?.delete(agentId)
      throw new Error(`Invalid schema: ${schemaTool.error}`)
    }

    const appState = deps.toolUseContext.getAppState()
    const workerTools = assembleToolPool(
      { ...appState.toolPermissionContext, mode: 'acceptEdits' },
      appState.mcp.tools,
    )
    const availableTools =
      schemaTool && 'tool' in schemaTool ? [...workerTools, schemaTool.tool] : workerTools

    const startTime = Date.now()
    const collected: Message[] = []
    try {
      for await (const msg of runAgent({
        agentDefinition: baseDef,
        promptMessages: [createUserMessage({ content: buildPrompt(prompt, opts) })],
        toolUseContext: deps.toolUseContext,
        canUseTool: async () => ({ behavior: 'allow', updatedInput: {} }),
        isAsync: true,
        querySource: 'agent:custom',
        model: opts.model as ModelAlias | undefined,
        availableTools,
        description: opts.label ?? prompt.slice(0, 60),
        transcriptSubdir: `workflows/${deps.runId}`,
        override: { agentId: agentId as AgentId, abortController: controller },
      })) {
        collected.push(msg)
      }
    } finally {
      deps.agentControllers?.delete(agentId)
    }

    if (controller.signal.aborted && signal.aborted) {
      return { skipped: true }
    }

    const finalized = finalizeAgentTool(collected, agentId, {
      prompt,
      resolvedAgentModel: opts.model ?? deps.defaultModel,
      isBuiltInAgent: baseDef.source === 'built-in',
      startTime,
      agentType: baseDef.agentType,
      isAsync: true,
    })

    if (opts.schema) {
      const structured = extractStructuredOutput(collected)
      if (structured !== undefined) {
        return {
          structured,
          tokens: finalized.totalTokens ?? 0,
          toolCalls: finalized.totalToolUseCount ?? 0,
          agentId,
        }
      }
    }

    return {
      text: extractTextContent(finalized.content, '\n'),
      tokens: finalized.totalTokens ?? 0,
      toolCalls: finalized.totalToolUseCount ?? 0,
      agentId,
    }
  }
}

function buildPrompt(prompt: string, opts: AgentOpts): string {
  if (!opts.schema) return prompt
  return `${prompt}\n\nRespond by calling the StructuredOutput tool with a value matching the required schema. Your final text is ignored in schema mode.`
}

function extractStructuredOutput(messages: Message[]): unknown {
  for (let i = messages.length - 1; i >= 0; i--) {
    const m = messages[i]
    if (!m) continue
    // toolUseResult is set on user messages that carry tool results
    const asUser = m as { toolUseResult?: { structured_output?: unknown } }
    if (asUser?.toolUseResult?.structured_output !== undefined) {
      return asUser.toolUseResult.structured_output
    }
  }
  return undefined
}

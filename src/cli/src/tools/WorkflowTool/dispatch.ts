import type { ToolUseContext } from '../../Tool.js'
import type { Message } from '../../types/message.js'
import type { AgentDefinition } from '../AgentTool/loadAgentsDir.js'
import { runAgent } from '../AgentTool/runAgent.js'
import { finalizeAgentTool } from '../AgentTool/agentToolUtils.js'
import { createSyntheticOutputTool } from '../SyntheticOutputTool/SyntheticOutputTool.js'
import { createUserMessage, extractTextContent } from '../../utils/messages.js'
import { assembleToolPool } from '../../tools.js'
import { createAgentId } from '../../utils/uuid.js'
import { runWithCwdOverride } from '../../utils/cwd.js'
import {
  createAgentWorktree,
  removeAgentWorktree,
  hasWorktreeChanges,
} from '../../utils/worktree.js'
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

    // Optional git-worktree isolation for file-mutating parallel agents.
    // Created in the ORIGINAL cwd (before the override) so the git root resolves
    // correctly; the agent then runs inside the worktree via runWithCwdOverride.
    let worktreeInfo:
      | {
          worktreePath: string
          worktreeBranch?: string
          headCommit?: string
          gitRoot?: string
          hookBased?: boolean
        }
      | null = null
    if (opts.isolation === 'worktree') {
      try {
        worktreeInfo = await createAgentWorktree(`agent-${agentId.slice(0, 8)}`)
      } catch {
        worktreeInfo = null // fall back to the shared cwd rather than fail the agent
      }
    }

    const startTime = Date.now()
    const collected: Message[] = []
    const runIteration = async () => {
      for await (const msg of runAgent({
        agentDefinition: baseDef,
        promptMessages: [createUserMessage({ content: buildPrompt(prompt, opts) })],
        toolUseContext: deps.toolUseContext,
        // Echo the tool's own input back. An empty updatedInput would CLOBBER
        // the real args (e.g. Bash's `command`), so the tool then sees
        // undefined → `command.includes(...)` throws. Workflow agents auto-run
        // in acceptEdits, so allow unconditionally but preserve the input.
        canUseTool: async (_tool: unknown, input: unknown) => ({
          behavior: 'allow' as const,
          updatedInput: input as Record<string, unknown>,
        }),
        isAsync: true,
        querySource: 'agent:custom',
        model: opts.model as ModelAlias | undefined,
        availableTools,
        description: opts.label ?? prompt.slice(0, 60),
        transcriptSubdir: `workflows/${deps.runId}`,
        worktreePath: worktreeInfo?.worktreePath,
        override: { agentId: agentId as AgentId, abortController: controller },
      })) {
        collected.push(msg)
      }
    }
    try {
      // AsyncLocalStorage-based override propagates across the whole iteration.
      if (worktreeInfo?.worktreePath) {
        await runWithCwdOverride(worktreeInfo.worktreePath, runIteration)
      } else {
        await runIteration()
      }
    } finally {
      deps.agentControllers?.delete(agentId)
      // Remove an unchanged worktree; keep it if the agent left changes.
      if (worktreeInfo && !worktreeInfo.hookBased) {
        try {
          const changed = worktreeInfo.headCommit
            ? await hasWorktreeChanges(
                worktreeInfo.worktreePath,
                worktreeInfo.headCommit,
              )
            : true
          if (!changed) {
            await removeAgentWorktree(
              worktreeInfo.worktreePath,
              worktreeInfo.worktreeBranch,
              worktreeInfo.gitRoot,
              worktreeInfo.hookBased,
            )
          }
        } catch {
          // Leave the worktree in place on cleanup failure.
        }
      }
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

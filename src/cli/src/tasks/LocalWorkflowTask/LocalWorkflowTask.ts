import type { SetAppState, Task, TaskStateBase } from '../../Task.js'
import type { SdkWorkflowProgress } from '../../types/tools.js'
import type { WorkflowPhase } from '../../tools/WorkflowTool/meta.js'
import { updateTaskState } from '../../utils/task/framework.js'
import { enqueuePendingNotification } from '../../utils/messageQueueManager.js'
import {
  appendTaskOutput,
  getTaskOutputPath,
} from '../../utils/task/diskOutput.js'
import { escapeXml } from '../../utils/xml.js'
import {
  OUTPUT_FILE_TAG,
  STATUS_TAG,
  SUMMARY_TAG,
  TASK_ID_TAG,
  TASK_NOTIFICATION_TAG,
  TASK_TYPE_TAG,
  TOOL_USE_ID_TAG,
} from '../../constants/xml.js'

export type LocalWorkflowTaskState = TaskStateBase & {
  type: 'local_workflow'
  workflowName?: string
  workflowRunId?: string
  summary?: string
  title?: string
  prompt?: string
  phases?: WorkflowPhase[]
  workflowProgress?: SdkWorkflowProgress[]
  totalTokens?: number
  totalToolCalls?: number
  agentCount: number
  // Non-reactive: Map identity is stable; mutating it never re-renders. Same
  // pattern as sessionHooks agentControllers.
  agentControllers?: Map<string, AbortController>
  runController?: AbortController
}

function markKilled(taskId: string, setAppState: SetAppState): void {
  updateTaskState<LocalWorkflowTaskState>(taskId, setAppState, task => {
    if (task.status !== 'running' && task.status !== 'pending') return task
    task.runController?.abort()
    if (task.agentControllers) {
      for (const c of task.agentControllers.values()) c.abort()
    }
    return { ...task, status: 'killed', endTime: Date.now(), notified: true }
  })
}

export const LocalWorkflowTask: Task = {
  name: 'LocalWorkflowTask',
  type: 'local_workflow',
  async kill(taskId, setAppState) {
    markKilled(taskId, setAppState)
  },
}

export function killWorkflowTask(taskId: string, setAppState: SetAppState): void {
  markKilled(taskId, setAppState)
}

// Abort just this agent's controller; its agent() call resolves null with
// state 'skipped by user'. Mutates the (non-reactive) Map, returns task
// unchanged so no re-render churn.
export function skipWorkflowAgent(
  taskId: string,
  agentId: string,
  setAppState: SetAppState,
): void {
  updateTaskState<LocalWorkflowTaskState>(taskId, setAppState, task => {
    task.agentControllers?.get(agentId)?.abort()
    return task
  })
}

// Retry is journal-resume based (post-run), surfaced in the detail dialog as
// a resume hint rather than a live control. No-op kept for the props contract.
export function retryWorkflowAgent(
  _taskId: string,
  _agentId: string,
  _setAppState: SetAppState,
): void {}

// Cap the result inlined into the model-facing notification. The full result
// is also written to the task output file (Read-able), so truncation loses
// nothing — the model can Read the file for the complete payload.
const MAX_INLINE_RESULT = 12_000

/**
 * Surface a finished workflow's result to the model.
 *
 * Every other background task type enqueues a completion notification (see
 * LocalAgentTask.enqueueAgentNotification, LocalShellTask, RemoteAgentTask) —
 * `framework.generateTaskAttachments` deliberately does NOT, to avoid racing
 * those per-type callbacks. The workflow runner previously set `notified:false`
 * and enqueued nothing, so a finished workflow sat in AppState as "unread"
 * forever and the model was never re-invoked with the result. This closes that
 * gap: it atomically claims the `notified` flag (a kill already sets it true →
 * skip, no double-delivery) and enqueues a `<task-notification>` carrying the
 * result inline plus a pointer to the full output file.
 */
export function notifyWorkflowComplete(params: {
  taskId: string
  toolUseId?: string
  workflowName: string
  status: 'completed' | 'failed'
  result: unknown
  error?: string
  failures: string[]
  agentCount: number
  durationMs: number
  transcriptDir: string
  setAppState: SetAppState
}): void {
  const {
    taskId,
    toolUseId,
    workflowName,
    status,
    result,
    error,
    failures,
    agentCount,
    durationMs,
    transcriptDir,
    setAppState,
  } = params

  // Atomic claim: skip if a kill (or a prior call) already notified. Flipping
  // notified:true also lets the terminal task evict on the next poll, matching
  // every other background task type.
  let shouldNotify = false
  updateTaskState<LocalWorkflowTaskState>(taskId, setAppState, task => {
    if (task.notified) return task
    shouldNotify = true
    return { ...task, notified: true }
  })
  if (!shouldNotify) return

  const resultText =
    error != null && error !== ''
      ? error
      : typeof result === 'string'
        ? result
        : result == null
          ? '(workflow returned no value)'
          : JSON.stringify(result, null, 2)

  const failuresText = failures.length
    ? `\n\nAgent failures (${failures.length}):\n${failures.join('\n')}`
    : ''

  const secs = Math.round(durationMs / 1000)
  const agentsLabel = `${agentCount} agent${agentCount === 1 ? '' : 's'}`

  // Full result → the task output file (Read-able, unbounded copy). Routed
  // through appendTaskOutput (not a raw writeFileSync) so it creates the tasks
  // dir if this is the session's first background task AND opens with O_NOFOLLOW
  // — the symlink defense diskOutput enforces for this directory. The file is
  // fresh per random taskId, so append == write.
  const outputPath = getTaskOutputPath(taskId)
  appendTaskOutput(
    taskId,
    `Workflow "${workflowName}" ${status} — ${agentsLabel}, ${secs}s\n` +
      `Transcript: ${transcriptDir}\n\nResult:\n${resultText}${failuresText}\n`,
  )

  const inlineResult =
    resultText.length > MAX_INLINE_RESULT
      ? `${resultText.slice(0, MAX_INLINE_RESULT)}\n… (truncated — full result in ${outputPath})`
      : resultText

  const summary =
    `Workflow "${workflowName}" ${status}` +
    ` (${agentsLabel}, ${secs}s` +
    (failures.length
      ? `, ${failures.length} agent failure${failures.length === 1 ? '' : 's'}`
      : '') +
    ')'

  // Workflow result/failure text is untrusted (it embeds subagent output, which
  // can carry text from fetched web pages). escapeXml the values that go between
  // tags so an embedded "</result>" can't forge notification structure and steer
  // the idle-drain model turn. status/taskId/toolUseId are host-generated.
  const toolUseIdLine = toolUseId
    ? `\n<${TOOL_USE_ID_TAG}>${toolUseId}</${TOOL_USE_ID_TAG}>`
    : ''
  const message = `<${TASK_NOTIFICATION_TAG}>
<${TASK_ID_TAG}>${taskId}</${TASK_ID_TAG}>${toolUseIdLine}
<${TASK_TYPE_TAG}>local_workflow</${TASK_TYPE_TAG}>
<${OUTPUT_FILE_TAG}>${outputPath}</${OUTPUT_FILE_TAG}>
<${STATUS_TAG}>${status}</${STATUS_TAG}>
<${SUMMARY_TAG}>${escapeXml(summary)}</${SUMMARY_TAG}>
<result>${escapeXml(inlineResult + failuresText)}</result>
</${TASK_NOTIFICATION_TAG}>`

  enqueuePendingNotification({ value: message, mode: 'task-notification' })
}

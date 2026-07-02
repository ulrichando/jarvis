import type { SetAppState, Task, TaskStateBase } from '../../Task.js'
import type { SdkWorkflowProgress } from '../../types/tools.js'
import type { WorkflowPhase } from '../../tools/WorkflowTool/meta.js'
import { updateTaskState } from '../../utils/task/framework.js'

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

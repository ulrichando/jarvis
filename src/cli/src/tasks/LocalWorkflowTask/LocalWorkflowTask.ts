import type { SetAppState, Task, TaskStateBase } from '../../Task.js'
import { updateTaskState } from '../../utils/task/framework.js'

export type LocalWorkflowTaskState = TaskStateBase & {
  type: 'local_workflow'
  workflowName?: string
  summary?: string
  prompt?: string
  agentCount: number
}

function markKilled(taskId: string, setAppState: SetAppState): void {
  updateTaskState<LocalWorkflowTaskState>(taskId, setAppState, task => {
    if (task.status !== 'running' && task.status !== 'pending') return task
    return {
      ...task,
      status: 'killed',
      endTime: Date.now(),
      notified: true,
    }
  })
}

export const LocalWorkflowTask: Task = {
  name: 'LocalWorkflowTask',
  type: 'local_workflow',
  async kill(taskId, setAppState) {
    markKilled(taskId, setAppState)
  },
}

export function killWorkflowTask(
  taskId: string,
  setAppState: SetAppState,
): void {
  markKilled(taskId, setAppState)
}

export function skipWorkflowAgent(
  _taskId: string,
  _agentId: string,
  _setAppState: SetAppState,
): void {}

export function retryWorkflowAgent(
  _taskId: string,
  _agentId: string,
  _setAppState: SetAppState,
): void {}

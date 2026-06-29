import type { AppState } from '../../state/AppState.js'
import type { SetAppState, Task, TaskStateBase } from '../../Task.js'
import type { AgentId } from '../../types/ids.js'
import { updateTaskState } from '../../utils/task/framework.js'

export type MonitorMcpTaskState = TaskStateBase & {
  type: 'monitor_mcp'
  agentId?: AgentId
  source?: string
}

function markKilled(taskId: string, setAppState: SetAppState): void {
  updateTaskState<MonitorMcpTaskState>(taskId, setAppState, task => {
    if (task.status !== 'running' && task.status !== 'pending') return task
    return {
      ...task,
      status: 'killed',
      endTime: Date.now(),
      notified: true,
    }
  })
}

export const MonitorMcpTask: Task = {
  name: 'MonitorMcpTask',
  type: 'monitor_mcp',
  async kill(taskId, setAppState) {
    markKilled(taskId, setAppState)
  },
}

export function killMonitorMcp(
  taskId: string,
  setAppState: SetAppState,
): void {
  markKilled(taskId, setAppState)
}

export function killMonitorMcpTasksForAgent(
  agentId: AgentId,
  getAppState: () => AppState,
  setAppState: SetAppState,
): void {
  for (const task of Object.values(getAppState().tasks ?? {})) {
    if (task.type === 'monitor_mcp' && task.agentId === agentId) {
      markKilled(task.id, setAppState)
    }
  }
}

// One progress row emitted per workflow agent lifecycle event, plus a
// narrator-log variant. Batched by WorkflowTool.call and surfaced via
// emitTaskProgress({workflowProgress}) + the WorkflowDetailDialog.
export type SdkWorkflowAgentProgress = {
  type: 'workflow_agent'
  agentId: string
  label: string
  phase?: string
  phaseTitle?: string
  phaseIndex?: number
  state: 'running' | 'done' | 'error'
  tokens?: number
  toolCalls?: number
  durationMs?: number
  error?: string
}

export type SdkWorkflowLog = {
  type: 'workflow_log'
  message: string
}

export type SdkWorkflowProgress = SdkWorkflowAgentProgress | SdkWorkflowLog

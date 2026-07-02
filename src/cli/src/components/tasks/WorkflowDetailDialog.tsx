// Vendored ink, NOT the npm 'ink' package: npm ink's reconciler has a
// top-level await, which (a) made this whole subtree an async module —
// the module-level require() of it in BackgroundTasksDialog deadlocked
// the source-run REPL boot — and (b) breaks `bun build --compile`
// ("require() async module is unsupported"). Every other component in
// this tree imports from src/ink.js; these two files were the only
// strays (2026-07-02).
import { Box, Text, useInput } from '../../ink.js'
import * as React from 'react'
import type { LocalWorkflowTaskState } from '../../tasks/LocalWorkflowTask/LocalWorkflowTask.js'
import type { SdkWorkflowAgentProgress } from '../../types/tools.js'

type Props = {
  workflow: LocalWorkflowTaskState
  onDone: (msg: string, opts?: { display?: string }) => void
  onKill?: () => void
  onSkipAgent?: (agentId: string) => void
  onRetryAgent?: (agentId: string) => void
  onBack: () => void
}

export function WorkflowDetailDialog({
  workflow,
  onKill,
  onSkipAgent,
  onBack,
}: Props): React.ReactNode {
  const progress = (workflow.workflowProgress ?? []).filter(
    (p): p is SdkWorkflowAgentProgress => p.type === 'workflow_agent',
  )
  const [sel, setSel] = React.useState(0)

  useInput((input, key) => {
    if (key.leftArrow || key.escape) return onBack()
    if (key.upArrow) setSel(s => Math.max(0, s - 1))
    if (key.downArrow) setSel(s => Math.min(Math.max(0, progress.length - 1), s + 1))
    if (input === 's' && onSkipAgent && progress[sel]) onSkipAgent(progress[sel]!.agentId)
    if (input === 'x' && onKill) onKill()
  })

  const glyph = (state: string): string =>
    state === 'done' ? '✓' : state === 'error' ? '✗' : '●'

  return (
    <Box flexDirection="column" borderStyle="round" paddingX={1}>
      <Text bold>
        {workflow.title ?? workflow.workflowName ?? 'Workflow'} — {workflow.status}
      </Text>
      {workflow.summary ? <Text dimColor>{workflow.summary}</Text> : null}
      <Box flexDirection="column" marginTop={1}>
        {progress.length === 0 ? <Text dimColor>No agent activity yet…</Text> : null}
        {progress.map((p, i) => (
          <Text key={`${p.agentId}-${i}`} inverse={i === sel}>
            {glyph(p.state)}{' '}
            {p.phaseTitle ? `[${p.phaseTitle}] ` : ''}
            {p.label}
            {p.tokens ? ` · ${p.tokens}tok` : ''}
            {p.error ? ` · ${p.error}` : ''}
          </Text>
        ))}
      </Box>
      {workflow.status !== 'running' && workflow.workflowRunId ? (
        <Text dimColor>
          Resume: Workflow({'{'} scriptPath, resumeFromRunId: &quot;{workflow.workflowRunId}&quot; {'}'})
        </Text>
      ) : null}
      <Text dimColor>↑/↓ select · s skip · x stop · ←/Esc back</Text>
    </Box>
  )
}

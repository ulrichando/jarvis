import * as React from 'react'
import { Box, Text } from '../../ink.js'
import { PermissionDialog } from '../../components/permissions/PermissionDialog.js'
import { PermissionPrompt } from '../../components/permissions/PermissionPrompt.js'
import type { PermissionRequestProps } from '../../components/permissions/PermissionRequest.js'
import { parseWorkflowMeta } from './meta.js'

type WorkflowOptionValue = 'yes' | 'no'

export function WorkflowPermissionRequest(props: PermissionRequestProps): React.ReactNode {
  const { toolUseConfirm, onDone, onReject, workerBadge } = props

  const script: string = (toolUseConfirm.input as Record<string, unknown>)?.script as string ?? ''
  const parsed = parseWorkflowMeta(script)
  const meta = 'error' in parsed ? null : parsed.meta

  const options: Array<{ label: string; value: WorkflowOptionValue }> = [
    { label: 'Yes, run workflow', value: 'yes' },
    { label: 'No', value: 'no' },
  ]

  function handleSelect(value: WorkflowOptionValue): void {
    if (value === 'yes') {
      toolUseConfirm.onAllow(toolUseConfirm.input as never, [])
      onDone()
    } else {
      toolUseConfirm.onReject()
      onReject()
      onDone()
    }
  }

  function handleCancel(): void {
    toolUseConfirm.onReject()
    onReject()
    onDone()
  }

  const title = meta ? `Run workflow: ${meta.name}` : 'Run dynamic workflow'
  const preview = script.slice(0, 1500) + (script.length > 1500 ? '\n… (truncated)' : '')

  return (
    <PermissionDialog title={title} workerBadge={workerBadge}>
      {meta && (
        <Box flexDirection="column" paddingY={1}>
          <Text>{meta.description}</Text>
          {meta.whenToUse ? <Text dimColor>When to use: {meta.whenToUse}</Text> : null}
          {meta.phases && meta.phases.length > 0 ? (
            <Box flexDirection="column" marginTop={1}>
              <Text bold>Phases:</Text>
              {meta.phases.map((p, i) => (
                <Text key={i} dimColor>  • {p.title}{p.detail ? ` — ${p.detail}` : ''}</Text>
              ))}
            </Box>
          ) : null}
        </Box>
      )}
      <Box flexDirection="column" marginTop={1}>
        <Text bold>Script preview:</Text>
        <Text dimColor>{preview}</Text>
      </Box>
      <PermissionPrompt
        options={options}
        onSelect={handleSelect}
        onCancel={handleCancel}
        question="Do you want to run this workflow?"
      />
    </PermissionDialog>
  )
}

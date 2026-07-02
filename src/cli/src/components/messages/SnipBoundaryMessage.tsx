// Vendored ink, NOT npm 'ink' (its reconciler has a top-level await that
// poisons the import graph async + breaks bun --compile; see
// WorkflowDetailDialog.tsx note, 2026-07-02).
import { Text } from '../../ink.js'
import * as React from 'react'

type Props = {
  message: { snipMetadata?: { removedUuids?: string[]; tokensFreed?: number } }
}

export function SnipBoundaryMessage({ message }: Props): React.ReactNode {
  const n = message.snipMetadata?.removedUuids?.length ?? 0
  const tok = message.snipMetadata?.tokensFreed ?? 0
  return (
    <Text dimColor>
      ✂ {n} message{n === 1 ? '' : 's'} snipped{tok ? ` (~${tok} tokens freed)` : ''}
    </Text>
  )
}

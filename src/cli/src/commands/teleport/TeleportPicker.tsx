import * as React from 'react'
import { useCallback, useEffect, useState } from 'react'

import { Box, Text } from '../../ink.js'
import { useKeybinding } from '../../keybindings/useKeybinding.js'
import { Select } from '../../components/CustomSelect/index.js'
import { Spinner } from '../../components/Spinner.js'
import { KeyboardShortcutHint } from '../../components/design-system/KeyboardShortcutHint.js'
import { Byline } from '../../components/design-system/Byline.js'
import type { LocalJSXCommandOnDone } from '../../types/command.js'
import {
  fetchCloudSessions,
  pullSession,
  type CloudSession,
  type PullResult,
} from './core.js'

// Interactive /teleport (+/tp) picker — the in-REPL counterpart of claude.ai's
// --teleport session picker. `/teleport` opens the arrow-key list; `/teleport
// <id>` pulls that session directly. All work goes through core.ts (no
// process.exit — this runs inside the live session).

function age(ts: number | null): string {
  if (!ts) return ''
  const m = Math.max(0, Math.round((Date.now() - ts) / 60_000))
  if (m < 60) return `${m}m`
  const h = Math.round(m / 60)
  return h < 48 ? `${h}h` : `${Math.round(h / 24)}d`
}

type State =
  | { s: 'loading' }
  | { s: 'picking'; sessions: CloudSession[] }
  | { s: 'pulling'; id: string }
  | { s: 'done'; result: PullResult; id: string }
  | { s: 'empty' }
  | { s: 'error'; message: string }

export function TeleportPicker({
  onDone,
  initialId,
}: {
  onDone: LocalJSXCommandOnDone
  initialId?: string
}): React.ReactNode {
  const [state, setState] = useState<State>({ s: 'loading' })

  const pull = useCallback(async (id: string) => {
    setState({ s: 'pulling', id })
    const r = await pullSession(id)
    if (r.ok) setState({ s: 'done', result: r.result, id })
    else setState({ s: 'error', message: r.error })
  }, [])

  useEffect(() => {
    let cancelled = false
    void (async () => {
      if (initialId) {
        await pull(initialId)
        return
      }
      const r = await fetchCloudSessions()
      if (cancelled) return
      if (!r.ok) setState({ s: 'error', message: r.error })
      else if (r.sessions.length === 0) setState({ s: 'empty' })
      else setState({ s: 'picking', sessions: r.sessions })
    })()
    return () => {
      cancelled = true
    }
  }, [initialId, pull])

  // Esc / Enter dismissal.
  useKeybinding('confirm:no', () => onDone('Teleport cancelled'), { context: 'Confirmation' })
  useKeybinding('confirm:yes', () => onDone('Teleported.'), {
    context: 'Confirmation',
    isActive: state.s === 'done' || state.s === 'error' || state.s === 'empty',
  })

  let body: React.ReactNode = null
  switch (state.s) {
    case 'loading':
      body = (
        <Box>
          <Spinner />
          <Text> Loading your cloud sessions…</Text>
        </Box>
      )
      break
    case 'empty':
      body = (
        <Box flexDirection="column" gap={1}>
          <Text>No cloud sessions yet.</Text>
          <Text dimColor>Start one from a shell: jarvis cloud &quot;fix the failing test&quot;</Text>
          <Text dimColor>
            Press <Text bold>Enter</Text> to close.
          </Text>
        </Box>
      )
      break
    case 'picking': {
      const options = state.sessions.map((s) => ({
        label: `${age(s.updated_at).padEnd(4)} ${s.repo.padEnd(24)} ${s.title.slice(0, 48)}`,
        value: s.session_id,
      }))
      body = (
        <Box flexDirection="column" gap={1}>
          <Text bold>Teleport a cloud session to this machine:</Text>
          <Select
            options={options}
            onChange={(value: string) => {
              void pull(value)
            }}
          />
          <Box>
            <Byline>
              <KeyboardShortcutHint shortcut="↑/↓" action="select" />
              <KeyboardShortcutHint shortcut="↵" action="teleport" />
              <KeyboardShortcutHint shortcut="esc" action="cancel" />
            </Byline>
          </Box>
        </Box>
      )
      break
    }
    case 'pulling':
      body = (
        <Box>
          <Spinner />
          <Text> Teleporting {state.id.slice(0, 12)}… (fetching branch + conversation)</Text>
        </Box>
      )
      break
    case 'done':
      body = (
        <Box flexDirection="column" gap={1}>
          <Text color="success">✓ Teleported {state.id.slice(0, 12)}</Text>
          <Text>
            repo <Text bold>{state.result.repo}</Text> · branch{' '}
            <Text bold>{state.result.branch}</Text> checked out
          </Text>
          <Text dimColor>
            {state.result.resumed
              ? `Continue the conversation:  jarvis --resume ${state.id}`
              : '(no conversation transcript — the container may be gone)'}
          </Text>
          <Text dimColor>
            Press <Text bold>Enter</Text> to close.
          </Text>
        </Box>
      )
      break
    case 'error':
      body = (
        <Box flexDirection="column" gap={1}>
          <Text color="error">{state.message}</Text>
          <Text dimColor>
            Press <Text bold>Enter</Text> to close.
          </Text>
        </Box>
      )
      break
  }

  return (
    <Box flexDirection="column" padding={1}>
      {body}
    </Box>
  )
}

export async function call(
  onDone: LocalJSXCommandOnDone,
  _context: unknown,
  args: string,
): Promise<React.ReactNode> {
  return <TeleportPicker onDone={onDone} initialId={(args ?? '').trim() || undefined} />
}

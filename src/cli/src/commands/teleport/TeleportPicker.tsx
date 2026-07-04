import * as React from 'react'
import { useCallback, useEffect, useState } from 'react'

import { Box, Text } from '../../ink.js'
import { useKeybinding } from '../../keybindings/useKeybinding.js'
import { useTerminalSize } from '../../hooks/useTerminalSize.js'
import { Select } from '../../components/CustomSelect/index.js'
import { Spinner } from '../../components/Spinner.js'
import { KeyboardShortcutHint } from '../../components/design-system/KeyboardShortcutHint.js'
import { Byline } from '../../components/design-system/Byline.js'
import { formatRelativeTime } from '../../utils/format.js'
import type { LocalJSXCommandOnDone } from '../../types/command.js'
import {
  fetchCloudSessions,
  pullSession,
  type CloudSession,
  type PullResult,
} from './core.js'

// Interactive /teleport (+/tp) picker — the in-REPL counterpart of claude.ai's
// --teleport session picker (mirrors the /resume picker, ResumeTask). `/teleport`
// opens the arrow-key list; `/teleport <id>` pulls that session directly. All
// work goes through core.ts (no process.exit — this runs inside the live session).

const UPDATED_STRING = 'Updated'
const REPO_STRING = 'Repository'
const COL_GAP = '  '

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
  // 1-based focused row, for the "(N of M)" scroll position in the title.
  const [focusedIndex, setFocusedIndex] = useState(1)
  const { rows } = useTerminalSize()

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
      const meta = state.sessions.map((s) => ({
        ...s,
        timeString: s.updated_at ? formatRelativeTime(new Date(s.updated_at)) : '',
      }))
      const timeW = Math.max(UPDATED_STRING.length, ...meta.map((m) => m.timeString.length))
      const repoW = Math.max(REPO_STRING.length, ...meta.map((m) => m.repo.length))
      const options = meta.map((m) => ({
        label: `${m.timeString.padEnd(timeW)}${COL_GAP}${m.repo.padEnd(repoW)}${COL_GAP}${m.title}`,
        value: m.session_id,
      }))
      // Layout overhead: padding + title + header + footer (~7 rows).
      const maxVisible = Math.max(1, Math.min(state.sessions.length, rows - 8))
      const showScroll = state.sessions.length > maxVisible
      body = (
        <Box flexDirection="column">
          <Text bold>
            Select a cloud session to teleport
            {showScroll && (
              <Text dimColor>
                {' '}
                ({focusedIndex} of {state.sessions.length})
              </Text>
            )}
            :
          </Text>
          <Box flexDirection="column" marginTop={1}>
            <Box marginLeft={2}>
              <Text bold dimColor>
                {UPDATED_STRING.padEnd(timeW)}
                {COL_GAP}
                {REPO_STRING.padEnd(repoW)}
                {COL_GAP}
                Session Title
              </Text>
            </Box>
            <Select
              visibleOptionCount={maxVisible}
              options={options}
              onChange={(value: string) => {
                void pull(value)
              }}
              onFocus={(value: string) => {
                const i = options.findIndex((o) => o.value === value)
                if (i >= 0) setFocusedIndex(i + 1)
              }}
            />
          </Box>
          <Box marginTop={1}>
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
    case 'done': {
      const { repo, branch, resumed, clonedPath } = state.result
      // cross-repo teleport → we cloned it elsewhere; you continue from there.
      const continueCmd = clonedPath
        ? `cd ${clonedPath} && jarvis --resume ${state.id}`
        : `jarvis --resume ${state.id}`
      body = (
        <Box flexDirection="column" gap={1}>
          <Text color="success">✓ Teleported {state.id.slice(0, 12)}</Text>
          <Text>
            repo <Text bold>{repo}</Text> · branch <Text bold>{branch}</Text>
            {clonedPath ? (
              <Text>
                {' '}
                cloned to <Text bold>{clonedPath}</Text>
              </Text>
            ) : (
              <Text> checked out</Text>
            )}
          </Text>
          <Text dimColor>
            {resumed
              ? `Continue the conversation:  ${continueCmd}`
              : '(no conversation transcript — the container may be gone)'}
          </Text>
          <Text dimColor>
            Press <Text bold>Enter</Text> to close.
          </Text>
        </Box>
      )
      break
    }
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

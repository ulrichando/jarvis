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
import { detectCurrentRepository } from '../../utils/detectRepository.js'
import { getLastSessionLog } from '../../utils/sessionStorage.js'
import type { UUID } from 'node:crypto'
import type { LocalJSXCommandContext } from '../../commands.js'
import type { LocalJSXCommandOnDone } from '../../types/command.js'
import { fetchCloudSessions, prepareTeleport, type CloudSession } from './core.js'

// Interactive /teleport (+/tp) picker — claude.ai's --teleport, self-hosted.
// SAME-REPO ONLY (like claude.ai): shows the cloud sessions for the repo you're
// in, and on select checks out the branch + RESUMES the conversation in place
// via context.resume — no clone, no "Press Enter" dead-end. A session for a
// different repo tells you to cd into that repo first.

const UPDATED_STRING = 'Updated'
const COL_GAP = '  '

type State =
  | { s: 'loading' }
  | { s: 'picking'; sessions: CloudSession[]; repo: string }
  | { s: 'resuming'; id: string }
  | { s: 'done'; branch: string }
  | { s: 'empty'; repo: string | null }
  | { s: 'error'; message: string }

export function TeleportPicker({
  onDone,
  context,
  initialId,
}: {
  onDone: LocalJSXCommandOnDone
  context?: LocalJSXCommandContext
  initialId?: string
}): React.ReactNode {
  const [state, setState] = useState<State>({ s: 'loading' })
  const [focusedIndex, setFocusedIndex] = useState(1)
  const { rows } = useTerminalSize()

  const teleport = useCallback(
    async (id: string) => {
      setState({ s: 'resuming', id })
      const r = await prepareTeleport(id)
      if (!r.ok) {
        setState({ s: 'error', message: r.error })
        return
      }
      if (r.prep.kind === 'resume' && context?.resume) {
        try {
          const log = await getLastSessionLog(r.prep.resumeId as UUID)
          if (log) {
            await context.resume(r.prep.resumeId as UUID, log, 'slash_command_picker')
            onDone(undefined, { display: 'skip' }) // resumed — hand control to the session
            return
          }
        } catch {
          /* fall through to the done screen */
        }
      }
      // Branch checked out but no conversation to resume (or no live REPL context).
      setState({ s: 'done', branch: r.prep.branch })
    },
    [context, onDone],
  )

  useEffect(() => {
    let cancelled = false
    void (async () => {
      if (initialId) {
        await teleport(initialId)
        return
      }
      const [repo, r] = await Promise.all([detectCurrentRepository(), fetchCloudSessions()])
      if (cancelled) return
      if (!r.ok) {
        setState({ s: 'error', message: r.error })
        return
      }
      // Claude parity: only sessions for the repo you're in are teleportable.
      const mine = repo ? r.sessions.filter((s) => s.repo.toLowerCase() === repo.toLowerCase()) : []
      if (mine.length === 0) setState({ s: 'empty', repo })
      else setState({ s: 'picking', sessions: mine, repo: repo! })
    })()
    return () => {
      cancelled = true
    }
  }, [initialId, teleport])

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
          <Text>
            No cloud sessions for {state.repo ? <Text bold>{state.repo}</Text> : 'this directory'}.
          </Text>
          <Text dimColor>
            Teleport is per-repo (like claude.ai): open the repo whose session you want (cd into
            that checkout), then run /teleport there.
          </Text>
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
      const options = meta.map((m) => ({
        label: `${m.timeString.padEnd(timeW)}${COL_GAP}${m.title}`,
        value: m.session_id,
      }))
      const maxVisible = Math.max(1, Math.min(state.sessions.length, rows - 8))
      const showScroll = state.sessions.length > maxVisible
      body = (
        <Box flexDirection="column">
          <Text bold>
            Select a session to teleport
            {showScroll && (
              <Text dimColor>
                {' '}
                ({focusedIndex} of {state.sessions.length})
              </Text>
            )}
            <Text dimColor> ({state.repo})</Text>:
          </Text>
          <Box flexDirection="column" marginTop={1}>
            <Box marginLeft={2}>
              <Text bold dimColor>
                {UPDATED_STRING.padEnd(timeW)}
                {COL_GAP}
                Session Title
              </Text>
            </Box>
            <Select
              visibleOptionCount={maxVisible}
              options={options}
              onChange={(value: string) => {
                void teleport(value)
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
    case 'resuming':
      body = (
        <Box>
          <Spinner />
          <Text> Teleporting {state.id.slice(0, 12)}… (checkout + conversation)</Text>
        </Box>
      )
      break
    case 'done':
      body = (
        <Box flexDirection="column" gap={1}>
          <Text color="success">✓ Teleported — branch {state.branch} checked out.</Text>
          <Text dimColor>
            This session had no saved conversation to resume (it never ran, or its data was
            reclaimed). The code is ready on the branch.
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
  context: LocalJSXCommandContext,
  args: string,
): Promise<React.ReactNode> {
  return <TeleportPicker onDone={onDone} context={context} initialId={(args ?? '').trim() || undefined} />
}

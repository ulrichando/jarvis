import * as React from 'react'
import { useEffect } from 'react'
import { useSetAppState } from 'src/state/AppState.js'
import { LOCAL_COMMAND_STDOUT_TAG } from '../constants/xml.js'
import { Box, Text } from '../ink.js'
import type { AppState } from '../state/AppStateStore.js'
import type { RemoteAgentTaskState } from '../tasks/RemoteAgentTask/RemoteAgentTask.js'
import { logForDebugging } from '../utils/debug.js'
import { logError } from '../utils/log.js'
import { enqueuePendingNotification } from '../utils/messageQueueManager.js'
import { createCommandInputMessage } from '../utils/messages.js'
import { updateTaskState } from '../utils/task/framework.js'
import { archiveRemoteSession } from '../utils/teleport.js'
import { escapeXml } from '../utils/xml.js'
import { Select } from './CustomSelect/index.js'
import { Dialog } from './design-system/Dialog.js'

// Post-approval dialog for a teleported ultraplan: the remote session's plan
// was approved with execution target "local", and the user decides what to do
// with it here. Authored 2026-07-09 — like UltraplanLaunchDialog, REPL.tsx
// referenced this component from the fork's inception but the implementation
// was stripped from the public donor build (rendering it crashed the REPL).
//
// Per the contracts documented in commands/ultraplan.tsx:
// - startDetachedPoll's teleport path "leaves status=running so the pill shows
//   the ultraplanPhase state until UltraplanChoiceDialog completes the task
//   after the user's choice" → we mark the task completed on choice.
// - "The dialog owns archive + URL clear on choice" → we archive the remote
//   session and clear ultraplanSessionUrl + ultraplanPendingChoice.
//
// Choices: implement the plan in THIS session (plan enters the transcript and
// a task-notification asks Jarvis to start on it), or dismiss (plan still
// lands in the transcript for reference; nothing else happens). A "fresh
// session" option (the setConversationId prop) is deliberately not offered
// yet — seeding a brand-new conversation is untested machinery; the prop is
// accepted to match REPL's callsite.
type Props = {
  plan: string
  sessionId: string
  taskId: string
  setMessages: React.Dispatch<React.SetStateAction<unknown[]>>
  readFileState?: unknown
  getAppState: () => AppState
  setConversationId?: (id: string) => void
}

const PREVIEW_LINES = 12

export function UltraplanChoiceDialog({
  plan,
  sessionId,
  taskId,
  setMessages,
}: Props): React.ReactNode {
  // One-shot guard — Select/escape should only act once even if a re-render
  // races the unmount that clearing ultraplanPendingChoice triggers.
  const chosenRef = React.useRef(false)
  const setAppState = useSetAppState()

  useEffect(() => {
    // The plan is here; the remote session has nothing left to do. Archive
    // eagerly (idempotent server-side) so a 30-minute orphan can't outlive a
    // user who walks away from the dialog. Failure is non-fatal — finish()
    // retries on choice.
    void archiveRemoteSession(sessionId).catch(e =>
      logForDebugging(`ultraplan choice: eager archive failed: ${String(e)}`),
    )
  }, [sessionId])

  const finish = (implementHere: boolean) => {
    if (chosenRef.current) return
    chosenRef.current = true

    // Plan always lands in the transcript so it isn't lost with the dialog.
    setMessages(prev => [
      ...prev,
      createCommandInputMessage(
        `<${LOCAL_COMMAND_STDOUT_TAG}>${escapeXml(
          `Ultraplan result:\n\n${plan}`,
        )}</${LOCAL_COMMAND_STDOUT_TAG}>`,
      ),
    ])
    if (implementHere) {
      enqueuePendingNotification({
        value: `The user approved this plan from an ultraplan session and chose to implement it HERE, in this session. Begin implementing it now:\n\n${plan}`,
        mode: 'task-notification',
        isMeta: true,
      })
    }

    // Cleanup owned by this dialog (see contract above): retry the archive,
    // complete the task so the pill settles, clear the URL (re-arms the
    // keyword trigger) and the pendingChoice (unmounts us). Writes go through
    // the app-state store REPL's getAppState reads from.
    void archiveRemoteSession(sessionId).catch(e =>
      logForDebugging(`ultraplan choice: archive failed: ${String(e)}`),
    )
    try {
      updateTaskState<RemoteAgentTaskState>(taskId, setAppState, t =>
        t.status !== 'running'
          ? t
          : { ...t, status: 'completed', endTime: Date.now() },
      )
      setAppState(prev => ({
        ...prev,
        ultraplanSessionUrl: undefined,
        ultraplanPendingChoice: undefined,
      }))
    } catch (e) {
      logError(e)
    }
  }

  const lines = plan.split('\n')
  const preview = lines.slice(0, PREVIEW_LINES).join('\n')
  const truncated = lines.length > PREVIEW_LINES

  return (
    <Dialog
      title="Ultraplan ready"
      subtitle="The remote session finished planning"
      onCancel={() => finish(false)}
    >
      <Box flexDirection="column" gap={1}>
        <Text>{preview}</Text>
        {truncated ? (
          <Text dimColor>
            … {lines.length - PREVIEW_LINES} more lines — the full plan lands in
            the transcript on choice.
          </Text>
        ) : null}
      </Box>
      <Select
        options={[
          { label: 'Implement the plan in this session', value: 'implement' },
          { label: 'Dismiss (keep the plan in the transcript)', value: 'dismiss' },
        ]}
        onChange={value => finish(value === 'implement')}
      />
    </Dialog>
  )
}

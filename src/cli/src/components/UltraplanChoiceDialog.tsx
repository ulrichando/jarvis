import * as React from 'react'
import { useEffect } from 'react'
import { useSetAppState } from 'src/state/AppState.js'
import { getSessionId } from '../bootstrap/state.js'
import { clearConversation } from '../commands/clear/conversation.js'
import { LOCAL_COMMAND_STDOUT_TAG } from '../constants/xml.js'
import { Box, Text } from '../ink.js'
import type { AppState } from '../state/AppStateStore.js'
import type { RemoteAgentTaskState } from '../tasks/RemoteAgentTask/RemoteAgentTask.js'
import { logForDebugging } from '../utils/debug.js'
import type { FileStateCache } from '../utils/fileStateCache.js'
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
// Choices:
// - implement: the plan enters THIS session's transcript and a
//   task-notification asks Jarvis to start on it.
// - new-session: reuses clearConversation for the core reset (messages,
//   session id, caches, cwd), then seeds the fresh transcript with only the
//   plan plus a `jarvis --resume <old session id>` hint so the user can
//   return. The REPL-local resets a full /clear performs AFTER
//   clearConversation (haiku title, bashTools cache) are not applied —
//   acceptable for a plan-seeded fresh session.
// - dismiss: the plan still lands in the transcript for reference; nothing
//   else happens.
type Choice = 'implement' | 'new-session' | 'dismiss'

type Props = {
  plan: string
  sessionId: string
  taskId: string
  setMessages: React.Dispatch<React.SetStateAction<unknown[]>>
  readFileState?: unknown
  getAppState: () => AppState
  setConversationId?: (id: string) => void
  // REPL's session-scoped dedup caches — passed to clearConversation so a
  // new-session reset re-attaches nested CLAUDE.md files and re-discovers
  // skills, matching the real /clear callsites.
  discoveredSkillNames?: Set<string>
  loadedNestedMemoryPaths?: Set<string>
}

const PREVIEW_LINES = 12

export function UltraplanChoiceDialog({
  plan,
  sessionId,
  taskId,
  setMessages,
  readFileState,
  getAppState,
  setConversationId,
  discoveredSkillNames,
  loadedNestedMemoryPaths,
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

  // Cleanup owned by this dialog (see contract above), split in two so the
  // new-session path can drop the dialog BEFORE its slow await (see finish):
  //
  // completeTask: retry the archive + complete the task so the pill settles.
  // clearDialogState: clear the URL (re-arms the keyword trigger) and the
  //   pendingChoice (unmounts us).
  //
  // Writes go through the app-state store REPL's getAppState reads from.
  const completeTask = () => {
    void archiveRemoteSession(sessionId).catch(e =>
      logForDebugging(`ultraplan choice: archive failed: ${String(e)}`),
    )
    try {
      updateTaskState<RemoteAgentTaskState>(taskId, setAppState, t =>
        t.status !== 'running'
          ? t
          : { ...t, status: 'completed', endTime: Date.now() },
      )
    } catch (e) {
      logError(e)
    }
  }
  const clearDialogState = () => {
    try {
      setAppState(prev => ({
        ...prev,
        ultraplanSessionUrl: undefined,
        ultraplanPendingChoice: undefined,
      }))
    } catch (e) {
      logError(e)
    }
  }

  const finish = async (choice: Choice) => {
    if (chosenRef.current) return
    chosenRef.current = true

    // Dismiss/implement path (and the new-session fallback): the plan lands
    // in the CURRENT transcript so it isn't lost with the dialog.
    const appendPlanToTranscript = () => {
      setMessages(prev => [
        ...prev,
        createCommandInputMessage(
          `<${LOCAL_COMMAND_STDOUT_TAG}>${escapeXml(
            `Ultraplan result:\n\n${plan}`,
          )}</${LOCAL_COMMAND_STDOUT_TAG}>`,
        ),
      ])
    }

    if (choice === 'new-session') {
      // Unmount FIRST: clearConversation can take seconds (SessionEnd/
      // SessionStart hooks), and if the dialog stayed mounted a focus flap
      // during that window could remount it with a fresh chosenRef and
      // re-run. The async work below continues after unmount — setMessages/
      // setAppState are stable store writers, not component state.
      clearDialogState()
      // Capture the PERSISTED session id BEFORE clearConversation
      // regenerates it (regenerateSessionId) — `jarvis --resume` resolves
      // session logs by this id (bootstrap getSessionId), NOT by REPL's
      // conversationId render key.
      const oldSessionId = getSessionId()
      try {
        // The canonical fresh-session reset (/clear's core machinery):
        // clears messages, bumps conversationId, clears session caches
        // (incl. the skill/nested-memory dedup sets passed above), resets
        // cwd, regenerates the session id, and re-persists state. If
        // setConversationId is absent, clearConversation tolerates it.
        await clearConversation({
          setMessages: setMessages as unknown as Parameters<
            typeof clearConversation
          >[0]['setMessages'],
          readFileState: readFileState as FileStateCache,
          discoveredSkillNames,
          loadedNestedMemoryPaths,
          getAppState,
          setAppState,
          setConversationId,
        })
        // Seed the fresh session with only the plan as context.
        setMessages(() => [
          createCommandInputMessage(
            `<${LOCAL_COMMAND_STDOUT_TAG}>${escapeXml(
              `Started a fresh session from your approved ultraplan.\n\nReturn to your previous conversation with:\n  jarvis --resume ${oldSessionId}\n\nPlan:\n${plan}`,
            )}</${LOCAL_COMMAND_STDOUT_TAG}>`,
          ),
        ])
      } catch (e) {
        // Reset failed — never lose the plan: fall back to the dismiss
        // behavior (plan appended to the current, un-cleared transcript).
        logError(e)
        appendPlanToTranscript()
      }
      completeTask()
      return
    }

    appendPlanToTranscript()
    if (choice === 'implement') {
      enqueuePendingNotification({
        value: `The user approved this plan from an ultraplan session and chose to implement it HERE, in this session. Begin implementing it now:\n\n${plan}`,
        mode: 'task-notification',
        isMeta: true,
      })
    }
    completeTask()
    clearDialogState()
  }

  const lines = plan.split('\n')
  const preview = lines.slice(0, PREVIEW_LINES).join('\n')
  const truncated = lines.length > PREVIEW_LINES

  return (
    <Dialog
      title="Ultraplan ready"
      subtitle="The remote session finished planning"
      onCancel={() => void finish('dismiss')}
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
          { label: 'Start a new session with this plan', value: 'new-session' },
          { label: 'Dismiss (keep the plan in the transcript)', value: 'dismiss' },
        ]}
        onChange={value => void finish(value as Choice)}
      />
    </Dialog>
  )
}

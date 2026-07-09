import * as React from 'react'
import { useCallback, useState } from 'react'

import { Box, Text } from '../../ink.js'
import { ResumeTask } from '../../components/ResumeTask.js'
import { Spinner } from '../../components/Spinner.js'
import type { CodeSession } from '../../utils/teleport/api.js'
import {
  checkOutTeleportedSessionBranch,
  processMessagesForTeleportResume,
  teleportResumeCodeSession,
} from '../../utils/teleport.js'
import { errorMessage } from '../../utils/errors.js'
import { switchToRepoCheckout } from './repoSwitch.js'
import type { LocalJSXCommandContext } from '../../commands.js'
import type { LocalJSXCommandOnDone } from '../../types/command.js'

// /teleport (+/tp): pull a cloud /code session to this machine and resume it,
// like claude.ai's --teleport. Reuses the fork's REAL machinery — ResumeTask
// (Claude's session list) + teleportResumeCodeSession/checkOut/processMessages
// (Claude's resume) — so we don't reimplement any of it (that from-scratch
// attempt is what caused the 2026-07 whack-a-mole).
//
// The one thing we add on top of Claude's picker: auto-switch. Claude's
// /teleport only resumes IN PLACE and hard-errors if you're not in the
// session's repo (its checkout-switch lives only on the `--teleport` startup
// flag). Since our picker lists sessions across ALL repos, selecting one for a
// repo you're not in would dead-end. switchToRepoCheckout() hops into a local
// checkout first (mirroring the flag's process.chdir), so the resume validates.
function TeleportPickerUI(props: {
  onDone: LocalJSXCommandOnDone
  context: LocalJSXCommandContext
}): React.ReactNode {
  const { onDone, context } = props
  const [status, setStatus] = useState<string | null>(null)

  const onSelect = useCallback(
    (session: CodeSession) => {
      void (async () => {
        // switchToRepoCheckout chdirs into the session's checkout BEFORE the
        // resume runs. If a later step throws, the working directory has
        // already moved — surface that in the error so the strand isn't
        // silent/confusing (full state-restore isn't attempted: the switch
        // mutates several coupled caches and a partial undo risks a worse
        // inconsistent state — the user is validly in the session's repo).
        let switchedPath: string | null = null
        try {
          setStatus(`Resuming "${session.title || 'session'}"…`)
          const sw = await switchToRepoCheckout(session)
          if (sw.kind === 'not_found') {
            onDone(
              `This session is for ${sw.repo}, which isn't checked out here. ` +
                `Clone it, then run: cd <checkout> && jarvis --teleport ${session.id}`,
            )
            return
          }
          if (sw.kind === 'switched') {
            switchedPath = sw.path
            setStatus(`Switched to ${sw.path} · resuming…`)
          }
          const result = await teleportResumeCodeSession(session.id)
          const { branchError } = await checkOutTeleportedSessionBranch(
            result.branch,
          )
          const messages = processMessagesForTeleportResume(
            result.log,
            branchError,
          )
          // Load the teleported conversation into the current session.
          context.setMessages(() => messages)
          onDone(undefined, { display: 'skip' })
        } catch (e) {
          onDone(
            `Teleport failed: ${errorMessage(e)}` +
              (switchedPath
                ? ` — note: your working directory was changed to ${switchedPath} (the session's repo). Retry the teleport there, or cd back to your previous directory.`
                : ''),
          )
        }
      })()
    },
    [onDone, context],
  )

  if (status) {
    return (
      <Box flexDirection="row" padding={1}>
        <Spinner />
        <Text> {status}</Text>
      </Box>
    )
  }

  return (
    <ResumeTask onSelect={onSelect} onCancel={() => onDone('Teleport cancelled')} />
  )
}

export async function call(
  onDone: LocalJSXCommandOnDone,
  context: LocalJSXCommandContext,
  _args: string,
): Promise<React.ReactNode> {
  return <TeleportPickerUI onDone={onDone} context={context} />
}

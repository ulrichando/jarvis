import * as React from 'react'

import { TeleportResumeWrapper } from '../../components/TeleportResumeWrapper.js'
import {
  checkOutTeleportedSessionBranch,
  processMessagesForTeleportResume,
} from '../../utils/teleport.js'
import type { LocalJSXCommandContext } from '../../commands.js'
import type { LocalJSXCommandOnDone } from '../../types/command.js'

// /teleport (+/tp): pull a cloud /code session to this machine and resume it,
// exactly like claude.ai's --teleport. This is a THIN wrapper over the fork's
// REAL teleport machinery (TeleportResumeWrapper + useTeleportResume), which
// hits the JARVIS server in JARVIS mode (JARVIS_CCR_BASE_URL + JARVIS_CCR_TOKEN,
// set by start-env.sh). The wrapper renders the session picker, checks out the
// branch, and returns the conversation; we load it into the live REPL. We do NOT
// reimplement any of this — that from-scratch attempt is what caused the
// whack-a-mole (see 2026-07 teleport rebuild).
export async function call(
  onDone: LocalJSXCommandOnDone,
  context: LocalJSXCommandContext,
  _args: string,
): Promise<React.ReactNode> {
  return (
    <TeleportResumeWrapper
      source="cliArg"
      onCancel={() => onDone('Teleport cancelled')}
      onError={(msg) => onDone(msg)}
      onComplete={(result) => {
        void (async () => {
          try {
            const { branchError } = await checkOutTeleportedSessionBranch(result.branch)
            const messages = processMessagesForTeleportResume(result.log, branchError)
            // Load the teleported conversation into the current session.
            context.setMessages(() => messages)
            onDone(undefined, { display: 'skip' })
          } catch (e) {
            onDone(`Teleport failed: ${e instanceof Error ? e.message : String(e)}`)
          }
        })()
      }}
    />
  )
}

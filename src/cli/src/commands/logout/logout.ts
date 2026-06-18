import { jarvisAuthLogout } from '../../cli/handlers/jarvisAuth.js'
import type { LocalJSXCommandContext } from '../../commands.js'
import type { LocalCommandResult } from '../../types/command.js'
import { resetUserCache } from '../../utils/user.js'

export async function call(
  _args: string,
  context: LocalJSXCommandContext,
): Promise<LocalCommandResult> {
  const removed = await jarvisAuthLogout({ quiet: true })
  // Rebuild the API client (jarvisAuthLogout dropped ANTHROPIC_AUTH_TOKEN) and
  // re-fetch auth-dependent data so the now-tokenless session is consistent.
  context.onChangeAPIKey()
  resetUserCache()
  context.setAppState(prev => ({ ...prev, authVersion: prev.authVersion + 1 }))
  return {
    type: 'text',
    value: removed
      ? 'Disconnected this machine from your JARVIS server. Remote Control ' +
        'credentials were removed from ~/.jarvis/keys.env; the local proxy ' +
        'returns to open (loopback-only) on its next restart.'
      : 'No JARVIS Remote Control credentials were stored on this machine.',
  }
}

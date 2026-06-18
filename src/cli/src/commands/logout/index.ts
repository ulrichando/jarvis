import type { Command } from '../../commands.js'
import { isEnvTruthy } from '../../utils/envUtils.js'

// JARVIS self-hosted sign-out (clears this machine's Remote Control + proxy
// credentials). REPLACES the upstream claude.ai /logout in proxy mode. A plain
// `local` text command — no browser, no process exit. The Anthropic logout
// teardown (performLogout / clearAuthRelatedCaches) still lives alongside in
// ./anthropicLogout.ts for the `auth.ts` logout path.
export default {
  type: 'local',
  name: 'logout',
  description: 'Disconnect this machine from your JARVIS server',
  isEnabled: () => !isEnvTruthy(process.env.DISABLE_LOGOUT_COMMAND),
  supportsNonInteractive: true,
  load: () => import('./logout.js'),
} satisfies Command

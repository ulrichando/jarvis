import { getJarvisBridgeStatus } from '../../cli/handlers/jarvisAuth.js'
import type { Command } from '../../commands.js'
import { isEnvTruthy } from '../../utils/envUtils.js'

// JARVIS self-hosted sign-in (the `jarvis auth login` flow, in-REPL). This
// REPLACES the upstream claude.ai /login: the CLI always runs in proxy mode
// (JARVIS_DISABLE_AUTH=1), so the Anthropic OAuth flow is dead here. The
// browser-loopback component lives in ./login.tsx.
export default () =>
  ({
    type: 'local-jsx',
    name: 'login',
    description: getJarvisBridgeStatus().tokenConfigured
      ? 'Switch JARVIS server / re-authenticate'
      : 'Sign in to your JARVIS server',
    isEnabled: () => !isEnvTruthy(process.env.DISABLE_LOGIN_COMMAND),
    load: () => import('./login.js'),
  }) satisfies Command

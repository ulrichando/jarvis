import { feature } from 'bun:bundle'
import type { Command } from '../../commands.js'

// Surface the command whenever BRIDGE_MODE is built-in. The original
// upstream check also called isBridgeEnabled() (claude.ai OAuth +
// `tengu_ccr_bridge` GrowthBook flag) — but the JARVIS fork uses
// Groq/DeepSeek without OAuth, so that gate always returned false and
// the command stayed permanently hidden. We let it surface here; the
// underlying bridge connection still validates entitlement at invocation
// time and returns a typed error if CCR auth is missing.
function isEnabled(): boolean {
  return feature('BRIDGE_MODE') ? true : false
}

const bridge = {
  type: 'local-jsx',
  name: 'remote-control',
  aliases: ['rc'],
  description: 'Connect this terminal for remote-control sessions',
  argumentHint: '[name]',
  isEnabled,
  get isHidden() {
    return !isEnabled()
  },
  immediate: true,
  load: () => import('./bridge.js'),
} satisfies Command

export default bridge

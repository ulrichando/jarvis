import { getIsNonInteractiveSession } from '../../bootstrap/state.js'
import type { Command } from '../../commands.js'

const command: Command = {
  name: 'chrome',
  description: 'Jarvis in Chrome (Beta) settings',
  // Upstream gated this to claude-ai subscribers; JARVIS runs in proxy/no-auth
  // mode (not a claude.ai subscriber) so that gate hid the command entirely.
  // The Jarvis-in-Chrome extension is local (native host + MCP), so make it
  // universal — actual use is still gated by setup + the interactive check.
  isEnabled: () => !getIsNonInteractiveSession(),
  type: 'local-jsx',
  load: () => import('./chrome.js'),
}

export default command

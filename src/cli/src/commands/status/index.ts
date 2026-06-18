import { getIsNonInteractiveSession } from '../../bootstrap/state.js'
import type { Command } from '../../commands.js'

const status = {
  type: 'local-jsx',
  name: 'status',
  description:
    'Show Jarvis status including version, model, account, API connectivity, and tool statuses',
  immediate: true,
  load: () => import('./status.js'),
} satisfies Command

export default status

// Text variant for non-interactive (--print / SDK / Remote Control + `/code`
// container) sessions, where the React/Ink UI can't render. Without it,
// `/status` falls through to skill resolution and errors "Unknown skill:
// status". Mirrors the `/context` dual-definition pattern.
export const statusNonInteractive: Command = {
  type: 'local',
  name: 'status',
  description: 'Show Jarvis status (model, account, working directory)',
  supportsNonInteractive: true,
  get isHidden() {
    return !getIsNonInteractiveSession()
  },
  isEnabled() {
    return getIsNonInteractiveSession()
  },
  load: () => import('./status-noninteractive.js'),
}

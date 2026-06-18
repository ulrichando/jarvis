import { getIsNonInteractiveSession } from '../../bootstrap/state.js'
import type { Command } from '../../commands.js'

const memory: Command = {
  type: 'local-jsx',
  name: 'memory',
  description: 'Edit Jarvis memory files',
  load: () => import('./memory.js'),
}

export default memory

// Text variant for non-interactive (--print / SDK / Remote Control + `/code`
// container) sessions, where the editor UI can't open. Lists the active memory
// files instead. Without it, `/memory` errors "Unknown skill: memory".
export const memoryNonInteractive: Command = {
  type: 'local',
  name: 'memory',
  description: 'List active Jarvis memory files',
  supportsNonInteractive: true,
  get isHidden() {
    return !getIsNonInteractiveSession()
  },
  isEnabled() {
    return getIsNonInteractiveSession()
  },
  load: () => import('./memory-noninteractive.js'),
}

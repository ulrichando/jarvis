import { getIsNonInteractiveSession } from '../../bootstrap/state.js'
import type { Command } from '../../commands.js'

const skills = {
  type: 'local-jsx',
  name: 'skills',
  description: 'List available skills',
  load: () => import('./skills.js'),
} satisfies Command

export default skills

// Text variant for non-interactive (--print / SDK / Remote Control + `/code`
// container) sessions, where the selectable menu can't render. Without it,
// `/skills` errors "Unknown skill: skills". Mirrors `/context`.
export const skillsNonInteractive: Command = {
  type: 'local',
  name: 'skills',
  description: 'List available skills',
  supportsNonInteractive: true,
  get isHidden() {
    return !getIsNonInteractiveSession()
  },
  isEnabled() {
    return getIsNonInteractiveSession()
  },
  load: () => import('./skills-noninteractive.js'),
}

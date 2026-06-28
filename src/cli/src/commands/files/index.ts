import type { Command } from '../../commands.js'

const files = {
  type: 'local',
  name: 'files',
  description: 'List all files currently in context',
  // Enabled for JARVIS (external): harmless read-only context listing. Was
  // ant-only upstream; no backend dependency, so safe to surface.
  isEnabled: () => true,
  supportsNonInteractive: true,
  load: () => import('./files.js'),
} satisfies Command

export default files

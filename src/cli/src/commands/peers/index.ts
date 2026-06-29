import type { Command } from '../../commands.js'

const peers = {
  type: 'local',
  name: 'peers',
  description: 'List local and Remote Control peer sessions',
  supportsNonInteractive: true,
  load: () => import('./peers.js'),
} satisfies Command

export default peers

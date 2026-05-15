import type { Command } from '../../commands.js'

const env = {
  type: 'local',
  name: 'env',
  description: 'Show environment info (stub: source files gitignored by env/)',
  supportsNonInteractive: true,
  load: () => import('./env.js'),
} satisfies Command

export default env

import type { Command } from '../../commands.js'

const env = {
  type: 'local',
  name: 'env',
  description: 'Show environment info (platform, runtime, terminal, versions)',
  isEnabled: () => true,
  supportsNonInteractive: true,
  load: () => import('./env.js'),
} satisfies Command

export default env

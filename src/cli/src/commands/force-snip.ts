import type { Command } from '../commands.js'

const forceSnip = {
  type: 'local',
  name: 'force-snip',
  description: 'Force a history-snip pass if the runtime supports it',
  supportsNonInteractive: true,
  load: () => import('./force-snip-command.js'),
} satisfies Command

export default forceSnip

import type { Command } from '../../commands.js'

const recap = {
  type: 'local',
  name: 'recap',
  description:
    'Recap this session now — the same card the away summary shows automatically',
  supportsNonInteractive: true,
  load: () => import('./recap.js'),
} satisfies Command

export default recap

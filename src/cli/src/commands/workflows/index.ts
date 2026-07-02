import type { Command } from '../../commands.js'

const workflows = {
  type: 'local',
  name: 'workflows',
  description: 'List workflow-script availability',
  supportsNonInteractive: true,
  load: () => import('./workflows.js'),
} satisfies Command

export default workflows

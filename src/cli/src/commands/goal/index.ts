import type { Command } from '../../commands.js'

export default {
  type: 'local-jsx',
  name: 'goal',
  description:
    'Set a completion condition — auto-continues turns until an evaluator judges it met',
  argumentHint: '[condition | clear]',
  // Never renders JSX (call() always invokes onDone and returns null), so it
  // is safe headless — required for -p testability of the goal loop.
  supportsNonInteractive: true,
  load: () => import('./goal.js'),
} satisfies Command

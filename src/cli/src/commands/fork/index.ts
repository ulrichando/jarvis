import type { Command } from '../../commands.js'

const fork = {
  type: 'prompt',
  name: 'fork',
  description: 'Start a forked worker with the current conversation context',
  argumentHint: '<directive>',
  progressMessage: 'forking worker',
  contentLength: 180,
  allowedTools: ['Agent'],
  source: 'builtin',
  async getPromptForCommand(args) {
    const directive = args.trim()
    return [
      {
        type: 'text' as const,
        text:
          directive.length > 0
            ? `Use the Agent tool without subagent_type to fork a worker for this directive: ${directive}`
            : 'Ask the user for the directive to give the forked worker.',
      },
    ]
  },
} satisfies Command

export default fork

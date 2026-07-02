import type { ContentBlockParam } from '@anthropic-ai/sdk/resources/index.mjs'
import type { Command } from '../../commands.js'
import { getAllWorkflows } from './namedWorkflows.js'

// Each named workflow → a prompt-type slash command instructing the model to
// invoke Workflow({name}). $ARGUMENTS flows into the workflow args.
export async function getWorkflowCommands(cwd: string): Promise<Command[]> {
  const workflows = await getAllWorkflows(cwd)
  return workflows.map(w => ({
    type: 'prompt' as const,
    name: w.name,
    description: w.description,
    isEnabled: () => true,
    isHidden: false,
    progressMessage: `running workflow ${w.name}`,
    contentLength: 0,
    source: 'builtin' as const,
    kind: 'workflow' as const,
    whenToUse: w.whenToUse,
    userFacingName: () => w.name,
    async getPromptForCommand(
      args: string,
      _context: unknown,
    ): Promise<ContentBlockParam[]> {
      return [
        {
          type: 'text' as const,
          text: `Run the "${w.name}" workflow via the Workflow tool: Workflow({ name: ${JSON.stringify(w.name)}${args ? `, args: ${JSON.stringify({ input: args })}` : ''} }).`,
        },
      ]
    },
  })) as unknown as Command[]
}

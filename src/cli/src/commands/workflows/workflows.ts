import type { LocalCommandResult } from '../../commands.js'
import { getCwd } from '../../utils/cwd.js'
import { getAllWorkflows } from '../../tools/WorkflowTool/namedWorkflows.js'

export async function call(): Promise<LocalCommandResult> {
  const workflows = await getAllWorkflows(getCwd())
  if (workflows.length === 0) {
    return {
      type: 'text',
      value:
        'No named workflows found. Add scripts to ~/.claude/workflows/ or .claude/workflows/, or ask me to "run a workflow" with an inline script.',
    }
  }
  const lines = workflows.map(
    w =>
      `  ${w.name} — ${w.description}${w.whenToUse ? ` (${w.whenToUse})` : ''} [${w.source === 'projectSettings' ? 'project' : 'user'}]`,
  )
  return { type: 'text', value: `Named workflows:\n${lines.join('\n')}` }
}

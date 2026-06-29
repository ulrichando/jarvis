import type { LocalCommandResult } from '../../commands.js'

export async function call(): Promise<LocalCommandResult> {
  return {
    type: 'text',
    value:
      'No bundled workflow scripts are installed in this build. Use Agent for multi-step delegation or Bash with run_in_background for long-running local work.',
  }
}

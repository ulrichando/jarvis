import type { LocalCommandResult } from '../commands.js'
import { isSnipRuntimeEnabled } from '../services/compact/snipCompact.js'

export async function call(): Promise<LocalCommandResult> {
  return {
    type: 'text',
    value: isSnipRuntimeEnabled()
      ? 'History snip is enabled; the next query turn will apply it.'
      : 'History snip is not active in this build.',
  }
}

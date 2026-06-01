// LocalWorkflowTask — stub task runner for WorkflowTool
import type { LocalAgentInput } from '../Task.js'

export async function runLocalWorkflowTask(_input: LocalAgentInput): Promise<void> {
  // Stub: full implementation would parse the workflow script,
  // orchestrate parallel agent()/pipeline()/parallel() calls,
  // and return results.
}

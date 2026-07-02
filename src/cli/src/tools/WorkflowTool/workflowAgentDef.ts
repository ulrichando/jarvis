import type { BuiltInAgentDefinition } from '../AgentTool/loadAgentsDir.js'

// Minimal built-in subagent for workflow agent() calls without an explicit
// agentType. Told its final text IS the return value.
export function getWorkflowAgentDefinition(): BuiltInAgentDefinition {
  return {
    agentType: 'workflow',
    whenToUse: 'Internal workflow subagent',
    source: 'built-in',
    baseDir: 'built-in',
    getSystemPrompt: () =>
      'You are a workflow subagent. Do exactly the task described and return the result. Your FINAL message text is consumed as the return value of this step — return raw data (or, in schema mode, call StructuredOutput). Do not address a human.',
    tools: undefined,
    model: undefined,
  }
}

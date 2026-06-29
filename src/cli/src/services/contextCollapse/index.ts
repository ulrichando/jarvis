import type { Message } from '../../types/message.js'
import type { ToolUseContext } from '../../Tool.js'
import type { QuerySource } from '../../constants/querySource.js'

let enabled = false

export function initContextCollapse(): void {
  enabled = true
}

export function isContextCollapseEnabled(): boolean {
  return enabled
}

export function setContextCollapseEnabled(value: boolean): void {
  enabled = value
}

export function resetContextCollapse(): void {
  enabled = false
}

export async function applyCollapsesIfNeeded(
  messages: Message[],
  _toolUseContext: ToolUseContext,
  _querySource: QuerySource,
): Promise<{ messages: Message[]; committed: number }> {
  return { messages, committed: 0 }
}

export function isWithheldPromptTooLong(
  _message: Message,
  _isPromptTooLongMessage?: (message: Message) => boolean,
  _querySource?: QuerySource,
): boolean {
  return false
}

export function recoverFromOverflow(
  messages: Message[],
  _querySource: QuerySource,
): { messages: Message[]; committed: number } {
  return { messages, committed: 0 }
}

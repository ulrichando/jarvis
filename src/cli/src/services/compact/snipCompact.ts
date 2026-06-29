import type { Message } from '../../types/message.js'

export function isSnipRuntimeEnabled(): boolean {
  return false
}

export function shouldNudgeForSnips(_messages: unknown[]): boolean {
  return false
}

export function snipCompactIfNeeded(
  messages: Message[],
  _options?: { force?: boolean },
): { messages: Message[]; tokensFreed: number; executed: boolean; boundaryMessage?: Message } {
  return { messages, tokensFreed: 0, executed: false }
}

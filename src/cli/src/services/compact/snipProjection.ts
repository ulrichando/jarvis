import type { Message } from '../../types/message.js'

export function projectSnipMessages(messages: Message[]): Message[] {
  return messages
}

export function projectSnippedView(messages: Message[]): Message[] {
  return messages
}

export function isSnipBoundaryMessage(_message: Message): boolean {
  return false
}

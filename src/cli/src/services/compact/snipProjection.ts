import type { Message } from '../../types/message.js'

type SnipBoundary = {
  type: 'system'
  subtype: 'snip_boundary'
  snipMetadata?: { removedUuids?: string[]; tokensFreed?: number; label?: string }
}

export function isSnipBoundaryMessage(message: Message): boolean {
  const m = message as unknown as SnipBoundary
  return m?.type === 'system' && m.subtype === 'snip_boundary'
}

// Collect every uuid named by any snip boundary in this list.
function collectSnipped(messages: Message[]): Set<string> {
  const removed = new Set<string>()
  for (const m of messages) {
    if (!isSnipBoundaryMessage(m)) continue
    const uuids = (m as unknown as SnipBoundary).snipMetadata?.removedUuids ?? []
    for (const u of uuids) removed.add(u)
  }
  return removed
}

// Drop snipped messages; KEEP the boundary markers themselves.
export function projectSnippedView(messages: Message[]): Message[] {
  const removed = collectSnipped(messages)
  if (removed.size === 0) return messages
  return messages.filter(m => {
    const uuid = (m as unknown as { uuid?: string }).uuid
    return !uuid || !removed.has(uuid)
  })
}

// Alias — the model-facing SDK path and the projection path want the same
// filtering; no distinct behavior was found across the call sites.
export function projectSnipMessages(messages: Message[]): Message[] {
  return projectSnippedView(messages)
}

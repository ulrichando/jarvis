import { randomUUID } from 'node:crypto'
import type { Message } from '../../types/message.js'
import { deriveShortMessageId } from '../../utils/messages.js'
import { tokenCountWithEstimation } from '../../utils/tokens.js'

type Any = Record<string, unknown>

function uuidOf(m: Message): string | undefined {
  return (m as Any).uuid as string | undefined
}
function isUser(m: Message): boolean {
  return (m as Any).type === 'user'
}
function isMeta(m: Message): boolean {
  return (m as Any).isMeta === true
}
function isSystem(m: Message): boolean {
  return (m as Any).type === 'system'
}

function findAnchor(messages: Message[], anchor: string): number {
  return messages.findIndex(m => {
    const u = uuidOf(m)
    return u !== undefined && deriveShortMessageId(u) === anchor
  })
}

function lastUserIndex(messages: Message[]): number {
  for (let i = messages.length - 1; i >= 0; i--) {
    if (isUser(messages[i]!) && !isMeta(messages[i]!)) return i
  }
  return -1
}

export type SnipRange = { removedUuids: string[]; startIndex: number; endIndex: number }

export function resolveSnipRange(
  messages: Message[],
  startId: string,
  endId: string,
): SnipRange | { error: string } {
  const start = findAnchor(messages, startId)
  const end = findAnchor(messages, endId)
  if (start === -1) return { error: `Could not find message [id:${startId}].` }
  if (end === -1) return { error: `Could not find message [id:${endId}].` }
  if (start > end) return { error: 'start_id must come before end_id.' }

  // Segment end: extend through everything up to (not including) the next
  // non-meta user message after `end`.
  let segEnd = end
  for (let i = end + 1; i < messages.length; i++) {
    if (isUser(messages[i]!) && !isMeta(messages[i]!)) break
    segEnd = i
  }

  const protectedIdx = lastUserIndex(messages)
  if (protectedIdx !== -1 && segEnd >= protectedIdx) {
    return { error: 'Cannot snip the current turn / latest user message.' }
  }

  const removedUuids: string[] = []
  for (let i = start; i <= segEnd; i++) {
    const m = messages[i]!
    if (isSystem(m)) continue
    const u = uuidOf(m)
    if (u) removedUuids.push(u)
  }
  if (removedUuids.length === 0) {
    return { error: 'Nothing removable in that range.' }
  }
  return { removedUuids, startIndex: start, endIndex: segEnd }
}

export function buildSnipBoundary(
  removedUuids: string[],
  tokensFreed: number,
  label = 'history snipped',
): Message {
  return {
    type: 'system',
    subtype: 'snip_boundary',
    content: `Snipped ${removedUuids.length} messages (~${tokensFreed} tokens)`,
    isMeta: false,
    level: 'info',
    timestamp: new Date().toISOString(),
    uuid: randomUUID(),
    snipMetadata: { removedUuids, tokensFreed, label },
  } as unknown as Message
}

export function estimateTokensFreed(
  messages: Message[],
  removedUuids: string[],
): number {
  const removedSet = new Set(removedUuids)
  const removed = messages.filter(m => {
    const u = uuidOf(m)
    return u && removedSet.has(u)
  })
  return tokenCountWithEstimation(removed as Message[])
}

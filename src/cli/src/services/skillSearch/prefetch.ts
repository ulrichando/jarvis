import type { Message } from '../../types/message.js'
import type { ToolUseContext } from '../../Tool.js'
import type { Attachment } from '../../utils/attachments.js'

export type SkillDiscoveryPrefetch = Promise<Attachment[]>

export function startSkillDiscoveryPrefetch(
  _signal: unknown,
  _messages: Message[],
  _context: ToolUseContext,
): SkillDiscoveryPrefetch | null {
  return null
}

export async function collectSkillDiscoveryPrefetch(
  prefetch: SkillDiscoveryPrefetch,
): Promise<Attachment[]> {
  return prefetch
}

export async function getTurnZeroSkillDiscovery(
  _input: string,
  _messages: Message[],
  _context: ToolUseContext,
): Promise<Attachment | null> {
  return null
}

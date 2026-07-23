import { getCompanion, roll, companionUserId } from '../../buddy/companion.js'
import {
  type CompanionBones,
  type CompanionSoul,
  STAT_NAMES,
} from '../../buddy/types.js'
import type { LocalCommandResult, LocalJSXCommandContext } from '../../commands.js'
import { queryModelWithoutStreaming } from '../../services/api/claude.js'
import { getEmptyToolPermissionContext } from '../../Tool.js'
import { getGlobalConfig, saveGlobalConfig } from '../../utils/config.js'
import { logForDebugging } from '../../utils/debug.js'
import {
  createUserMessage,
  getAssistantMessageText,
} from '../../utils/messages.js'
import { getSmallFastModel } from '../../utils/model/model.js'
import { asSystemPrompt } from '../../utils/systemPromptType.js'

// Abort a hung soul request; hatch falls back to the stub rather than hang.
const SOUL_TIMEOUT_MS = 20_000
const SOUL_NAME_MAX_CHARS = 40
const SOUL_PERSONALITY_MAX_CHARS = 300

function defaultName(species: string): string {
  return `Jarvis ${species[0]?.toUpperCase() ?? 'B'}${species.slice(1)}`
}

// The pre-soul-gen hardcoded soul — kept as the fallback so hatch NEVER
// breaks on an API error, timeout, or unparseable model reply.
function stubSoul(species: string): CompanionSoul {
  return {
    name: defaultName(species),
    personality: 'Quiet, practical, and observant.',
  }
}

function buildSoulPrompt(bones: CompanionBones): string {
  const stats = STAT_NAMES.map(s => `${s} ${bones.stats[s]}`).join(', ')
  return `A ${bones.rarity}${bones.shiny ? ' SHINY' : ''} ${bones.species} companion has just hatched in the user's coding terminal. It will perch beside their input box and watch their sessions.
Its stats (0-100): ${stats}.

Invent a distinctive creature name (one or two words — a proper name, not a description) and a personality in 1-2 sentences that FITS the stats: let the highest stats dominate (high SNARK → sharp-tongued, high PATIENCE → unflappable, high CHAOS → gleefully unhinged, high WISDOM → sage-like, high DEBUGGING → obsessed with hunting bugs) and let a rock-bottom stat show as a flaw.

Reply in EXACTLY this format and nothing else:
Name: <the name>
Personality: <the personality, no emoji>`
}

/**
 * Parse `Name: X` / `Personality: Y` out of the model reply. Tolerates
 * markdown decoration, stray quotes, and the labels being omitted (first
 * line = name, rest = personality). Returns null — caller falls back to
 * the stub — when no sane name + personality pair can be extracted.
 */
export function parseSoulReply(raw: string | null): CompanionSoul | null {
  if (!raw) return null
  const clean = (s: string) =>
    s
      .replace(/\*\*|__|[*_`#>]/g, '')
      .replace(/^["'“”‘’\s]+|["'“”‘’\s]+$/g, '')
      .trim()
  const lines = raw
    .split('\n')
    .map(l => l.trim())
    .filter(Boolean)
  if (lines.length === 0) return null

  let name: string | undefined
  const personalityParts: string[] = []
  for (const line of lines) {
    const nameMatch = line.match(/^[*_`#\s]*name\s*:\s*(.+)$/i)
    if (nameMatch && !name) {
      name = clean(nameMatch[1]!)
      continue
    }
    const persMatch = line.match(/^[*_`#\s]*personality\s*:\s*(.+)$/i)
    const part = clean(persMatch ? persMatch[1]! : line)
    if (part) personalityParts.push(part)
  }
  // Labels absent → first line is the name, the rest the personality.
  if (!name) name = personalityParts.shift()
  const personality = personalityParts.join(' ').replace(/\s+/g, ' ').trim()
  if (!name || name.length > SOUL_NAME_MAX_CHARS) return null
  if (!personality) return null
  return {
    name,
    personality:
      personality.length > SOUL_PERSONALITY_MAX_CHARS
        ? `${personality.slice(0, SOUL_PERSONALITY_MAX_CHARS - 1)}…`
        : personality,
  }
}

/**
 * ONE small-fast-model call to invent a name + personality that fit the
 * rolled bones (same call pattern as src/buddy/observer.ts). Falls back
 * to the hardcoded stub soul on ANY failure — hatch never breaks.
 */
export async function generateSoul(
  bones: CompanionBones,
): Promise<CompanionSoul> {
  try {
    const response = await queryModelWithoutStreaming({
      messages: [createUserMessage({ content: buildSoulPrompt(bones) })],
      systemPrompt: asSystemPrompt([]),
      thinkingConfig: { type: 'disabled' },
      tools: [],
      signal: AbortSignal.timeout(SOUL_TIMEOUT_MS),
      options: {
        getToolPermissionContext: async () => getEmptyToolPermissionContext(),
        model: getSmallFastModel(),
        toolChoice: undefined,
        isNonInteractiveSession: false,
        hasAppendSystemPrompt: false,
        agents: [],
        querySource: 'buddy_hatch',
        mcpTools: [],
        skipCacheWrite: true,
      },
    })
    if (response.isApiErrorMessage) {
      logForDebugging(
        `[buddyHatch] API error: ${getAssistantMessageText(response)}`,
      )
      return stubSoul(bones.species)
    }
    return parseSoulReply(getAssistantMessageText(response)) ?? stubSoul(bones.species)
  } catch (err) {
    logForDebugging(`[buddyHatch] soul generation failed: ${err}`)
    return stubSoul(bones.species)
  }
}

function statusText(): string {
  const companion = getCompanion()
  const muted = getGlobalConfig().companionMuted
  if (!companion) return 'Buddy is not hatched. Run /buddy hatch.'
  return [
    `${companion.name} is ${muted ? 'muted' : 'visible'}.`,
    `Rarity: ${companion.rarity}`,
    `Species: ${companion.species}`,
    `Personality: ${companion.personality}`,
  ].join('\n')
}

export async function call(
  args: string,
  context: LocalJSXCommandContext,
): Promise<LocalCommandResult> {
  const [actionRaw, argRaw] = args.trim().split(/\s+/)
  const action = actionRaw || 'status'

  switch (action) {
    case 'status':
      return { type: 'text', value: statusText() }
    case 'hatch': {
      const { bones } = roll(companionUserId())
      // Model call only on a FRESH hatch — an existing companion's soul is
      // never regenerated. The `current.companion ??` guard below stays as
      // the authoritative clobber protection.
      const soul = getGlobalConfig().companion
        ? undefined
        : await generateSoul(bones)
      saveGlobalConfig(current => ({
        ...current,
        companion: current.companion ?? {
          ...(soul ?? stubSoul(bones.species)),
          hatchedAt: Date.now(),
        },
        companionMuted: false,
      }))
      return { type: 'text', value: statusText() }
    }
    case 'mute':
      saveGlobalConfig(current => ({ ...current, companionMuted: true }))
      return { type: 'text', value: 'Buddy muted.' }
    case 'unmute':
      saveGlobalConfig(current => ({ ...current, companionMuted: false }))
      return { type: 'text', value: statusText() }
    case 'pet':
      context.setAppState(prev => ({ ...prev, companionPetAt: Date.now() }))
      return { type: 'text', value: 'Buddy acknowledged.' }
    case 'reset': {
      const companion = getCompanion()
      if (!companion) {
        return { type: 'text', value: 'No companion to reset.' }
      }
      const confirmed = ['confirm', 'yes', 'force'].includes(
        argRaw?.toLowerCase() ?? '',
      )
      if (!confirmed) {
        return {
          type: 'text',
          value: [
            `This permanently deletes ${companion.name} — the generated soul cannot be recovered.`,
            'Run /buddy reset confirm to proceed.',
          ].join('\n'),
        }
      }
      saveGlobalConfig(current => ({
        ...current,
        companion: undefined,
        companionMuted: undefined,
      }))
      return {
        type: 'text',
        value: `${companion.name} released. Run /buddy hatch to meet a new companion.`,
      }
    }
    default:
      return {
        type: 'text',
        value: 'Usage: /buddy [status|hatch|mute|unmute|pet|reset]',
      }
  }
}

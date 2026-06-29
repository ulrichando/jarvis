import { getCompanion, roll, companionUserId } from '../../buddy/companion.js'
import type { LocalCommandResult, LocalJSXCommandContext } from '../../commands.js'
import { getGlobalConfig, saveGlobalConfig } from '../../utils/config.js'

function defaultName(species: string): string {
  return `Jarvis ${species[0]?.toUpperCase() ?? 'B'}${species.slice(1)}`
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
  const [actionRaw] = args.trim().split(/\s+/)
  const action = actionRaw || 'status'

  switch (action) {
    case 'status':
      return { type: 'text', value: statusText() }
    case 'hatch': {
      const { bones } = roll(companionUserId())
      saveGlobalConfig(current => ({
        ...current,
        companion: current.companion ?? {
          name: defaultName(bones.species),
          personality: 'Quiet, practical, and observant.',
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
    case 'reset':
      saveGlobalConfig(current => ({
        ...current,
        companion: undefined,
        companionMuted: undefined,
      }))
      return { type: 'text', value: 'Buddy reset.' }
    default:
      return {
        type: 'text',
        value: 'Usage: /buddy [status|hatch|mute|unmute|pet|reset]',
      }
  }
}

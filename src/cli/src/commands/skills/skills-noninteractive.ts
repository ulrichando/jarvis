import type { Command } from '../../commands.js'
import type { LocalCommandCall } from '../../types/command.js'

/** The same predicate the interactive SkillsMenu uses to identify a skill. */
function isSkill(cmd: Command): boolean {
  return (
    cmd.type === 'prompt' &&
    (cmd.loadedFrom === 'skills' ||
      cmd.loadedFrom === 'commands_DEPRECATED' ||
      cmd.loadedFrom === 'plugin' ||
      cmd.loadedFrom === 'mcp')
  )
}

/**
 * Text `/skills` for non-interactive sessions — lists the available skills
 * (the interactive `skills.tsx` renders a selectable menu, which can't show in
 * --print / SDK / Remote Control / a `/code` container). Without this variant
 * `/skills` errored with "Unknown skill: skills".
 */
export const call: LocalCommandCall = async (_args, context) => {
  const commands =
    (context.options as { commands?: Command[] } | undefined)?.commands ?? []
  const skills = commands.filter(isSkill)
  if (skills.length === 0) {
    return { type: 'text', value: 'No skills are available in this session.' }
  }
  const lines = skills
    .map((s) => {
      const name = s.userFacingName?.() ?? s.name
      const desc = (s.description ?? '').trim()
      return desc ? `• /${name} — ${desc}` : `• /${name}`
    })
    .sort((a, b) => a.localeCompare(b))
  return {
    type: 'text',
    value: `Available skills (${skills.length}):\n${lines.join('\n')}`,
  }
}

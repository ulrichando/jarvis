import type { LocalCommandCall } from '../../types/command.js'
import { isClaudeAISubscriber } from '../../utils/auth.js'

/**
 * Text `/status` for non-interactive sessions. The interactive `status.tsx`
 * renders an Ink UI that can't show in --print / SDK / Remote Control / a
 * `/code` container, so without this variant `/status` fell through to skill
 * resolution and errored with "Unknown skill: status". Mirrors the `/context`
 * dual-definition pattern.
 *
 * Kept dependency-light + best-effort: every field degrades rather than
 * throwing, so a status query can never break the turn.
 */
export const call: LocalCommandCall = async (_args, context) => {
  const lines: string[] = []
  const model = (context.options as { mainLoopModel?: string } | undefined)
    ?.mainLoopModel
  if (model) lines.push(`Model:     ${model}`)
  const provider = process.env.JARVIS_PROVIDER
  if (provider) lines.push(`Provider:  ${provider}`)
  let account = 'API key'
  try {
    if (isClaudeAISubscriber()) account = 'Claude subscription'
    else if (process.env.USER_TYPE) account = process.env.USER_TYPE
  } catch {
    /* keep the default */
  }
  lines.push(`Account:   ${account}`)
  lines.push(`Workdir:   ${process.cwd()}`)
  return { type: 'text', value: `Jarvis status\n${lines.join('\n')}` }
}

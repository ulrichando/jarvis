/**
 * /swarm command — opens the agent-teams (swarm) manager dialog.
 *
 * Swarm itself (tmux / iTerm2 / in-process teammate panes) is intact and
 * cross-platform; it was previously reachable only via the PromptInput footer
 * 'teams' menu + the model-invoked TeamCreate/TeamDelete tools. This surfaces
 * the same TeamsDialog as an explicit slash command. The whole path is gated by
 * isAgentSwarmsEnabled() (utils/agentSwarmsEnabled.ts), which start.sh enables
 * for JARVIS via CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS=1.
 *
 * Implementation is lazy-loaded from swarm.tsx to keep startup cheap.
 */
import type { Command } from '../../commands.js'
import { isAgentSwarmsEnabled } from '../../utils/agentSwarmsEnabled.js'

const swarm = {
  type: 'local-jsx',
  name: 'swarm',
  description: 'View and manage agent teams (swarm)',
  aliases: ['team', 'teams'],
  isEnabled: () => isAgentSwarmsEnabled(),
  supportsNonInteractive: false,
  load: () => import('./swarm.js'),
} satisfies Command

export default swarm

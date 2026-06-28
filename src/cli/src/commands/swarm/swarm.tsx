import * as React from 'react'
import { useMemo } from 'react'
import type { LocalJSXCommandContext } from '../../commands.js'
import type { LocalJSXCommandOnDone } from '../../types/command.js'
import { TeamsDialog } from '../../components/teams/TeamsDialog.js'
import { useAppState } from '../../state/AppState.js'
import type { TeamSummary } from '../../utils/teamDiscovery.js'

/**
 * Reads the current team from AppState and renders the TeamsDialog — mirrors
 * the cachedTeams derivation in PromptInput (a session leads at most one team).
 */
function SwarmDialog({ onDone }: { onDone: () => void }): React.ReactNode {
  const teamContext = useAppState(s => s.teamContext)
  const initialTeams: TeamSummary[] = useMemo(() => {
    if (!teamContext) return []
    const memberCount = Object.values(teamContext.teammates).filter(
      t => t.name !== 'team-lead',
    ).length
    return [
      {
        name: teamContext.teamName,
        memberCount,
        runningCount: 0,
        idleCount: 0,
      },
    ]
  }, [teamContext])
  return <TeamsDialog initialTeams={initialTeams} onDone={onDone} />
}

export async function call(
  onDone: LocalJSXCommandOnDone,
  _context: LocalJSXCommandContext,
): Promise<React.ReactNode> {
  return <SwarmDialog onDone={() => onDone()} />
}

import type { LocalCommandResult } from '../../commands.js'
import { listAllLiveSessions } from '../../utils/udsClient.js'

export async function call(): Promise<LocalCommandResult> {
  const peers = await listAllLiveSessions()
  if (peers.length === 0) {
    return { type: 'text', value: 'No live peer sessions found.' }
  }
  return {
    type: 'text',
    value: peers
      .map(peer => {
        const addr = peer.messagingSocketPath
          ? `uds:${peer.messagingSocketPath}`
          : peer.bridgeSessionId
            ? `bridge:${peer.bridgeSessionId}`
            : 'unreachable'
        const name = peer.name ? ` ${peer.name}` : ''
        return `${addr}${name} (${peer.kind ?? 'interactive'}, pid ${peer.pid}, ${peer.cwd ?? 'unknown cwd'})`
      })
      .join('\n'),
  }
}

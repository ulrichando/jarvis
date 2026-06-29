export type BridgePeerSession = {
  id: string
  title?: string
  cwd?: string
}

export async function listBridgePeerSessions(): Promise<BridgePeerSession[]> {
  return []
}

export async function postInterClaudeMessage(
  sessionId: string,
  _message: string,
): Promise<{ ok: boolean; error?: string }> {
  return {
    ok: false,
    error: `Remote Control peer messaging is not implemented for bridge session ${sessionId} in this build.`,
  }
}

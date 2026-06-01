// MCP Skills bridge — stub
// Enable with --feature=MCP_SKILLS at build time.
// Full implementation would bridge MCP server capabilities into the skill registry.

export interface MCPSkill {
  name: string
  description: string
  serverName: string
}

export async function fetchMcpSkillsForClient(
  _serverName: string,
  _client: unknown,
): Promise<MCPSkill[]> {
  // Stub: no MCP skills loaded yet
  return []
}

export function registerMcpSkill(_skill: MCPSkill): void {
  // Stub
}

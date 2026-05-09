// Wire types mirroring src/cli/src/bridge/types.ts — kept in sync manually.
// Server keeps these minimal; the CLI is authoritative for the full shape.

export interface RegisterRequest {
  machine_name: string
  directory: string
  branch?: string
  git_repo_url?: string
  max_sessions: number
  metadata?: { worker_type?: string }
  environment_id?: string
}

export interface RegisterResponse {
  environment_id: string
  environment_secret: string
}

export interface WorkResponse {
  id: string
  type: 'work'
  environment_id: string
  state: string
  data: unknown
  secret: string
  created_at: string
}

export interface WorkSecret {
  version: number
  session_ingress_token: string
  api_base_url: string
  sources: unknown[]
  auth: Array<{ type: string; token: string }>
  claude_code_args: Record<string, string> | null
  mcp_config: unknown | null
  environment_variables: Record<string, string> | null
  use_code_sessions: boolean
}

export interface HeartbeatResponse {
  lease_extended: boolean
  state: string
  last_heartbeat: string
  ttl_seconds: number
}

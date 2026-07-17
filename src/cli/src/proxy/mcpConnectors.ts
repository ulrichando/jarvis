/**
 * Anthropic MCP-connector injection for the /v1/messages passthrough.
 *
 * Mirrors how Anthropic's own apps expose connectors: the mobile client stays a
 * THIN client, and the gateway attaches the box's ~/.jarvis/mcp.json connectors
 * as Anthropic's server-side MCP connector (beta `mcp-client-2025-11-20`).
 * Anthropic connects to the remote MCP servers and runs the tools itself,
 * streaming `mcp_tool_use` / `mcp_tool_result` blocks back — the phone executes
 * nothing MCP-side. (Help center: "Claude connects to your remote MCP server
 * from Anthropic's cloud infrastructure, rather than from your local device …
 * across every Claude client, including … the mobile apps.")
 *
 * We read the JARVIS connector files DIRECTLY with node fs — do NOT import from
 * src/web/src/lib/mcp/*, those are Next `"server-only"` modules that throw in the
 * Bun proxy. The `normalize`/`isEnabled`/`isExpired` logic below mirrors
 * store.ts / oauth-store.ts.
 *
 * Scope: enabled + REMOTE (http/sse) connectors only — Anthropic's connector
 * cannot reach a local stdio server. Single-user box: reads the machine-global
 * mcp.json (multi-user would scope by proxy-JWT `sub` — follow-up).
 */
import { promises as fs } from 'node:fs'
import os from 'node:os'
import path from 'node:path'

export const MCP_CONNECTOR_BETA = 'mcp-client-2025-11-20'

// Resolved at CALL time (not module load). JARVIS_MCP_DIR relocates the config
// dir (used by tests, and lets a deployment point at a non-home path); defaults
// to ~/.jarvis — the same file the web UI and voice agent read/write.
function jarvisDir(): string {
  return process.env.JARVIS_MCP_DIR?.trim() || path.join(os.homedir(), '.jarvis')
}

/** Off by default — flip JARVIS_MCP_CONNECTOR=1 on the box to enable. */
export function mcpConnectorEnabled(): boolean {
  const v = process.env.JARVIS_MCP_CONNECTOR
  return v === '1' || v === 'true' || v === 'yes'
}

// Loose shapes — the files are user-editable / voice-agent-shared.
type McpSpec = {
  url?: string
  transport?: string
  headers?: Record<string, string>
  disabled?: boolean
  enabled?: boolean
  /** Tool names to disable on this server (denylist / read-only lockdown). */
  deny?: string[]
  [k: string]: unknown
}
type OAuthShape = {
  servers?: Record<
    string,
    { tokens?: { access_token?: string; expires_in?: number }; obtainedAt?: number }
  >
}

export type AnthropicMcpServer = {
  type: 'url'
  name: string
  url: string
  authorization_token?: string
}
export type AnthropicMcpToolset = {
  type: 'mcp_toolset'
  mcp_server_name: string
  /** Per-tool overrides — used to disable specific tools (read-only lockdown). */
  configs?: Record<string, { enabled: boolean }>
}
export type ConnectorPayload = {
  servers: AnthropicMcpServer[]
  toolsets: AnthropicMcpToolset[]
}

function isEnabled(spec: McpSpec): boolean {
  return !(spec.disabled === true || spec.enabled === false)
}

// Remote = reachable by Anthropic over HTTPS. stdio/local (a `command`, no url)
// is excluded — the connector can't reach it. Absent transport + url ⇒ http
// (matches store.ts `normalize`).
function isRemote(spec: McpSpec): boolean {
  if (!spec.url) return false
  return spec.transport === 'sse' || spec.transport === 'http' || spec.transport == null
}

function bearerFromHeaders(headers?: Record<string, string>): string | undefined {
  const authz = headers?.Authorization ?? headers?.authorization
  if (authz && /^bearer /i.test(authz)) return authz.slice(7).trim()
  return undefined
}

// Build the toolset for a server. A `deny` list (tool names) disables just
// those tools via per-tool `configs` (denylist — everything else stays enabled).
function buildToolset(name: string, spec: McpSpec): AnthropicMcpToolset {
  const ts: AnthropicMcpToolset = { type: 'mcp_toolset', mcp_server_name: name }
  const deny = Array.isArray(spec.deny)
    ? spec.deny.filter((t): t is string => typeof t === 'string' && t.trim().length > 0)
    : []
  if (deny.length) {
    ts.configs = Object.fromEntries(deny.map(t => [t, { enabled: false }]))
  }
  return ts
}

// Mirrors oauth-store.isExpired (60s skew). No expires_in ⇒ treat as valid.
function oauthToken(name: string, oauth: OAuthShape): string | undefined {
  const s = oauth.servers?.[name]
  const tok = s?.tokens?.access_token
  if (!tok) return undefined
  const exp = s?.tokens?.expires_in
  if (exp && s?.obtainedAt && Date.now() > s.obtainedAt + (exp - 60) * 1000) return undefined
  return tok
}

/**
 * Pure builder: parsed mcp.json server map (+ parsed mcp-oauth.json) → the
 * Anthropic `mcp_servers` and `mcp_toolset` arrays. Enabled + remote only; the
 * token comes from the mirrored Bearer header first, then a non-expired OAuth
 * token. Every server is paired with exactly one toolset (Anthropic requires it).
 */
export function buildConnectorPayload(
  servers: Record<string, McpSpec> | undefined,
  oauth: OAuthShape = {},
): ConnectorPayload {
  const out: ConnectorPayload = { servers: [], toolsets: [] }
  if (!servers || typeof servers !== 'object') return out
  for (const [name, spec] of Object.entries(servers)) {
    if (!name?.trim() || !spec || typeof spec !== 'object') continue
    if (!isEnabled(spec) || !isRemote(spec)) continue
    const token = bearerFromHeaders(spec.headers) ?? oauthToken(name, oauth)
    out.servers.push({
      type: 'url',
      name,
      url: spec.url!.trim(),
      ...(token ? { authorization_token: token } : {}),
    })
    out.toolsets.push(buildToolset(name, spec))
  }
  return out
}

async function readJson<T>(file: string): Promise<T | undefined> {
  try {
    return JSON.parse(await fs.readFile(file, 'utf8')) as T
  } catch {
    return undefined // absent / malformed → treat as empty; never throw
  }
}

/** Read ~/.jarvis/mcp.json (+ mcp-oauth.json) and build the payload. Never throws. */
export async function readEnabledRemoteConnectors(): Promise<ConnectorPayload> {
  const dir = jarvisDir()
  const raw = await readJson<any>(path.join(dir, 'mcp.json'))
  // Accept {servers:{…}} or a bare {name: spec} map (both valid per the voice loader).
  const servers: Record<string, McpSpec> | undefined =
    raw && typeof raw === 'object'
      ? raw.servers && typeof raw.servers === 'object'
        ? raw.servers
        : raw
      : undefined
  const oauth = (await readJson<OAuthShape>(path.join(dir, 'mcp-oauth.json'))) ?? {}
  return buildConnectorPayload(servers, oauth)
}

/**
 * Merge a connector payload into an outbound Anthropic request + upstream headers.
 * - sets `mcp_servers` (concatenating onto any already present)
 * - APPENDS the `mcp_toolset` entries to the EXISTING `tools` array — never
 *   replaces (the phone already sends its device tools + web_search)
 * - adds the MCP beta to `anthropic-beta` (comma-join + dedup, case-insensitive)
 * No-op when the payload has no servers. Mutates `req` and `headers` in place.
 */
export function applyConnectorPayload(
  req: any,
  headers: Record<string, string>,
  payload: ConnectorPayload,
): void {
  if (!payload.servers.length) return
  req.mcp_servers = [
    ...(Array.isArray(req.mcp_servers) ? req.mcp_servers : []),
    ...payload.servers,
  ]
  req.tools = [...(Array.isArray(req.tools) ? req.tools : []), ...payload.toolsets]

  const key = Object.keys(headers).find(k => k.toLowerCase() === 'anthropic-beta') ?? 'anthropic-beta'
  const betas = new Set(
    (headers[key] ? headers[key].split(',') : []).map(s => s.trim()).filter(Boolean),
  )
  betas.add(MCP_CONNECTOR_BETA)
  headers[key] = [...betas].join(',')
}

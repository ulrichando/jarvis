import "server-only";
import { promises as fs } from "node:fs";
import os from "node:os";
import path from "node:path";

// JARVIS's voice-agent MCP config — the SAME file the voice agent reads at
// startup (src/voice-agent/tools/mcp_client.py). Managing it here makes the web
// Settings → Connectors → MCP card a real control panel for the assistant the
// user talks to. The voice loader already honors `headers` and a
// `disabled`/`enabled` flag, so add/remove/toggle here are picked up on its next
// restart (the web chat reads this file per-turn, so it sees changes immediately).
// Machine-global (single-user box), not keyed per web user.
const FILE = path.join(os.homedir(), ".jarvis", "mcp.json");

export type McpTransport = "http" | "sse" | "stdio";

// Normalized shape returned to the API/UI.
export type McpServer = {
  id: string; // == name (the key in mcp.json)
  name: string;
  url?: string;
  command?: string;
  args?: string[];
  transport: McpTransport;
  headers?: Record<string, string>;
  enabled: boolean;
};

// Raw per-server spec as stored in ~/.jarvis/mcp.json.
type AgentSpec = {
  url?: string;
  command?: string;
  args?: string[];
  env?: Record<string, string>;
  headers?: Record<string, string>;
  transport?: string;
  type?: string;
  disabled?: boolean;
  enabled?: boolean;
  [k: string]: unknown;
};
type FileShape = { servers: Record<string, AgentSpec> };

async function read(): Promise<FileShape> {
  try {
    const data = JSON.parse(await fs.readFile(FILE, "utf8")) as unknown;
    if (data && typeof data === "object") {
      const obj = data as Record<string, unknown>;
      // Accept {"servers": {…}} or a bare {name: spec} mapping (both valid per
      // the voice loader).
      if (obj.servers && typeof obj.servers === "object") {
        return { servers: obj.servers as Record<string, AgentSpec> };
      }
      return { servers: obj as Record<string, AgentSpec> };
    }
  } catch {
    /* absent / malformed → treat as empty */
  }
  return { servers: {} };
}

async function write(data: FileShape): Promise<void> {
  await fs.mkdir(path.dirname(FILE), { recursive: true });
  const tmp = `${FILE}.tmp-${process.pid}`;
  await fs.writeFile(tmp, JSON.stringify(data, null, 2) + "\n", "utf8");
  await fs.rename(tmp, FILE); // atomic replace — never leaves a half-written file
}

function isEnabled(spec: AgentSpec): boolean {
  return !(spec.disabled === true || spec.enabled === false);
}

function normalize(name: string, spec: AgentSpec): McpServer {
  const transport: McpTransport =
    spec.transport === "sse" ? "sse" : spec.url ? "http" : "stdio";
  return {
    id: name,
    name,
    url: spec.url,
    command: spec.command,
    args: spec.args,
    transport,
    headers: spec.headers,
    enabled: isEnabled(spec),
  };
}

export async function listMcpServers(): Promise<McpServer[]> {
  const { servers } = await read();
  return Object.entries(servers)
    .filter(([name, spec]) => typeof name === "string" && name.trim() && spec && typeof spec === "object")
    .map(([name, spec]) => normalize(name, spec));
}

export async function addMcpServer(input: {
  name: string;
  url: string;
  transport: "http" | "sse";
  headers?: Record<string, string>;
}): Promise<McpServer> {
  const data = await read();
  const name = input.name.trim().slice(0, 60);
  const spec: AgentSpec = {
    transport: input.transport,
    url: input.url.trim(),
    ...(input.headers && Object.keys(input.headers).length ? { headers: input.headers } : {}),
  };
  data.servers[name] = spec;
  await write(data);
  return normalize(name, spec);
}

// Write (or refresh) an OAuth-backed server: the access token is mirrored into
// `headers` so BOTH the web loader and the voice agent authenticate with it. The
// `oauth: true` marker tells the web loader to use the refreshing auth provider
// (oauth-provider.ts) instead of the static header; the long-lived refresh token
// + client registration live separately in oauth-store (~/.jarvis/mcp-oauth.json).
export async function upsertOAuthServer(input: {
  name: string;
  url: string;
  transport: "http" | "sse";
  accessToken: string;
}): Promise<void> {
  const data = await read();
  const name = input.name.trim().slice(0, 60);
  data.servers[name] = {
    transport: input.transport,
    url: input.url.trim(),
    headers: { Authorization: `Bearer ${input.accessToken}` },
    oauth: true,
  };
  await write(data);
}

export async function removeMcpServer(name: string): Promise<void> {
  const data = await read();
  if (name in data.servers) {
    delete data.servers[name];
    await write(data);
  }
}

export async function setMcpServerEnabled(name: string, enabled: boolean): Promise<void> {
  const data = await read();
  const spec = data.servers[name];
  if (!spec) return;
  if (enabled) {
    delete spec.disabled;
    delete spec.enabled;
  } else {
    spec.disabled = true;
  }
  await write(data);
}

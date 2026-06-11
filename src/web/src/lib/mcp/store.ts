import "server-only";
import { promises as fs } from "node:fs";
import { randomUUID } from "node:crypto";
import path from "node:path";

// Per-user MCP server configs. Mirrors the settings store location
// (process.cwd()/.jarvis). Keyed by JARVIS user id so each account manages its
// own connectors.
const FILE = path.join(process.cwd(), ".jarvis", "mcp-servers.json");

export type McpTransport = "http" | "sse";

export type McpServer = {
  id: string;
  name: string;
  url: string;
  transport: McpTransport;
  enabled: boolean;
  createdAt: number;
};

type StoreShape = Record<string, McpServer[]>;

async function load(): Promise<StoreShape> {
  try {
    return JSON.parse(await fs.readFile(FILE, "utf8")) as StoreShape;
  } catch {
    return {};
  }
}

async function save(s: StoreShape): Promise<void> {
  await fs.mkdir(path.dirname(FILE), { recursive: true });
  await fs.writeFile(FILE, JSON.stringify(s, null, 2), "utf8");
}

export async function listMcpServers(userId: string): Promise<McpServer[]> {
  return (await load())[userId] ?? [];
}

export async function addMcpServer(
  userId: string,
  input: { name: string; url: string; transport: McpTransport },
): Promise<McpServer> {
  const s = await load();
  const server: McpServer = {
    id: randomUUID(),
    name: input.name,
    url: input.url,
    transport: input.transport,
    enabled: true,
    createdAt: Date.now(),
  };
  s[userId] = [...(s[userId] ?? []), server];
  await save(s);
  return server;
}

export async function removeMcpServer(userId: string, id: string): Promise<void> {
  const s = await load();
  s[userId] = (s[userId] ?? []).filter((x) => x.id !== id);
  await save(s);
}

export async function setMcpServerEnabled(userId: string, id: string, enabled: boolean): Promise<void> {
  const s = await load();
  s[userId] = (s[userId] ?? []).map((x) => (x.id === id ? { ...x, enabled } : x));
  await save(s);
}

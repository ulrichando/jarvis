import "server-only";
import { Client } from "@modelcontextprotocol/sdk/client/index.js";
import { StreamableHTTPClientTransport } from "@modelcontextprotocol/sdk/client/streamableHttp.js";
import { SSEClientTransport } from "@modelcontextprotocol/sdk/client/sse.js";
import { tool, jsonSchema, type ToolSet } from "ai";
import type { McpServer } from "./store";

async function connect(server: McpServer): Promise<Client> {
  const client = new Client({ name: "jarvis-web", version: "1.0.0" });
  const url = new URL(server.url);
  const transport =
    server.transport === "sse"
      ? new SSEClientTransport(url)
      : new StreamableHTTPClientTransport(url);
  // Bound the handshake so a slow/hung server can't stall a chat turn.
  await Promise.race([
    client.connect(transport),
    new Promise<never>((_, reject) =>
      setTimeout(() => reject(new Error("connect timeout (5s)")), 5000),
    ),
  ]);
  return client;
}

/** Connect, list tools, disconnect — for the Settings "Test" button. */
export async function testMcpServer(
  server: McpServer,
): Promise<{ ok: true; tools: string[] } | { ok: false; error: string }> {
  let client: Client | null = null;
  try {
    client = await connect(server);
    const { tools } = await client.listTools();
    return { ok: true, tools: tools.map((t) => t.name) };
  } catch (e) {
    return { ok: false, error: (e instanceof Error ? e.message : String(e)).slice(0, 200) };
  } finally {
    await client?.close().catch(() => {});
  }
}

/**
 * Connect to every enabled server and expose their tools as AI SDK tools for
 * streamText. Returns a `close()` to disconnect after the turn. Unreachable
 * servers are skipped (a broken connector must not break chat).
 */
export async function loadMcpTools(
  servers: McpServer[],
): Promise<{ tools: ToolSet; close: () => Promise<void> }> {
  const clients: Client[] = [];
  const tools: ToolSet = {};
  for (const server of servers.filter((s) => s.enabled)) {
    try {
      const client = await connect(server);
      clients.push(client);
      const { tools: mcpTools } = await client.listTools();
      for (const t of mcpTools) {
        const key = `${server.name}_${t.name}`.replace(/[^a-zA-Z0-9_]/g, "_").slice(0, 60);
        tools[key] = tool({
          description: (t.description ?? t.name).slice(0, 1000),
          inputSchema: jsonSchema(
            (t.inputSchema ?? { type: "object", properties: {} }) as Parameters<typeof jsonSchema>[0],
          ),
          execute: async (args: unknown) => {
            const res = await client.callTool({
              name: t.name,
              arguments: (args ?? {}) as Record<string, unknown>,
            });
            return res.content ?? res;
          },
        });
      }
    } catch {
      /* skip unreachable server */
    }
  }
  return {
    tools,
    close: async () => {
      await Promise.all(clients.map((c) => c.close().catch(() => {})));
    },
  };
}

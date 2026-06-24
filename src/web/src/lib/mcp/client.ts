import "server-only";
import { randomUUID } from "node:crypto";
import { Client } from "@modelcontextprotocol/sdk/client/index.js";
import { StreamableHTTPClientTransport } from "@modelcontextprotocol/sdk/client/streamableHttp.js";
import { SSEClientTransport } from "@modelcontextprotocol/sdk/client/sse.js";
import { tool, jsonSchema, type ToolSet } from "ai";
import type { McpServer } from "./store";
import { getServerAuth } from "./oauth-store";
import { FileOAuthProvider } from "./oauth-provider";

async function connect(server: McpServer): Promise<Client> {
  if (!server.url) {
    throw new Error("stdio servers are managed but not loaded over HTTP");
  }
  const client = new Client({ name: "jarvis-web", version: "1.0.0" });
  const url = new URL(server.url);
  // OAuth-backed connector → drive it through the refreshing auth provider so an
  // expired access token is silently refreshed (and the new one re-mirrored into
  // mcp.json). Otherwise forward static auth headers (e.g. a PAT Bearer).
  const oauth = await getServerAuth(server.name);
  const opts = oauth
    ? {
        authProvider: new FileOAuthProvider({
          name: server.name,
          state: randomUUID(),
          url: server.url,
          transport: server.transport === "sse" ? "sse" : "http",
          redirectUri: oauth.redirectUri,
          seed: { clientInfo: oauth.clientInfo, tokens: oauth.tokens },
        }),
      }
    : server.headers
      ? { requestInit: { headers: server.headers } }
      : undefined;
  const transport =
    server.transport === "sse"
      ? new SSEClientTransport(url, opts)
      : new StreamableHTTPClientTransport(url, opts);
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

// Sanitize an MCP tool's JSON Schema into a provider-safe subset. Some MCP
// servers ship schemas that don't validate against the JSON-Schema 2020-12
// meta-schema (e.g. a non-string `pattern`, an exotic `format`, or an
// unresolved `$ref`). Strict providers (Anthropic) reject the ENTIRE tools
// array at request time when ANY tool's schema is malformed, which aborts
// the whole chat turn with `finish: error` and no output — i.e. one bad
// connector tool silently breaks all chat. We recursively drop the
// validation-only keywords that commonly break strict validators while
// preserving the structure the model actually needs (type / properties /
// required / enum / description). The MCP server still validates real args.
const DROP_KEYS = new Set([
  "pattern",
  "format",
  "$schema",
  "$id",
  "$ref",
  "$dynamicRef",
  "$anchor",
  "$dynamicAnchor",
]);

export function sanitizeJsonSchema(node: unknown): unknown {
  if (Array.isArray(node)) return node.map(sanitizeJsonSchema);
  if (!node || typeof node !== "object") return node;
  const out: Record<string, unknown> = {};
  for (const [k, v] of Object.entries(node as Record<string, unknown>)) {
    if (DROP_KEYS.has(k)) continue;
    out[k] = sanitizeJsonSchema(v);
  }
  return out;
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
  for (const server of servers.filter((s) => s.enabled && !!s.url)) {
    try {
      const client = await connect(server);
      clients.push(client);
      const { tools: mcpTools } = await client.listTools();
      for (const t of mcpTools) {
        const key = `${server.name}_${t.name}`.replace(/[^a-zA-Z0-9_]/g, "_").slice(0, 60);
        try {
          const safeSchema = sanitizeJsonSchema(
            t.inputSchema ?? { type: "object", properties: {} },
          ) as Parameters<typeof jsonSchema>[0];
          tools[key] = tool({
            description: (t.description ?? t.name).slice(0, 1000),
            inputSchema: jsonSchema(safeSchema),
            execute: async (args: unknown) => {
              const res = await client.callTool({
                name: t.name,
                arguments: (args ?? {}) as Record<string, unknown>,
              });
              return res.content ?? res;
            },
          });
        } catch (err) {
          // A single malformed tool must not drop the rest (or the turn).
          console.warn(`[mcp] skipped tool ${key}: ${String(err)}`);
        }
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

import { loadMcpTools } from "@/lib/mcp/client";
import { listMcpServers } from "@/lib/mcp/store";
import { withUser } from "@/lib/auth-route";

export const runtime = "nodejs";
export const maxDuration = 60;

// Runtime MCP tool call for "live" artifacts — an artifact's JS calls
// `window.jarvis.callTool(server, tool, args)` to pull real data on load.
// Authed via the parent's session (logged-out public viewers get 401), so
// only the owner's own artifacts can reach the owner's MCP servers.
export async function POST(req: Request) {
  return withUser(req, async () => {
    let body: { server?: unknown; tool?: unknown; args?: unknown } = {};
    try {
      body = await req.json();
    } catch {
      return Response.json({ error: "invalid body" }, { status: 400 });
    }
    const server = typeof body.server === "string" ? body.server : "";
    const tool = typeof body.tool === "string" ? body.tool : "";
    if (!tool) return Response.json({ error: "missing tool" }, { status: 400 });
    const args =
      body.args && typeof body.args === "object"
        ? (body.args as Record<string, unknown>)
        : {};

    const servers = await listMcpServers();
    if (!servers.some((s) => s.enabled && s.url)) {
      return Response.json({ error: "no MCP servers configured" }, { status: 404 });
    }
    const loaded = await loadMcpTools(servers);
    try {
      const sanitize = (s: string) => s.replace(/[^a-zA-Z0-9_]/g, "_");
      const key = `${sanitize(server)}_${sanitize(tool)}`.slice(0, 60);
      const entry =
        loaded.tools[key] ??
        Object.entries(loaded.tools).find(([k]) =>
          k.endsWith(`_${sanitize(tool)}`),
        )?.[1];
      const exec = (entry as { execute?: unknown } | undefined)?.execute;
      if (typeof exec !== "function") {
        return Response.json(
          { error: `tool not found: ${server || "*"}/${tool}` },
          { status: 404 },
        );
      }
      const result = await (
        exec as (a: unknown, o: unknown) => Promise<unknown>
      )(args, {});
      return Response.json({ result });
    } catch (e) {
      return Response.json(
        { error: String((e as Error)?.message ?? e) },
        { status: 500 },
      );
    } finally {
      await loaded.close().catch(() => {});
    }
  });
}

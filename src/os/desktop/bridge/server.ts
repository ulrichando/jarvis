import type { LLMClient, Message } from "../providers/types.ts";
import type { ToolRegistry } from "../agent/types.ts";
import { runAgent } from "../agent/loop.ts";

export type BridgeOpts = {
  host: string;
  port: number;
  client: LLMClient;
  defaultModel: string;
  tools: ToolRegistry;
};

export function startBridge(opts: BridgeOpts): ReturnType<typeof Bun.serve> {
  return Bun.serve({
    hostname: opts.host,
    port: opts.port,
    async fetch(req: Request): Promise<Response> {
      const url = new URL(req.url);

      if (url.pathname === "/health" && req.method === "GET") {
        return Response.json({ status: "ok" });
      }

      if (url.pathname === "/api/models" && req.method === "GET") {
        return Response.json({ provider: opts.client.name, model: opts.defaultModel });
      }

      if (url.pathname === "/api/think" && req.method === "POST") {
        let body: { messages: Message[]; model?: string; system?: string };
        try {
          body = (await req.json()) as { messages: Message[]; model?: string; system?: string };
        } catch {
          return Response.json({ error: "invalid JSON body" }, { status: 400 });
        }
        if (!Array.isArray(body.messages)) {
          return Response.json({ error: "messages must be an array" }, { status: 400 });
        }
        const result = await runAgent({
          client: opts.client,
          model: body.model ?? opts.defaultModel,
          messages: body.messages,
          tools: opts.tools,
          system: body.system,
        });
        return Response.json(result);
      }

      return new Response("not found", { status: 404 });
    },
  });
}

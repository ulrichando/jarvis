// HTTP bridge. Owns routes only; no LLM or tool logic lives here.
type Options = { host: string; port: number };

export function startBridge(opts: Options): void {
  Bun.serve({
    hostname: opts.host,
    port: opts.port,
    fetch(req: Request): Response {
      const url = new URL(req.url);
      if (url.pathname === "/health" && req.method === "GET") {
        return Response.json({ status: "ok" });
      }
      return new Response("not found", { status: 404 });
    },
  });
}

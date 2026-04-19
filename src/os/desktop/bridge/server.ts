import type { LLMClient, Message } from "../providers/types.ts";
import type { ToolRegistry } from "../agent/types.ts";
import { runAgent } from "../agent/loop.ts";
import { synthesize } from "../voice/tts.ts";
import { transcribe } from "../voice/stt.ts";
import { ConfirmationQueue } from "../voice/confirmations.ts";

export type BridgeOpts = {
  host: string;
  port: number;
  client: LLMClient;
  defaultModel: string;
  tools: ToolRegistry;
  apiKey: string;
  ttsVoice: string;
  queue?: ConfirmationQueue;
};

export function startBridge(opts: BridgeOpts) {
  const queue = opts.queue ?? new ConfirmationQueue();

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

      if (url.pathname === "/api/speak" && req.method === "POST") {
        try {
          const body = (await req.json()) as { text?: string; voice?: string; format?: "wav" | "mp3" | "flac" | "ogg" };
          if (typeof body.text !== "string" || body.text.length === 0) {
            return Response.json({ error: "text is required" }, { status: 400 });
          }
          const audio = await synthesize({
            apiKey: opts.apiKey,
            text: body.text,
            voice: body.voice ?? opts.ttsVoice,
            format: body.format ?? "wav",
          });
          return new Response(audio, {
            status: 200,
            headers: { "content-type": `audio/${body.format ?? "wav"}` },
          });
        } catch (err) {
          console.error("[misty-core] /api/speak error:", err);
          return Response.json({ error: err instanceof Error ? err.message : String(err) }, { status: 500 });
        }
      }

      if (url.pathname === "/api/transcribe" && req.method === "POST") {
        try {
          const form = await req.formData();
          const file = form.get("audio");
          if (!(file instanceof Blob)) {
            return Response.json({ error: "multipart field 'audio' (Blob) required" }, { status: 400 });
          }
          const buf = new Uint8Array(await file.arrayBuffer());
          const text = await transcribe({ apiKey: opts.apiKey, audio: buf });
          return Response.json({ text });
        } catch (err) {
          console.error("[misty-core] /api/transcribe error:", err);
          return Response.json({ error: err instanceof Error ? err.message : String(err) }, { status: 500 });
        }
      }

      const confirmMatch = url.pathname.match(/^\/api\/confirmation\/([^/]+)$/);
      if (confirmMatch && req.method === "POST") {
        const id = confirmMatch[1]!;
        let body: { decision?: "allow" | "deny" };
        try {
          body = (await req.json()) as { decision?: "allow" | "deny" };
        } catch {
          return Response.json({ error: "invalid JSON body" }, { status: 400 });
        }
        if (body.decision !== "allow" && body.decision !== "deny") {
          return Response.json({ error: "decision must be 'allow' or 'deny'" }, { status: 400 });
        }
        const ok = queue.resolve(id, body.decision);
        if (!ok) return Response.json({ error: "unknown or already-resolved confirmation id" }, { status: 404 });
        return Response.json({ ok: true });
      }

      if (url.pathname === "/api/confirmation" && req.method === "GET") {
        return Response.json({ pending: queue.list() });
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
        const interactive = url.searchParams.get("interactive") === "1";
        try {
          const result = await runAgent({
            client: opts.client,
            model: body.model ?? opts.defaultModel,
            messages: body.messages,
            tools: opts.tools,
            system: body.system,
            confirm: interactive
              ? async (creq) => {
                  const { id, wait } = queue.open(creq);
                  console.log(`[misty-core] awaiting confirmation ${id} for ${creq.tool}`);
                  return wait;
                }
              : undefined,
          });
          return Response.json(result);
        } catch (err) {
          const message = err instanceof Error ? err.message : String(err);
          console.error("[misty-core] /api/think error:", err);
          return Response.json({ error: message }, { status: 500 });
        }
      }

      return new Response("not found", { status: 404 });
    },
  });
}

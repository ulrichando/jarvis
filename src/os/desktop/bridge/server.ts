import type { LLMClient, Message } from "../providers/types.ts";
import type { ToolRegistry } from "../agent/types.ts";
import { runAgent } from "../agent/loop.ts";
import { synthesize } from "../voice/tts.ts";
import { transcribe } from "../voice/stt.ts";
import { ConfirmationQueue } from "../voice/confirmations.ts";
import { VoiceModeState, isVoiceMode, type VoiceMode } from "../voice/mode.ts";
import { EventBus, type PanelKind } from "./events.ts";
import { PanelState } from "../panels/state.ts";
import { JARVIS_PERSONA } from "../personality.ts";

export type BridgeOpts = {
  host: string;
  port: number;
  client: LLMClient;
  defaultModel: string;
  tools: ToolRegistry;
  apiKey: string;
  ttsVoice: string;
  queue?: ConfirmationQueue;
  voiceMode?: VoiceModeState;
  events?: EventBus;
  panels?: PanelState;
};

export function startBridge(opts: BridgeOpts) {
  const queue = opts.queue ?? new ConfirmationQueue();
  const voiceMode = opts.voiceMode ?? new VoiceModeState();
  const events = opts.events ?? new EventBus();
  const panels = opts.panels ?? new PanelState();

  // Wrap voiceMode setters so changes publish on the event bus.
  const setVoiceMode = (mode: VoiceMode) => {
    const next = voiceMode.set(mode);
    events.emit({ type: "voice.mode_changed", mode: next.mode, changedAt: next.changedAt });
    return next;
  };
  const cycleVoiceMode = () => {
    const next = voiceMode.cycle();
    events.emit({ type: "voice.mode_changed", mode: next.mode, changedAt: next.changedAt });
    return next;
  };

  return Bun.serve<{ unsub: () => void }, never>({
    hostname: opts.host,
    port: opts.port,
    websocket: {
      open(ws) {
        const unsub = events.subscribe((event) => {
          try { ws.send(JSON.stringify(event)); } catch { /* client gone; fine */ }
        });
        ws.data = { unsub };
        // Send a hello with current state so clients don't need to GET first.
        ws.send(JSON.stringify({ type: "voice.mode_changed", ...voiceMode.get() }));
        // Rehydrate panels already open before this client connected.
        for (const panel of panels.list()) {
          ws.send(JSON.stringify({ type: "panel.opened", panel }));
        }
      },
      close(ws) {
        ws.data?.unsub?.();
      },
      message(ws, msg) {
        // Clients can send ping; we ignore everything else.
        if (msg === "ping") ws.send("pong");
      },
    },
    async fetch(req: Request, server): Promise<Response | undefined> {
      const url = new URL(req.url);

      if (url.pathname === "/ws") {
        const upgraded = server.upgrade(req, { data: { unsub: () => {} } });
        if (upgraded) return undefined; // Bun handles the 101
        return new Response("websocket upgrade failed", { status: 400 });
      }

      if (url.pathname === "/health" && req.method === "GET") {
        return Response.json({ status: "ok" });
      }

      // Tauri's jarvis-desktop binary has http://127.0.0.1:8765/ hardcoded
      // as its window URL. Redirect root to the HUD so the Tauri app lands
      // on our custom HUD with panels and reactor state instead of falling
      // back to the bundled React dist.
      if ((url.pathname === "/" || url.pathname === "") && req.method === "GET") {
        return Response.redirect("/hud/", 302);
      }

      if (url.pathname.startsWith("/hud") && req.method === "GET") {
        // Serve anything under hud/web/. /hud and /hud/ → index.html.
        let relPath = url.pathname.slice("/hud".length);
        if (relPath === "" || relPath === "/") relPath = "/index.html";
        // Reject path traversal.
        if (relPath.includes("..") || !relPath.startsWith("/")) {
          return new Response("forbidden", { status: 403 });
        }
        const base = new URL("../hud/web/", import.meta.url).pathname;
        const filePath = base + relPath.slice(1);
        const file = Bun.file(filePath);
        if (!(await file.exists())) return new Response("not found", { status: 404 });
        const type = relPath.endsWith(".html") ? "text/html; charset=utf-8"
                   : relPath.endsWith(".js") ? "application/javascript; charset=utf-8"
                   : relPath.endsWith(".css") ? "text/css; charset=utf-8"
                   : "application/octet-stream";
        return new Response(file, {
          headers: {
            "content-type": type,
            // Prevent WebKit from caching during dev — HUD updates must land immediately.
            "cache-control": "no-cache, no-store, must-revalidate",
            "pragma": "no-cache",
          },
        });
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

      if (url.pathname === "/api/voice/mode" && req.method === "GET") {
        return Response.json(voiceMode.get());
      }

      if (url.pathname === "/api/voice/mode" && req.method === "POST") {
        let body: { mode?: string; cycle?: boolean };
        try {
          body = (await req.json()) as { mode?: string; cycle?: boolean };
        } catch {
          return Response.json({ error: "invalid JSON body" }, { status: 400 });
        }
        try {
          const next = body.cycle ? cycleVoiceMode() : setVoiceMode(body.mode as "off" | "ptt" | "wake");
          return Response.json(next);
        } catch (err) {
          if (!body.cycle && !isVoiceMode(body.mode)) {
            return Response.json({ error: `mode must be one of: off, ptt, wake` }, { status: 400 });
          }
          return Response.json({ error: err instanceof Error ? err.message : String(err) }, { status: 500 });
        }
      }

      if (url.pathname === "/api/voice/wake-triggered" && req.method === "POST") {
        let body: { source?: string } = {};
        try {
          body = (await req.json()) as { source?: string };
        } catch { /* accept empty body */ }
        const at = Date.now();
        events.emit({ type: "voice.wake_triggered", source: body.source, at });
        return Response.json({ ok: true, at });
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
        events.emit({ type: "confirmation.resolved", id, decision: body.decision });
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
            system: body.system ?? JARVIS_PERSONA,
            confirm: interactive
              ? async (creq) => {
                  const { id, wait } = queue.open(creq);
                  events.emit({ type: "confirmation.opened", id, tool: creq.tool });
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

      if (url.pathname === "/api/turn/state" && req.method === "POST") {
        let body: { phase?: string; audioLevel?: number };
        try { body = await req.json() as typeof body; } catch { body = {}; }
        const validPhases = ["idle", "listening", "thinking", "speaking"];
        if (!body.phase || !validPhases.includes(body.phase)) {
          return Response.json({ error: `phase must be one of: ${validPhases.join(", ")}` }, { status: 400 });
        }
        const at = Date.now();
        events.emit({ type: "turn.state", phase: body.phase as "idle" | "listening" | "thinking" | "speaking", audioLevel: body.audioLevel, at });
        return Response.json({ ok: true, at });
      }

      if (url.pathname === "/api/panel" && req.method === "GET") {
        return Response.json({ panels: panels.list() });
      }

      if (url.pathname === "/api/panel" && req.method === "POST") {
        let body: {
          kind?: PanelKind;
          title?: string;
          src?: string;
          content?: string;
          x?: number; y?: number; width?: number; height?: number;
        };
        try {
          body = await req.json() as typeof body;
        } catch {
          return Response.json({ error: "invalid JSON body" }, { status: 400 });
        }
        const validKinds = ["browser", "video", "image", "text", "file"];
        if (!body.kind || !validKinds.includes(body.kind)) {
          return Response.json({ error: `kind must be one of: ${validKinds.join(", ")}` }, { status: 400 });
        }
        if (body.kind !== "text" && !body.src) {
          return Response.json({ error: `kind=${body.kind} requires 'src'` }, { status: 400 });
        }
        if (body.kind === "text" && !body.content) {
          return Response.json({ error: "kind=text requires 'content'" }, { status: 400 });
        }
        const spec = panels.open(body as { kind: PanelKind });
        events.emit({ type: "panel.opened", panel: spec });
        return Response.json(spec);
      }

      if (url.pathname === "/api/panel" && req.method === "DELETE") {
        const ids = panels.list().map((p) => p.id);
        panels.clear();
        for (const id of ids) events.emit({ type: "panel.closed", id });
        return Response.json({ cleared: ids.length });
      }

      const panelIdMatch = url.pathname.match(/^\/api\/panel\/([^/]+)$/);
      if (panelIdMatch && req.method === "DELETE") {
        const id = panelIdMatch[1]!;
        const ok = panels.close(id);
        if (!ok) return Response.json({ error: "unknown panel id" }, { status: 404 });
        events.emit({ type: "panel.closed", id });
        return Response.json({ ok: true });
      }

      return new Response("not found", { status: 404 });
    },
  });
}

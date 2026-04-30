// src/cli/src/bridge/ext_browse.ts
//
// /api/ext_browse — accepts a browser command from the voice agent,
// forwards it to the connected jarvis-screen extension over WebSocket,
// and returns the extension's response synchronously to the caller.
//
// Correlation: each command gets a UUID cmd_id. The extension echoes
// the cmd_id back with its response. The bridge holds Map<cmd_id,
// {resolve, reject, timer}> until either response arrives or timeout.

import { randomUUID } from "node:crypto";

interface PendingCmd {
  resolve: (result: any) => void;
  reject: (err: Error) => void;
  timer: ReturnType<typeof setTimeout>;
}

let extensionWS: any = null;
const pending = new Map<string, PendingCmd>();

const DEFAULT_TIMEOUT_MS = parseInt(process.env.JARVIS_EXT_TIMEOUT_MS || "10000", 10);

export function registerExtensionWS(ws: any) {
  if (extensionWS) {
    try { extensionWS.close?.(); } catch {}
  }
  extensionWS = ws;
}

export function unregisterExtensionWS(ws: any) {
  if (extensionWS === ws) extensionWS = null;
}

export function isExtensionConnected(): boolean {
  return !!extensionWS && extensionWS.readyState === 1;
}

export function resolveExtensionResponse(msg: { cmd_id: string; [k: string]: any }) {
  const p = pending.get(msg.cmd_id);
  if (!p) return;
  pending.delete(msg.cmd_id);
  clearTimeout(p.timer);
  p.resolve(msg);
}

export async function handleExtBrowse(req: Request): Promise<Response> {
  let body: any;
  try { body = await req.json(); }
  catch { return Response.json({ ok: false, error: "bad json" }, { status: 400 }); }

  const action = body?.action;
  if (!action) return Response.json({ ok: false, error: "action required" }, { status: 400 });

  if (!isExtensionConnected()) {
    return Response.json(
      { ok: false, error: "extension not connected" },
      { status: 503 },
    );
  }

  const cmd_id = randomUUID();
  const timeout_ms = body.timeout_ms || DEFAULT_TIMEOUT_MS;
  const cmd = { cmd_id, action, args: body.args || {}, confirmed: !!body.confirmed };

  const responsePromise = new Promise<any>((resolve, reject) => {
    const timer = setTimeout(() => {
      pending.delete(cmd_id);
      reject(new Error("timeout"));
    }, timeout_ms);
    pending.set(cmd_id, { resolve, reject, timer });
  });

  extensionWS.send(JSON.stringify(cmd));

  try {
    const result = await responsePromise;
    return Response.json(result, { status: 200 });
  } catch (e: any) {
    return Response.json(
      { ok: false, error: e.message || String(e) },
      { status: e.message === "timeout" ? 504 : 500 },
    );
  }
}

export function _resetForTests() {
  for (const p of pending.values()) clearTimeout(p.timer);
  pending.clear();
  extensionWS = null;
}

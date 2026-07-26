// src/cli/src/bridge/ext_browse.ts
//
// /api/ext_browse — accepts a browser command from the voice agent,
// forwards it to the connected jarvis-screen extension over WebSocket,
// and returns the extension's response synchronously to the caller.
//
// Correlation: each command gets a UUID cmd_id. The extension echoes
// the cmd_id back with its response. The bridge holds Map<cmd_id,
// {resolve, reject, timer}> until either response arrives or timeout.
//
// Policy: sendExtCommand is the choke point for EVERY extension action
// (this HTTP handler, the side-panel browser agent in server.ts, and the
// jarvis-in-chrome MCP via /api/ext_browse), so the web app's persisted
// Settings → Jarvis in Chrome policy (chromePolicy.ts) is enforced here
// before a command ever reaches the extension.

import { randomUUID } from "node:crypto";
import {
  decideSitePolicy,
  hostOf,
  loadChromePolicy,
  SITE_AGNOSTIC_ACTIONS,
  type ChromePolicy,
} from "./chromePolicy";

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

// ── Current-page host tracking (for the web policy's blocklist) ───────
// `navigate` is judged on its DESTINATION url; every other site-gated action
// needs the CURRENT page's host, which only the extension knows. Rather than
// changing the extension's command envelope, the bridge tracks the last host
// reported by any command response that carries a url (navigate / get_url /
// activate_tab), with a short TTL, and probes the extension with a get_url
// when the tracked host is missing or stale. The probe only fires when the
// policy could actually block (allow-mode with a non-empty blocklist), so the
// default allow-all config pays zero overhead.
const HOST_TTL_MS = 2000;
let lastKnownHost: { host: string; ts: number } | null = null;

const PAGE_CHANGING_ACTIONS = new Set(["navigate", "back", "forward", "close_tab", "activate_tab"]);

function noteHostFromResult(action: string, result: any) {
  const url =
    result && typeof result.url === "string" ? result.url
    : result && typeof result.page_state?.url === "string" ? result.page_state.url
    : "";
  const host = url ? hostOf(url) : "";
  if (host) lastKnownHost = { host, ts: Date.now() };
  // Page changed but the reply carried no url → whatever we knew is stale.
  else if (PAGE_CHANGING_ACTIONS.has(action)) lastKnownHost = null;
}

async function resolveTargetHost(
  action: string,
  args: any,
  policy: ChromePolicy,
  timeoutMs: number,
): Promise<string | null> {
  if (action === "navigate") return hostOf(String(args?.url ?? "")) || null;
  if (SITE_AGNOSTIC_ACTIONS.has(action)) return null; // never site-gated
  if (policy.defaultPolicy === "block") return null; // refused regardless — skip the probe
  if (policy.blockedSites.length === 0) return null; // nothing can block — zero overhead
  if (lastKnownHost && Date.now() - lastKnownHost.ts < HOST_TTL_MS) return lastKnownHost.host;
  try {
    const r = await sendRaw("get_url", {}, false, Math.min(timeoutMs, 4000));
    if (r && r.ok !== false && typeof r.url === "string") return hostOf(r.url) || null;
  } catch {
    // content script unreachable (chrome:// page, load race) — host unknown;
    // the action itself will fail downstream on such pages anyway.
  }
  return null;
}

// Raw send, no policy: correlate cmd_id, await the extension's reply.
async function sendRaw(
  action: string,
  args: any,
  confirmed: boolean,
  timeoutMs: number,
): Promise<any> {
  if (!isExtensionConnected()) throw new Error("extension not connected");
  const cmd_id = randomUUID();
  const cmd = { cmd_id, action, args: args || {}, confirmed: !!confirmed };
  const responsePromise = new Promise<any>((resolve, reject) => {
    const timer = setTimeout(() => {
      pending.delete(cmd_id);
      reject(new Error("timeout"));
    }, timeoutMs);
    pending.set(cmd_id, { resolve, reject, timer });
  });
  extensionWS.send(JSON.stringify(cmd));
  const result = await responsePromise;
  noteHostFromResult(action, result);
  return result;
}

// Send ONE command to the connected extension and await its {ok, ...} reply.
// Shared by the HTTP handler (below) and the in-process browser agent loop.
// Throws "extension not connected" / "timeout" so callers can classify.
// Enforces the web-persisted Jarvis-in-Chrome policy first: a refused action
// resolves to { ok:false, site_blocked:true, error } — the same shape the
// extension's own site gate returns, so every existing caller (browser agent,
// MCP adapter, HTTP clients) already understands it.
export async function sendExtCommand(
  action: string,
  args: any = {},
  confirmed = false,
  timeoutMs = DEFAULT_TIMEOUT_MS,
): Promise<any> {
  if (!isExtensionConnected()) throw new Error("extension not connected");
  const policy = loadChromePolicy();
  const host = await resolveTargetHost(action, args, policy, timeoutMs);
  const decision = decideSitePolicy(action, host, policy);
  if (decision.allow !== true) {
    return { ok: false, error: decision.reason, site_blocked: true };
  }
  return sendRaw(action, args, confirmed, timeoutMs);
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

  try {
    const result = await sendExtCommand(
      action, body.args || {}, !!body.confirmed, body.timeout_ms || DEFAULT_TIMEOUT_MS,
    );
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
  lastKnownHost = null;
}

/**
 * GET /api/chrome/status — live status of the "Jarvis in Chrome" path.
 *
 * The browser integration runs through the local bridge (started by the Jarvis
 * desktop app on 127.0.0.1:8765): the extension connects to the bridge over WS,
 * and the voice agent drives it via /api/ext_browse. This route reports, from
 * the web server (same box), whether that path is live:
 *   - bridgeReachable:  the bridge answers /health (public, no auth)
 *   - extensionConnected: the bridge's /api/ext_status (isExtensionConnected) —
 *     needs the local bearer token; null when unknown (token/endpoint absent).
 * Best-effort + short timeouts; never throws.
 */
import { promises as fs } from "fs";
import os from "os";
import path from "path";

export const dynamic = "force-dynamic";
export const runtime = "nodejs";

const BRIDGE = process.env.JARVIS_BRIDGE_URL ?? "http://127.0.0.1:8765";

async function ping(url: string, init?: RequestInit): Promise<Response | null> {
  const ctrl = new AbortController();
  const timer = setTimeout(() => ctrl.abort(), 2000);
  try {
    return await fetch(url, { ...init, signal: ctrl.signal, cache: "no-store" });
  } catch {
    return null;
  } finally {
    clearTimeout(timer);
  }
}

async function bridgeToken(): Promise<string> {
  if (process.env.JARVIS_LOCAL_API_TOKEN) return process.env.JARVIS_LOCAL_API_TOKEN;
  try {
    const txt = await fs.readFile(
      path.join(os.homedir(), ".jarvis", "local-api-token.env"),
      "utf-8",
    );
    return txt.match(/JARVIS_LOCAL_API_TOKEN=(.+)/)?.[1]?.trim() ?? "";
  } catch {
    return "";
  }
}

export async function GET(): Promise<Response> {
  const health = await ping(`${BRIDGE}/health`);
  const bridgeReachable = !!health?.ok;
  let extensionConnected: boolean | null = null;
  if (bridgeReachable) {
    const token = await bridgeToken();
    const res = await ping(
      `${BRIDGE}/api/ext_status`,
      token ? { headers: { Authorization: `Bearer ${token}` } } : undefined,
    );
    if (res?.ok) {
      try {
        extensionConnected = !!(await res.json())?.connected;
      } catch {
        extensionConnected = null;
      }
    }
  }
  return Response.json({ bridgeReachable, extensionConnected });
}

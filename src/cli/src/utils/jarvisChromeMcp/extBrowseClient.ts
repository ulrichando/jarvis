// src/cli/src/utils/jarvisChromeMcp/extBrowseClient.ts
import type { ChromeBridge } from './resolveChromeBridge.js'

export type ExtResult =
  | { ok: true; result: unknown }
  | { ok: false; kind: 'BRIDGE_DOWN' | 'EXT_NOT_CONNECTED' | 'TIMEOUT' | 'NEEDS_CONFIRMATION' | 'SITE_BLOCKED' | 'ERROR'; message: string }

export async function callExtBrowse(
  action: string,
  args: Record<string, unknown>,
  confirmed: boolean,
  bridge: ChromeBridge,
  fetchImpl: typeof fetch = fetch,
): Promise<ExtResult> {
  let res: Response
  try {
    res = await fetchImpl(`${bridge.baseUrl}/api/ext_browse`, {
      method: 'POST',
      headers: { 'content-type': 'application/json', authorization: `Bearer ${bridge.token}` },
      body: JSON.stringify({ action, args, confirmed }),
    })
  } catch (e: any) {
    return { ok: false, kind: 'BRIDGE_DOWN', message: 'Jarvis-in-Chrome bridge is not running — start the Jarvis desktop app.' }
  }

  if (res.status === 503) return { ok: false, kind: 'EXT_NOT_CONNECTED', message: 'Browser extension not connected — open your browser with the jarvis-screen extension.' }
  if (res.status === 504) return { ok: false, kind: 'TIMEOUT', message: 'Browser action timed out.' }

  let body: any = null
  try { body = await res.json() } catch { /* fallthrough */ }

  if (res.status >= 500 || !body) return { ok: false, kind: 'ERROR', message: body?.error || `Bridge error (HTTP ${res.status}).` }

  // HTTP 200 body-level refusals:
  if (body.ok === false && body.needs_confirmation) return { ok: false, kind: 'NEEDS_CONFIRMATION', message: 'This action needs confirmation (it modifies the page or navigates). Re-run the tool with confirmed: true to proceed.' }
  if (body.ok === false && body.site_blocked) return { ok: false, kind: 'SITE_BLOCKED', message: 'This site is blocked by your extension policy.' }
  if (body.ok === false) return { ok: false, kind: 'ERROR', message: body.error || 'Browser action failed.' }

  return { ok: true, result: body }
}

import { KOKORO_ID_RE } from '@/lib/chat/voices'

/**
 * GET /api/tts/voices — the voice list the local Kokoro engine actually
 * serves (proxied from kokoro-fastapi /v1/audio/voices). Drives the
 * Settings → General → Voice picker. 503 when Kokoro is down — the picker
 * shows a "not reachable" hint instead of a stale invented list.
 */
export const dynamic = 'force-dynamic'
export const runtime = 'nodejs'

const KOKORO_URL = (process.env.JARVIS_LOCAL_TTS_URL ?? 'http://127.0.0.1:8880/v1').replace(/\/$/, '')

// ponytail: module-level 5-min cache; per-user caching if this ever multi-tenants.
let cache: { at: number; voices: string[] } | null = null
const TTL_MS = 5 * 60 * 1000

export async function GET(): Promise<Response> {
  if (cache && Date.now() - cache.at < TTL_MS) {
    return Response.json({ voices: cache.voices })
  }
  try {
    const r = await fetch(`${KOKORO_URL}/audio/voices`, {
      signal: AbortSignal.timeout(5_000),
    })
    if (!r.ok) return new Response('kokoro unavailable', { status: 503 })
    const j = (await r.json()) as { voices?: Array<{ id?: string } | string> }
    const voices = (j.voices ?? [])
      .map((v) => (typeof v === 'string' ? v : v.id ?? ''))
      // v0 entries are legacy duplicates of the same speakers — hide them.
      .filter((id) => KOKORO_ID_RE.test(id) && !id.includes('_v0'))
    cache = { at: Date.now(), voices }
    return Response.json({ voices })
  } catch {
    return new Response('kokoro unavailable', { status: 503 })
  }
}

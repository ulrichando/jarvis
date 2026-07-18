import { KOKORO_ID_RE, EDGE_VOICES } from '@/lib/chat/voices'

/**
 * GET /api/tts/voices — both TTS engines for the Settings → General → Voice
 * picker:
 *   - kokoro: the voice list the LOCAL Kokoro engine actually serves
 *     (proxied from kokoro-fastapi /v1/audio/voices). Empty when Kokoro is
 *     down — the picker shows a "not reachable" hint for that group only.
 *   - edge: the static Microsoft Edge neural catalog (online, keyless) —
 *     always available, so online voices work without the local container.
 * Shape: { kokoro: string[], edge: {id,label,gender,region}[] }
 */
export const dynamic = 'force-dynamic'
export const runtime = 'nodejs'

const KOKORO_URL = (process.env.JARVIS_LOCAL_TTS_URL ?? 'http://127.0.0.1:8880/v1').replace(/\/$/, '')

// ponytail: module-level 5-min cache; per-user caching if this ever multi-tenants.
let cache: { at: number; voices: string[] } | null = null
const TTL_MS = 5 * 60 * 1000

async function kokoroVoices(): Promise<string[]> {
  if (cache && Date.now() - cache.at < TTL_MS) return cache.voices
  try {
    const r = await fetch(`${KOKORO_URL}/audio/voices`, {
      signal: AbortSignal.timeout(5_000),
    })
    if (!r.ok) return []
    const j = (await r.json()) as { voices?: Array<{ id?: string } | string> }
    const voices = (j.voices ?? [])
      .map((v) => (typeof v === 'string' ? v : v.id ?? ''))
      // v0 entries are legacy duplicates of the same speakers — hide them.
      .filter((id) => KOKORO_ID_RE.test(id) && !id.includes('_v0'))
    cache = { at: Date.now(), voices }
    return voices
  } catch {
    return [] // Kokoro down — Edge list still ships below
  }
}

export async function GET(): Promise<Response> {
  return Response.json({ kokoro: await kokoroVoices(), edge: EDGE_VOICES })
}

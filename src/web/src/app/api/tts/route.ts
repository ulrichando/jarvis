/**
 * /api/tts — neural read-aloud for /chat voice mode. Uses JARVIS's LOCAL
 * Kokoro TTS (kokoro-fastapi on :8880, $0, OpenAI-compatible — the same engine
 * the voice agent uses via JARVIS_LOCAL_TTS_ENGINE=kokoro); on a miss the client
 * falls back to browser speechSynthesis (503). Same-origin from the logged-in
 * page (proxy.ts).
 *
 * Groq Orpheus was removed 2026-06-29 (full-Groq-eradication pass) — Kokoro is
 * the only server-side engine now.
 */
import { KOKORO_ID_RE } from '@/lib/chat/voices'

export const dynamic = 'force-dynamic'
export const runtime = 'nodejs'

// Local Kokoro — reuse the voice agent's env so one setting drives both.
const KOKORO_URL = (process.env.JARVIS_LOCAL_TTS_URL ?? 'http://127.0.0.1:8880/v1').replace(/\/$/, '')
const KOKORO_MODEL = process.env.JARVIS_LOCAL_TTS_MODEL ?? 'kokoro'
const KOKORO_VOICE = process.env.JARVIS_LOCAL_TTS_VOICE ?? 'af_heart'

function passthrough(r: Response): Response {
  return new Response(r.body, {
    status: 200,
    headers: {
      'content-type': r.headers.get('content-type') ?? 'audio/wav',
      'cache-control': 'no-store',
    },
  })
}

async function viaKokoro(text: string, voice: string): Promise<Response | null> {
  try {
    const r = await fetch(`${KOKORO_URL}/audio/speech`, {
      method: 'POST',
      headers: { 'content-type': 'application/json' },
      body: JSON.stringify({ model: KOKORO_MODEL, voice, input: text, response_format: 'wav' }),
      signal: AbortSignal.timeout(30_000),
    })
    return r.ok && r.body ? passthrough(r) : null
  } catch {
    return null // not running / unreachable → client falls back to speechSynthesis
  }
}

export async function POST(req: Request): Promise<Response> {
  let text = ''
  let voice = KOKORO_VOICE
  try {
    const body = (await req.json()) as { text?: unknown; voice?: unknown }
    text = typeof body?.text === 'string' ? body.text.slice(0, 4000) : ''
    // Settings → General → Voice. Shape-gated — the value reaches an
    // internal HTTP body, so never pass arbitrary client strings through.
    // Kokoro itself rejects ids it doesn't serve (client then falls back).
    if (typeof body?.voice === 'string' && KOKORO_ID_RE.test(body.voice)) {
      voice = body.voice
    }
  } catch {
    /* empty body */
  }
  if (!text.trim()) return new Response('text required', { status: 400 })

  const res = await viaKokoro(text, voice)
  if (res) return res
  // Kokoro unavailable — client falls back to browser speechSynthesis.
  return new Response('no tts engine available', { status: 503 })
}

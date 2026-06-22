/**
 * /api/tts — neural read-aloud for /chat voice mode. Prefers JARVIS's LOCAL
 * Kokoro TTS (kokoro-fastapi on :8880, $0, OpenAI-compatible — the same engine
 * the voice agent uses via JARVIS_LOCAL_TTS_ENGINE=kokoro) and falls back to
 * Groq's Orpheus when local isn't reachable, then to browser speechSynthesis
 * (client-side, on a 503). Same-origin from the logged-in page (proxy.ts).
 *
 * Engine order via JARVIS_WEB_TTS_ENGINE: "auto" (default — local first),
 * "kokoro", or "groq". Both rungs are OpenAI `audio/speech` shaped, so the only
 * differences are base URL, model, voice, and (Groq) the bearer key.
 */
export const dynamic = 'force-dynamic'
export const runtime = 'nodejs'

const ENGINE = (process.env.JARVIS_WEB_TTS_ENGINE ?? 'auto').toLowerCase()

// Local Kokoro — reuse the voice agent's env so one setting drives both.
const KOKORO_URL = (process.env.JARVIS_LOCAL_TTS_URL ?? 'http://127.0.0.1:8880/v1').replace(/\/$/, '')
const KOKORO_MODEL = process.env.JARVIS_LOCAL_TTS_MODEL ?? 'kokoro'
const KOKORO_VOICE = process.env.JARVIS_LOCAL_TTS_VOICE ?? 'af_heart'

// Cloud Groq Orpheus.
const GROQ_TTS_URL = 'https://api.groq.com/openai/v1/audio/speech'
const GROQ_MODEL = process.env.JARVIS_TTS_MODEL ?? 'canopylabs/orpheus-v1-english'
const GROQ_VOICE = process.env.JARVIS_TTS_VOICE ?? 'troy'

function passthrough(r: Response): Response {
  return new Response(r.body, {
    status: 200,
    headers: {
      'content-type': r.headers.get('content-type') ?? 'audio/wav',
      'cache-control': 'no-store',
    },
  })
}

async function viaKokoro(text: string): Promise<Response | null> {
  try {
    const r = await fetch(`${KOKORO_URL}/audio/speech`, {
      method: 'POST',
      headers: { 'content-type': 'application/json' },
      body: JSON.stringify({ model: KOKORO_MODEL, voice: KOKORO_VOICE, input: text, response_format: 'wav' }),
      signal: AbortSignal.timeout(30_000),
    })
    return r.ok && r.body ? passthrough(r) : null
  } catch {
    return null // not running / unreachable → caller tries the next rung
  }
}

async function viaGroq(text: string): Promise<Response | null> {
  const key = process.env.GROQ_API_KEY
  if (!key) return null
  try {
    const r = await fetch(GROQ_TTS_URL, {
      method: 'POST',
      headers: { authorization: `Bearer ${key}`, 'content-type': 'application/json' },
      body: JSON.stringify({ model: GROQ_MODEL, voice: GROQ_VOICE, input: text, response_format: 'wav' }),
      signal: AbortSignal.timeout(30_000),
    })
    return r.ok && r.body ? passthrough(r) : null
  } catch {
    return null
  }
}

export async function POST(req: Request): Promise<Response> {
  let text = ''
  try {
    const body = (await req.json()) as { text?: unknown }
    text = typeof body?.text === 'string' ? body.text.slice(0, 4000) : ''
  } catch {
    /* empty body */
  }
  if (!text.trim()) return new Response('text required', { status: 400 })

  const rungs = ENGINE === 'groq' ? [viaGroq, viaKokoro] : [viaKokoro, viaGroq]
  for (const rung of rungs) {
    const res = await rung(text)
    if (res) return res
  }
  // Nothing available — client falls back to browser speechSynthesis.
  return new Response('no tts engine available', { status: 503 })
}

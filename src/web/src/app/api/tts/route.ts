/**
 * /api/tts — neural read-aloud for voice mode. Proxies to Groq's
 * OpenAI-compatible TTS (the same Orpheus voice JARVIS's voice agent uses), so
 * /chat voice mode sounds natural instead of the robotic browser speechSynthesis
 * (which on Linux is espeak). Same-origin from the logged-in page (proxy.ts).
 *
 * Returns audio (wav). 503 when GROQ_API_KEY isn't configured — the client then
 * falls back to browser speechSynthesis, so this is a pure upgrade.
 */
export const dynamic = 'force-dynamic'
export const runtime = 'nodejs'

const GROQ_TTS_URL = 'https://api.groq.com/openai/v1/audio/speech'
const MODEL = process.env.JARVIS_TTS_MODEL ?? 'canopylabs/orpheus-v1-english'
const VOICE = process.env.JARVIS_TTS_VOICE ?? 'troy'

export async function POST(req: Request): Promise<Response> {
  const key = process.env.GROQ_API_KEY
  if (!key) return new Response('GROQ_API_KEY not configured', { status: 503 })

  let text = ''
  try {
    const body = (await req.json()) as { text?: unknown }
    text = typeof body?.text === 'string' ? body.text.slice(0, 4000) : ''
  } catch {
    /* empty body */
  }
  if (!text.trim()) return new Response('text required', { status: 400 })

  try {
    const r = await fetch(GROQ_TTS_URL, {
      method: 'POST',
      headers: {
        authorization: `Bearer ${key}`,
        'content-type': 'application/json',
      },
      body: JSON.stringify({ model: MODEL, voice: VOICE, input: text, response_format: 'wav' }),
      signal: AbortSignal.timeout(30_000),
    })
    if (!r.ok || !r.body) {
      return new Response(`tts upstream ${r.status}`, { status: 502 })
    }
    return new Response(r.body, {
      status: 200,
      headers: {
        'content-type': r.headers.get('content-type') ?? 'audio/wav',
        'cache-control': 'no-store',
      },
    })
  } catch {
    return new Response('tts request failed', { status: 502 })
  }
}

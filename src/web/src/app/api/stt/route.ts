/**
 * /api/stt — speech-to-text for /chat voice mode. Proxies one captured
 * utterance to Groq's OpenAI-compatible transcription endpoint (Whisper Large
 * v3 Turbo — ~$0.04/hr, 228x real-time, so a spoken sentence comes back in
 * well under a second). Non-Deepgram on purpose.
 *
 * Why server-side STT at all: the browser Web Speech API depends on Google's
 * speech servers that aren't bundled in Chromium builds, so it fails with
 * "not-allowed" there. Voice mode instead captures mic audio itself
 * (getUserMedia + MediaRecorder) and posts each utterance here, which works on
 * every Chromium/Firefox/Safari with no model assets to load.
 *
 * Returns { text }. 503 when GROQ_API_KEY isn't configured (the client toasts).
 * Same-origin from the logged-in page (proxy.ts gates it).
 */
export const dynamic = 'force-dynamic'
export const runtime = 'nodejs'

const GROQ_STT_URL = 'https://api.groq.com/openai/v1/audio/transcriptions'
const MODEL = process.env.JARVIS_STT_MODEL ?? 'whisper-large-v3-turbo'

export async function POST(req: Request): Promise<Response> {
  const key = process.env.GROQ_API_KEY
  if (!key) return new Response('GROQ_API_KEY not configured', { status: 503 })

  let inForm: FormData
  try {
    inForm = await req.formData()
  } catch {
    return new Response('multipart/form-data required', { status: 400 })
  }
  const file = inForm.get('file')
  if (!(file instanceof Blob) || file.size === 0) {
    return new Response('file required', { status: 400 })
  }
  // Guard the body — a runaway recorder shouldn't be able to post tens of MB.
  if (file.size > 25 * 1024 * 1024) {
    return new Response('audio too large', { status: 413 })
  }

  const out = new FormData()
  out.append('file', file, (file as File).name || 'utterance.webm')
  out.append('model', MODEL)
  out.append('response_format', 'json')
  out.append('temperature', '0')

  try {
    const r = await fetch(GROQ_STT_URL, {
      method: 'POST',
      headers: { authorization: `Bearer ${key}` },
      body: out,
      signal: AbortSignal.timeout(30_000),
    })
    if (!r.ok) {
      return new Response(`stt upstream ${r.status}`, { status: 502 })
    }
    const data = (await r.json()) as { text?: string }
    return Response.json({ text: (data?.text ?? '').trim() })
  } catch {
    return new Response('stt request failed', { status: 502 })
  }
}

/**
 * /api/tts — neural read-aloud for /chat voice mode. Two engines:
 *   - Kokoro (on-device): JARVIS's LOCAL kokoro-fastapi on :8880 ($0,
 *     OpenAI-compatible — the same engine the voice agent uses via
 *     JARVIS_LOCAL_TTS_ENGINE=kokoro). Default.
 *   - Edge (online): Microsoft Edge neural voices via msedge-tts (free WS
 *     endpoint, no key) — selected when `voice` is an EDGE_VOICES catalog id
 *     (exact-match allowlist). Streams mp3 (audio/mpeg) — the client's
 *     `new Audio(blob)` plays it fine.
 * On a miss the client falls back to browser speechSynthesis (503).
 * Same-origin from the logged-in page (proxy.ts).
 *
 * The old cloud TTS engine was removed 2026-06-29; Edge (added 2026-07)
 * matches the mobile app's online voice option.
 */
import { MsEdgeTTS, OUTPUT_FORMAT } from 'msedge-tts'
import { KOKORO_ID_RE, isEdgeVoice } from '@/lib/chat/voices'

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

// Online synth is usually 1–3 s; bound it so a wedged WS can't hold the
// request open (client would sit in "previewing" forever).
const EDGE_TIMEOUT_MS = 20_000

async function viaEdge(text: string, voice: string): Promise<Response | null> {
  const tts = new MsEdgeTTS()
  let timer: ReturnType<typeof setTimeout> | undefined
  try {
    const buf = await Promise.race([
      (async () => {
        await tts.setMetadata(voice, OUTPUT_FORMAT.AUDIO_24KHZ_96KBITRATE_MONO_MP3)
        const { audioStream } = tts.toStream(text)
        const chunks: Buffer[] = []
        for await (const chunk of audioStream) chunks.push(chunk as Buffer)
        return Buffer.concat(chunks)
      })(),
      new Promise<never>((_, reject) => {
        timer = setTimeout(() => reject(new Error('edge tts timeout')), EDGE_TIMEOUT_MS)
      }),
    ])
    if (buf.length === 0) return null
    return new Response(new Uint8Array(buf), {
      status: 200,
      headers: { 'content-type': 'audio/mpeg', 'cache-control': 'no-store' },
    })
  } catch {
    return null // offline / endpoint hiccup → client falls back to speechSynthesis
  } finally {
    if (timer !== undefined) clearTimeout(timer)
    try {
      tts.close()
    } catch {
      /* never connected */
    }
  }
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
  let edge = false
  try {
    const body = (await req.json()) as { text?: unknown; voice?: unknown }
    text = typeof body?.text === 'string' ? body.text.slice(0, 4000) : ''
    // Settings → General → Voice. Gated — the value reaches an internal
    // synth call, so never pass arbitrary client strings through. Edge ids
    // are exact-match allowlisted (isEdgeVoice); Kokoro ids are shape-gated
    // (Kokoro itself rejects ids it doesn't serve; client then falls back).
    if (typeof body?.voice === 'string') {
      if (isEdgeVoice(body.voice)) {
        edge = true
        voice = body.voice
      } else if (KOKORO_ID_RE.test(body.voice)) {
        voice = body.voice
      }
    }
  } catch {
    /* empty body */
  }
  if (!text.trim()) return new Response('text required', { status: 400 })

  const res = edge ? await viaEdge(text, voice) : await viaKokoro(text, voice)
  if (res) return res
  // Engine unavailable — client falls back to browser speechSynthesis.
  return new Response('no tts engine available', { status: 503 })
}

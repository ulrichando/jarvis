/**
 * /api/stt — speech-to-text for /chat voice mode. Proxies one captured
 * utterance to an OpenAI-compatible transcription endpoint. Restored
 * 2026-07-09: the original route rode the cloud provider eradicated on
 * 2026-06-29, which left web voice input dead (the mic captured fine, then
 * every utterance posted into a 404). Defaults to OpenAI's transcription API
 * (gpt-4o-mini-transcribe — fast + cheap, verified live against the account);
 * point JARVIS_STT_URL at any OpenAI-compatible server (e.g. a self-hosted
 * faster-whisper) to swap providers without a code change.
 *
 * Why server-side STT at all: the browser Web Speech API depends on Google's
 * speech servers that aren't bundled in Chromium builds, so it fails with
 * "not-allowed" there. Voice mode instead captures mic audio itself
 * (getUserMedia + MediaRecorder) and posts each utterance here, which works on
 * every Chromium/Firefox/Safari with no model assets to load.
 *
 * Returns { text }. 503 when no key is configured (the client toasts once).
 * Same-origin from the logged-in page (proxy.ts gates it).
 */
export const dynamic = 'force-dynamic'
export const runtime = 'nodejs'

export async function POST(req: Request): Promise<Response> {
  // Read env per-request (not module scope) so a key added at runtime — or a
  // test's stubbed env — takes effect without a rebuild.
  const url =
    process.env.JARVIS_STT_URL?.trim() ||
    'https://api.openai.com/v1/audio/transcriptions'
  const model = process.env.JARVIS_STT_MODEL?.trim() || 'gpt-4o-mini-transcribe'
  const key =
    process.env.JARVIS_STT_API_KEY?.trim() ||
    process.env.OPENAI_API_KEY?.trim() ||
    ''
  if (!key) return new Response('stt not configured (set OPENAI_API_KEY or JARVIS_STT_API_KEY)', { status: 503 })

  // Reject oversize bodies on the declared length BEFORE req.formData() buffers
  // the whole thing into memory — the file.size check below only bounds what we
  // forward upstream, not what we accept. (Multipart framing adds a little over
  // the file size, so allow 1 MiB of headroom above the 25 MiB file cap.)
  const declared = Number(req.headers.get('content-length'))
  if (Number.isFinite(declared) && declared > 26 * 1024 * 1024) {
    return new Response('audio too large', { status: 413 })
  }

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
  out.append('model', model)
  out.append('response_format', 'json')
  out.append('temperature', '0')

  try {
    const r = await fetch(url, {
      method: 'POST',
      headers: { authorization: `Bearer ${key}` },
      body: out,
      signal: AbortSignal.timeout(30_000),
    })
    if (!r.ok) {
      // Log server-side (the client toast sends users here); never forward the
      // upstream body to the client — it can echo request details.
      const snippet = (await r.text().catch(() => '')).slice(0, 300)
      console.error(`[stt] upstream ${r.status} from ${url}: ${snippet}`)
      return new Response(`stt upstream ${r.status}`, { status: 502 })
    }
    const data = (await r.json()) as { text?: string }
    return Response.json({ text: (data?.text ?? '').trim() })
  } catch (err) {
    console.error(`[stt] request to ${url} failed:`, err instanceof Error ? `${err.name}: ${err.message}` : err)
    return new Response('stt request failed', { status: 502 })
  }
}

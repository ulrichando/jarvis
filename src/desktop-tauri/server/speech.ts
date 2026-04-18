// Jarvis Desktop Speech Sidecar
//
// Proxies STT + TTS requests from the desktop webview to Groq so the
// API key stays server-side. Runs alongside the bridge (8765) on its
// own port — no changes to the CLI bridge.
//
// Endpoints:
//   GET  /health                        → { status: 'ok' }
//   POST /stt  (multipart: audio file)  → { text: string }
//   POST /tts  ({ text, voice? })       → audio stream (wav)

const PORT      = parseInt(process.env.JARVIS_SPEECH_PORT ?? '8766')
const GROQ_KEY  = process.env.GROQ_API_KEY ?? ''
const GROQ_BASE = 'https://api.groq.com/openai/v1'

const STT_MODEL = process.env.JARVIS_STT_MODEL ?? 'whisper-large-v3-turbo'
const STT_LANG  = process.env.JARVIS_STT_LANGUAGE ?? 'en'
const TTS_MODEL = process.env.JARVIS_TTS_MODEL ?? 'canopylabs/orpheus-v1-english'
const PROXY_URL = process.env.JARVIS_PROXY_URL ?? 'http://localhost:4000'
const CHAT_MODEL = process.env.JARVIS_CHAT_MODEL ?? 'deepseek-chat'

// Set to 1 to route voice turns through the full CLI agent loop
// (tools, MCP, permissions). Defaults on — the voice assistant can
// actually do things, not just chat.
const AGENT_ENABLED = (process.env.JARVIS_VOICE_AGENT ?? '1') !== '0'
const AGENT_SCRIPT  = process.env.JARVIS_CLI_SCRIPT
  ?? `${process.env.HOME}/Documents/Projects/jarvis/src/cli/scripts/start.sh`
const AGENT_TIMEOUT_MS = parseInt(process.env.JARVIS_AGENT_TIMEOUT_MS ?? '60000')
// Which provider to pass to start.sh as the first positional arg.
// Groq is 3-5x faster than DeepSeek for short voice replies.
const AGENT_PROVIDER = process.env.JARVIS_VOICE_PROVIDER ?? 'groq'
// Valid Groq Orpheus voices: autumn, diana, hannah, austin, daniel, troy
const TTS_VOICE = process.env.JARVIS_TTS_VOICE ?? 'daniel'

if (!GROQ_KEY) {
  console.error('[speech] GROQ_API_KEY not set — /stt and /tts will fail')
}

const CORS_HEADERS = {
  'Access-Control-Allow-Origin':  '*',
  'Access-Control-Allow-Methods': 'GET, POST, OPTIONS',
  'Access-Control-Allow-Headers': 'Content-Type, Authorization',
}

async function handleSTT(req: Request): Promise<Response> {
  const form = await req.formData()
  const file = form.get('audio') as File | null
  if (!file) return Response.json({ error: 'missing audio field' }, { status: 400, headers: CORS_HEADERS })
  console.log(`[speech] STT <- ${file.name || 'audio'} ${file.size}B ${file.type}`)

  const upstream = new FormData()
  upstream.append('file', file, file.name || 'audio.webm')
  upstream.append('model', STT_MODEL)
  upstream.append('response_format', 'json')
  // Lock to English — stops Whisper's language auto-detect from labelling
  // silence / background noise as Japanese and emitting garbage transcripts.
  upstream.append('language', STT_LANG)
  // temperature=0 also reduces hallucinated output on ambiguous audio.
  upstream.append('temperature', '0')

  const resp = await fetch(`${GROQ_BASE}/audio/transcriptions`, {
    method: 'POST',
    headers: { Authorization: `Bearer ${GROQ_KEY}` },
    body: upstream,
  })

  if (!resp.ok) {
    const err = await resp.text()
    console.error(`[speech] STT upstream ${resp.status}: ${err}`)
    return Response.json({ error: err }, { status: resp.status, headers: CORS_HEADERS })
  }

  const data = await resp.json() as { text?: string }
  const raw  = (data.text ?? '').trim()

  // Defensive filter: drop transcripts with any non-Latin1 characters
  // (Japanese/Chinese/etc. hallucinations on near-silent audio) and drop
  // common Whisper silence-filler phrases.
  const WHISPER_FILLERS = [
    'thank you for watching', 'thanks for watching',
    'please subscribe', 'music',
    'you', '.',
  ]
  const isNonLatin = /[^\x00-\x7F]/.test(raw)
  const isFiller   = WHISPER_FILLERS.includes(raw.toLowerCase())
  const text = (isNonLatin || isFiller) ? '' : raw

  console.log(`[speech] STT -> "${text.slice(0, 80)}${text.length > 80 ? '…' : ''}"${text === raw ? '' : ` (filtered from "${raw.slice(0, 40)}…")`}`)
  return Response.json({ text }, { headers: CORS_HEADERS })
}

async function handleTTS(req: Request): Promise<Response> {
  // Accept both JSON and FormData bodies so the webview can post as a
  // "simple" CORS request (FormData) without triggering a preflight that
  // WebKit2GTK silently drops.
  let text = ''
  let voice: string | undefined
  const ctype = req.headers.get('content-type') ?? ''
  if (ctype.startsWith('application/json')) {
    const body = await req.json() as { text?: string; voice?: string }
    text  = (body.text  ?? '').trim()
    voice = body.voice
  } else {
    const form = await req.formData()
    text  = ((form.get('text')  ?? '').toString()).trim()
    const v = form.get('voice')
    voice = v ? v.toString() : undefined
  }
  if (!text) return Response.json({ error: 'missing text' }, { status: 400, headers: CORS_HEADERS })
  console.log(`[speech] TTS -> "${text.slice(0, 80)}${text.length > 80 ? '…' : ''}"`)

  const resp = await fetch(`${GROQ_BASE}/audio/speech`, {
    method: 'POST',
    headers: {
      Authorization:  `Bearer ${GROQ_KEY}`,
      'Content-Type': 'application/json',
    },
    body: JSON.stringify({
      model: TTS_MODEL,
      input: text,
      voice: voice ?? TTS_VOICE,
      response_format: 'wav',
    }),
  })

  if (!resp.ok || !resp.body) {
    const err = await resp.text()
    console.error(`[speech] TTS upstream ${resp.status}: ${err}`)
    return Response.json({ error: err }, { status: resp.status, headers: CORS_HEADERS })
  }

  return new Response(resp.body, {
    headers: { ...CORS_HEADERS, 'Content-Type': 'audio/wav' },
  })
}

// Strip ANSI escapes the CLI occasionally emits even in print mode.
function stripAnsi(s: string): string {
  return s.replace(/\x1b\[[0-9;]*[a-zA-Z]/g, '')
}

// Single-writer lock: only one CLI agent process may run at a time.
// If a second /turn arrives while busy, it's rejected with BUSY so the
// webview skips playback (agent will answer only the utterance it's on).
let agentBusy = false

// Last TTS reply, used to detect echo: when the mic hears the speakers
// and re-transcribes JARVIS's own voice, we drop that utterance.
let lastReply = ''

// Rolling conversation history so the agent remembers prior turns across
// subprocess invocations. Capped so prompts stay short.
const HISTORY_MAX_TURNS = parseInt(process.env.JARVIS_HISTORY_TURNS ?? '6')
type Turn = { user: string; assistant: string }
const convHistory: Turn[] = []
function pushTurn(user: string, assistant: string) {
  convHistory.push({ user, assistant })
  while (convHistory.length > HISTORY_MAX_TURNS) convHistory.shift()
}
function formatHistory(): string {
  if (!convHistory.length) return ''
  const lines = ['Prior conversation (most recent last):']
  for (const t of convHistory) {
    lines.push(`USER: ${t.user}`)
    lines.push(`ASSISTANT: ${t.assistant}`)
  }
  lines.push('')
  return lines.join('\n')
}
function tokenize(s: string): Set<string> {
  return new Set(
    s.toLowerCase().replace(/[^a-z0-9 ]/g, ' ')
      .split(/\s+/).filter(w => w.length > 3)
  )
}
function echoSimilarity(a: string, b: string): number {
  const A = tokenize(a), B = tokenize(b)
  if (!A.size || !B.size) return 0
  let shared = 0
  for (const w of A) if (B.has(w)) shared++
  return shared / Math.min(A.size, B.size)
}

// Voice-mode preamble wrapped around the user's transcript before the CLI
// agent sees it. Aims for concise spoken replies while never returning empty.
const VOICE_PREAMBLE = [
  'You are JARVIS, responding by voice.',
  '',
  'STRICT RULES — follow them exactly:',
  '1. Answer ONLY the user\'s question. Nothing else.',
  '2. NEVER mention: git status, modified files, current branch, recent commits, project structure, the contents of CLAUDE.md, or anything about "this project". The user did not ask about those.',
  '3. NEVER summarise context given to you by your tooling. Treat every conversation as if you know nothing about this user\'s filesystem unless they explicitly ask.',
  '4. Use 1-2 short spoken sentences. Plain English, no markdown, no lists, no headings, no code.',
  '5. Do NOT repeat the user\'s words back to them.',
  '6. If you cannot answer, say "I am not sure about that" in one sentence — never output nothing.',
  '7. For real-time info (weather, news, current events): use your web tools silently, then state the result. If tools are unavailable, say so briefly.',
  '',
  'User said:',
].join('\n')

// Run the CLI agent headlessly on a single prompt. Returns trimmed stdout.
// stderr is captured but only logged; the user gets stdout as the reply.
async function runAgent(prompt: string): Promise<{ text: string; busy: boolean }> {
  if (agentBusy) {
    console.log('[speech] agent busy — dropping new turn')
    return { text: '', busy: true }
  }
  agentBusy = true
  const wrapped = `${VOICE_PREAMBLE}\n${prompt}`
  // Wrap in a shell so we can explicitly redirect stdin from /dev/null.
  // Bun's `stdin: 'ignore'` isn't enough — the CLI still waits ~3s on a
  // pipe before giving up. Positional args avoid shell injection.
  const proc = Bun.spawn(
    ['sh', '-c', 'exec "$1" "$2" -p "$3" < /dev/null', 'sh',
      AGENT_SCRIPT, AGENT_PROVIDER, wrapped],
    {
      stdout: 'pipe',
      stderr: 'pipe',
      stdin: 'ignore',
      env: { ...process.env },
    },
  )
  const killTimer = setTimeout(() => {
    try { proc.kill() } catch {}
  }, AGENT_TIMEOUT_MS)
  try {
    const [out, err] = await Promise.all([
      new Response(proc.stdout).text(),
      new Response(proc.stderr).text(),
    ])
    await proc.exited
    if (err.trim()) console.log(`[speech] agent stderr: ${err.slice(-400).trim()}`)
    return { text: stripAnsi(out).trim(), busy: false }
  } finally {
    clearTimeout(killTimer)
    agentBusy = false
  }
}

async function handleTurn(req: Request): Promise<Response> {
  // Full voice turn: receives audio → Whisper → LLM → Orpheus TTS → WAV.
  // Bypasses the bridge WebSocket entirely so CORS/timing can't drop replies.
  const form = await req.formData()
  const audio = form.get('audio') as File | null
  if (!audio) return Response.json({ error: 'missing audio' }, { status: 400, headers: CORS_HEADERS })

  // 1) STT
  const sttForm = new FormData()
  sttForm.append('file', audio, audio.name || 'audio.webm')
  sttForm.append('model', STT_MODEL)
  sttForm.append('language', STT_LANG)
  sttForm.append('temperature', '0')
  sttForm.append('response_format', 'json')
  const sttResp = await fetch(`${GROQ_BASE}/audio/transcriptions`, {
    method: 'POST',
    headers: { Authorization: `Bearer ${GROQ_KEY}` },
    body: sttForm,
  })
  if (!sttResp.ok) {
    const err = await sttResp.text()
    return Response.json({ error: `stt: ${err}` }, { status: 502, headers: CORS_HEADERS })
  }
  const sttData = await sttResp.json() as { text?: string }
  const raw = (sttData.text ?? '').trim()
  // Whisper hallucinates short phrases on background noise. Filter:
  //   - non-Latin script (language leak)
  //   - known silence-fillers
  //   - anything too short / too few words to be a real query
  const FILLERS = [
    'thank you for watching','thanks for watching','please subscribe','music',
    'you','yeah','okay','ok','yes','no','mm','uh','um','hmm','bye','.',
  ]
  const letters = raw.replace(/[^a-zA-Z]/g, '')
  const wordCount = raw.split(/\s+/).filter(Boolean).length
  const tooShort = letters.length < 6 || wordCount < 3
  // Echo reject: only near-verbatim matches of the last reply are
  // treated as echo. Lower thresholds were dropping genuine follow-up
  // questions that happened to share vocabulary.
  const echoScore = lastReply ? echoSimilarity(raw, lastReply) : 0
  const isEcho = echoScore > 0.85
  const bad = /[^\x00-\x7F]/.test(raw) || FILLERS.includes(raw.toLowerCase()) || tooShort || isEcho
  const userText = bad ? '' : raw
  if (isEcho) console.log(`[speech] echo rejected (${(echoScore*100).toFixed(0)}% match)`)
  console.log(`[speech] TURN user="${userText || '(dropped: ' + raw.slice(0,40) + ')'}"`)
  if (!userText) {
    return new Response('{}', { headers: { ...CORS_HEADERS, 'Content-Type': 'application/json', 'X-Heard': '', 'X-Reply': '' } })
  }

  // 2) Reply — either via the full CLI agent loop (tools enabled) or
  //    a plain LLM call (faster, no tools).
  async function plainLLM(text: string): Promise<string> {
    const r = await fetch(`${PROXY_URL}/v1/messages`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        model: CHAT_MODEL,
        max_tokens: 150,
        stream: false,
        messages: [{ role: 'user', content: text }],
        system: 'You are JARVIS, a concise voice assistant. Reply in 1-2 short spoken sentences. Plain text, no markdown, no lists. If you cannot answer, say so briefly.',
      }),
    })
    if (!r.ok) return ''
    const d = await r.json() as any
    return (d?.content?.[0]?.text ?? '').trim()
  }

  // Prepend rolling conversation history so the agent has continuity.
  const contextual = formatHistory() + userText

  let reply = ''
  if (AGENT_ENABLED) {
    const r = await runAgent(contextual)
    if (r.busy) {
      return new Response('{}', { headers: { ...CORS_HEADERS, 'Content-Type': 'application/json', 'X-Heard': encodeURIComponent(userText), 'X-Reply': '', 'X-Busy': '1' } })
    }
    reply = r.text
    // Agent silently refused (empty stdout) — fall back to a plain LLM
    // with different guardrails so the user gets *some* answer.
    if (!reply) {
      console.log('[speech] agent empty — falling back to plain LLM')
      reply = await plainLLM(contextual)
    }
  } else {
    reply = await plainLLM(contextual)
  }
  reply = reply || 'Sorry, I do not have information on that right now.'
  console.log(`[speech] TURN reply="${reply.slice(0,120)}" (history=${convHistory.length})`)
  pushTurn(userText, reply)
  lastReply = reply

  // 3) Cache the reply text keyed by a tts-id. The webview will then
  //    GET /tts/play/:id which streams audio progressively — it starts
  //    playing as the first bytes arrive instead of waiting for the
  //    full WAV to finish.
  const ttsId = crypto.randomUUID()
  ttsCache.set(ttsId, { text: reply, at: Date.now() })
  // Keep cache bounded — drop anything older than 60s.
  for (const [k, v] of ttsCache) {
    if (Date.now() - v.at > 60_000) ttsCache.delete(k)
  }

  return Response.json({ heard: userText, reply, ttsId }, {
    headers: {
      ...CORS_HEADERS,
      'Access-Control-Expose-Headers': 'X-Heard, X-Reply',
      'X-Heard': encodeURIComponent(userText),
      'X-Reply': encodeURIComponent(reply),
    },
  })
}

// In-memory cache: ttsId → reply text. Consumed by /tts/play/:id.
const ttsCache = new Map<string, { text: string; at: number }>()

async function handleTtsPlay(id: string): Promise<Response> {
  const entry = ttsCache.get(id)
  if (!entry) return new Response('unknown tts id', { status: 404, headers: CORS_HEADERS })
  const resp = await fetch(`${GROQ_BASE}/audio/speech`, {
    method: 'POST',
    headers: { Authorization: `Bearer ${GROQ_KEY}`, 'Content-Type': 'application/json' },
    body: JSON.stringify({ model: TTS_MODEL, input: entry.text, voice: TTS_VOICE, response_format: 'wav' }),
  })
  if (!resp.ok || !resp.body) {
    const err = await resp.text()
    console.error(`[speech] TTS ${resp.status}: ${err}`)
    return new Response(err, { status: resp.status, headers: CORS_HEADERS })
  }
  return new Response(resp.body, {
    headers: { ...CORS_HEADERS, 'Content-Type': 'audio/wav', 'Cache-Control': 'no-store' },
  })
}

Bun.serve({
  port: PORT,
  async fetch(req) {
    const url = new URL(req.url)
    // Log EVERY request regardless of path to see if GETs arrive
    console.log(`[speech] REQ ${req.method} ${url.pathname}${url.search}`)

    if (req.method === 'OPTIONS') {
      return new Response(null, { headers: CORS_HEADERS })
    }

    // Streaming TTS playback — GET by id so <audio>.src can fetch it.
    if (url.pathname.startsWith('/tts/play/') && req.method === 'GET') {
      return handleTtsPlay(url.pathname.slice('/tts/play/'.length))
    }

    // Silero VAD assets — Tauri's asset protocol serves .wasm as text/html
    // which ORT refuses. We serve them here with the correct MIME so the
    // webview can load them without errors.
    if (url.pathname.startsWith('/vad/') && (req.method === 'GET' || req.method === 'HEAD')) {
      const fname = url.pathname.slice('/vad/'.length).replace(/[^A-Za-z0-9._-]/g, '')
      const vadDir = `${process.env.HOME}/Documents/Projects/jarvis/src/desktop-tauri/public`
      const file = Bun.file(`${vadDir}/${fname}`)
      if (!(await file.exists())) return new Response('nope', { status: 404, headers: CORS_HEADERS })
      const type = fname.endsWith('.wasm') ? 'application/wasm'
                 : fname.endsWith('.onnx') ? 'application/octet-stream'
                 : fname.endsWith('.mjs')  ? 'application/javascript'
                 : fname.endsWith('.js')   ? 'application/javascript'
                 : 'application/octet-stream'
      const headers = { ...CORS_HEADERS, 'Content-Type': type, 'Cache-Control': 'public, max-age=3600' }
      if (req.method === 'HEAD') return new Response(null, { headers })
      return new Response(file.stream(), { headers })
    }

    if (url.pathname === '/health') {
      return Response.json({ status: 'ok' }, { headers: CORS_HEADERS })
    }

    if (url.pathname === '/debug/level') {
      const ctype = req.headers.get('content-type') ?? ''
      let tag = ''
      if (req.method === 'POST') {
        if (ctype.startsWith('application/json')) {
          const body = await req.json() as { rms?: number; tag?: string }
          tag = `${(body.rms ?? 0).toFixed(3)}  ${body.tag ?? ''}`
        } else {
          const form = await req.formData()
          tag = (form.get('tag') ?? '').toString()
        }
      } else {
        tag = url.searchParams.get('tag') ?? ''
      }
      console.log(`[speech] DBG ${tag}`)
      return Response.json({ ok: true }, { headers: CORS_HEADERS })
    }

    try {
      if (url.pathname === '/stt'  && req.method === 'POST') return await handleSTT(req)
      if (url.pathname === '/tts'  && req.method === 'POST') return await handleTTS(req)
      if (url.pathname === '/turn' && req.method === 'POST') return await handleTurn(req)
    } catch (e: any) {
      console.error('[speech] handler error:', e)
      return Response.json({ error: e?.message ?? 'internal error' }, { status: 500, headers: CORS_HEADERS })
    }

    return new Response('Not found', { status: 404, headers: CORS_HEADERS })
  },
})

console.log(`[speech] Jarvis speech sidecar listening on http://localhost:${PORT}`)
console.log(`[speech] STT model: ${STT_MODEL}  |  TTS model: ${TTS_MODEL} (voice: ${TTS_VOICE})`)

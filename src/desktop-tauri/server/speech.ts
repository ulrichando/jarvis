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

// Shared conversation DB with the bridge — voice turns land in the same
// sessions sidebar the chat panel shows. Fresh UUID per sidecar process;
// each restart = a new voice session on the timeline.
import {
  saveTurn as saveTurnToDb,
  recallRelevant,
} from '../../cli/src/bridge/storage.ts'
import { arbitrate, buildArbiterInput } from './arbiter.ts'
import { randomUUID } from 'node:crypto'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'

// Charter — the authoritative JARVIS system prompt. Lives in prompts/
// so it can be edited without touching code. Section 11 handles
// per-channel behavior; we append a one-liner below naming the active
// channel so the model routes to the right section.
const JARVIS_CHARTER = readFileSync(
  join(import.meta.dir, 'prompts', 'jarvis_charter.md'),
  'utf8',
)

// Full Kali/XFCE/Linux operational reference. Worth every token for
// agent-routed turns: eliminates the class of errors where the model
// invents commands that don't exist on this host.
const KALI_REFERENCE = readFileSync(
  join(import.meta.dir, 'prompts', 'kali_reference.md'),
  'utf8',
)

// Big static content gets written to a file and passed via
// --append-system-prompt-file to stay under posix_spawn's argv limit
// (E2BIG on >128 KB). Per-turn bits (now block, history, user text)
// still fit comfortably in argv.
import { writeFileSync as _writeSync } from 'node:fs'
const STATIC_PROMPT_PATH = `/tmp/jarvis-voice-system-${process.pid}.md`
function persistStaticPrompt(systemBlock: string) {
  _writeSync(
    STATIC_PROMPT_PATH,
    [
      systemBlock,
      '',
      '=== KALI / XFCE / LINUX REFERENCE (authoritative for this system) ===',
      KALI_REFERENCE,
      '=== END REFERENCE ===',
      '',
      JARVIS_CHARTER,
      '',
      '---',
      '',
      '## Active channel: VOICE',
      '',
      'This turn is on the voice channel — follow §11 Voice rules strictly. Replies are spoken aloud: no markdown, no code blocks, no URLs, no file paths, no UUIDs. Sentences under 15 words where possible. Numbers spoken the way a human says them ("twenty gigabytes", not "20GB").',
      '',
      '**Never speak command names or shell syntax.** Do not say "running xfce4-popup-applicationsmenu" or "trying xdg-open" — those are unspeakable. Run the command silently via Bash, then tell Ulrich the RESULT in plain English: "menu\'s open" / "chrome\'s opening youtube" / "that failed — the display isn\'t reachable".',
      '',
      '**For real-time facts — time, weather, news, prices, scores — you MUST call a tool, not guess.**',
      '',
      '**When a step fails, try the next reasonable approach on your own.** Do not list options and ask which to try. Do not wait for permission unless the next step would be destructive (Tier 2+: rm outside scratch, force push, drop table, key rotation, prod writes, anything costing money). For a Tier 1 install that failed, you are authorized to: switch package manager (snap → flatpak → apt → deb download), retry with different mirrors, clean up partial state and re-run. Report each failure + what you tried next, compactly. Ulrich said "go ahead" once; that stands for the whole install thread. Stop asking "would you like me to try X?" and just try X. Only surface a question when a real irreversible fork appears.',
      '',
      '**Check reality before acting.** If Ulrich says "open Spotify", first run `command -v spotify || snap list spotify || flatpak list | grep -i spotify` — don\'t claim it\'s uninstalled based on guessing. Verify before you speak.',
      '',
      '**Power operations on THIS workstation are Tier 1, not Tier 3.** A reboot, shutdown, suspend, hibernate, or logout is fully reversible — the machine comes back. Do NOT demand "confirm irreversible" for these. Ulrich said "restart my computer" → you run `systemctl reboot` (or `shutdown -r now` / `systemctl poweroff` / `systemctl suspend` / `loginctl terminate-user $USER` as appropriate), acknowledge once in voice ("Rebooting now."), and do it. The "confirm irreversible" pattern is strictly for Tier 3: rm -rf against anything real, dd to a disk, dropping prod databases, revoking production keys.',
    ].join('\n'),
  )
}

// Voice Arbiter gate — filters echo/quotes/third-party refs/etc. before
// an utterance reaches the CLI agent. Default OFF: the 8B arbiter model
// over-silences by misapplying content-based heuristics instead of
// trusting upstream signals (labels complete transcripts "partial",
// flags Whisper mishearings of "Jarvis" as vocatives to a person).
// Re-enable with JARVIS_ARBITER_ENABLED=1 once we have better signals:
// neural speaker embedding (e.g. ECAPA-TDNN), media-loopback classifier,
// and streaming-STT partial detection.
const ARBITER_ENABLED = (process.env.JARVIS_ARBITER_ENABLED ?? '0') !== '0'

// When JARVIS last finished speaking — used by arbiter to compute
// convo.active / seconds_since_jarvis_spoke.
let lastTtsAt = 0
let lastUserIntent: string | null = null
// Remember whether the last turn was agent-routed. Follow-ups inside a
// tool-using thread ("youtube.com", "go ahead", "it didn't work") keep
// routing to the agent even when they lack explicit trigger words.
let lastRouteWasAgent = false

// Voice-commanded "soft mute" — mic stays live (so JARVIS can hear the
// unmute phrase), but all turns are dropped until he hears it. Separate
// from the tray "hard mute" (App.jsx / bridge), which actually stops
// the VAD. Triggered only by the user speaking a mute-intent phrase.
let voiceMuted = false
// Muting requires intent directed at JARVIS — otherwise ambient media
// can flip the flag. Either a long unambiguous command, or a wake-word
// + basic keyword combo. "the driver is mute" on a podcast: nope.
// "jarvis mute" or "mute yourself": yes.
// Widened to catch common Whisper mishearings: javis (r dropped),
// jorvis, jervis, jarvice, garvis (g swap), harvis (h swap), jervice.
const WAKE_WORD_FUZZY = /\b[jgh][aeo]?r?v(is|ish|es|us|ice|ius)\b/i
const MUTE_EXPLICIT   = /\b(mute yourself|stop listening|go to sleep|sleep mode|go quiet|go silent)\b/i
const MUTE_SHORT      = /\b(mute|quiet|silent|shush|shut up|be quiet|sleep)\b/i
const UNMUTE_EXPLICIT = /\b(unmute|wake up|start listening|are you (there|awake|back)|come back|resume listening)\b/i
const UNMUTE_SHORT    = /\b(wake|resume|listen|back)\b/i
function isMuteIntent(text: string): boolean {
  return MUTE_EXPLICIT.test(text) || (WAKE_WORD_FUZZY.test(text) && MUTE_SHORT.test(text))
}
function isUnmuteIntent(text: string): boolean {
  return UNMUTE_EXPLICIT.test(text) || (WAKE_WORD_FUZZY.test(text) && UNMUTE_SHORT.test(text))
}

// Short-circuit reply used by mute/unmute acks and anywhere else we need
// a synthesized response without hitting the LLM. Re-uses the shared
// ttsCache so the webview picks it up via the normal /tts/play/:id path.
function quickTtsReply(heard: string, replyText: string): Response {
  const ttsId = crypto.randomUUID()
  ttsCache.set(ttsId, { text: replyText, at: Date.now() })
  return Response.json({ heard, reply: replyText, ttsId }, {
    headers: {
      ...CORS_HEADERS,
      'Access-Control-Expose-Headers': 'X-Heard, X-Reply',
      'X-Heard': encodeURIComponent(heard),
      'X-Reply': encodeURIComponent(replyText),
    },
  })
}
const VOICE_SESSION_ID = randomUUID()

const PORT      = parseInt(process.env.JARVIS_SPEECH_PORT ?? '8766')
const GROQ_KEY  = process.env.GROQ_API_KEY ?? ''
const GROQ_BASE = 'https://api.groq.com/openai/v1'

const STT_MODEL = process.env.JARVIS_STT_MODEL ?? 'whisper-large-v3-turbo'
const STT_LANG  = process.env.JARVIS_STT_LANGUAGE ?? 'en'
const TTS_MODEL = process.env.JARVIS_TTS_MODEL ?? 'canopylabs/orpheus-v1-english'
const PROXY_URL = process.env.JARVIS_PROXY_URL ?? 'http://localhost:4000'
// Voice defaults to Groq Llama — ~3× faster first-token than DeepSeek.
// Override with JARVIS_CHAT_MODEL if you want DeepSeek for voice.
const CHAT_MODEL = process.env.JARVIS_CHAT_MODEL ?? 'llama-3.3-70b-versatile'

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
// Rolling memory window. Enough to feel continuous — if Ulrich circles
// back to "what I said earlier", he's there. Semantic recall (below) pulls
// anything older that's actually relevant, so we don't need infinite
// context.
const HISTORY_MAX_TURNS = parseInt(process.env.JARVIS_HISTORY_TURNS ?? '12')
type Turn = { user: string; assistant: string }
const convHistory: Turn[] = []
function pushTurn(user: string, assistant: string) {
  convHistory.push({ user, assistant })
  while (convHistory.length > HISTORY_MAX_TURNS) convHistory.shift()
  try {
    saveTurnToDb(VOICE_SESSION_ID, 'user',      user)
    saveTurnToDb(VOICE_SESSION_ID, 'assistant', assistant)
  } catch (e) {
    console.error('[speech] saveTurn to DB failed:', e)
  }
}
function formatHistory(): string {
  if (!convHistory.length) return ''
  const lines = [
    '=== PRIOR TURNS (context only — NOT the current question) ===',
  ]
  for (const t of convHistory) {
    lines.push(`[past] USER: ${t.user}`)
    lines.push(`[past] ASSISTANT: ${t.assistant}`)
  }
  lines.push('=== END PRIOR TURNS ===')
  lines.push('')
  return lines.join('\n')
}

// Does this utterance need the full CLI agent (bash/web/files)?
// Deterministic keyword check: low-false-negative. Anything asking for
// real-time data (time, weather, news), tool use (explicit mention of
// "tool"/"internet"/"web"), or system actions (open/run/check/etc.)
// needs the agent. Casual chat / opinions / training-data recall goes
// to plainLLM.
const AGENT_TRIGGERS = /\b(open|launch|start|run|execute|check|find|search|look ?up|show|list|read|write|edit|create|make|delete|remove|kill|install|update|upgrade|uninstall|fetch|download|browse|go ?to|navigate|type|click|copy|move|rename|build|deploy|restart|reboot|scan|ping|curl|post|git|npm|bun|cargo|tool|tools|internet|web|access|weather|news|tim(e|ing|es)|date|today|now|current(ly)?|latest|price|score|stock|stocks|play|pause|stop|skip|next|previous|volume|mute|brightness|screenshot|spotify|youtube|chrome|firefox|terminal|thunar|file ?manager|music|song|playlist)\b/i
function needsAgent(text: string): boolean {
  return AGENT_TRIGGERS.test(text)
}

// Strip code fragments before the reply goes to TTS. Voice output can't
// speak backticks or shell syntax sanely — saying "trying backtick
// xfce4-popup-applicationsmenu backtick" is exactly what users hate.
// After this pass, if the reply has gone empty, fall back to a neutral
// acknowledgement so there's at least SOMETHING to speak.
function sanitiseForTTS(text: string): string {
  let out = text
    .replace(/```[\s\S]*?```/g, '')                 // fenced code blocks
    .replace(/`[^`\n]*`/g, '')                      // inline backtick code
    .replace(/^\s*[\$#>]\s+.*$/gm, '')              // shell-prompt lines
    .replace(/\b\/[\w./-]{4,}\b/g, '')              // long absolute paths
    .replace(/https?:\/\/\S+/g, '')                 // bare URLs
    .replace(/\s{2,}/g, ' ')
    .replace(/\n{2,}/g, '. ')
    .replace(/\n/g, '. ')
    .replace(/\.\s*\./g, '.')
    .trim()
  if (out.length < 4) out = 'Done.'
  return out
}

// Grounded "now" block — injected above the preamble so the model has
// authoritative current-time facts and can't answer time/date questions
// from training data. For any *other* timezone the model should still
// run `TZ=<zone> date` via Bash, but at least local-now is anchored.
function buildNowBlock(): string {
  const now = new Date()
  const iso = now.toISOString()
  const local = now.toString()
  const tz = Intl.DateTimeFormat().resolvedOptions().timeZone
  return [
    '=== CURRENT TIME (authoritative — use instead of guessing) ===',
    `UTC:         ${iso}`,
    `Local:       ${local}`,
    `Local zone:  ${tz}`,
    '=== END CURRENT TIME ===',
    '',
  ].join('\n')
}

// System-awareness block — probed once at sidecar boot so the model knows
// exactly what OS / DE / shell it's running on. Without this, small
// models invent generic Linux commands (`menu-app`, `open-menu`) that
// don't exist on Kali+XFCE. With it, they know to reach for things like
// `xfce4-popup-applicationsmenu`, `xdg-open`, `google-chrome-stable`.
let SYSTEM_BLOCK: string = (() => {
  try {
    const { execSync } = require('node:child_process')
    const safe = (cmd: string) => {
      try {
        return execSync(cmd, { encoding: 'utf8', timeout: 1000 }).trim()
      } catch { return '' }
    }
    const os = safe("grep PRETTY_NAME /etc/os-release | cut -d= -f2 | tr -d '\"'") || 'Linux'
    const kernel = safe('uname -r')
    const de = process.env.XDG_CURRENT_DESKTOP ?? process.env.DESKTOP_SESSION ?? 'unknown'
    const wm = process.env.XDG_SESSION_TYPE ?? 'unknown'
    const shell = process.env.SHELL ?? '/bin/sh'
    const home = process.env.HOME ?? ''
    const user = process.env.USER ?? 'ulrich'
    const browser = safe('command -v google-chrome-stable || command -v google-chrome || command -v chromium || command -v firefox') || 'none'
    const arch = safe('uname -m')
    // Probe which menu plugin is bound so we don't advertise the wrong
    // one. On this Kali+XFCE host Whisker replaces the classic menu;
    // advertising `xfce4-popup-applicationsmenu` sends the model to a
    // no-op. Prefer whichever command exists AND is wired.
    const whisker = safe('command -v xfce4-popup-whiskermenu')
    const appsmenu = safe('command -v xfce4-popup-applicationsmenu')
    const menuCmd = whisker ? 'xfce4-popup-whiskermenu' : (appsmenu ? 'xfce4-popup-applicationsmenu' : 'xdg-open menu://')
    return [
      '=== SYSTEM (authoritative — this is what you control) ===',
      `User:        ${user}  (home: ${home})`,
      `OS:          ${os}  (kernel ${kernel}, ${arch})`,
      `Desktop:     ${de}   (session ${wm})`,
      `Shell:       ${shell}`,
      `Browser:     ${browser}`,
      `Menu cmd:    ${menuCmd}`,
      '',
      'Concrete commands for this desktop:',
      `- Open applications menu: \`${menuCmd}\``,
      '- Open a URL in default browser: `xdg-open <url>` (or use the browser path above)',
      '- Launch an app by .desktop name: `gtk-launch <name>` or `dex /usr/share/applications/<name>.desktop`',
      '- List running windows: `wmctrl -l`',
      '- Activate/focus a window: `wmctrl -a "<title>"` or `xdotool search --name "<title>" windowactivate`',
      '- Screenshot: `xfce4-screenshooter -f -s /tmp/shot.png`',
      '- System notifications: `notify-send "<title>" "<body>"`',
      '',
      'GUI rules: DISPLAY and DBUS are in your env. Run GUI commands via Bash — do not claim to "try" them, just run and report the actual exit status / visible effect.',
      '=== END SYSTEM ===',
      '',
    ].join('\n')
  } catch {
    return ''
  }
})()

// Write the big static system prompt (system block + KB + charter +
// channel note) to a tmp file once — it gets fed to the CLI via
// --append-system-prompt-file so we don't blow the argv limit.
persistStaticPrompt(SYSTEM_BLOCK)
console.log(`[speech] static system prompt written to ${STATIC_PROMPT_PATH}`)

// Keyword-based recall of older turns from prior sessions. Cheap and
// doesn't need embeddings — good enough to surface "we talked about X
// before" moments. Returns an empty string if nothing relevant found.
function formatRecall(userText: string): string {
  try {
    const hits = recallRelevant(userText, VOICE_SESSION_ID, 3)
    if (!hits.length) return ''
    const lines = [
      '=== POSSIBLY RELEVANT FROM PRIOR CONVERSATIONS (older context) ===',
    ]
    for (const h of hits) {
      const when = new Date(h.ts * 1000).toISOString().slice(0, 10)
      const tag = h.role === 'user' ? 'USER' : 'ASSISTANT'
      lines.push(`[${when}] ${tag}: ${h.text.slice(0, 240)}`)
    }
    lines.push('=== END PRIOR CONVERSATIONS ===')
    lines.push('')
    return lines.join('\n')
  } catch (e) {
    console.error('[speech] recall failed:', e)
    return ''
  }
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

// Voice preamble = full JARVIS charter + active-channel marker + one
// concrete tool-use reminder (charter covers it in prose; voice needs
// the command spelled out because small agent models miss it otherwise).
// Prior turns and semantic recall are injected ABOVE this block; "User
// said:" is the last marker before the new utterance.
const VOICE_PREAMBLE = [
  JARVIS_CHARTER,
  "",
  "---",
  "",
  "## Active channel: VOICE",
  "",
  "This turn is on the voice channel — follow §11 Voice rules strictly. Replies are spoken aloud: no markdown, no code blocks, no URLs, no file paths, no UUIDs. Sentences under 15 words where possible. Numbers spoken the way a human says them (\"twenty gigabytes\", not \"20GB\").",
  "",
  "**Never speak command names or shell syntax.** Do not say \"running xfce4-popup-applicationsmenu\" or \"trying xdg-open\" — those are unspeakable. Run the command silently via Bash, then tell Ulrich the RESULT in plain English: \"menu's open\" / \"chrome's opening youtube\" / \"that failed — the display isn't reachable\". Commands go through tools; speech gets outcomes.",
  "",
  "**For real-time facts — time, weather, news, prices, scores — you MUST call a tool, not guess.** The current time in any timezone is `TZ='<zone>' date` via Bash (e.g. `TZ='Africa/Douala' date` for Cameroon). Weather, news, stock prices: WebFetch a real source. If the source is unreachable, say so — don't fabricate.",
  "",
  "Zzz.",  // ignored — placeholder so the next splice is the last line
  "User said:",
].filter(l => l !== 'Zzz.').join('\n')


// Run the CLI agent headlessly on a single prompt. Returns trimmed stdout.
// stderr is captured but only logged; the user gets stdout as the reply.
async function runAgent(prompt: string): Promise<{ text: string; busy: boolean }> {
  if (agentBusy) {
    console.log('[speech] agent busy — dropping new turn')
    return { text: '', busy: true }
  }
  agentBusy = true
  // Caller assembles the full prompt (history + preamble + current turn) —
  // we used to blindly prepend VOICE_PREAMBLE here, but that put the
  // preamble ABOVE the history and made the model treat prior turns as the
  // question. Now runAgent is agnostic to structure.
  const wrapped = prompt
  // Wrap in a shell so we can explicitly redirect stdin from /dev/null.
  // Bun's `stdin: 'ignore'` isn't enough — the CLI still waits ~3s on a
  // pipe before giving up. Positional args avoid shell injection.
  // --bare strips CLAUDE.md, auto-memory, hooks, background prefetches, etc.
  // Without it the CLI injects project context into every voice turn, which
  // leaks as the model pulling unrelated questions back to "your project".
  // `--` terminates option parsing — without it, any line in the prompt
  // that starts with `--` (recall/history delimiters, markdown rules)
  // gets interpreted as an unknown CLI flag and the CLI exits with code 1.
  // The large static context (charter + Kali ref + system block) is fed
  // via --append-system-prompt-file to keep this argv small — passing it
  // inline blew past posix_spawn's E2BIG limit.
  // Strip every CLAUDE_CODE_*, CLAUDECODE, and CLAUDE_DESKTOP_* env var
  // that might make the nested Claude Code CLI bypass our proxy or
  // enable features we don't want (analytics, SDK checkpointing,
  // nested-session detection). When the sidecar is launched from a
  // Claude-Code-integrated terminal, these leak in; without this filter
  // the spawned CLI hits api.anthropic.com directly instead of our
  // localhost:4000 proxy → 9-minute zombie turns.
  const cleanEnv: Record<string, string> = {}
  for (const [k, v] of Object.entries(process.env)) {
    if (v === undefined) continue
    if (k.startsWith('CLAUDE_CODE_')) continue
    if (k.startsWith('CLAUDE_DESKTOP_')) continue
    if (k === 'CLAUDECODE') continue
    cleanEnv[k] = v
  }
  // Explicitly re-assert the proxy URL so the CLI can't fall through to
  // Anthropic direct even if some other env logic tries.
  cleanEnv.ANTHROPIC_BASE_URL = cleanEnv.ANTHROPIC_BASE_URL ?? 'http://localhost:4000'
  cleanEnv.ANTHROPIC_API_KEY  = cleanEnv.ANTHROPIC_API_KEY  ?? 'jarvis-proxy'
  // Force bash for the CLI's Bash tool — zsh's NOMATCH glob turns
  // unquoted URLs with "?" into "no matches found" errors (observed
  // when JARVIS tried xdg-open on google search URLs).
  cleanEnv.SHELL = '/bin/bash'
  // Silence non-essential Anthropic telemetry / Statsig / update /
  // Sentry / cost-warning traffic. Main LLM still goes through the
  // proxy; these are out-of-band channels we don't want leaking.
  cleanEnv.DISABLE_TELEMETRY                = '1'
  cleanEnv.DISABLE_ERROR_REPORTING          = '1'
  cleanEnv.DISABLE_BUG_COMMAND              = '1'
  cleanEnv.DISABLE_NON_ESSENTIAL_MODEL_CALLS = '1'
  cleanEnv.DISABLE_AUTOUPDATER              = '1'
  cleanEnv.DISABLE_COST_WARNINGS            = '1'
  const proc = Bun.spawn(
    ['sh', '-c',
      'exec "$1" "$2" -p --bare --append-system-prompt-file "$3" -- "$4" < /dev/null',
      'sh', AGENT_SCRIPT, AGENT_PROVIDER, STATIC_PROMPT_PATH, wrapped],
    {
      stdout: 'pipe',
      stderr: 'pipe',
      stdin: 'ignore',
      // Run the agent from /tmp so casual questions don't trigger
      // project-aware tool use (git status, reading CLAUDE.md, etc).
      cwd: '/tmp',
      env: cleanEnv,
    },
  )
  // Two-stage kill: SIGTERM first to let the CLI flush stdout / DB
  // writes, then SIGKILL 2 s later if it hasn't died. Claude Code traps
  // SIGTERM and sometimes hangs in its shutdown path, which pinned
  // agentBusy=true forever on previous runs.
  const killTimer = setTimeout(() => {
    try { proc.kill('SIGTERM') } catch {}
    setTimeout(() => {
      try { if (proc.exitCode === null) proc.kill('SIGKILL') } catch {}
    }, 2_000)
  }, AGENT_TIMEOUT_MS)
  try {
    const [out, err] = await Promise.all([
      new Response(proc.stdout).text(),
      new Response(proc.stderr).text(),
    ])
    await proc.exited
    console.log(`[speech] agent exit=${proc.exitCode} promptLen=${prompt.length} outLen=${out.length} errLen=${err.length}`)
    if (err.trim()) {
      console.log(`[speech] agent stderr HEAD: ${err.slice(0, 1500).trim()}`)
      console.log(`[speech] agent stderr TAIL: ${err.slice(-500).trim()}`)
    }
    if (out.trim()) console.log(`[speech] agent stdout HEAD: ${out.slice(0, 500).trim()}`)
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
  // Client-side speaker fingerprint confidence (useSpeakerId). null
  // during enrollment or if the hook errored.
  const speakerConfidenceStr = form.get('speaker_confidence') as string | null
  const speakerConfidence = speakerConfidenceStr != null ? parseFloat(speakerConfidenceStr) : null

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
  // Don't drop short follow-ups — common when JARVIS asks "which one?"
  // or "shall I?" and user replies with a domain ("youtube.com"),
  // number ("eighty four"), or a short affirmative ("yes please").
  const looksLikeAnswer =
    /\b[a-z0-9-]+\.(com|co|ai|dev|io|org|net|gov|edu|app|xyz|me|tv|fr|uk|us|ca)\b/i.test(raw) ||
    /^https?:\/\//i.test(raw) ||
    /\d/.test(raw) ||
    /^\s*(yes|yeah|yep|sure|ok|okay|please|go ?ahead|do it|fire|launch it|go for it|confirm|confirmed|affirmative|no|nope|cancel|abort|stop|nevermind)\b/i.test(raw)
  const tooShort = !looksLikeAnswer && (letters.length < 6 || wordCount < 3)
  // Echo reject: only near-verbatim matches of the last reply are
  // treated as echo. Lower thresholds were dropping genuine follow-up
  // questions that happened to share vocabulary.
  const echoScore = lastReply ? echoSimilarity(raw, lastReply) : 0
  const isEcho = echoScore > 0.85
  const bad = /[^\x00-\x7F]/.test(raw) || FILLERS.includes(raw.toLowerCase()) || tooShort || isEcho
  const userText = bad ? '' : raw
  if (isEcho) console.log(`[speech] echo rejected (${(echoScore*100).toFixed(0)}% match)`)
  console.log(`[speech] TURN user="${userText || '(dropped: ' + raw.slice(0,40) + ')'}"`)
  // 1.25) Voice-commanded mute / unmute — checked against RAW transcript
  // BEFORE filler-drop so short phrases ("unmute", "wake up") aren't
  // filtered as too-short. While voiceMuted is true, only the unmute
  // phrase wakes him; everything else is silently dropped.
  //
  // Gate: only Ulrich's own voice can mute/unmute. If the speaker
  // fingerprint score is low (background podcast, TV, someone else
  // talking), do NOT toggle. Client-side fingerprint comes via the
  // `speaker_confidence` form field; null = enrollment phase (trust).
  const speakerTrusted = speakerConfidence == null || speakerConfidence >= 0.70
  if (voiceMuted) {
    if (isUnmuteIntent(raw) && speakerTrusted) {
      voiceMuted = false
      console.log('[speech] voice UNMUTE')
      return quickTtsReply(raw, "I'm back.")
    }
    if (isUnmuteIntent(raw) && !speakerTrusted) {
      console.log(`[speech] unmute-intent IGNORED (speaker confidence ${speakerConfidence?.toFixed(2)})`)
    }
    console.log(`[speech] muted — dropped "${raw.slice(0,60)}"`)
    return new Response('{}', { headers: { ...CORS_HEADERS, 'Content-Type': 'application/json', 'X-Heard': encodeURIComponent(raw), 'X-Reply': '' } })
  }
  if (isMuteIntent(raw)) {
    if (!speakerTrusted) {
      console.log(`[speech] mute-intent IGNORED (speaker confidence ${speakerConfidence?.toFixed(2)})`)
    } else {
      voiceMuted = true
      console.log('[speech] voice MUTE')
      return quickTtsReply(raw, 'Going quiet.')
    }
  }

  if (!userText) {
    return new Response('{}', { headers: { ...CORS_HEADERS, 'Content-Type': 'application/json', 'X-Heard': '', 'X-Reply': '' } })
  }

  // 1.27) Status query during a long agent run — answer directly so the
  // user isn't left wondering. Agent can't be interrupted cleanly, so we
  // just acknowledge rather than try to queue another tool call.
  if (agentBusy) {
    if (/\b(what are you doing|what.s going on|still there|are you there|still working|finished|done yet|are you done|status|progress|hang on|hurry up)\b/i.test(userText)) {
      return quickTtsReply(userText, 'Still working on the last task.')
    }
  }

  // 1.5) Voice Arbiter — gates echo, quotes, third-party refs, non-user
  // speech, and ambiguous utterances before they reach the brain. Safe
  // default is stay_silent, so infra failures suppress rather than leak.
  let arbitratedText = userText
  let briefSpeak: string | null = null
  if (ARBITER_ENABLED) {
    const t0 = Date.now()
    const decision = await arbitrate(buildArbiterInput({
      transcript:  userText,
      ttsPlaying:  Date.now() - lastTtsAt < 30_000,  // rough: within 30s of last reply
      ttsRemainingText: lastReply || null,
      secondsSinceJarvisSpoke: lastTtsAt ? Math.floor((Date.now() - lastTtsAt) / 1000) : 999,
      history: convHistory.flatMap(t => [
        { role: 'user'   as const, text: t.user },
        { role: 'jarvis' as const, text: t.assistant },
      ]),
      lastUserIntent,
      speakerConfidence,
    }))
    console.log(`[arbiter] action=${decision.action} reason=${decision.reason} conf=${decision.confidence} latency=${Date.now() - t0}ms`)
    if (decision.action === 'stay_silent' || decision.action === 'defer') {
      return new Response('{}', { headers: { ...CORS_HEADERS, 'Content-Type': 'application/json', 'X-Heard': encodeURIComponent(userText), 'X-Reply': '' } })
    }
    if (decision.forwarded_text) arbitratedText = decision.forwarded_text
    if (decision.action === 'stop_and_forward') {
      briefSpeak = decision.brief_speak
      // Client-side: useSpeech.sendUtterance pauses any previous audio
      // element when a new TTS arrives, so the "stop" half is automatic.
    }
  }
  // Keep the rest of the pipeline reading `userText` so the downstream
  // prompt/history still reflects what the user actually said.
  const forwardUserText = arbitratedText

  // 2) Reply — plainLLM uses proper chat structure: VOICE_PREAMBLE as
  //    system prompt, prior turns as role/content pairs, current user
  //    text alone. Dramatically faster than stuffing everything into a
  //    single user message (enables caching; model isn't reparsing the
  //    whole persona on every call).
  async function plainLLM(userTurn: string): Promise<string> {
    const messages: Array<{ role: 'user' | 'assistant'; content: string }> = []
    for (const t of convHistory) {
      messages.push({ role: 'user',      content: t.user })
      messages.push({ role: 'assistant', content: t.assistant })
    }
    messages.push({ role: 'user', content: userTurn })
    // On the plain-LLM path there are NO tools — no Bash, no WebFetch,
    // no filesystem. If the model claims "opening X" or "running Y" it
    // is lying. Append a hard constraint so it refuses gracefully and
    // the caller can decide whether to retry on the agent path.
    const system = VOICE_PREAMBLE
      .replace(/\nUser said:\s*$/, '')
      .trim()
      + '\n\n## THIS TURN: no tools available\n'
      + 'You are being called without Bash, WebFetch, or file tools this turn. '
      + 'DO NOT claim to open, launch, run, browse, fetch, or execute anything. '
      + "If Ulrich asked for an action you can't perform without tools, say exactly: "
      + '"I need tool access for that, let me retry." Never fabricate a result.'
    const r = await fetch(`${PROXY_URL}/v1/messages`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        model: CHAT_MODEL,
        max_tokens: 200,
        stream: false,
        messages,
        system,
      }),
    })
    if (!r.ok) return ''
    const d = await r.json() as any
    return (d?.content?.[0]?.text ?? '').trim()
  }

  // Layout (top → bottom):
  //   1. Semantic recall — snippets from older sessions that share keywords
  //      with the current question. Lets JARVIS respond to "like I was
  //      saying last week" without needing a giant context window.
  //   2. Prior turns, clearly labelled as short-term context.
  //   3. VOICE_PREAMBLE (character + examples + "User said:" marker).
  //   4. The raw current utterance.
  const recallBlock = formatRecall(forwardUserText)
  const historyBlock = formatHistory()
  // The agent path now gets the large static context (charter + Kali
  // reference + system block + channel note) via a file — see
  // persistStaticPrompt. What we pass via argv is ONLY the per-turn
  // bits: current time, short recall, recent history, current utterance.
  // Keeps argv tiny so posix_spawn doesn't E2BIG.
  const nowBlock = buildNowBlock()
  const contextual = `${nowBlock}${recallBlock}${historyBlock}User said:\n${forwardUserText}`

  // Intent routing: casual chat → plainLLM (~600 ms). Tool-requiring
  // requests → CLI agent (~2-3 s but can execute bash/web/files). Cheap
  // keyword check keeps latency low for most voice turns. If the prior
  // turn went to the agent and this turn happens within 30 s, treat it
  // as a continuation of the same tool thread — catches "youtube.com"
  // (URL follow-up), "go ahead", "it didn't work", "try again".
  const withinThread = (Date.now() - lastTtsAt) < 30_000
  const wantsAgent = AGENT_ENABLED && (needsAgent(forwardUserText) || (lastRouteWasAgent && withinThread))
  const t0 = Date.now()
  let reply = ''
  if (wantsAgent) {
    const r = await runAgent(contextual)
    if (r.busy) {
      // Agent is mid-task — don't silently drop, tell Ulrich so he
      // knows JARVIS heard him. Multiple-of-these in a row will vary
      // slightly so it doesn't sound like a broken record.
      const fillers = [
        'Still on the last one, one second.',
        'Almost there, hang tight.',
        "Got it, I'll handle that after this one.",
        "Heard you, finishing the last task first.",
      ]
      const msg = fillers[Math.floor(Math.random() * fillers.length)]
      return quickTtsReply(userText, msg)
    }
    reply = r.text
    if (!reply) {
      console.log('[speech] agent empty — falling back to plain LLM')
      reply = await plainLLM(forwardUserText)
    }
  } else {
    reply = await plainLLM(forwardUserText)
    // Plain LLM asked to do something it can't — auto-retry on agent.
    // If the retry can't run (agent busy) or produces nothing useful,
    // replace the sentinel string — it must never reach the TTS mouth.
    if (/I need tool access for that/i.test(reply)) {
      console.log('[speech] plain asked for tools — retrying on agent')
      const r = await runAgent(contextual)
      if (r.busy) {
        reply = "Still on the last task. One second."
      } else if (r.text && !/I need tool access for that/i.test(r.text)) {
        reply = r.text
      } else {
        reply = "Let me try that a different way."
      }
    }
  }
  console.log(`[speech] route=${wantsAgent ? 'agent' : 'plain'} latency=${Date.now() - t0}ms`)
  lastRouteWasAgent = wantsAgent
  // Optional immediate filler from the arbiter (only on stop_and_forward).
  if (briefSpeak) reply = `${briefSpeak}. ${reply}`
  reply = reply || 'Sorry, I do not have information on that right now.'
  // Sanitise for TTS — voice must not speak command names, backtick
  // fragments, fenced code blocks, shell prompts, or long paths. Strip
  // all of that and collapse whitespace before anything else sees it.
  reply = sanitiseForTTS(reply)
  console.log(`[speech] TURN reply="${reply.slice(0,120)}" (history=${convHistory.length})`)
  pushTurn(forwardUserText, reply)
  lastReply = reply
  lastTtsAt = Date.now()

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

// Voice Arbiter — gates every transcribed utterance before it hits the
// brain. System prompt lives in prompts/voice_arbiter.md; the model is
// called via the existing Jarvis proxy (Anthropic-shape → Groq/etc.) so
// we don't need the Anthropic SDK or a second auth path.

import { readFileSync } from 'node:fs'
import { join } from 'node:path'

const PROXY_URL   = process.env.JARVIS_PROXY_URL   ?? 'http://localhost:4000'
const ARBITER_MODEL = process.env.JARVIS_ARBITER_MODEL ?? 'llama-3.1-8b-instant'
const ARBITER_TIMEOUT_MS = parseInt(process.env.JARVIS_ARBITER_TIMEOUT_MS ?? '2500')

const VOICE_ARBITER_PROMPT = readFileSync(
  join(import.meta.dir, 'prompts', 'voice_arbiter.md'),
  'utf8',
)

// ── Types ────────────────────────────────────────────────────────────────

export type ArbiterInput = {
  transcript: string
  speaker: {
    id: 'ulrich' | string
    confidence: number
    is_enrolled_user: boolean
  }
  audio: {
    tts_playing: boolean
    tts_remaining_text: string | null
    source: 'microphone' | 'system_loopback' | 'mixed'
    snr_db: number
    detected_media: boolean
    is_partial: boolean
  }
  convo: {
    seconds_since_jarvis_spoke: number
    active: boolean
    wake_word_detected: boolean
    wake_word_age_s: number | null
    last_user_intent: string | null
  }
  history: Array<{ role: 'user' | 'jarvis'; text: string }>
}

export type ArbiterAction =
  | 'stay_silent'
  | 'defer'
  | 'forward'
  | 'stop_and_forward'

export type ArbiterReason =
  | 'self_echo' | 'media_audio' | 'non_user_speaker' | 'low_speaker_confidence'
  | 'addressed_to_user' | 'addressed_to_third_party' | 'quoting'
  | 'third_party_reference' | 'explicit_interrupt' | 'barge_in_new_intent'
  | 'backchannel' | 'continuation' | 'wake_word_address' | 'wake_word_followup'
  | 'direct_command' | 'no_address' | 'partial_transcript' | 'ambiguous'

export type ArbiterDecision = {
  action: ArbiterAction
  reason: ArbiterReason
  confidence: number
  forwarded_text: string | null
  brief_speak: string | null
  continue_listening: boolean
  debug?: { signals_used?: string[]; echo_prefix_stripped?: boolean }
}

const SAFE_DEFAULT: ArbiterDecision = {
  action: 'stay_silent',
  reason: 'ambiguous',
  confidence: 0,
  forwarded_text: null,
  brief_speak: null,
  continue_listening: true,
  debug: { signals_used: ['fallback_safe_default'] },
}

// ── The arbiter call ────────────────────────────────────────────────────

export async function arbitrate(input: ArbiterInput): Promise<ArbiterDecision> {
  const ctrl = new AbortController()
  const killTimer = setTimeout(() => ctrl.abort(), ARBITER_TIMEOUT_MS)
  try {
    const resp = await fetch(`${PROXY_URL}/v1/messages`, {
      method: 'POST',
      signal: ctrl.signal,
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        model: ARBITER_MODEL,
        max_tokens: 400,
        stream: false,
        temperature: 0,
        system: VOICE_ARBITER_PROMPT,
        messages: [{ role: 'user', content: JSON.stringify(input) }],
      }),
    })
    if (!resp.ok) {
      console.error(`[arbiter] upstream ${resp.status}`)
      return SAFE_DEFAULT
    }
    const data = await resp.json() as any
    const raw = (data?.content?.[0]?.text ?? '').trim()

    // Defensive parse — spec forbids prose but models sometimes add it.
    const start = raw.indexOf('{')
    const end = raw.lastIndexOf('}')
    if (start === -1 || end === -1) {
      console.warn(`[arbiter] no JSON in response: ${raw.slice(0, 200)}`)
      return SAFE_DEFAULT
    }
    const decision = JSON.parse(raw.slice(start, end + 1)) as ArbiterDecision
    if (!decision.action || !decision.reason) return SAFE_DEFAULT
    if (
      (decision.action === 'forward' || decision.action === 'stop_and_forward') &&
      !decision.forwarded_text
    ) {
      // Action requires text but model didn't provide — fail closed to silence.
      return SAFE_DEFAULT
    }
    return decision
  } catch (e: any) {
    if (e?.name === 'AbortError') {
      console.warn('[arbiter] timeout — defaulting to silence')
    } else {
      console.error('[arbiter] error:', e?.message ?? e)
    }
    return SAFE_DEFAULT
  } finally {
    clearTimeout(killTimer)
  }
}

// ── Signal builders ──────────────────────────────────────────────────────
// Single-user machine — no speaker verification yet, so trust that
// everything on this mic is Ulrich. Media detection is also off. These
// defaults can be replaced when those subsystems land.

// Wake-word regex covers JARVIS itself plus the frequent Whisper
// mishearings. Matches at the start of the utterance OR after a short
// filler ("hey/ok/yo/alright/so/okay/oi"). Including the mishearings
// (joris/jervis/jarvish/davis) is critical — Whisper often slips on the
// two-syllable consonant-vowel pattern.
const WAKE_WORD_REGEX = /^\s*(hey\s+|ok\s+|okay\s+|alright\s+|yo\s+|so\s+|oi\s+)?j(a|e|o)rv(is|ish|es|us)\b/i

// Fuzzy Levenshtein fallback — catches the mishears the regex misses
// (Jorius, Jarvish, Davis). Only applied to the first 1–2 words of the
// utterance so we don't accidentally trigger on random sentence middles.
function levenshtein(a: string, b: string): number {
  if (a === b) return 0
  if (!a.length) return b.length
  if (!b.length) return a.length
  let prev = new Array(b.length + 1).fill(0).map((_, i) => i)
  for (let i = 1; i <= a.length; i++) {
    const curr = [i]
    for (let j = 1; j <= b.length; j++) {
      curr[j] = Math.min(
        curr[j - 1] + 1,
        prev[j] + 1,
        prev[j - 1] + (a[i - 1] === b[j - 1] ? 0 : 1),
      )
    }
    prev = curr
  }
  return prev[b.length]
}

function fuzzyWakeDetected(text: string): boolean {
  const tokens = text
    .toLowerCase()
    .replace(/[^a-z\s]/g, ' ')
    .split(/\s+/)
    .filter(w => w.length >= 4 && w.length <= 8)
    .slice(0, 3)   // only consider the first 3 candidate words
  for (const t of tokens) {
    if (levenshtein(t, 'jarvis') <= 2) return true
  }
  return false
}

// Softer anchor used by the single-user permissive mode: if any of these
// signs are present, assume the user is talking TO the assistant, not
// ABOUT him. Deliberately broad — false positives cost a wasted LLM
// call, false negatives cost "he can't hear me". The PRIME DIRECTIVE
// still applies when SINGLE_USER is off.
const DIRECT_ADDRESS_REGEX = /^\s*(can you|could you|will you|would you|do you|did you|are you|were you|what('s| is| are)|who('s| is)|when|where|why|how|tell me|show me|find|search|open|play|run|check|set|remind|schedule|what time|please)/i

const SINGLE_USER = (process.env.JARVIS_ARBITER_SINGLE_USER ?? '1') !== '0'

export function buildArbiterInput(args: {
  transcript: string
  ttsPlaying: boolean
  ttsRemainingText: string | null
  secondsSinceJarvisSpoke: number
  history: Array<{ role: 'user' | 'jarvis'; text: string }>
  lastUserIntent?: string | null
  speakerConfidence?: number | null
}): ArbiterInput {
  const { transcript } = args
  // In single-user mode, treat direct-address patterns as an implicit
  // wake word. Personal machine, one speaker — the C12d "no address"
  // path was designed for public / multi-speaker environments.
  const explicitWake = WAKE_WORD_REGEX.test(transcript)
  const fuzzyWake    = fuzzyWakeDetected(transcript)
  const implicitWake = SINGLE_USER && DIRECT_ADDRESS_REGEX.test(transcript)
  const wakeDetected = explicitWake || fuzzyWake || implicitWake

  // Map the client-side fingerprint score (0–1) to speaker identity.
  // If we haven't scored the voice yet (enrollment phase) the client
  // sends null → fall back to the trust-Ulrich default.
  const clientConf = typeof args.speakerConfidence === 'number' ? args.speakerConfidence : null
  const isUlrich = clientConf == null || clientConf >= 0.70
  const speaker = {
    id:               isUlrich ? 'ulrich' : 'unknown_human_1',
    confidence:       clientConf ?? 0.95,
    is_enrolled_user: isUlrich,
  }

  return {
    transcript,
    speaker,
    audio: {
      tts_playing: args.ttsPlaying,
      tts_remaining_text: args.ttsRemainingText,
      source: 'microphone',
      snr_db: 20,
      detected_media: false,
      is_partial: false,
    },
    convo: {
      seconds_since_jarvis_spoke: args.secondsSinceJarvisSpoke,
      active: args.secondsSinceJarvisSpoke < 30,
      wake_word_detected: wakeDetected,
      wake_word_age_s: wakeDetected ? 0 : null,
      last_user_intent: args.lastUserIntent ?? null,
    },
    history: args.history.slice(-4),
  }
}

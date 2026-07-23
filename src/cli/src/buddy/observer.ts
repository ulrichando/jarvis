import { feature } from 'bun:bundle'
import { getTerminalFocusState } from '../ink/terminal-focus-state.js'
import { queryModelWithoutStreaming } from '../services/api/claude.js'
import { getEmptyToolPermissionContext } from '../Tool.js'
import type { Message } from '../types/message.js'
import { getGlobalConfig } from '../utils/config.js'
import { logForDebugging } from '../utils/debug.js'
import {
  createUserMessage,
  getAssistantMessageText,
  INTERRUPT_MESSAGE,
  INTERRUPT_MESSAGE_FOR_TOOL_USE,
} from '../utils/messages.js'
import { getSmallFastModel } from '../utils/model/model.js'
import { asSystemPrompt } from '../utils/systemPromptType.js'
import { getCompanion } from './companion.js'
import { type Companion, STAT_NAMES } from './types.js'

// ─── Tunables — all taste, adjust freely ────────────────────────────────────
// Chance the companion speaks on an eligible ambient (un-addressed) turn.
const AMBIENT_QUIP_CHANCE = 0.18
// Minimum completed turns between ambient quips.
const COOLDOWN_TURNS = 3
// Minimum wall-clock between ambient quips.
const COOLDOWN_MS = 90_000
// How many trailing non-meta messages feed the digest.
const DIGEST_MESSAGE_WINDOW = 10
// Per-message char cap inside the digest (tool-heavy turns stay bounded).
const DIGEST_PER_MESSAGE_CAP = 280
// Hard cap on the whole digest.
const DIGEST_TOTAL_CAP = 2400
// Sanitized bubble cap (~10 words). Longer model output is DROPPED, not cut.
const QUIP_MAX_CHARS = 80
// "Avoid repeating" memory — last N emitted quips fed back to the model.
const RECENT_QUIPS_KEPT = 5
// Abort a hung request so inFlight can't wedge the observer forever.
const REQUEST_TIMEOUT_MS = 20_000

// ─── Module state (rate limiting) ───────────────────────────────────────────
let lastQuipAt = 0
// Start eligible: first turn can quip (still subject to the ambient dice roll).
let turnsSinceQuip = COOLDOWN_TURNS
let inFlight = false
const recentQuips: string[] = []

// `../types/message.js` is a type-only phantom module (same convention as
// awaySummary.ts / prompt.ts — erased at transpile), so runtime message shapes
// are accessed structurally through these minimal views.
type ContentBlockish = {
  type: string
  text?: string
  name?: string
  is_error?: boolean
}
type Messageish = {
  type: string
  isMeta?: true
  message?: { content?: string | ContentBlockish[] }
}

export type TurnSignals = {
  toolUses: number
  toolErrors: number
  interrupted: boolean
}

function textOf(
  content: string | ContentBlockish[] | undefined,
): string | null {
  if (typeof content === 'string') return content
  if (Array.isArray(content)) {
    const text = content
      .filter(b => b.type === 'text' && typeof b.text === 'string')
      .map(b => b.text)
      .join('\n')
      .trim()
    return text || null
  }
  return null
}

function clip(text: string): string {
  const flat = text.replace(/\s+/g, ' ').trim()
  return flat.length > DIGEST_PER_MESSAGE_CAP
    ? `${flat.slice(0, DIGEST_PER_MESSAGE_CAP)}…`
    : flat
}

/**
 * The last thing the user actually typed. Unbounded backward scan on purpose:
 * a tool-heavy turn can push the user's prompt past the digest window, and
 * addressed-by-name must still see it. An interrupt as the latest user action
 * returns null — the interrupt IS the current intent, not an older address.
 */
export function latestUserText(messages: readonly Message[]): string | null {
  for (let i = messages.length - 1; i >= 0; i--) {
    const m = messages[i] as unknown as Messageish
    if (m.type !== 'user' || m.isMeta) continue
    const content = m.message?.content
    if (Array.isArray(content) && content.some(b => b.type === 'tool_result')) {
      continue
    }
    const text = textOf(content)
    if (!text) continue
    if (text === INTERRUPT_MESSAGE || text === INTERRUPT_MESSAGE_FOR_TOOL_USE) {
      return null
    }
    return text
  }
  return null
}

/** Case-insensitive whole-word match of the companion's name in user text. */
export function addressedByName(text: string | null, name: string): boolean {
  if (!text || !name) return false
  const escaped = name.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')
  return new RegExp(
    `(?:^|[^\\p{L}\\p{N}])${escaped}(?:[^\\p{L}\\p{N}]|$)`,
    'iu',
  ).test(text)
}

/**
 * Char-capped plain-text digest of the tail of the conversation, plus turn
 * signals. Raw messages never go to the model — giant tool results are
 * collapsed to one-line ok/ERROR markers.
 */
export function buildDigest(messages: readonly Message[]): {
  digest: string
  signals: TurnSignals
} {
  const signals: TurnSignals = {
    toolUses: 0,
    toolErrors: 0,
    interrupted: false,
  }
  // Collected newest-first, reversed at the end.
  const lines: string[] = []
  let total = 0
  let kept = 0
  for (
    let i = messages.length - 1;
    i >= 0 && kept < DIGEST_MESSAGE_WINDOW && total < DIGEST_TOTAL_CAP;
    i--
  ) {
    const m = messages[i] as unknown as Messageish
    if (m.type !== 'user' && m.type !== 'assistant') continue
    const content = m.message?.content
    let line: string | null = null

    if (m.type === 'user') {
      const results = Array.isArray(content)
        ? content.filter(b => b.type === 'tool_result')
        : []
      if (results.length > 0) {
        const errors = results.filter(b => b.is_error === true).length
        signals.toolErrors += errors
        line = errors > 0 ? 'Tool result: ERROR' : 'Tool result: ok'
      } else if (!m.isMeta) {
        const text = textOf(content)
        if (
          text === INTERRUPT_MESSAGE ||
          text === INTERRUPT_MESSAGE_FOR_TOOL_USE
        ) {
          signals.interrupted = true
          line = 'User: [interrupted the assistant]'
        } else if (text) {
          line = `User: ${clip(text)}`
        }
      }
    } else {
      const blocks = Array.isArray(content) ? content : []
      const toolNames = blocks
        .filter(b => b.type === 'tool_use')
        .map(b => b.name ?? 'tool')
      signals.toolUses += toolNames.length
      const text = textOf(content)
      const parts: string[] = []
      if (text) parts.push(clip(text))
      if (toolNames.length > 0) {
        parts.push(`[ran: ${toolNames.slice(0, 4).join(', ')}]`)
      }
      if (parts.length > 0) line = `Assistant: ${parts.join(' ')}`
    }

    if (line === null) continue
    lines.push(line)
    total += line.length + 1
    kept++
  }
  lines.reverse()
  return { digest: lines.join('\n'), signals }
}

/**
 * Strip wrapping quotes/backticks, keep the first line, collapse whitespace.
 * Returns null (stay silent) for empty, PASS, or over-cap output.
 */
export function sanitizeQuip(raw: string | null): string | null {
  if (!raw) return null
  let line =
    raw
      .trim()
      .split('\n')
      .find(l => l.trim().length > 0)
      ?.trim() ?? ''
  const pairs: Record<string, string> = {
    '"': '"',
    "'": "'",
    '`': '`',
    '“': '”',
    '‘': '’',
  }
  while (line.length >= 2 && pairs[line[0]!] === line[line.length - 1]) {
    line = line.slice(1, -1).trim()
  }
  line = line.replace(/\s+/g, ' ').trim()
  if (!line) return null
  if (/^pass[.!]?$/i.test(line)) return null
  if (line.length > QUIP_MAX_CHARS) return null
  return line
}

function buildQuipPrompt(
  companion: Companion,
  digest: string,
  signals: TurnSignals,
  addressed: boolean,
  avoid: readonly string[],
): string {
  const stats = STAT_NAMES.map(s => `${s} ${companion.stats[s]}`).join(', ')
  const signalBits = [
    `${signals.toolUses} tool call(s)`,
    `${signals.toolErrors} tool error(s)`,
  ]
  if (signals.interrupted) signalBits.push('the user interrupted the assistant')
  const avoidBlock =
    avoid.length > 0
      ? `You already said these recently — do not repeat or rephrase them: ${avoid
          .map(q => `"${q}"`)
          .join(', ')}\n\n`
      : ''
  const stance = addressed
    ? "The user's latest message speaks to you by name. Answer them directly, in character."
    : 'Nothing is required of you. Only speak if something genuinely quip-worthy happened (an error, a win, an interruption, something funny). Otherwise reply exactly PASS.'
  return `You are ${companion.name}, a tiny ${companion.species} perched beside the user's input box in their coding terminal, watching the session. Personality: ${companion.personality}
Your stats (0-100): ${stats}. Let the high ones show.

Recent session activity, condensed:
${digest}

This turn: ${signalBits.join(', ')}.

${stance}

${avoidBlock}Reply with ONLY the speech-bubble text: one line, at most 10 words, in character, no quotes, no markdown, no emoji. Or reply exactly PASS to stay silent.`
}

/**
 * Fire-and-forget end-of-turn companion observer. Gates locally (cooldown +
 * low ambient probability; always speaks when the user addresses the
 * companion by name), then makes ONE small-fast-model call for a short
 * in-character quip. Never throws.
 */
export async function fireCompanionObserver(
  messages: readonly Message[],
  setReaction: (reaction: string) => void,
): Promise<void> {
  if (!feature('BUDDY')) return
  try {
    if (inFlight) return
    const companion = getCompanion()
    if (!companion || getGlobalConfig().companionMuted) return
    // Skip when blurred — nobody is watching the bubble. 'unknown' (terminal
    // without focus reporting) still quips, matching useAwaySummary where
    // only 'blurred' means away.
    if (getTerminalFocusState() === 'blurred') return
    if (messages.length === 0) return

    turnsSinceQuip++
    const addressed = addressedByName(latestUserText(messages), companion.name)
    if (!addressed) {
      if (turnsSinceQuip < COOLDOWN_TURNS) return
      if (Date.now() - lastQuipAt < COOLDOWN_MS) return
      if (Math.random() >= AMBIENT_QUIP_CHANCE) return
    }

    const { digest, signals } = buildDigest(messages)
    if (!digest) return
    const prompt = buildQuipPrompt(
      companion,
      digest,
      signals,
      addressed,
      recentQuips,
    )

    inFlight = true
    try {
      const response = await queryModelWithoutStreaming({
        messages: [createUserMessage({ content: prompt })],
        systemPrompt: asSystemPrompt([]),
        thinkingConfig: { type: 'disabled' },
        tools: [],
        signal: AbortSignal.timeout(REQUEST_TIMEOUT_MS),
        options: {
          getToolPermissionContext: async () => getEmptyToolPermissionContext(),
          model: getSmallFastModel(),
          toolChoice: undefined,
          isNonInteractiveSession: false,
          hasAppendSystemPrompt: false,
          agents: [],
          querySource: 'buddy_observer',
          mcpTools: [],
          skipCacheWrite: true,
        },
      })
      if (response.isApiErrorMessage) {
        logForDebugging(
          `[buddyObserver] API error: ${getAssistantMessageText(response)}`,
        )
        return
      }
      const quip = sanitizeQuip(getAssistantMessageText(response))
      if (!quip) return
      lastQuipAt = Date.now()
      turnsSinceQuip = 0
      recentQuips.push(quip)
      if (recentQuips.length > RECENT_QUIPS_KEPT) recentQuips.shift()
      setReaction(quip)
    } finally {
      inFlight = false
    }
  } catch (err) {
    inFlight = false
    logForDebugging(`[buddyObserver] failed: ${err}`)
  }
}

import { afterAll, beforeAll, describe, expect, test } from 'bun:test'

// Captured original env so each suite can mutate freely + restore.
const ORIG = { ...process.env }

beforeAll(() => {
  // Provide non-Gemini keys so the rest of the registry doesn't throw
  // on import. Gemini suite below mutates GEMINI/GOOGLE specifically.
  process.env.DEEPSEEK_API_KEY = 'test-deepseek'
  process.env.GROQ_API_KEY = 'test-groq'
  process.env.OPENAI_API_KEY = 'test-openai'
  process.env.KIMI_API_KEY = 'test-kimi'
  process.env.ANTHROPIC_API_KEY = 'test-anthropic'
})

afterAll(() => {
  process.env = { ...ORIG }
})

// Re-import per test where env mutation matters. Bun caches modules,
// but providers.ts reads env at call time (resolveApiKey runs inside
// the builder), so plain re-imports work — the variable just needs to
// be set BEFORE the builder runs.
import { getProviderForModel } from './providers.js'
import { convertRequest, clampRequestForProvider, stripThinkTags, ThinkTagStripper } from './convert.js'

describe('Gemini env-var alias (Fix 1)', () => {
  test('GEMINI_API_KEY alone is enough to build a Gemini provider', () => {
    delete process.env.GOOGLE_API_KEY
    process.env.GEMINI_API_KEY = 'gemini-only-key'
    const p = getProviderForModel('gemini-2.5-pro')
    expect(p).not.toBeNull()
    expect(p?.apiKey).toBe('gemini-only-key')
  })

  test('GOOGLE_API_KEY alone is also enough (legacy fallback)', () => {
    delete process.env.GEMINI_API_KEY
    process.env.GOOGLE_API_KEY = 'google-legacy-key'
    const p = getProviderForModel('gemini-2.5-pro')
    expect(p).not.toBeNull()
    expect(p?.apiKey).toBe('google-legacy-key')
  })

  test('GEMINI_API_KEY wins when both are set (preferred name first)', () => {
    process.env.GEMINI_API_KEY = 'gemini-wins'
    process.env.GOOGLE_API_KEY = 'google-loses'
    const p = getProviderForModel('gemini-flash')
    expect(p?.apiKey).toBe('gemini-wins')
  })

  test('Neither set → throws with a helpful error message', () => {
    delete process.env.GEMINI_API_KEY
    delete process.env.GOOGLE_API_KEY
    expect(() => getProviderForModel('gemini-2.0-flash')).toThrow(
      /GEMINI_API_KEY.*GOOGLE_API_KEY/,
    )
  })
})

describe('GPT-5 family request shape (Fix 2)', () => {
  // GPT-5* family rejects:
  //   - max_tokens (must be max_completion_tokens)
  //   - temperature !== 1
  // The proxy used to send max_tokens + temperature=0.3, killing every
  // gpt-5* request with HTTP 400.

  beforeAll(() => {
    process.env.OPENAI_API_KEY = 'test-openai'
  })

  test.each(['gpt-5-nano', 'gpt-5-mini', 'gpt-5', 'gpt-5.1'])(
    '%s: emits max_completion_tokens, not max_tokens',
    (modelId) => {
      const p = getProviderForModel(modelId)!
      const out = convertRequest(
        { messages: [{ role: 'user', content: 'hi' }], max_tokens: 50 },
        p,
      )
      expect(out.max_completion_tokens).toBeGreaterThan(0)
      expect(out.max_tokens).toBeUndefined()
    },
  )

  test.each(['gpt-5-nano', 'gpt-5-mini', 'gpt-5', 'gpt-5.1'])(
    '%s: forces temperature to 1 regardless of client request',
    (modelId) => {
      const p = getProviderForModel(modelId)!
      const out = convertRequest(
        { messages: [{ role: 'user', content: 'hi' }], temperature: 0.3 },
        p,
      )
      expect(out.temperature).toBe(1)
    },
  )

  test('gpt-4o (non-GPT-5) keeps the legacy max_tokens + temperature 0.3 shape', () => {
    const p = getProviderForModel('gpt-4o')!
    const out = convertRequest(
      { messages: [{ role: 'user', content: 'hi' }], max_tokens: 50, temperature: 0.3 },
      p,
    )
    expect(out.max_tokens).toBe(50)
    expect(out.max_completion_tokens).toBeUndefined()
    expect(out.temperature).toBe(0.3)
  })
})

describe('Kimi K2.6 temperature pinning (Fix 3)', () => {
  // Moonshot's K2.6 endpoint rejects any temperature !== 1 with HTTP 400
  // "invalid temperature: only 1 is allowed for this model". Same shape as
  // GPT-5. Proxy must pin temperature=1 for the kimi provider.

  test.each([
    'kimi-k2.6-instant',
    'kimi-k2.6-thinking',
    'kimi-k2.6-agent',
    'kimi-k2.6-swarm',
  ])('%s: forces temperature to 1', (modelId) => {
    const p = getProviderForModel(modelId)!
    const out = convertRequest(
      { messages: [{ role: 'user', content: 'hi' }], temperature: 0.2 },
      p,
    )
    expect(out.temperature).toBe(1)
  })

  test('kimi: temperature stays 1 even when the client sets it to 1', () => {
    const p = getProviderForModel('kimi-k2.6-instant')!
    const out = convertRequest(
      { messages: [{ role: 'user', content: 'hi' }], temperature: 1 },
      p,
    )
    expect(out.temperature).toBe(1)
  })

  test('non-kimi provider (deepseek) keeps client temperature', () => {
    const p = getProviderForModel('deepseek-chat')!
    const out = convertRequest(
      { messages: [{ role: 'user', content: 'hi' }], temperature: 0.5 },
      p,
    )
    expect(out.temperature).toBe(0.5)
  })

  test('kimi: low client max_tokens is floored to provider.maxOutputTokens', () => {
    // K2.6 burns 50+ tokens on reasoning_content before any visible
    // text. A small client max_tokens (e.g., 30) → empty answer.
    // Same hidden-reasoning treatment as gpt-oss below.
    const p = getProviderForModel('kimi-k2.6-instant')!
    const out = convertRequest(
      { messages: [{ role: 'user', content: 'hi' }], max_tokens: 30 },
      p,
    )
    expect(out.max_tokens).toBe(p.maxOutputTokens)
  })
})

describe('Gemini upstream model rename (Fix 5)', () => {
  // Google retired `gemini-2.0-flash` for new API keys and never had
  // `gemini-2.5-pro-preview-03-25` on the OpenAI-compat endpoint. Both
  // were 404'ing for fresh accounts. Remap to the current stable ids.
  beforeAll(() => { process.env.GEMINI_API_KEY = 'test-gemini' })

  test.each([
    ['gemini-flash', 'gemini-3.5-flash'],
    ['gemini-2.0-flash', 'gemini-3.5-flash'],
    ['gemini-pro', 'gemini-pro-latest'],
    ['gemini-2.5-pro', 'gemini-pro-latest'],
  ] as const)('jarvis id %s now resolves to upstream %s', (jarvisId, expectedUpstream) => {
    const p = getProviderForModel(jarvisId)!
    expect(p.model).toBe(expectedUpstream)
  })
})

describe('Gemini 2.5 Pro & GPT-5 hidden-reasoning floor (Fix 6)', () => {
  // gemini-2.5-pro and gpt-5/-mini/-nano (but NOT gpt-5.1) generate
  // hidden chain-of-thought tokens that eat the response budget.
  // Symptom: completion_tokens=0, finish_reason=length, content=null.
  // Floor to provider.maxOutputTokens like gpt-oss / kimi / DeepSeek-thinking.

  test.each([
    // Provider defaults bumped in #33 (vendor-docs verification on
    // 2026-05-27). Gemini: 8K → 32K; OpenAI: 16K → 32K. The floor
    // mechanism still works — it just floors to the new larger cap.
    ['gemini-2.5-pro', 32768],
    ['gemini-pro', 32768],
    ['gpt-5', 32768],
    ['gpt-5-mini', 32768],
    ['gpt-5-nano', 32768],
  ] as const)('%s: max_tokens budget is floored to %p', (modelId, expectedMax) => {
    const p = getProviderForModel(modelId)!
    const out = convertRequest(
      { messages: [{ role: 'user', content: 'hi' }], max_tokens: 30 },
      p,
    )
    // GPT-5 family uses max_completion_tokens; the others max_tokens.
    const actual = out.max_completion_tokens ?? out.max_tokens
    expect(actual).toBe(expectedMax)
  })

  test('gpt-5 chat-latest variant (non-reasoning) keeps the client max_completion_tokens', () => {
    // gpt-5.1 now routes to gpt-5.5-pro (a reasoning model → floored). The
    // non-reasoning case is the chat-latest variant, which must NOT be floored.
    const p = getProviderForModel('gpt-5.1-chat-latest')!
    const out = convertRequest(
      { messages: [{ role: 'user', content: 'hi' }], max_tokens: 30 },
      p,
    )
    expect(out.max_completion_tokens).toBe(30)
  })

  test('gemini-flash (3.5-flash upstream) keeps client max_tokens (no floor needed)', () => {
    const p = getProviderForModel('gemini-flash')!
    const out = convertRequest(
      { messages: [{ role: 'user', content: 'hi' }], max_tokens: 30 },
      p,
    )
    expect(out.max_tokens).toBe(30)
  })
})

describe('Qwen <think> tag strip — non-streaming (Fix 7a)', () => {
  // Qwen3-32b (and some other open-source models) emit chain-of-thought
  // inside <think>...</think> blocks INSIDE the visible content. Most
  // models put it in a separate reasoning_content field. The proxy
  // strips those blocks so the CLI doesn't render them as user-visible
  // text. Safe to apply universally — <think> isn't a real HTML tag.

  test('leading think block + answer → strip the think block', () => {
    expect(
      stripThinkTags('<think>let me think about it</think>\n\nactual answer'),
    ).toBe('actual answer')
  })

  test('think block in the middle → strip it, keep surrounding text', () => {
    expect(
      stripThinkTags('before <think>thoughts</think> after'),
    ).toBe('before  after')
  })

  test('multi-line think block (newlines inside)', () => {
    const input =
      '<think>\nstep 1\nstep 2\n</think>\n\nFinal: pong'
    expect(stripThinkTags(input)).toBe('Final: pong')
  })

  test('multiple think blocks all stripped', () => {
    expect(
      stripThinkTags('<think>a</think>x<think>b</think>y'),
    ).toBe('xy')
  })

  test('no think tags → unchanged', () => {
    expect(stripThinkTags('plain text')).toBe('plain text')
  })

  test('only a think block → empty after strip', () => {
    expect(stripThinkTags('<think>only thinking</think>')).toBe('')
  })

  test('empty string → empty string', () => {
    expect(stripThinkTags('')).toBe('')
  })
})

describe('Qwen <think> tag strip — streaming (Fix 7b)', () => {
  // Streaming case is harder: <think> open + </think> close tags can
  // span chunk boundaries. The ThinkTagStripper holds a small lookback
  // buffer of bytes that might be a partial tag start and emits the
  // rest. When inside a block, all bytes are discarded until the close.

  test('full open + close in one chunk → strip, emit remainder', () => {
    const s = new ThinkTagStripper()
    expect(s.feed('<think>x</think>actual')).toBe('actual')
  })

  test('split open across chunks', () => {
    const s = new ThinkTagStripper()
    expect(s.feed('text <thi')).toBe('text ')
    expect(s.feed('nk>thought</think>more')).toBe('more')
  })

  test('split close across chunks', () => {
    const s = new ThinkTagStripper()
    expect(s.feed('<think>think')).toBe('')
    expect(s.feed('ing</thi')).toBe('')
    expect(s.feed('nk>real answer')).toBe('real answer')
  })

  test('no think tags → pass through unchanged', () => {
    const s = new ThinkTagStripper()
    expect(s.feed('hello ')).toBe('hello ')
    expect(s.feed('world')).toBe('world')
  })

  test('tail emit: leftover lookback flushed via end()', () => {
    const s = new ThinkTagStripper()
    // "<t" is a partial-tag candidate so it's held back…
    expect(s.feed('foo <t')).toBe('foo ')
    // …and emitted as final bytes when the stream ends without a complete tag.
    expect(s.end()).toBe('<t')
  })

  test('end() while still inside a think block drops the held bytes', () => {
    const s = new ThinkTagStripper()
    expect(s.feed('<think>still going')).toBe('')
    // Stream ended mid-think; remaining bytes are discarded so the
    // user never sees the unfinished thought.
    expect(s.end()).toBe('')
  })
})

describe('gpt-oss-120b reasoning-budget floor (Fix 4)', () => {
  // Groq's openai/gpt-oss-120b is a hidden-reasoning model — it burns
  // input tokens on chain-of-thought that the proxy then strips via
  // include_reasoning=false. With max_tokens=30 the response budget is
  // gone before any visible text. Treat gpt-oss-* like requiresReasoning
  // models: always get the provider.maxOutputTokens floor.

  test('gpt-oss-120b: max_tokens is provider.maxOutputTokens regardless of client request', () => {
    const p = getProviderForModel('openai/gpt-oss-120b')!
    const out = convertRequest(
      { messages: [{ role: 'user', content: 'hi' }], max_tokens: 30 },
      p,
    )
    expect(out.max_tokens).toBe(p.maxOutputTokens)
  })

  test('non-reasoning groq model (qwen3-32b) respects client max_tokens', () => {
    const p = getProviderForModel('qwen/qwen3-32b')!
    const out = convertRequest(
      { messages: [{ role: 'user', content: 'hi' }], max_tokens: 30 },
      p,
    )
    expect(out.max_tokens).toBe(30)
  })
})

// ── Fix 8: clampRequestForProvider — fallback re-shaping ─────────────────────
//
// Bug: executeWithFallback re-targets each fallback provider by ONLY swapping
// the model field (`{ ...openaiReq, model: provider.model }`). The rest of the
// body stays shaped for the PRIMARY — max_tokens clamped to primary's cap,
// tool list truncated to primary's maxTools, wrong token field name for the
// family. A fallback with a lower cap (e.g. groq qwen3-32b at 32K) receives
// max_tokens=65536 (deepseek primary's cap) and 400s immediately. The 400 is
// non-transient → fallback is defeated exactly when it fires.
//
// Fix: clampRequestForProvider(openaiReq, provider) re-shapes an already-
// converted OpenAI body for a different target provider. The primary path is
// clamped in executeWithFallback (server.ts), not inside convertRequest;
// convertRequest and clampRequestForProvider apply equivalent clamp logic
// independently (verified equivalent by the last test in this describe block).

describe('clampRequestForProvider — fallback re-shaping (Fix 8)', () => {
  // Scenario: deepseek-v4-pro primary (cap 65536, no maxTools limit) fails;
  // fallback is qwen/qwen3-32b (groq, cap 32768, maxTools 20).
  // The already-converted body has max_tokens=65536 + 25 tools.

  function makeFatOpenAIBody(maxTokens: number, toolCount: number) {
    const tools = Array.from({ length: toolCount }, (_, i) => ({
      type: 'function' as const,
      function: { name: `tool_${i}`, description: `tool ${i}`, parameters: {} },
    }))
    return {
      model: 'deepseek-v4-pro',   // primary's model — will be overwritten
      messages: [{ role: 'user', content: 'hi' }],
      max_tokens: maxTokens,
      temperature: 0.3,
      stream: false,
      tools,
      tool_choice: 'auto',
    }
  }

  test('clamps max_tokens to fallback provider cap', () => {
    const groq = getProviderForModel('qwen/qwen3-32b')!
    expect(groq.maxOutputTokens).toBe(32768)
    const body = makeFatOpenAIBody(65536, 5)
    const out = clampRequestForProvider({ ...body, model: groq.model }, groq)
    expect(out.max_tokens).toBeLessThanOrEqual(groq.maxOutputTokens)
    expect(out.max_tokens).toBe(groq.maxOutputTokens)
  })

  test('truncates tools to fallback provider maxTools', () => {
    const groq = getProviderForModel('qwen/qwen3-32b')!
    expect(groq.maxTools).toBe(20)
    const body = makeFatOpenAIBody(65536, 25)
    const out = clampRequestForProvider({ ...body, model: groq.model }, groq)
    expect(out.tools.length).toBeLessThanOrEqual(groq.maxTools!)
    expect(out.tools.length).toBe(groq.maxTools)
  })

  test('does not over-truncate tools when count is already within cap', () => {
    const groq = getProviderForModel('qwen/qwen3-32b')!
    const body = makeFatOpenAIBody(65536, 10)
    const out = clampRequestForProvider({ ...body, model: groq.model }, groq)
    expect(out.tools.length).toBe(10)
  })

  test('uses max_completion_tokens (not max_tokens) when fallback is a GPT-5 provider', () => {
    const gpt5 = getProviderForModel('gpt-5')!
    // Body was shaped for deepseek (max_tokens field)
    const body = makeFatOpenAIBody(65536, 3)
    const out = clampRequestForProvider({ ...body, model: gpt5.model }, gpt5)
    expect(out.max_completion_tokens).toBeDefined()
    expect(out.max_tokens).toBeUndefined()
    expect(out.max_completion_tokens).toBeLessThanOrEqual(gpt5.maxOutputTokens)
  })

  test('uses max_tokens (not max_completion_tokens) when fallback is a non-GPT-5 provider', () => {
    const groq = getProviderForModel('qwen/qwen3-32b')!
    // Body was shaped for gpt-5 (max_completion_tokens field)
    const body = { ...makeFatOpenAIBody(65536, 3), max_completion_tokens: 65536 }
    delete (body as any).max_tokens
    const out = clampRequestForProvider({ ...body, model: groq.model }, groq)
    expect(out.max_tokens).toBeDefined()
    expect(out.max_completion_tokens).toBeUndefined()
    expect(out.max_tokens).toBeLessThanOrEqual(groq.maxOutputTokens)
  })

  test('kimi fallback pins temperature to 1', () => {
    const kimi = getProviderForModel('kimi-k2.6-instant')!
    const body = makeFatOpenAIBody(65536, 2)   // temperature: 0.3
    const out = clampRequestForProvider({ ...body, model: kimi.model }, kimi)
    expect(out.temperature).toBe(1)
  })

  test('non-kimi non-gpt5 fallback keeps temperature', () => {
    const groq = getProviderForModel('qwen/qwen3-32b')!
    const body = { ...makeFatOpenAIBody(65536, 2), temperature: 0.7 }
    const out = clampRequestForProvider({ ...body, model: groq.model }, groq)
    expect(out.temperature).toBe(0.7)
  })

  test('tool_choice dropped when provider does not supportsToolChoice', () => {
    const ollama = getProviderForModel('ollama') ?? {
      name: 'ollama',
      baseUrl: 'http://localhost:11434/v1',
      apiKey: 'ollama',
      model: 'llama3',
      supportsToolChoice: false,
      maxTools: undefined,
      maxOutputTokens: 4096,
      requiresReasoning: false,
      supportsVision: false,
      jarvisModelId: null,
      fallback: [] as readonly string[],
    }
    const body = makeFatOpenAIBody(65536, 2)
    const out = clampRequestForProvider({ ...body, model: ollama.model }, ollama)
    expect(out.tool_choice).toBeUndefined()
  })

  // Idempotency: primary went through executeWithFallback → clampRequestForProvider;
  // running clamp again for the same provider must not change values.
  test('idempotent when applied twice with the same provider', () => {
    const groq = getProviderForModel('qwen/qwen3-32b')!
    const body = makeFatOpenAIBody(65536, 25)
    const once = clampRequestForProvider({ ...body, model: groq.model }, groq)
    const twice = clampRequestForProvider({ ...once }, groq)
    expect(twice.max_tokens).toBe(once.max_tokens)
    expect(twice.tools.length).toBe(once.tools.length)
  })

  // Primary behavior equivalence: convertRequest output for a groq provider
  // must equal clampRequestForProvider applied to an unclamped body for the
  // same provider. This confirms that executeWithFallback's clamp (applied to
  // the primary at index 0) produces the same result as convertRequest alone.
  test('convertRequest primary output equals clampRequestForProvider output for same provider', () => {
    const groq = getProviderForModel('qwen/qwen3-32b')!
    const anthropicReq = {
      messages: [{ role: 'user', content: 'hi' }],
      max_tokens: 65536,
      temperature: 0.3,
      stream: false,
    }
    const viaConvertRequest = convertRequest(anthropicReq, groq)
    // Simulate what executeWithFallback passes to clampRequestForProvider:
    // an already-converted body with the groq model already set.
    const rawBody = {
      model: groq.model,
      messages: viaConvertRequest.messages,
      max_tokens: 65536,    // unclamped primary value
      temperature: 0.3,
      stream: false,
    }
    const viaClamped = clampRequestForProvider(rawBody, groq)
    // Both must produce the same clamped max_tokens.
    expect(viaClamped.max_tokens).toBe(viaConvertRequest.max_tokens)
    expect(viaClamped.max_completion_tokens).toBe(viaConvertRequest.max_completion_tokens)
  })
})

// Converts an OpenAI SSE stream (fetch Response) to Anthropic SSE format
// and writes it to a ReadableStream controller.

import { setReasoning } from './reasoning-cache.js'
import { ThinkTagStripper, modelLeaksThinkTags } from './convert.js'

const HEARTBEAT_INTERVAL_MS = 5000

export type StreamStats = {
  inputTokens: number
  outputTokens: number
  cacheReadTokens: number
  stopReason: string
}

type StreamState = {
  messageId: string
  model: string
  inputTokens: number
  // Subset of inputTokens that hit DeepSeek's prompt cache. Surfaces
  // in the final message_delta as Anthropic's cache_read_input_tokens
  // so the CLI cost-tracker bills them at the cheap cache rate.
  // Always 0 for Groq (no cache).
  cacheReadTokens: number
  textBlockIndex: number | null
  toolBlocks: Map<number, { id: string; name: string; argsAccum: string }>
  nextContentIndex: number
  // Accumulated reasoning_content for the server-side cache. Saved against
  // each tool_call.id as it's observed; the cache is consulted on the
  // follow-up turn to repopulate the OpenAI request's reasoning_content.
  reasoningBuffer: string
}

function sseEvent(event: string, data: unknown): string {
  return `event: ${event}\ndata: ${JSON.stringify(data)}\n\n`
}

function stopReasonFromFinish(finish: string | null): string {
  if (finish === 'tool_calls') return 'tool_use'
  if (finish === 'length') return 'max_tokens'
  return 'end_turn'
}

export async function convertOpenAIStreamToAnthropic(
  openaiResponse: Response,
  model: string,
  controller: ReadableStreamDefaultController<Uint8Array>,
): Promise<StreamStats> {
  const enc = new TextEncoder()
  const send = (event: string, data: unknown) => {
    controller.enqueue(enc.encode(sseEvent(event, data)))
  }
  // Defensive variant for the heartbeat — if the client has disconnected
  // mid-stream the controller throws on enqueue, and we don't want a stray
  // ping tick to surface that as a noisy unhandled rejection.
  const safeSend = (event: string, data: unknown) => {
    try { send(event, data) } catch {}
  }

  // Heartbeat: emit a ping every 5s for the duration of the upstream
  // stream. Anthropic SDK treats pings as no-ops, but their presence
  // keeps intermediaries (load balancers, the CLI's own UI) from
  // declaring the connection dead during long thinking-mode pauses.
  const heartbeat = setInterval(() => {
    safeSend('ping', { type: 'ping' })
  }, HEARTBEAT_INTERVAL_MS)

  const messageId = 'msg_' + Math.random().toString(36).slice(2)
  const state: StreamState = {
    messageId,
    model,
    inputTokens: 0,
    cacheReadTokens: 0,
    textBlockIndex: null,
    toolBlocks: new Map(),
    nextContentIndex: 0,
    reasoningBuffer: '',
  }

  // Qwen3 (and any future open-source model marked by modelLeaksThinkTags)
  // emits <think>...</think> blocks inline in the visible content.
  // Filter them on the fly so the CLI never sees the reasoning text.
  const thinkStripper = modelLeaksThinkTags(model) ? new ThinkTagStripper() : null

  // Send message_start
  send('message_start', {
    type: 'message_start',
    message: {
      id: messageId,
      type: 'message',
      role: 'assistant',
      content: [],
      model,
      stop_reason: null,
      stop_sequence: null,
      usage: { input_tokens: 0, output_tokens: 0 },
    },
  })

  // Send ping
  send('ping', { type: 'ping' })

  const reader = openaiResponse.body!.getReader()
  const decoder = new TextDecoder()
  let buffer = ''
  let finalFinishReason: string | null = null
  let outputTokens = 0
  let stats: StreamStats = {
    inputTokens: 0,
    outputTokens: 0,
    cacheReadTokens: 0,
    stopReason: 'end_turn',
  }

  try {
    while (true) {
      const { done, value } = await reader.read()
      if (done) break
      buffer += decoder.decode(value, { stream: true })

      const lines = buffer.split('\n')
      buffer = lines.pop() ?? ''

      for (const line of lines) {
        if (!line.startsWith('data: ')) continue
        const raw = line.slice(6).trim()
        if (raw === '[DONE]') continue

        let chunk: any
        try { chunk = JSON.parse(raw) } catch { continue }

        const choice = chunk.choices?.[0]
        if (!choice) {
          // Capture usage from top-level (some providers send usage here)
          if (chunk.usage) {
            state.inputTokens = chunk.usage.prompt_tokens ?? 0
            state.cacheReadTokens = chunk.usage.prompt_cache_hit_tokens ?? 0
            outputTokens = chunk.usage.completion_tokens ?? 0
          }
          continue
        }

        const delta = choice.delta ?? {}
        const finish = choice.finish_reason

        // Usage in choice
        if (chunk.usage) {
          state.inputTokens = chunk.usage.prompt_tokens ?? 0
          state.cacheReadTokens = chunk.usage.prompt_cache_hit_tokens ?? 0
          outputTokens = chunk.usage.completion_tokens ?? 0
        }

        // Reasoning (DeepSeek thinking-mode chain-of-thought). Buffered;
        // saved to the reasoning cache against each tool_call.id below.
        if (delta.reasoning_content) {
          state.reasoningBuffer += delta.reasoning_content
        }

        // Text content. For qwen3 (and other think-tag-leaking models)
        // the chunks are routed through the stripper first — chunks
        // that are entirely inside a <think>...</think> block are
        // dropped silently; chunks spanning the close tag emit only
        // the post-think portion.
        if (delta.content) {
          const visible = thinkStripper ? thinkStripper.feed(delta.content) : delta.content
          if (visible) {
            if (state.textBlockIndex === null) {
              state.textBlockIndex = state.nextContentIndex++
              send('content_block_start', {
                type: 'content_block_start',
                index: state.textBlockIndex,
                content_block: { type: 'text', text: '' },
              })
            }
            send('content_block_delta', {
              type: 'content_block_delta',
              index: state.textBlockIndex,
              delta: { type: 'text_delta', text: visible },
            })
          }
        }

        // Tool calls
        if (delta.tool_calls) {
          for (const tc of delta.tool_calls) {
            const tcIndex = tc.index ?? 0
            if (!state.toolBlocks.has(tcIndex)) {
              // New tool block — need id and name
              const blockIndex = state.nextContentIndex++
              state.toolBlocks.set(tcIndex, {
                id: tc.id ?? '',
                name: tc.function?.name ?? '',
                argsAccum: '',
              })
              // Close the text block before opening a tool block
              if (state.textBlockIndex !== null) {
                send('content_block_stop', {
                  type: 'content_block_stop',
                  index: state.textBlockIndex,
                })
                state.textBlockIndex = null
              }
              send('content_block_start', {
                type: 'content_block_start',
                index: blockIndex,
                content_block: {
                  type: 'tool_use',
                  id: tc.id ?? '',
                  name: tc.function?.name ?? '',
                  input: {},
                },
              })
              ;(state.toolBlocks.get(tcIndex) as any)._contentIndex = blockIndex
            }

            const tb = state.toolBlocks.get(tcIndex)!
            if (tc.id) {
              tb.id = tc.id
              if (state.reasoningBuffer) {
                setReasoning(tc.id, state.reasoningBuffer)
              }
            }
            if (tc.function?.name) tb.name = tc.function.name

            if (tc.function?.arguments) {
              tb.argsAccum += tc.function.arguments
              const blockIndex = (tb as any)._contentIndex
              send('content_block_delta', {
                type: 'content_block_delta',
                index: blockIndex,
                delta: { type: 'input_json_delta', partial_json: tc.function.arguments },
              })
            }
          }
        }

        if (finish) finalFinishReason = finish
      }
    }
  } catch (e) {
    console.error('[jarvis-proxy] stream read error:', e)
  } finally {
    clearInterval(heartbeat)
    try { reader.releaseLock() } catch {}

    // Always emit closing events so the Anthropic SDK sees message_stop
    // and the CLI doesn't hang in "assistant is streaming" state, even if
    // the upstream provider connection was interrupted mid-response.

    // Flush any lookback bytes the think-stripper was holding (a tail
    // that turned out NOT to be a partial tag). Emit them before the
    // content_block_stop so the client sees a complete answer.
    if (thinkStripper) {
      const tail = thinkStripper.end()
      if (tail) {
        if (state.textBlockIndex === null) {
          state.textBlockIndex = state.nextContentIndex++
          send('content_block_start', {
            type: 'content_block_start',
            index: state.textBlockIndex,
            content_block: { type: 'text', text: '' },
          })
        }
        send('content_block_delta', {
          type: 'content_block_delta',
          index: state.textBlockIndex,
          delta: { type: 'text_delta', text: tail },
        })
      }
    }

    if (state.textBlockIndex !== null) {
      send('content_block_stop', {
        type: 'content_block_stop',
        index: state.textBlockIndex,
      })
    }

    for (const [, tb] of state.toolBlocks) {
      const blockIndex = (tb as any)._contentIndex
      send('content_block_stop', {
        type: 'content_block_stop',
        index: blockIndex,
      })
    }

    const stopReason = stopReasonFromFinish(finalFinishReason)

    // Usage breakdown sent here so the CLI cost-tracker sees the
    // final input/output/cache split. Anthropic's spec puts
    // input_tokens in message_start, but proxy doesn't know prompt
    // size at that point — DeepSeek/Groq report it in the LAST chunk.
    // The CLI's parser tolerates input_tokens arriving in
    // message_delta.
    send('message_delta', {
      type: 'message_delta',
      delta: { stop_reason: stopReason, stop_sequence: null },
      usage: {
        input_tokens: Math.max(0, state.inputTokens - state.cacheReadTokens),
        output_tokens: outputTokens,
        cache_read_input_tokens: state.cacheReadTokens,
      },
    })

    send('message_stop', { type: 'message_stop' })

    stats = {
      inputTokens: state.inputTokens,
      outputTokens,
      cacheReadTokens: state.cacheReadTokens,
      stopReason,
    }
  }

  return stats
}

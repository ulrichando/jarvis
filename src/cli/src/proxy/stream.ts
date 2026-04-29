// Converts an OpenAI SSE stream (fetch Response) to Anthropic SSE format
// and writes it to a ReadableStream controller.

import { Buffer } from 'node:buffer'

const REASONING_PREFIX = '​​​'
const REASONING_SUFFIX = '​​​'

function encodeReasoningMarker(reasoning: string): string {
  return REASONING_PREFIX + Buffer.from(reasoning, 'utf-8').toString('base64') + REASONING_SUFFIX
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
  // Accumulated reasoning_content for the marker round-trip.
  // The marker is prepended to the first text delta (or emitted as a
  // standalone text block when reasoning → tool_use with no text).
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
) {
  const enc = new TextEncoder()
  const send = (event: string, data: unknown) => {
    controller.enqueue(enc.encode(sseEvent(event, data)))
  }

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

        // Reasoning (DeepSeek thinking-mode chain-of-thought). Buffered for
        // the text-marker round-trip: the marker is prepended to the first
        // text block so reasoning_content survives the SDK serialization.
        if (delta.reasoning_content) {
          state.reasoningBuffer += delta.reasoning_content
        }

        // Text content
        if (delta.content) {
          if (state.textBlockIndex === null) {
            state.textBlockIndex = state.nextContentIndex++
            // Open empty (SDK ignores inline text in start).
            send('content_block_start', {
              type: 'content_block_start',
              index: state.textBlockIndex,
              content_block: { type: 'text', text: '' },
            })
            // Emit reasoning marker as the FIRST delta so reasoning_content
            // survives the round-trip, ahead of the actual content.
            if (state.reasoningBuffer) {
              const markerText = encodeReasoningMarker(state.reasoningBuffer)
              state.reasoningBuffer = ''
              send('content_block_delta', {
                type: 'content_block_delta',
                index: state.textBlockIndex,
                delta: { type: 'text_delta', text: markerText },
              })
            }
          }
          send('content_block_delta', {
            type: 'content_block_delta',
            index: state.textBlockIndex,
            delta: { type: 'text_delta', text: delta.content },
          })
        }

        // Tool calls
        if (delta.tool_calls) {
          for (const tc of delta.tool_calls) {
            const tcIndex = tc.index ?? 0
            if (!state.toolBlocks.has(tcIndex)) {
              // BUG FIX 2026-04-29: previously reserved the tool block
              // index BEFORE the reasoning marker, then emitted the
              // marker at a higher index than the tool. Anthropic SDK
              // requires content_block indices in monotonic order, so
              // the out-of-order marker got dropped → reasoning_content
              // never round-tripped → DeepSeek 400 on next turn.
              // Now: emit the marker block FIRST (at the next free index)
              // so the tool gets the higher index and order is preserved.

              // Close the text block before opening a tool block
              if (state.textBlockIndex !== null) {
                send('content_block_stop', {
                  type: 'content_block_stop',
                  index: state.textBlockIndex,
                })
                state.textBlockIndex = null
              }

              // Reasoning → tool_use with no text: emit a marker-only
              // text block so reasoning_content survives the round-trip.
              // BUG FIX 2026-04-29 (round 2): the Anthropic SDK ignores
              // non-empty `text` in content_block_start and only assembles
              // text from text_delta events. Emit start (empty), delta
              // (marker), then stop.
              if (state.reasoningBuffer) {
                const markerIdx = state.nextContentIndex++
                const markerText = encodeReasoningMarker(state.reasoningBuffer)
                state.reasoningBuffer = ''
                send('content_block_start', {
                  type: 'content_block_start',
                  index: markerIdx,
                  content_block: { type: 'text', text: '' },
                })
                send('content_block_delta', {
                  type: 'content_block_delta',
                  index: markerIdx,
                  delta: { type: 'text_delta', text: markerText },
                })
                send('content_block_stop', {
                  type: 'content_block_stop',
                  index: markerIdx,
                })
              }

              // NOW reserve the tool's index — guaranteed > markerIdx.
              const blockIndex = state.nextContentIndex++
              state.toolBlocks.set(tcIndex, {
                id: tc.id ?? '',
                name: tc.function?.name ?? '',
                argsAccum: '',
              })
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
            if (tc.id) tb.id = tc.id
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
    try { reader.releaseLock() } catch {}

    // Always emit closing events so the Anthropic SDK sees message_stop
    // and the CLI doesn't hang in "assistant is streaming" state, even if
    // the upstream provider connection was interrupted mid-response.

    // Stream interrupted during reasoning with no text or tools yet:
    // emit a marker-only text block so reasoning survives the round-trip.
    // Empty start + delta + stop (SDK ignores text in start).
    if (state.reasoningBuffer && state.textBlockIndex === null && state.toolBlocks.size === 0) {
      const markerIdx = state.nextContentIndex++
      const markerText = encodeReasoningMarker(state.reasoningBuffer)
      send('content_block_start', {
        type: 'content_block_start',
        index: markerIdx,
        content_block: { type: 'text', text: '' },
      })
      send('content_block_delta', {
        type: 'content_block_delta',
        index: markerIdx,
        delta: { type: 'text_delta', text: markerText },
      })
      send('content_block_stop', {
        type: 'content_block_stop',
        index: markerIdx,
      })
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
  }
}

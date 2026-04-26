// Converts an OpenAI SSE stream (fetch Response) to Anthropic SSE format
// and writes it to a ReadableStream controller.

type StreamState = {
  messageId: string
  model: string
  inputTokens: number
  // Track content blocks by index
  thinkingBlockIndex: number | null
  textBlockIndex: number | null
  toolBlocks: Map<number, { id: string; name: string; argsAccum: string }>
  nextContentIndex: number
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
    thinkingBlockIndex: null,
    textBlockIndex: null,
    toolBlocks: new Map(),
    nextContentIndex: 0,
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
            outputTokens = chunk.usage.completion_tokens ?? 0
          }
          continue
        }

        const delta = choice.delta ?? {}
        const finish = choice.finish_reason

        // Usage in choice
        if (chunk.usage) {
          state.inputTokens = chunk.usage.prompt_tokens ?? 0
          outputTokens = chunk.usage.completion_tokens ?? 0
        }

        // Reasoning (DeepSeek thinking-mode chain-of-thought). Streamed
        // BEFORE the actual content per DeepSeek's protocol. Surface as
        // an Anthropic `thinking` content block so the round-trip
        // through Anthropic schema preserves it for the next turn
        // (see convert.ts convertMessages).
        if (delta.reasoning_content) {
          if (state.thinkingBlockIndex === null) {
            state.thinkingBlockIndex = state.nextContentIndex++
            send('content_block_start', {
              type: 'content_block_start',
              index: state.thinkingBlockIndex,
              content_block: { type: 'thinking', thinking: '', signature: '' },
            })
          }
          send('content_block_delta', {
            type: 'content_block_delta',
            index: state.thinkingBlockIndex,
            delta: { type: 'thinking_delta', thinking: delta.reasoning_content },
          })
        }

        // Text content
        if (delta.content) {
          // Close thinking block on first content token — DeepSeek
          // streams reasoning in full before any actual content
          // arrives, so this is where the cut belongs.
          if (state.thinkingBlockIndex !== null) {
            // Anthropic spec requires a signature_delta on thinking
            // blocks before stop. Empty string is fine for proxy
            // round-tripping (we don't verify upstream).
            send('content_block_delta', {
              type: 'content_block_delta',
              index: state.thinkingBlockIndex,
              delta: { type: 'signature_delta', signature: '' },
            })
            send('content_block_stop', {
              type: 'content_block_stop',
              index: state.thinkingBlockIndex,
            })
            state.thinkingBlockIndex = null
          }
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
            delta: { type: 'text_delta', text: delta.content },
          })
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
              // We might need to close the thinking block first
              // (DeepSeek can go reasoning → tool_use with no text
              // in between).
              if (state.thinkingBlockIndex !== null) {
                send('content_block_delta', {
                  type: 'content_block_delta',
                  index: state.thinkingBlockIndex,
                  delta: { type: 'signature_delta', signature: '' },
                })
                send('content_block_stop', {
                  type: 'content_block_stop',
                  index: state.thinkingBlockIndex,
                })
                state.thinkingBlockIndex = null
              }
              // We might need to close the text block first
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
              // Store the content block index alongside the tool block
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
    if (state.thinkingBlockIndex !== null) {
      send('content_block_delta', {
        type: 'content_block_delta',
        index: state.thinkingBlockIndex,
        delta: { type: 'signature_delta', signature: '' },
      })
      send('content_block_stop', {
        type: 'content_block_stop',
        index: state.thinkingBlockIndex,
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

    send('message_delta', {
      type: 'message_delta',
      delta: { stop_reason: stopReason, stop_sequence: null },
      usage: { output_tokens: outputTokens },
    })

    send('message_stop', { type: 'message_stop' })
  }
}

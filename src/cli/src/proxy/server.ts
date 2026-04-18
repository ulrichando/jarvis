import { convertRequest, convertResponse } from './convert.js'
import { convertOpenAIStreamToAnthropic } from './stream.js'
import { getProvider, getProviderForModel } from './providers.js'

const PORT = parseInt(process.env.JARVIS_PROXY_PORT ?? '4000')

console.log(`[jarvis-proxy] Starting on port ${PORT}`)

const server = Bun.serve({
  port: PORT,
  async fetch(req) {
    const url = new URL(req.url)

    // Health check
    if (url.pathname === '/health' || url.pathname === '/v1/health') {
      return new Response(JSON.stringify({ status: 'ok' }), {
        headers: { 'Content-Type': 'application/json' },
      })
    }

    // Messages endpoint (Anthropic SDK sends here)
    if (req.method === 'POST' && (url.pathname.endsWith('/messages') || url.pathname === '/v1/messages')) {
      let anthropicReq: any
      try {
        anthropicReq = await req.json()
      } catch {
        return new Response(JSON.stringify({ error: { message: 'Invalid JSON', type: 'invalid_request_error' } }), {
          status: 400, headers: { 'Content-Type': 'application/json' },
        })
      }

      let provider
      try {
        // If the CLI sent a known model name (e.g. via /model), route by that.
        // Otherwise fall back to the JARVIS_PROVIDER default.
        provider = getProviderForModel(anthropicReq.model) ?? getProvider()
      } catch (e: any) {
        return new Response(JSON.stringify({ error: { message: e.message, type: 'invalid_request_error' } }), {
          status: 400, headers: { 'Content-Type': 'application/json' },
        })
      }

      const isStream = anthropicReq.stream === true
      let openaiReq: any
      try {
        openaiReq = convertRequest(anthropicReq, provider)
      } catch (e: any) {
        console.error('[jarvis-proxy] Conversion error:', e)
        return new Response(JSON.stringify({ error: { message: e.message, type: 'invalid_request_error' } }), {
          status: 400, headers: { 'Content-Type': 'application/json' },
        })
      }

      const cliModel = anthropicReq.model ?? '(default)'
      console.log(`[jarvis-proxy] CLI="${cliModel}" → ${provider.baseUrl}/chat/completions model="${openaiReq.model}" stream=${isStream}`)

      let providerResp: Response
      try {
        providerResp = await fetch(`${provider.baseUrl}/chat/completions`, {
          method: 'POST',
          headers: {
            'Content-Type': 'application/json',
            'Authorization': `Bearer ${provider.apiKey}`,
          },
          body: JSON.stringify(openaiReq),
        })
      } catch (e: any) {
        console.error('[jarvis-proxy] Provider fetch error:', e)
        const errMsg = `Failed to reach ${provider.name}: ${e.message}`

        if (isStream) {
          const enc = new TextEncoder()
          const errStream = new ReadableStream<Uint8Array>({
            start(controller) {
              const errorEvent = `event: error\ndata: ${JSON.stringify({ type: 'error', error: { type: 'api_error', message: errMsg } })}\n\n`
              controller.enqueue(enc.encode(errorEvent))
              controller.close()
            },
          })
          return new Response(errStream, {
            headers: {
              'Content-Type': 'text/event-stream',
              'Cache-Control': 'no-cache',
              'Connection': 'keep-alive',
            },
          })
        }

        return new Response(JSON.stringify({
          type: 'error',
          error: { message: errMsg, type: 'api_error' },
        }), { status: 502, headers: { 'Content-Type': 'application/json' } })
      }

      if (!providerResp.ok) {
        const errText = await providerResp.text()
        console.error(`[jarvis-proxy] Provider error ${providerResp.status}:`, errText)
        const errMsg = `${provider.name} error (${providerResp.status}): ${errText}`

        if (isStream) {
          // For streaming requests, return the error as an SSE stream so the
          // Anthropic SDK doesn't hang waiting for events.
          const enc = new TextEncoder()
          const errStream = new ReadableStream<Uint8Array>({
            start(controller) {
              const errorEvent = `event: error\ndata: ${JSON.stringify({ type: 'error', error: { type: 'api_error', message: errMsg } })}\n\n`
              controller.enqueue(enc.encode(errorEvent))
              controller.close()
            },
          })
          return new Response(errStream, {
            headers: {
              'Content-Type': 'text/event-stream',
              'Cache-Control': 'no-cache',
              'Connection': 'keep-alive',
            },
          })
        }

        return new Response(JSON.stringify({
          type: 'error',
          error: { message: errMsg, type: 'api_error' },
        }), { status: providerResp.status, headers: { 'Content-Type': 'application/json' } })
      }

      if (isStream) {
        const stream = new ReadableStream<Uint8Array>({
          async start(controller) {
            try {
              await convertOpenAIStreamToAnthropic(providerResp, provider.model, controller)
            } catch (e) {
              console.error('[jarvis-proxy] Stream error:', e)
            } finally {
              controller.close()
            }
          },
        })
        return new Response(stream, {
          headers: {
            'Content-Type': 'text/event-stream',
            'Cache-Control': 'no-cache',
            'Connection': 'keep-alive',
          },
        })
      } else {
        const openaiResp = await providerResp.json()
        const anthropicResp = convertResponse(openaiResp, provider.model)
        return new Response(JSON.stringify(anthropicResp), {
          headers: { 'Content-Type': 'application/json' },
        })
      }
    }

    return new Response('Not found', { status: 404 })
  },
})

// Preflight: build the default provider at boot so missing env fails loud
// here instead of returning 401s on the first real request.
try {
  const p = getProvider()
  console.log(`[jarvis-proxy] Ready — provider: ${p.name} (${p.baseUrl})`)
} catch (e: any) {
  console.error(`[jarvis-proxy] FATAL: ${e?.message ?? e}`)
  process.exit(1)
}

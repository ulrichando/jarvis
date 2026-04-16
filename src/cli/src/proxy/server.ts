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
        return new Response(JSON.stringify({
          error: { message: `Failed to reach ${provider.name}: ${e.message}`, type: 'api_error' },
        }), { status: 502, headers: { 'Content-Type': 'application/json' } })
      }

      if (!providerResp.ok) {
        const errText = await providerResp.text()
        console.error(`[jarvis-proxy] Provider error ${providerResp.status}:`, errText)
        return new Response(JSON.stringify({
          error: { message: `${provider.name} error: ${errText}`, type: 'api_error', code: providerResp.status },
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

console.log(`[jarvis-proxy] Ready — provider: ${process.env.JARVIS_PROVIDER ?? 'deepseek'}`)

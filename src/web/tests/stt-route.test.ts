// @vitest-environment node
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { POST } from '@/app/api/stt/route'

// /api/stt was restored 2026-07-09 (dead since the 2026-06-29 provider
// eradication removed its only backend, killing web voice input). These guard
// the proxy contract: key gating, body validation, and the upstream shape.

function sttRequest(file?: Blob): Request {
  const fd = new FormData()
  if (file) fd.append('file', file, 'utterance.webm')
  return new Request('http://localhost/api/stt', { method: 'POST', body: fd })
}

beforeEach(() => {
  vi.stubEnv('OPENAI_API_KEY', '')
  vi.stubEnv('JARVIS_STT_API_KEY', '')
  vi.stubEnv('JARVIS_STT_URL', '')
  vi.stubEnv('JARVIS_STT_MODEL', '')
})

afterEach(() => {
  vi.unstubAllEnvs()
  vi.unstubAllGlobals()
})

describe('/api/stt', () => {
  it('503s when no transcription key is configured', async () => {
    const res = await POST(sttRequest(new Blob(['x'])))
    expect(res.status).toBe(503)
  })

  it('400s without a file', async () => {
    vi.stubEnv('OPENAI_API_KEY', 'sk-test')
    const res = await POST(sttRequest())
    expect(res.status).toBe(400)
  })

  it('400s on a non-multipart body', async () => {
    vi.stubEnv('OPENAI_API_KEY', 'sk-test')
    const res = await POST(
      new Request('http://localhost/api/stt', {
        method: 'POST',
        headers: { 'content-type': 'application/json' },
        body: '{}',
      }),
    )
    expect(res.status).toBe(400)
  })

  it('400s a present-but-empty (zero-byte) file', async () => {
    vi.stubEnv('OPENAI_API_KEY', 'sk-test')
    const res = await POST(sttRequest(new Blob([])))
    expect(res.status).toBe(400)
  })

  it('413s oversize audio without calling upstream', async () => {
    vi.stubEnv('OPENAI_API_KEY', 'sk-test')
    const fetchSpy = vi.fn()
    vi.stubGlobal('fetch', fetchSpy)
    const big = new Blob([new Uint8Array(26 * 1024 * 1024)])
    const res = await POST(sttRequest(big))
    expect(res.status).toBe(413)
    expect(fetchSpy).not.toHaveBeenCalled()
  })

  it('413s on the declared Content-Length before buffering the body', async () => {
    vi.stubEnv('OPENAI_API_KEY', 'sk-test')
    const fetchSpy = vi.fn()
    vi.stubGlobal('fetch', fetchSpy)
    // Small actual body, huge declared length — the pre-buffer gate must fire
    // without ever parsing the form (protects memory, not just the upstream).
    const res = await POST(
      new Request('http://localhost/api/stt', {
        method: 'POST',
        headers: {
          'content-type': 'multipart/form-data; boundary=x',
          'content-length': String(500 * 1024 * 1024),
        },
        body: '--x--',
      }),
    )
    expect(res.status).toBe(413)
    expect(fetchSpy).not.toHaveBeenCalled()
  })

  it('proxies to the OpenAI-compatible endpoint and returns trimmed { text }', async () => {
    vi.stubEnv('OPENAI_API_KEY', 'sk-test')
    const fetchSpy = vi.fn(async (url: string | URL, init?: RequestInit) => {
      expect(String(url)).toBe('https://api.openai.com/v1/audio/transcriptions')
      expect((init?.headers as Record<string, string>).authorization).toBe('Bearer sk-test')
      const body = init?.body as FormData
      expect(body.get('model')).toBe('gpt-4o-mini-transcribe')
      expect(body.get('file')).toBeInstanceOf(Blob)
      return Response.json({ text: '  hello there  ' })
    })
    vi.stubGlobal('fetch', fetchSpy)
    const res = await POST(sttRequest(new Blob(['fake-audio'])))
    expect(res.status).toBe(200)
    expect(await res.json()).toEqual({ text: 'hello there' })
    expect(fetchSpy).toHaveBeenCalledOnce()
  })

  it('honors JARVIS_STT_URL / _MODEL / _API_KEY overrides (self-hosted swap)', async () => {
    vi.stubEnv('JARVIS_STT_URL', 'http://127.0.0.1:9999/v1/audio/transcriptions')
    vi.stubEnv('JARVIS_STT_MODEL', 'faster-whisper-large-v3')
    vi.stubEnv('JARVIS_STT_API_KEY', 'local-key')
    vi.stubEnv('OPENAI_API_KEY', 'sk-should-not-be-used')
    const fetchSpy = vi.fn(async (url: string | URL, init?: RequestInit) => {
      expect(String(url)).toBe('http://127.0.0.1:9999/v1/audio/transcriptions')
      expect((init?.headers as Record<string, string>).authorization).toBe('Bearer local-key')
      expect((init?.body as FormData).get('model')).toBe('faster-whisper-large-v3')
      return Response.json({ text: 'ok' })
    })
    vi.stubGlobal('fetch', fetchSpy)
    const res = await POST(sttRequest(new Blob(['fake-audio'])))
    expect(res.status).toBe(200)
  })

  it('502s on an upstream error without leaking the upstream body', async () => {
    vi.stubEnv('OPENAI_API_KEY', 'sk-test')
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => new Response('key sk-live-XYZ invalid', { status: 401 })),
    )
    const res = await POST(sttRequest(new Blob(['fake-audio'])))
    expect(res.status).toBe(502)
    expect(await res.text()).not.toContain('sk-live')
  })

  it('502s when the upstream fetch throws (timeout/network)', async () => {
    vi.stubEnv('OPENAI_API_KEY', 'sk-test')
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => {
        throw new Error('timeout')
      }),
    )
    const res = await POST(sttRequest(new Blob(['fake-audio'])))
    expect(res.status).toBe(502)
  })

  it('502s on an upstream 200 with a non-JSON body (pins the catch path)', async () => {
    vi.stubEnv('OPENAI_API_KEY', 'sk-test')
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => new Response('<html>not json</html>', { status: 200 })),
    )
    const res = await POST(sttRequest(new Blob(['fake-audio'])))
    expect(res.status).toBe(502)
  })
})

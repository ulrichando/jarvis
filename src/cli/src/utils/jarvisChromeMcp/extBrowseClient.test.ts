// extBrowseClient.test.ts
import { test, expect } from 'bun:test'
import { callExtBrowse } from './extBrowseClient.js'

const bridge = { baseUrl: 'http://127.0.0.1:8765', token: 't' }

function fetchStub(status: number, body: any) {
  return async () => new Response(JSON.stringify(body), { status })
}

test('success returns the extension result', async () => {
  const r = await callExtBrowse('get_url', {}, false, bridge, fetchStub(200, { ok: true, url: 'https://x' }))
  expect(r.ok).toBe(true)
  expect((r.result as any).url).toBe('https://x')
})

test('503 maps to extension-not-connected', async () => {
  const r = await callExtBrowse('click', { selector: 'a' }, false, bridge, fetchStub(503, { ok: false, error: 'extension not connected' }))
  expect(r.ok).toBe(false)
  expect(r.kind).toBe('EXT_NOT_CONNECTED')
})

test('200 needs_confirmation maps to NEEDS_CONFIRMATION', async () => {
  const r = await callExtBrowse('navigate', { url: 'https://x' }, false, bridge, fetchStub(200, { ok: false, needs_confirmation: true }))
  expect(r.kind).toBe('NEEDS_CONFIRMATION')
})

test('connect error maps to BRIDGE_DOWN', async () => {
  const boom = async () => { throw new Error('ECONNREFUSED') }
  const r = await callExtBrowse('get_url', {}, false, bridge, boom)
  expect(r.kind).toBe('BRIDGE_DOWN')
})

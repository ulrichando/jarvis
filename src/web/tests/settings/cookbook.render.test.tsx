import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest'
import { render, screen, cleanup, waitFor } from '@testing-library/react'
import { CookbookSection } from '@/components/settings/cookbook'

// jsdom defaults to http://localhost. Override window.location.protocol per
// test to exercise the hosted (https) vs local (http) branches.
function setProtocol(proto: 'http:' | 'https:') {
  Object.defineProperty(window, 'location', {
    value: { ...window.location, protocol: proto },
    writable: true,
    configurable: true,
  })
}

beforeEach(() => {
  vi.stubGlobal('fetch', vi.fn(async () => new Response(null, { status: 200 })))
})
afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
})

describe('CookbookSection', () => {
  it('hosted (https): shows the desktop-app explainer and NEVER probes the http sidecar', async () => {
    setProtocol('https:')
    render(<CookbookSection />)
    await waitFor(() =>
      expect(screen.getByText(/runs in the Jarvis desktop app/i)).toBeTruthy(),
    )
    // Mixed-content guard: no fetch to the http://127.0.0.1 sidecar.
    expect(fetch).not.toHaveBeenCalled()
    // Provider config link is offered instead of a dead retry.
    const link = screen.getByText(/Configure model providers/i).closest('a')
    expect(link?.getAttribute('href')).toBe('/settings?tab=providers')
  })

  it('local (http): probes the sidecar and embeds the iframe when reachable', async () => {
    setProtocol('http:')
    const { container } = render(<CookbookSection />)
    await waitFor(() => expect(fetch).toHaveBeenCalled())
    await waitFor(() =>
      expect(container.querySelector('iframe[title="Cookbook — local model browser"]')).toBeTruthy(),
    )
  })
})

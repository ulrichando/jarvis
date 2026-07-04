import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest'
import { render, screen, fireEvent, waitFor, cleanup } from '@testing-library/react'
import { ApiTokensSection } from '@/components/settings/api-tokens'

// The component talks to /api/bridge/api-tokens; stub fetch so the render test
// exercises load → generate → one-time reveal without a server or a login.
const tokens: { name: string; created_at: number; last_used_at: number | null; hint: string }[] = []

beforeEach(() => {
  tokens.length = 0
  vi.stubGlobal(
    'fetch',
    vi.fn(async (_url: string, init?: RequestInit) => {
      const method = init?.method ?? 'GET'
      if (method === 'GET') {
        return new Response(JSON.stringify({ tokens }), { status: 200 })
      }
      if (method === 'POST') {
        const { name } = JSON.parse(String(init?.body)) as { name: string }
        tokens.unshift({ name, created_at: Date.now(), last_used_at: null, hint: 'ab12' })
        return new Response(JSON.stringify({ name, token: `jbr_secret_${name}` }), { status: 201 })
      }
      return new Response(JSON.stringify({ revoked: 'x' }), { status: 200 })
    }),
  )
  // navigator.clipboard for the copy button.
  Object.assign(navigator, { clipboard: { writeText: vi.fn(async () => {}) } })
})

afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
})

describe('ApiTokensSection', () => {
  it('renders the heading + empty state, then generates and reveals a token once', async () => {
    render(<ApiTokensSection />)
    // Heading present, empty-state after the initial load resolves.
    expect(screen.getByText('API Tokens')).toBeTruthy()
    await waitFor(() => expect(screen.getByText('No API tokens yet.')).toBeTruthy())

    // Type a name and generate.
    fireEvent.change(screen.getByPlaceholderText(/Token name/i), { target: { value: 'laptop' } })
    fireEvent.click(screen.getByText('Generate'))

    // The one-time secret is revealed, and the new token shows in the list.
    await waitFor(() => expect(screen.getByText('jbr_secret_laptop')).toBeTruthy())
    expect(screen.getByText(/New token/i)).toBeTruthy()
    expect(screen.getAllByText('laptop').length).toBeGreaterThan(0)
  })
})

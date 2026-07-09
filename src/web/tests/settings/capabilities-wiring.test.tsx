import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest'
import { render, screen, waitFor, cleanup } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { Markdown } from '@/components/markdown/markdown'
import { CodeBlock } from '@/components/markdown/code-block'
import { CapabilitiesSection } from '@/components/settings/capabilities'
import { Message } from '@/components/chat/message'
import type { UIMessage } from 'ai'

// Settings → Capabilities wiring. The toggles have always PERSISTED
// (PATCH /api/settings); these tests pin the DOWNSTREAM effects:
//   - markdown OFF  → assistant body renders plain text, not markdown
//   - codeHighlight OFF → CodeBlock skips shiki, renders escaped <pre><code>
//   - Tools rows are informational status, not fake controls

// Deterministic shiki: the real bundle lazily loads grammars/themes (slow,
// wasm) — mock it with a recognizable `.shiki` wrapper so "highlighted vs
// plain" is a cheap synchronous assertion.
vi.mock('shiki', () => ({
  codeToHtml: async (code: string) =>
    `<pre class="shiki"><code><span style="color:#f00">${code
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')}</span></code></pre>`,
}))

// Fetch stub serving /api/settings with per-test capability flags.
let capabilities = { markdown: true, codeHighlight: true, streaming: true }

beforeEach(() => {
  capabilities = { markdown: true, codeHighlight: true, streaming: true }
  vi.stubGlobal(
    'fetch',
    vi.fn(async (url: unknown) => {
      if (String(url).includes('/api/settings')) {
        return new Response(JSON.stringify({ capabilities }), { status: 200 })
      }
      return new Response('{}', { status: 200 })
    }),
  )
})

afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
})

function withQuery(ui: React.ReactElement) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  })
  return render(<QueryClientProvider client={client}>{ui}</QueryClientProvider>)
}

describe('Markdown plain mode (capabilities.markdown OFF)', () => {
  it('default render formats markdown', () => {
    const { container } = render(<Markdown content={'**bold** move'} />)
    expect(container.querySelector('strong')).toBeTruthy()
    expect(container.textContent).not.toContain('**bold**')
  })

  it('plain render shows the raw markdown text with no formatting', () => {
    const { container } = render(<Markdown content={'**bold** move'} plain />)
    expect(container.querySelector('strong')).toBeNull()
    expect(container.textContent).toContain('**bold** move')
  })

  it('plain render still strips internal design-tag blocks', () => {
    const { container } = render(
      <Markdown
        content={'before <jarvisArtifact kind="html" slug="x" title="X">secret</jarvisArtifact> after'}
        plain
      />,
    )
    expect(container.textContent).toContain('before')
    expect(container.textContent).toContain('after')
    expect(container.textContent).not.toContain('secret')
  })
})

describe('Message honors capabilities.markdown', () => {
  const assistant: UIMessage = {
    id: 'm1',
    role: 'assistant',
    parts: [{ type: 'text', text: '**bold** move' }],
  } as UIMessage

  it('renders markdown while the setting is ON', async () => {
    const { container } = withQuery(<Message message={assistant} />)
    await waitFor(() => expect(container.querySelector('strong')).toBeTruthy())
  })

  it('renders plain text once the setting resolves OFF', async () => {
    capabilities = { markdown: false, codeHighlight: true, streaming: true }
    const { container } = withQuery(<Message message={assistant} />)
    // NB: can't wait on raw '**bold**' text — the sr-only live region always
    // holds the unformatted text. Wait for the <strong> to disappear (the
    // settings query resolving OFF flips Markdown into plain mode).
    await waitFor(() => expect(container.querySelector('strong')).toBeNull())
    expect(container.textContent).toContain('**bold** move')
  })
})

describe('CodeBlock honors capabilities.codeHighlight', () => {
  it('highlights via shiki while the setting is ON', async () => {
    const { container } = withQuery(
      <CodeBlock code={'const x = 1'} language="ts" />,
    )
    await waitFor(() => expect(container.querySelector('.shiki')).toBeTruthy())
  })

  it('renders escaped plain <pre><code> when OFF — no shiki markup', async () => {
    capabilities = { markdown: true, codeHighlight: false, streaming: true }
    const { container } = withQuery(
      <CodeBlock code={'const x = 1'} language="ts" />,
    )
    // Wait for the settings query to resolve, then assert no highlight.
    await waitFor(() =>
      expect(
        (globalThis.fetch as ReturnType<typeof vi.fn>).mock.calls.length,
      ).toBeGreaterThan(0),
    )
    await waitFor(() => expect(container.textContent).toContain('const x = 1'))
    expect(container.querySelector('.shiki')).toBeNull()
    expect(container.querySelector('pre code')).toBeTruthy()
  })
})

describe('CapabilitiesSection Tools rows', () => {
  it('shows exactly 3 real switches; Tools rows are informational status', async () => {
    withQuery(<CapabilitiesSection />)
    await waitFor(() =>
      expect(screen.getAllByRole('switch').length).toBe(3),
    )
    // The two tool rows read as status, not controls.
    expect(screen.getAllByText('always on').length).toBe(2)
    expect(screen.getByText(/Status only/i)).toBeTruthy()
    // The old fake-control pill label is gone.
    expect(screen.queryByText(/^enabled$/)).toBeNull()
  })
})

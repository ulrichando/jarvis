import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest'
import { render, screen, fireEvent, cleanup, waitFor } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'

// The shell reads useRouter for the danger-zone redirect; stub it so the
// render test runs without a Next.js app router context.
vi.mock('next/navigation', () => ({
  useRouter: () => ({ replace: vi.fn(), push: vi.fn() }),
}))

import { SettingsTab } from '@/components/workbench/tabs/settings-tab'

// Record every fetched URL so we can assert that hidden sections do NOT
// fire their queries until the user actually opens them (sections are
// conditionally mounted by the shell — the god-component split must not
// regress that).
let fetchedUrls: string[] = []

function jsonResponse(body: unknown) {
  return new Response(JSON.stringify(body), { status: 200 })
}

beforeEach(() => {
  fetchedUrls = []
  vi.stubGlobal(
    'fetch',
    vi.fn(async (input: string | URL | Request) => {
      const url = String(input)
      fetchedUrls.push(url)
      if (url.includes('/runtime')) {
        return jsonResponse({ mode: 'docker', state: 'running', ports: { '5173': 40123 } })
      }
      if (url.includes('/git-status')) {
        return jsonResponse({ isRepo: true, branch: 'master', dirtyCount: 0, lastCommit: null })
      }
      if (url.includes('/db-info')) {
        return jsonResponse({ exists: false, files: [], tables: [] })
      }
      if (url.includes('/commit')) {
        return jsonResponse({ commits: [] })
      }
      if (url.includes('/analytics')) {
        return jsonResponse({
          configured: true,
          total: 3,
          errorCount: 1,
          topRoutes: [],
          statusBuckets: { '2xx': 2, '3xx': 0, '4xx': 0, '5xx': 1 },
          recentErrors: [],
        })
      }
      if (/\/api\/workspace\/ws1(\?|$)/.test(url)) {
        return jsonResponse({
          workspace: {
            id: 'ws1',
            name: 'My Store',
            createdAt: 1700000000000,
            updatedAt: 1700000600000,
            kind: 'workbench',
            envVars: {},
          },
        })
      }
      return jsonResponse({})
    }),
  )
})

afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
})

function renderTab() {
  const qc = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  })
  return render(
    <QueryClientProvider client={qc}>
      <SettingsTab workspaceId="ws1" workspaceName="fallback-name" />
    </QueryClientProvider>,
  )
}

describe('workbench SettingsTab (post-split shell)', () => {
  it('renders the sidebar with all 13 sections and the General section by default', async () => {
    renderTab()

    // Sidebar nav labels — one button per section.
    for (const label of [
      'General',
      'Domains & Hosting',
      'Analytics',
      'Database',
      'Authentication',
      'Server Functions',
      'Secrets',
      'User Management',
      'File Storage',
      'Knowledge',
      'Skills',
      'Backups',
      'Danger zone',
    ]) {
      expect(screen.getByRole('button', { name: label })).toBeTruthy()
    }

    // General section content: header (server name — shown in both the
    // sidebar subtitle and the Header h2), custom instructions, dev
    // command, runtime — all composed from the split-out files.
    await waitFor(() =>
      expect(screen.getAllByText('My Store').length).toBeGreaterThan(0),
    )
    expect(screen.getByText('Custom instructions')).toBeTruthy()
    expect(screen.getByText('Dev command override')).toBeTruthy()
    expect(screen.getByText('Sandbox runtime')).toBeTruthy()
    await waitFor(() => expect(screen.getByText('running')).toBeTruthy())
  })

  it('switches sections on nav click (Secrets, Danger zone)', async () => {
    renderTab()

    fireEvent.click(screen.getByRole('button', { name: 'Secrets' }))
    expect(screen.getByText('Environment variables')).toBeTruthy()
    await waitFor(() =>
      expect(screen.getByText('No environment variables yet.')).toBeTruthy(),
    )

    fireEvent.click(screen.getByRole('button', { name: 'Danger zone' }))
    expect(screen.getByText('Reset files')).toBeTruthy()
    expect(screen.getByText('Delete workspace')).toBeTruthy()
    // Secrets content unmounted.
    expect(screen.queryByText('Environment variables')).toBeNull()
  })

  it('does not fetch a hidden section until it is opened (Analytics)', async () => {
    renderTab()

    // Shell-level queries fire immediately…
    await waitFor(() =>
      expect(fetchedUrls.some((u) => u.includes('/runtime'))).toBe(true),
    )
    // …but the Analytics section (its own polling query) must not — it is
    // only mounted when the user opens it.
    expect(fetchedUrls.some((u) => u.includes('/analytics'))).toBe(false)

    fireEvent.click(screen.getByRole('button', { name: 'Analytics' }))
    expect(screen.getByText('Request analytics')).toBeTruthy()
    await waitFor(() =>
      expect(fetchedUrls.some((u) => u.includes('/analytics'))).toBe(true),
    )
    // Stats render from the stubbed payload.
    await waitFor(() => expect(screen.getByText('Requests')).toBeTruthy())
  })
})

import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest'
import { render, screen, cleanup } from '@testing-library/react'

// Both sections read settings via the query hook; stub it so the render
// exercises the static links without a server.
vi.mock('@/hooks/use-settings', () => ({
  useSettings: () => ({ data: { integrations: {}, chrome: { defaultPolicy: 'allow', blockedSites: [] } } }),
  useUpdateSettings: () => ({ mutateAsync: vi.fn(), isPending: false }),
}))

import { IntegrationsSection } from '@/components/settings/integrations'
import { JarvisInChromeSection } from '@/components/settings/jarvis-in-chrome'

beforeEach(() => {
  vi.stubGlobal('fetch', vi.fn(async () => new Response('{}', { status: 200 })))
})
afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
})

describe('Applications → Jarvis GitHub Bot', () => {
  it('renders the bot card with the install + app-page links', () => {
    render(<IntegrationsSection />)
    expect(screen.getByText('Jarvis GitHub Bot')).toBeTruthy()
    const install = screen.getByText(/Install on GitHub/i).closest('a')
    expect(install?.getAttribute('href')).toBe(
      'https://github.com/apps/talos-hq/installations/new',
    )
    const appPage = screen.getByText(/View app page/i).closest('a')
    expect(appPage?.getAttribute('href')).toBe('https://github.com/apps/talos-hq')
  })
})

describe('Jarvis in Chrome → Download', () => {
  it('renders a download link to the static extension zip', () => {
    render(<JarvisInChromeSection />)
    const dl = screen.getByText(/Download for Chrome/i).closest('a')
    expect(dl?.getAttribute('href')).toBe('/jarvis-in-chrome.zip')
    expect(dl?.hasAttribute('download')).toBe(true)
    expect(screen.getByText(/Load unpacked/i)).toBeTruthy()
  })
})

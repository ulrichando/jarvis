import { describe, expect, test } from 'vitest'
import { existsSync, readFileSync } from 'node:fs'
import { execFileSync } from 'node:child_process'
import { join } from 'node:path'
import { GET } from '@/app/api/route'

// Regression guard for the "https://0wlan.com/api → 404" report: bare /api now
// resolves to a JSON index instead of a dead route handler.
describe('/api index', () => {
  test('GET returns a public ok descriptor, no secrets', async () => {
    const res = GET()
    expect(res.status).toBe(200)
    const body = (await res.json()) as { status: string; endpoints: Record<string, string> }
    expect(body.status).toBe('ok')
    expect(body.endpoints.health).toBe('/api/health')
    // Never leak keys/tokens through the index.
    expect(JSON.stringify(body)).not.toMatch(/jbr_|sk-|ghp_|secret/i)
  })
})

// The Jarvis-in-Chrome download is a committed static asset (the web Docker
// build can't reach the extension source). Guard that it exists and is a real
// extension zip — not empty, and contains the manifest at the folder root.
describe('packed Chrome extension', () => {
  const zip = join(process.cwd(), 'public', 'jarvis-in-chrome.zip')

  test('public/jarvis-in-chrome.zip exists and is non-trivial', () => {
    expect(existsSync(zip)).toBe(true)
    expect(readFileSync(zip).length).toBeGreaterThan(1000)
  })

  test('contains manifest.json under the jarvis-in-chrome/ folder', () => {
    const listing = execFileSync('unzip', ['-l', zip], { encoding: 'utf8' })
    expect(listing).toContain('jarvis-in-chrome/manifest.json')
    expect(listing).toContain('jarvis-in-chrome/background.js')
    // Test harness + node_modules must never ship in the download.
    expect(listing).not.toMatch(/\/tests\//)
    expect(listing).not.toMatch(/node_modules/)
  })
})

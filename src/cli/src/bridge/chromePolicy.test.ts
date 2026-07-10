// src/cli/src/bridge/chromePolicy.test.ts
import { test, expect, afterEach } from 'bun:test'
import { mkdtempSync, writeFileSync, rmSync } from 'node:fs'
import { tmpdir } from 'node:os'
import path from 'node:path'
import {
  decideSitePolicy,
  hostInList,
  hostOf,
  loadChromePolicy,
  _setSettingsFileForTests,
  type ChromePolicy,
} from './chromePolicy.js'

const ALLOW = (blockedSites: string[] = []): ChromePolicy => ({ defaultPolicy: 'allow', blockedSites })
const BLOCK: ChromePolicy = { defaultPolicy: 'block', blockedSites: [] }

afterEach(() => _setSettingsFileForTests(null))

test('hostInList matches exact host and subdomains only', () => {
  expect(hostInList('evil.com', ['evil.com'])).toBe(true)
  expect(hostInList('sub.evil.com', ['evil.com'])).toBe(true)
  expect(hostInList('notevil.com', ['evil.com'])).toBe(false) // not a subdomain
  expect(hostInList('evil.com', ['other.com'])).toBe(false)
})

test('decideSitePolicy: site-agnostic actions always pass, even under block-all', () => {
  expect(decideSitePolicy('list_tabs', null, BLOCK).allow).toBe(true)
  expect(decideSitePolicy('back', 'evil.com', ALLOW(['evil.com'])).allow).toBe(true)
})

test('decideSitePolicy: block-all refuses every site-gated action regardless of host', () => {
  expect(decideSitePolicy('navigate', 'anything.com', BLOCK).allow).toBe(false)
  expect(decideSitePolicy('click', null, BLOCK).allow).toBe(false)
})

test('decideSitePolicy: allow-mode blocks listed host + subdomains, passes others', () => {
  const p = ALLOW(['evil.com'])
  expect(decideSitePolicy('click', 'evil.com', p).allow).toBe(false)
  expect(decideSitePolicy('click', 'app.evil.com', p).allow).toBe(false)
  expect(decideSitePolicy('click', 'good.com', p).allow).toBe(true)
  expect(decideSitePolicy('click', null, p).allow).toBe(true) // unknown host passes under allow
})

test('hostOf extracts lowercased hostname, empty on garbage', () => {
  expect(hostOf('https://APP.Evil.com/path')).toBe('app.evil.com')
  expect(hostOf('not a url')).toBe('')
})

test('loadChromePolicy: missing file → allow-all defaults', () => {
  _setSettingsFileForTests(path.join(tmpdir(), 'does-not-exist-jarvis-settings.json'))
  const p = loadChromePolicy()
  expect(p.defaultPolicy).toBe('allow')
  expect(p.blockedSites).toEqual([])
})

test('loadChromePolicy: reads chrome.{defaultPolicy,blockedSites}, lowercases hosts', () => {
  const dir = mkdtempSync(path.join(tmpdir(), 'jarvis-policy-'))
  const file = path.join(dir, 'settings.json')
  writeFileSync(file, JSON.stringify({ chrome: { defaultPolicy: 'block', blockedSites: ['Evil.COM'] } }))
  _setSettingsFileForTests(file)
  const p = loadChromePolicy()
  expect(p.defaultPolicy).toBe('block')
  expect(p.blockedSites).toEqual(['evil.com'])
  rmSync(dir, { recursive: true, force: true })
})

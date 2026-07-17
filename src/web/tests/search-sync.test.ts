import { describe, expect, test } from 'vitest'
import { readFileSync } from 'node:fs'
import path from 'node:path'
import { cleanResults, isBareHomepage, parseBraveWebResponse } from '@/lib/search/core'
import { runSearchPipeline } from '@/lib/search/pipeline'

// The search pipeline is a MIRRORED module: the hub proxy (src/cli, bun) and
// this Next app (node) each ship their own copy because they build from
// separate Docker contexts. This test is the sync contract — if someone edits
// one side only, CI fails here instead of the two runtimes silently drifting.
describe('search module mirror stays in sync with src/cli', () => {
  const webDir = path.resolve(__dirname, '../src/lib/search')
  const cliDir = path.resolve(__dirname, '../../cli/src/proxy/search')

  for (const file of ['core.ts', 'pipeline.ts']) {
    test(`${file} is byte-identical in both trees`, () => {
      const web = readFileSync(path.join(webDir, file), 'utf8')
      const cli = readFileSync(path.join(cliDir, file), 'utf8')
      expect(web).toBe(cli)
    })
  }
})

// Smoke coverage that the mirror actually works under this runtime/aliasing
// (the deep unit suite lives beside the canonical copy in src/cli).
describe('search core under the web runtime', () => {
  test('parseBraveWebResponse maps the documented shape', () => {
    const hits = parseBraveWebResponse({
      web: {
        results: [
          {
            title: 'T',
            url: 'https://a.com/x',
            description: 'd',
            extra_snippets: ['s1'],
          },
        ],
      },
    })
    expect(hits).toEqual([
      { title: 'T', url: 'https://a.com/x', snippet: 'd', extraSnippets: ['s1'] },
    ])
  })

  test('cleanResults drops the measured homepage garbage', () => {
    const out = cleanResults(
      [
        { title: 'BBC News', url: 'https://www.bbc.com/', snippet: '' },
        {
          title: 'AI agents 2026 explained',
          url: 'https://example.org/ai-agents',
          snippet: 'ai agents 2026',
        },
      ],
      'ai agents 2026',
    )
    expect(out).toHaveLength(1)
    expect(isBareHomepage(out[0].url)).toBe(false)
  })

  test('keyless pipeline never calls Brave (no-key regression guard)', async () => {
    const origFetch = globalThis.fetch
    const calls: string[] = []
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    globalThis.fetch = (async (url: any) => {
      calls.push(String(url))
      return {
        ok: true,
        status: 200,
        headers: new Headers(),
        json: async () => ({ results: [{ title: 'S', url: 'https://s.com/x' }] }),
        text: async () => '',
      } as unknown as Response
    }) as typeof fetch
    const hadBrave = process.env.BRAVE_SEARCH_API_KEY
    const hadSearx = process.env.SEARXNG_URL
    delete process.env.BRAVE_SEARCH_API_KEY
    process.env.SEARXNG_URL = 'http://searx.local'
    try {
      const { provider } = await runSearchPipeline('q')
      expect(provider).toBe('searxng')
      expect(calls.some((u) => u.includes('api.search.brave.com'))).toBe(false)
    } finally {
      globalThis.fetch = origFetch
      if (hadBrave !== undefined) process.env.BRAVE_SEARCH_API_KEY = hadBrave
      else delete process.env.BRAVE_SEARCH_API_KEY
      if (hadSearx !== undefined) process.env.SEARXNG_URL = hadSearx
      else delete process.env.SEARXNG_URL
    }
  })
})

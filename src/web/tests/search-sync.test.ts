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

// Runtime-parity smoke for the NEW research stages under node/vitest (the
// deep suites live beside the canonical copy in src/cli, under bun): the
// no-key fallbacks must hold in this runtime too.
describe('research stages under the web runtime', () => {
  const ENV = [
    'BRAVE_SEARCH_API_KEY',
    'SEARXNG_URL',
    'COHERE_API_KEY',
    'JINA_API_KEY',
    'RERANK_PROVIDER',
    'SEARCH_QUERY_REWRITE',
  ] as const

  test('no rerank key → rerankHits is a pure no-op (no fetch, same order)', async () => {
    const saved = ENV.map((k) => [k, process.env[k]] as const)
    for (const k of ENV) delete process.env[k]
    const origFetch = globalThis.fetch
    const calls: string[] = []
    globalThis.fetch = (async (url: unknown) => {
      calls.push(String(url))
      return { ok: true, status: 200, json: async () => ({}) } as unknown as Response
    }) as typeof fetch
    try {
      const { rerankHits } = await import('@/lib/search/pipeline')
      const hits = [
        { title: 'A', url: 'https://a.com/x', snippet: 'sa' },
        { title: 'B', url: 'https://b.com/y', snippet: 'sb' },
      ]
      const out = await rerankHits('q', hits)
      expect(out).toEqual(hits)
      expect(calls).toEqual([])
    } finally {
      globalThis.fetch = origFetch
      for (const [k, v] of saved) {
        if (v !== undefined) process.env[k] = v
        else delete process.env[k]
      }
    }
  })

  test('query rewrite: off passes through, heuristic splits compound questions', async () => {
    const savedMode = process.env.SEARCH_QUERY_REWRITE
    delete process.env.SEARCH_QUERY_REWRITE
    try {
      const { expandQueries } = await import('@/lib/search/pipeline')
      expect(await expandQueries('raw query untouched', { mode: 'off' })).toEqual([
        'raw query untouched',
      ])
      expect(
        await expandQueries('what is the capital of australia and how many people live there'),
      ).toEqual(['what is the capital of australia', 'how many people live there'])
    } finally {
      if (savedMode !== undefined) process.env.SEARCH_QUERY_REWRITE = savedMode
      else delete process.env.SEARCH_QUERY_REWRITE
    }
  })
})

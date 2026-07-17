import { describe, expect, test } from 'bun:test'

import {
  applyConnectorPayload,
  buildConnectorPayload,
  MCP_CONNECTOR_BETA,
} from './mcpConnectors.js'

describe('buildConnectorPayload', () => {
  test('keeps only enabled remote servers, pairs each with one toolset, resolves the bearer', () => {
    const servers = {
      github:  { transport: 'http', url: 'https://mcp.github.example/mcp', headers: { Authorization: 'Bearer ght_abc' } },
      localfs: { command: 'mcp-fs', args: ['/tmp'] },                                   // stdio (no url) → excluded
      notion:  { transport: 'sse', url: 'https://mcp.notion.example/sse', disabled: true }, // disabled → excluded
      publics: { transport: 'http', url: 'https://mcp.public.example/mcp' },            // no token → included, no authorization_token
      bareurl: { url: 'https://mcp.bare.example/mcp' },                                 // absent transport + url ⇒ http → included
    }
    const { servers: out, toolsets } = buildConnectorPayload(servers as any)

    expect(out.map(s => s.name).sort()).toEqual(['bareurl', 'github', 'publics'])
    // Anthropic rule: every server referenced by exactly one MCPToolset.
    expect(toolsets.map(t => t.mcp_server_name).sort()).toEqual(['bareurl', 'github', 'publics'])
    expect(toolsets).toHaveLength(out.length)

    expect(out.find(s => s.name === 'github')).toEqual({
      type: 'url',
      name: 'github',
      url: 'https://mcp.github.example/mcp',
      authorization_token: 'ght_abc',
    })
    expect(out.find(s => s.name === 'publics')!.authorization_token).toBeUndefined()
    expect(toolsets[0]).toMatchObject({ type: 'mcp_toolset' })
  })

  test('falls back to a non-expired OAuth access token; drops expired', () => {
    const servers = {
      fresh: { transport: 'http', url: 'https://a.example/mcp' },
      stale: { transport: 'http', url: 'https://b.example/mcp' },
    }
    const oauth = {
      servers: {
        fresh: { tokens: { access_token: 'good', expires_in: 3600 }, obtainedAt: Date.now() },
        stale: { tokens: { access_token: 'old', expires_in: 60 }, obtainedAt: Date.now() - 7_200_000 }, // 2h ago → expired
      },
    }
    const { servers: out } = buildConnectorPayload(servers as any, oauth as any)
    expect(out.find(s => s.name === 'fresh')!.authorization_token).toBe('good')
    expect(out.find(s => s.name === 'stale')!.authorization_token).toBeUndefined()
  })

  test('mirrored header token wins over the OAuth store', () => {
    const { servers: out } = buildConnectorPayload(
      { s: { transport: 'http', url: 'https://x/mcp', headers: { Authorization: 'Bearer header-tok' } } } as any,
      { servers: { s: { tokens: { access_token: 'oauth-tok' } } } } as any,
    )
    expect(out[0]!.authorization_token).toBe('header-tok')
  })

  test('empty / missing config → empty payload', () => {
    expect(buildConnectorPayload(undefined)).toEqual({ servers: [], toolsets: [] })
    expect(buildConnectorPayload({})).toEqual({ servers: [], toolsets: [] })
  })
})

describe('applyConnectorPayload', () => {
  test('appends servers + toolsets to existing tools and merges the beta header', () => {
    const req: any = { model: 'claude-opus-4-8', tools: [{ name: 'ui_tap' }, { name: 'web_search' }] }
    const headers: Record<string, string> = { 'anthropic-beta': 'interleaved-thinking-2025-05-14' }

    applyConnectorPayload(req, headers, {
      servers: [{ type: 'url', name: 'github', url: 'https://x/mcp', authorization_token: 't' }],
      toolsets: [{ type: 'mcp_toolset', mcp_server_name: 'github' }],
    })

    // Existing device tools preserved; toolset APPENDED (not replaced).
    expect(req.tools.map((t: any) => t.name ?? t.type)).toEqual(['ui_tap', 'web_search', 'mcp_toolset'])
    expect(req.mcp_servers).toHaveLength(1)
    expect(headers['anthropic-beta']).toBe(`interleaved-thinking-2025-05-14,${MCP_CONNECTOR_BETA}`)
  })

  test('no servers → request + headers untouched', () => {
    const req: any = { tools: [{ name: 'ui_tap' }] }
    const headers: Record<string, string> = {}
    applyConnectorPayload(req, headers, { servers: [], toolsets: [] })
    expect(req.mcp_servers).toBeUndefined()
    expect(req.tools).toHaveLength(1)
    expect(headers['anthropic-beta']).toBeUndefined()
  })

  test('creates anthropic-beta and the tools array when absent; dedups the beta', () => {
    const req: any = {}
    const headers: Record<string, string> = { 'anthropic-beta': MCP_CONNECTOR_BETA } // already present
    applyConnectorPayload(req, headers, {
      servers: [{ type: 'url', name: 'g', url: 'https://x/mcp' }],
      toolsets: [{ type: 'mcp_toolset', mcp_server_name: 'g' }],
    })
    expect(headers['anthropic-beta']).toBe(MCP_CONNECTOR_BETA) // no duplicate
    expect(req.tools).toEqual([{ type: 'mcp_toolset', mcp_server_name: 'g' }])
  })
})

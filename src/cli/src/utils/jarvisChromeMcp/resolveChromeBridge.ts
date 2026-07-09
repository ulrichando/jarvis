// src/cli/src/utils/jarvisChromeMcp/resolveChromeBridge.ts
import { readFileSync } from 'fs'
import { homedir } from 'os'
import { join } from 'path'

const TOKEN_FILE = join(homedir(), '.jarvis', 'local-api-token.env')

/** Extract JARVIS_LOCAL_API_TOKEN=<value> from an env-file body (quotes optional). */
export function parseLocalApiToken(body: string): string | null {
  const m = body.match(/^\s*JARVIS_LOCAL_API_TOKEN\s*=\s*(.+?)\s*$/m)
  if (!m) return null
  return m[1].replace(/^["']|["']$/g, '')
}

export function resolveBaseUrl(env: Record<string, string | undefined> = process.env): string {
  return env.JARVIS_CHROME_BRIDGE_URL || 'http://127.0.0.1:8765'
}

export type ChromeBridge = { baseUrl: string; token: string }

/** Throws a bridge-not-running error if the desktop never wrote the token file. */
export function resolveChromeBridge(): ChromeBridge {
  const baseUrl = resolveBaseUrl()
  let token: string | null = null
  try {
    token = parseLocalApiToken(readFileSync(TOKEN_FILE, 'utf8'))
  } catch {
    token = null
  }
  if (!token) {
    const e = new Error('BRIDGE_DOWN')
    ;(e as any).code = 'BRIDGE_DOWN'
    throw e
  }
  return { baseUrl, token }
}

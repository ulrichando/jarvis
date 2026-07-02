/* eslint-disable custom-rules/no-process-exit -- CLI subcommand handler intentionally exits */

/**
 * `jarvis keys pull` — sync provider API keys from the JARVIS server.
 *
 * Server side: GET /api/bridge/v1/keys (strict Remote Control token gate —
 * needs `jarvis auth login` first). Local side: upsert the returned
 * NAME=value pairs into ~/.jarvis/keys.env — the same store every launcher
 * sources, the desktop tray edits, and the voice agent loads at start.
 * The server is the source of truth; the local file is the cache.
 *
 * Values are never printed — only which names changed. Running services
 * (voice agent, :4000 proxy) read keys.env at start, so a restart is needed
 * before they see updated keys; the summary says so when anything changed.
 */

import {
  keysEnvPath,
  readKeysEnvValue,
  upsertKeysEnv,
} from '../../utils/jarvisKeysEnv.js'
import { resolveServerRoot } from './jarvisAuth.js'

const TOKEN_KEY = 'JARVIS_BRIDGE_TOKEN'
const FETCH_TIMEOUT_MS = 10_000

function fail(message: string): never {
  process.stderr.write(message.endsWith('\n') ? message : message + '\n')
  process.exit(1)
}

export async function jarvisKeysPull(
  opts: { url?: string } = {},
): Promise<void> {
  const root = resolveServerRoot(opts.url)
  const token = process.env[TOKEN_KEY] ?? readKeysEnvValue(TOKEN_KEY)
  if (!token) {
    fail('Not logged in — run `jarvis auth login` first.')
  }

  let res: Response
  try {
    res = await fetch(`${root}/api/bridge/v1/keys`, {
      headers: { authorization: `Bearer ${token}` },
      signal: AbortSignal.timeout(FETCH_TIMEOUT_MS),
    })
  } catch (e) {
    fail(
      `Could not reach ${root}: ${e instanceof Error ? e.message : String(e)}`,
    )
  }
  if (res.status === 401) {
    fail('Server rejected the token — run `jarvis auth login` again.')
  }
  if (!res.ok) {
    fail(`Server error: HTTP ${res.status}`)
  }

  const body = (await res.json().catch(() => null)) as {
    keys?: Record<string, string>
  } | null
  const keys = body?.keys ?? {}
  const names = Object.keys(keys).sort()
  if (names.length === 0) {
    process.stdout.write(
      'Server has no provider keys configured — nothing to pull.\n',
    )
    return
  }

  const changed = names.filter(
    (n) => (readKeysEnvValue(n) ?? '') !== keys[n],
  )
  upsertKeysEnv(keys)

  process.stdout.write(
    `Pulled ${names.length} provider key(s) from ${root} into ${keysEnvPath()}\n`,
  )
  for (const n of names) {
    process.stdout.write(
      `  ${changed.includes(n) ? 'updated ' : 'unchanged'} ${n}\n`,
    )
  }
  if (changed.length > 0) {
    process.stdout.write(
      'Restart consumers to pick the changes up (e.g. `systemctl --user restart jarvis-voice-agent jarvis-proxy`).\n',
    )
  }
}

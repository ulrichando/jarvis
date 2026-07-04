/**
 * Shared client for the CLI↔JARVIS-server bridge endpoints (`jarvis cloud`,
 * `jarvis teleport`). One place for base-URL + token resolution and the
 * stale-token 401 message, so every subcommand fails the same actionable way
 * instead of a bare "server returned 401".
 */

import { readKeysEnvValue } from '../../utils/jarvisKeysEnv.js'

const TIMEOUT_MS = 20_000

export function bridgeFail(message: string): never {
  process.stderr.write(message.endsWith('\n') ? message : message + '\n')
  process.exit(1)
}

export function bridgeAuth(): { base: string; token: string } {
  const base =
    process.env.JARVIS_BRIDGE_BASE_URL ||
    readKeysEnvValue('JARVIS_BRIDGE_BASE_URL') ||
    process.env.JARVIS_SERVER_URL ||
    readKeysEnvValue('JARVIS_SERVER_URL')
  const token =
    process.env.JARVIS_BRIDGE_TOKEN || readKeysEnvValue('JARVIS_BRIDGE_TOKEN')
  if (!base || !token) {
    bridgeFail('Not linked to a JARVIS server. Run `jarvis auth login` first.')
  }
  return { base: base.replace(/\/+$/, ''), token }
}

export async function bridgeFetch<T>(
  path: string,
  init: { method?: string; body?: unknown } = {},
): Promise<T> {
  const { base, token } = bridgeAuth()
  let res: Response
  try {
    res = await fetch(`${base}${path}`, {
      method: init.method ?? 'GET',
      headers: {
        authorization: `Bearer ${token}`,
        ...(init.body !== undefined ? { 'content-type': 'application/json' } : {}),
      },
      ...(init.body !== undefined ? { body: JSON.stringify(init.body) } : {}),
      signal: AbortSignal.timeout(TIMEOUT_MS),
    })
  } catch (e) {
    bridgeFail(`Could not reach ${base}: ${e instanceof Error ? e.message : String(e)}`)
  }
  if (res.status === 401) {
    bridgeFail('Server rejected the token (it may have expired) — run `jarvis auth login` again.')
  }
  if (!res.ok) {
    const detail = await res
      .json()
      .then((j: { error?: { message?: string } }) => j?.error?.message ?? '')
      .catch(() => '')
    bridgeFail(`Server error: HTTP ${res.status}${detail ? ` — ${detail}` : ''}`)
  }
  return (await res.json()) as T
}

export type CliSessionRow = {
  session_id: string
  title: string
  repo: string
  updated_at: number | null
}

export async function listCloudSessions(): Promise<CliSessionRow[]> {
  const j = await bridgeFetch<{ sessions?: CliSessionRow[] }>(
    '/api/bridge/v1/cli/sessions',
  )
  return j.sessions ?? []
}

export function formatAge(ts: number | null): string {
  if (!ts) return ''
  const mins = Math.max(0, Math.round((Date.now() - ts) / 60_000))
  if (mins < 60) return `${mins}m ago`
  const hours = Math.round(mins / 60)
  if (hours < 48) return `${hours}h ago`
  return `${Math.round(hours / 24)}d ago`
}

export function printSessionTable(rows: CliSessionRow[]): void {
  rows.forEach((s, i) => {
    process.stdout.write(
      `${String(i + 1).padStart(2)}. ${s.session_id}  ${formatAge(s.updated_at).padEnd(8)} ${s.repo.padEnd(28)} ${s.title.slice(0, 60)}\n`,
    )
  })
}

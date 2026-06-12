/**
 * Read/write helpers for ~/.jarvis/keys.env — the user-local secret store
 * shared by every launcher (cli start.sh, run-cli.mjs, start-desktop.sh, and
 * the voice-agent's _load_user_keys_env()). All consumers parse plain
 * `KEY=value` lines: bash `source` under `set -a`, and line-splitting loaders
 * that take the raw text after the first `=` (no quote stripping, no `export`
 * prefix support in the JS/Python loaders).
 *
 * Because there is no quoting layer, values are restricted to characters that
 * every consumer reads back identically (SAFE_VALUE below). Callers should
 * treat a throw as "this value cannot be represented in keys.env".
 */

import {
  existsSync,
  mkdirSync,
  readFileSync,
  renameSync,
  statSync,
  writeFileSync,
} from 'node:fs'
import { homedir } from 'node:os'
import { dirname, join } from 'node:path'

export function keysEnvPath(): string {
  return join(homedir(), '.jarvis', 'keys.env')
}

const SAFE_KEY = /^[A-Z][A-Z0-9_]*$/
// URLs (http://host:3000/path) + token shapes (jbr_<base64url>) + typical API
// keys. Anything with whitespace, quotes, `$`, `#`, `;`, `&`, backslash, etc.
// would parse differently between bash-source and the line-split loaders.
const SAFE_VALUE = /^[A-Za-z0-9_\-.:/+=@~%]*$/

/** Any line whose key matches — tolerates leading whitespace and a legacy
 * `export ` prefix (normalized away on rewrite; the JS/Python loaders never
 * understood `export` anyway). */
const LINE_KEY_RE = /^\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)=/

function assertWritable(key: string, value: string): void {
  if (!SAFE_KEY.test(key)) {
    throw new Error(`keys.env: invalid key ${JSON.stringify(key)}`)
  }
  if (!SAFE_VALUE.test(value)) {
    throw new Error(
      `keys.env: value for ${key} contains characters the keys.env consumers ` +
        `(bash source / run-cli.mjs / voice-agent loader) do not parse consistently`,
    )
  }
}

function readLines(path: string): string[] | null {
  if (!existsSync(path)) return null
  const lines = readFileSync(path, 'utf8').split('\n')
  // Drop trailing blank lines; a single final newline is re-added on write.
  while (lines.length > 0 && lines[lines.length - 1]!.trim() === '') {
    lines.pop()
  }
  return lines
}

function writeLines(path: string, lines: string[], existed: boolean): void {
  const mode = existed ? statSync(path).mode & 0o777 : 0o600
  mkdirSync(dirname(path), { recursive: true })
  const tmp = `${path}.tmp.${process.pid}`
  writeFileSync(tmp, lines.length === 0 ? '' : lines.join('\n') + '\n', {
    mode,
  })
  renameSync(tmp, path)
}

/**
 * Set the given keys in keys.env, preserving every other line (comments,
 * ordering, unrelated keys). Existing entries are rewritten in place — every
 * occurrence, since all loaders are last-wins and a stale duplicate would
 * shadow the new value. Missing keys are appended. Creates the file (0600)
 * and ~/.jarvis/ if absent; atomic via temp+rename.
 */
export function upsertKeysEnv(
  entries: Record<string, string>,
  path: string = keysEnvPath(),
): void {
  for (const [key, value] of Object.entries(entries)) {
    assertWritable(key, value)
  }
  const existing = readLines(path)
  const lines = existing ?? []
  const unseen = new Set(Object.keys(entries))
  const out = lines.map(line => {
    const match = LINE_KEY_RE.exec(line)
    const key = match?.[1]
    if (key !== undefined && key in entries) {
      unseen.delete(key)
      return `${key}=${entries[key]}`
    }
    return line
  })
  for (const key of Object.keys(entries)) {
    if (unseen.has(key)) out.push(`${key}=${entries[key]}`)
  }
  writeLines(path, out, existing !== null)
}

/**
 * Remove the given keys from keys.env (every occurrence). Returns true if
 * anything was removed. Missing file or keys are a no-op.
 */
export function removeKeysEnvKeys(
  keys: string[],
  path: string = keysEnvPath(),
): boolean {
  const lines = readLines(path)
  if (lines === null) return false
  const drop = new Set(keys)
  const out = lines.filter(line => {
    const match = LINE_KEY_RE.exec(line)
    return !(match?.[1] !== undefined && drop.has(match[1]))
  })
  if (out.length === lines.length) return false
  writeLines(path, out, true)
  return true
}

/** Current value of a key in keys.env (last occurrence wins, matching the
 * loaders), or undefined. */
export function readKeysEnvValue(
  key: string,
  path: string = keysEnvPath(),
): string | undefined {
  const lines = readLines(path)
  if (lines === null) return undefined
  let value: string | undefined
  for (const line of lines) {
    const match = LINE_KEY_RE.exec(line)
    if (match?.[1] === key) {
      value = line.slice(line.indexOf('=') + 1).trim()
    }
  }
  return value
}
